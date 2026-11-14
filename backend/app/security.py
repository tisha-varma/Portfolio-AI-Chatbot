"""Rate limits, body-size guards, security headers, and request logging."""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any

from starlette.datastructures import MutableHeaders

from app.config import Settings

logger = logging.getLogger(__name__)

CHAT_BODY_LIMIT_BYTES = 64 * 1024
MINUTE_WINDOW_SECONDS = 60
DAY_WINDOW_SECONDS = 24 * 60 * 60
CLEANUP_INTERVAL_SECONDS = 60

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'"
    ),
}

SUSPICIOUS_PHRASES = (
    "ignore previous instructions",
    "ignore all instructions",
    "system prompt",
    "developer mode",
    "jailbreak",
)

RATE_LIMITED_MESSAGE = "Too many requests. Please wait a moment and try again."
DAILY_LIMIT_MESSAGE = (
    "The assistant has reached today's limit. Please try again tomorrow"
)
MAINTENANCE_MESSAGE = "The assistant is taking a short break. Please try again later"
TOO_LARGE_MESSAGE = "That request is too large."
INTERNAL_MESSAGE = "Something went wrong."


@dataclass(frozen=True)
class LimitDecision:
    """Outcome of one rate-limit check."""

    allowed: bool
    retry_after: int = 0
    reason: str = ""


class RateLimiter:
    """Thread-safe in-memory sliding-window limiter with periodic cleanup."""

    def __init__(
        self,
        *,
        per_ip_day_limit: int,
        global_daily_cap: int,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._per_ip_day_limit = per_ip_day_limit
        self._global_daily_cap = global_daily_cap
        self._clock = clock
        self._utcnow = utcnow
        self._lock = threading.Lock()
        self._minute: dict[tuple[str, str], deque[float]] = {}
        self._day: dict[str, deque[float]] = {}
        self._global_date = self._utcnow().date()
        self._global_count = 0
        self._last_cleanup = self._clock()

    def tracked_buckets(self) -> tuple[int, int]:
        """Return the live minute and day bucket counts for tests."""
        with self._lock:
            return len(self._minute), len(self._day)

    def acquire(self, ip: str, kind: str, minute_limit: int) -> LimitDecision:
        """Reserve one request for ``ip`` or report why it was rejected."""
        with self._lock:
            now = self._clock()
            utc_now = self._utcnow()
            today = utc_now.date()
            self._maybe_cleanup(now)

            if self._global_date != today:
                self._global_date = today
                self._global_count = 0

            if self._global_count >= self._global_daily_cap:
                tomorrow = datetime.combine(
                    today + timedelta(days=1), dt_time.min, tzinfo=timezone.utc
                )
                retry = math.ceil((tomorrow - utc_now).total_seconds())
                return LimitDecision(False, max(1, retry), "global")

            day_events = self._events(self._day, ip, now, DAY_WINDOW_SECONDS)
            if len(day_events) >= self._per_ip_day_limit:
                retry = day_events[0] + DAY_WINDOW_SECONDS - now
                return LimitDecision(False, max(1, math.ceil(retry)), "day")

            minute_key = (ip, kind)
            minute_events = self._events(
                self._minute, minute_key, now, MINUTE_WINDOW_SECONDS
            )
            if len(minute_events) >= minute_limit:
                retry = minute_events[0] + MINUTE_WINDOW_SECONDS - now
                return LimitDecision(False, max(1, math.ceil(retry)), "minute")

            minute_events.append(now)
            day_events.append(now)
            self._global_count += 1

        return LimitDecision(True)

    def _events(
        self,
        mapping: dict[Any, deque[float]],
        key: Any,
        now: float,
        window: int,
    ) -> deque[float]:
        """Return the live event deque for a key, pruning expired entries."""
        events = mapping.setdefault(key, deque())
        cutoff = now - window
        while events and events[0] <= cutoff:
            events.popleft()
        return events

    def _maybe_cleanup(self, now: float) -> None:
        """Drop empty and expired buckets so memory cannot grow without bound."""
        if now - self._last_cleanup < CLEANUP_INTERVAL_SECONDS:
            return
        self._last_cleanup = now
        self._clean_mapping(self._minute, now, MINUTE_WINDOW_SECONDS)
        self._clean_mapping(self._day, now, DAY_WINDOW_SECONDS)

    @staticmethod
    def _clean_mapping(
        mapping: dict[Any, deque[float]], now: float, window: int
    ) -> None:
        """Remove buckets whose newest event is outside the window."""
        stale = [
            key
            for key, events in mapping.items()
            if not events or events[-1] <= now - window
        ]
        for key in stale:
            mapping.pop(key, None)


def _header(scope: dict[str, Any], name: bytes) -> str | None:
    """Read a request header from an ASGI scope."""
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def client_ip(scope: dict[str, Any], trust_proxy: bool) -> str:
    """Return the visitor IP, honouring the right-most forwarded hop if trusted.

    The direct peer is used by default. When ``TRUSTED_PROXY`` is enabled, the
    right-most valid X-Forwarded-For hop is used because it is the hop closest
    to the trusted reverse proxy. With one proxy in front of the app, that is
    the real client; with several, configure the platform to append, not
    replace, the header.
    """
    direct = str(scope.get("client", ("unknown", 0))[0])
    if not trust_proxy:
        return direct

    forwarded = _header(scope, b"x-forwarded-for")
    if not forwarded:
        return direct

    for hop in reversed([part.strip() for part in forwarded.split(",") if part.strip()]):
        try:
            return str(ipaddress.ip_address(hop))
        except ValueError:
            continue
    return direct


class SecurityMiddleware:
    """Apply limits, size checks, headers, and JSON access logging."""

    def __init__(
        self,
        app: Callable[..., Any],
        settings: Settings | None = None,
        *,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.app = app
        self._trust_proxy = settings.trusted_proxy if settings else False
        self._maintenance = settings.maintenance_mode if settings else False
        self._chat_minute_limit = settings.rate_limit_per_minute if settings else 10
        self._fit_minute_limit = settings.rate_limit_fit_per_minute if settings else 3
        self._max_upload_bytes = settings.max_upload_bytes if settings else 2_000_000
        self._contact_email = settings.contact_email if settings else ""
        self._limiter = limiter or RateLimiter(
            per_ip_day_limit=settings.rate_limit_per_day_per_ip if settings else 60,
            global_daily_cap=settings.global_daily_request_cap if settings else 400,
        )

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Any],
        send: Callable[..., Any],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        method = str(scope.get("method", "GET")).upper()
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.monotonic()
        status: int | None = None

        async def send_with_headers(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                headers["X-Request-ID"] = request_id
            await send(message)

        error_type: str | None = None
        message_count: int | None = None
        suspicious_input = False

        try:
            if self._maintenance and path.startswith("/api/"):
                await self._json_response(
                    send_with_headers,
                    503,
                    "busy",
                    self._with_contact(MAINTENANCE_MESSAGE),
                )
                return

            is_api = path.startswith("/api/")
            if is_api and method == "POST":
                decision = self._limiter.acquire(
                    client_ip(scope, self._trust_proxy),
                    "fit" if path.startswith("/api/fit") else "chat",
                    self._fit_minute_limit
                    if path.startswith("/api/fit")
                    else self._chat_minute_limit,
                )
                if not decision.allowed:
                    if decision.reason == "global":
                        await self._json_response(
                            send_with_headers,
                            503,
                            "busy",
                            self._with_contact(DAILY_LIMIT_MESSAGE),
                            retry_after=decision.retry_after,
                        )
                    else:
                        await self._json_response(
                            send_with_headers,
                            429,
                            "rate_limited",
                            RATE_LIMITED_MESSAGE,
                            retry_after=decision.retry_after,
                        )
                    return

            body_limit = self._body_limit(path)
            if body_limit is not None and method == "POST":
                declared = _header(scope, b"content-length")
                if declared is not None:
                    try:
                        declared_bytes = int(declared)
                    except ValueError:
                        declared_bytes = 0
                    if declared_bytes > body_limit:
                        await self._json_response(
                            send_with_headers, 413, "invalid", TOO_LARGE_MESSAGE
                        )
                        return

                body, too_large = await self._read_body(receive, body_limit)
                if too_large:
                    await self._json_response(
                        send_with_headers, 413, "invalid", TOO_LARGE_MESSAGE
                    )
                    return

                replay_sent = False
                original_receive = receive

                async def replay() -> dict[str, Any]:
                    nonlocal replay_sent
                    if not replay_sent:
                        replay_sent = True
                        return {
                            "type": "http.request",
                            "body": body,
                            "more_body": False,
                        }
                    return await original_receive()

                receive = replay
                if path == "/api/chat":
                    message_count, suspicious_input = self._inspect_chat_body(body)

            await self.app(scope, receive, send_with_headers)
        except Exception as exc:  # noqa: BLE001 - last-resort boundary
            error_type = type(exc).__name__
            if status is None:
                logger.error(
                    "Unhandled request error",
                    exc_info=(type(exc), exc, exc.__traceback__),
                    extra={
                        "request_id": request_id,
                        "route": path,
                        "status": 500,
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                        "message_count": message_count,
                        "error_type": error_type,
                    },
                )
                await self._json_response(
                    send_with_headers,
                    500,
                    "internal",
                    INTERNAL_MESSAGE,
                    request_id=request_id,
                )
            else:
                # The response already started, so it cannot become an error
                # response. A client disconnect during a stream is normal.
                logger.warning(
                    "Request failed after the response started",
                    exc_info=(type(exc), exc, exc.__traceback__),
                    extra={
                        "request_id": request_id,
                        "route": path,
                        "status": status,
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                        "message_count": message_count,
                        "error_type": error_type,
                    },
                )
        finally:
            logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "route": path,
                    "status": status if status is not None else 499,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    "message_count": message_count,
                    "error_type": error_type,
                    "suspicious_input": suspicious_input,
                },
            )

    def _body_limit(self, path: str) -> int | None:
        """Return the request body limit for a path, if any."""
        if path == "/api/chat":
            return CHAT_BODY_LIMIT_BYTES
        if path == "/api/fit":
            return self._max_upload_bytes
        return None

    async def _read_body(
        self, receive: Callable[..., Any], limit: int
    ) -> tuple[bytes, bool]:
        """Read a request body, stopping as soon as it exceeds ``limit``."""
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return bytes(body), False
            body.extend(message.get("body", b""))
            if len(body) > limit:
                return bytes(body), True
            if not message.get("more_body", False):
                return bytes(body), False

    def _inspect_chat_body(self, body: bytes) -> tuple[int | None, bool]:
        """Return message count and an injection-metadata flag, never content."""
        message_count: int | None = None
        suspicious = False
        try:
            data = json.loads(body)
            messages = data.get("messages") if isinstance(data, dict) else None
            if isinstance(messages, list):
                message_count = len(messages)
            lowered = body.decode("utf-8", "ignore").lower()
            suspicious = any(phrase in lowered for phrase in SUSPICIOUS_PHRASES)
        except (json.JSONDecodeError, UnicodeError):
            pass
        return message_count, suspicious

    def _with_contact(self, message: str) -> str:
        """Append the public contact email when one is configured."""
        if not self._contact_email:
            return f"{message}."
        return f"{message}, or email {self._contact_email}."

    async def _json_response(
        self,
        send: Callable[..., Any],
        status: int,
        code: str,
        message: str,
        *,
        retry_after: int | None = None,
        request_id: str | None = None,
    ) -> None:
        """Send a small friendly JSON response."""
        body: dict[str, Any] = {"code": code, "message": message}
        if request_id is not None:
            body["request_id"] = request_id
        payload = json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(payload)).encode("ascii")),
        ]
        if retry_after is not None:
            headers.append((b"retry-after", str(retry_after).encode("ascii")))
        await send(
            {"type": "http.response.start", "status": status, "headers": headers}
        )
        await send({"type": "http.response.body", "body": payload})
