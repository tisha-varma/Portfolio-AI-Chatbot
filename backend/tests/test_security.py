"""Tests for rate limits, security headers, size limits, and log hygiene."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.llm import get_llm_client  # noqa: E402
from app.logging_setup import JsonFormatter  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security import (  # noqa: E402
    SECURITY_HEADERS,
    LimitDecision,
    RateLimiter,
    client_ip,
)
from app.schemas import ChatMessage, clean_text  # noqa: E402


def make_settings(**overrides: Any) -> Settings:
    """Build settings without reading the project .env."""
    values: dict[str, Any] = {
        "groq_api_keys": ["test-groq-key"],
        "groq_model": "test-groq-model",
        "contact_email": "tisha@example.com",
        "rate_limit_per_minute": 100,
        "rate_limit_fit_per_minute": 100,
        "rate_limit_per_day_per_ip": 1000,
        "global_daily_request_cap": 1000,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


class FakeLLM:
    """Minimal async chat client for endpoint-level tests."""

    def __init__(self, chunks: list[str] | None = None) -> None:
        self.chunks = chunks or ["ok"]
        self.calls: list[list[dict[str, Any]]] = []

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> Any:
        self.calls.append(messages)
        for chunk in self.chunks:
            yield chunk


def make_app(settings: Settings, llm: FakeLLM | None = None) -> Any:
    """Create an app with test settings and a fake model client."""
    application = create_app(settings)
    application.dependency_overrides[get_llm_client] = lambda: (llm or FakeLLM())
    return application


def user(content: str = "hi") -> dict[str, str]:
    """Build one user message mapping."""
    return {"role": "user", "content": content}


class FakeTime:
    """Advance a monotonic clock and a UTC clock together."""

    def __init__(self) -> None:
        self.seconds = 0.0
        self.start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def clock(self) -> float:
        return 1000.0 + self.seconds

    def utcnow(self) -> datetime:
        return self.start + timedelta(seconds=self.seconds)

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


def make_limiter(
    fake: FakeTime,
    *,
    per_ip_day_limit: int = 100,
    global_daily_cap: int = 100,
) -> RateLimiter:
    """Build a limiter driven by a controllable clock."""
    return RateLimiter(
        per_ip_day_limit=per_ip_day_limit,
        global_daily_cap=global_daily_cap,
        clock=fake.clock,
        utcnow=fake.utcnow,
    )


# ---------------------------------------------------------------------------
# Limiter arithmetic and resets
# ---------------------------------------------------------------------------


def test_minute_limit_allows_the_configured_count_then_rejects() -> None:
    """The third request is refused when the minute limit is two."""
    fake = FakeTime()
    limiter = make_limiter(fake)

    assert limiter.acquire("1.1.1.1", "chat", 2).allowed
    assert limiter.acquire("1.1.1.1", "chat", 2).allowed
    decision = limiter.acquire("1.1.1.1", "chat", 2)

    assert decision == LimitDecision(False, 60, "minute")


def test_minute_limit_resets_after_the_window() -> None:
    """A minute of quiet restores the allowance for the same IP."""
    fake = FakeTime()
    limiter = make_limiter(fake)

    assert limiter.acquire("1.1.1.1", "chat", 1).allowed
    assert not limiter.acquire("1.1.1.1", "chat", 1).allowed

    fake.advance(61)

    assert limiter.acquire("1.1.1.1", "chat", 1).allowed


def test_minute_limit_is_independent_per_kind_and_ip() -> None:
    """Chat and fit budgets, and different visitors, do not share a bucket."""
    fake = FakeTime()
    limiter = make_limiter(fake)

    assert limiter.acquire("1.1.1.1", "chat", 1).allowed
    assert not limiter.acquire("1.1.1.1", "chat", 1).allowed
    assert limiter.acquire("1.1.1.1", "fit", 1).allowed
    assert limiter.acquire("2.2.2.2", "chat", 1).allowed


def test_per_ip_daily_limit_resets_after_a_day() -> None:
    """The 24 hour per-IP window expires without a process restart."""
    fake = FakeTime()
    limiter = make_limiter(fake, per_ip_day_limit=2)

    assert limiter.acquire("1.1.1.1", "chat", 100).allowed
    assert limiter.acquire("1.1.1.1", "chat", 100).allowed
    assert limiter.acquire("1.1.1.1", "chat", 100).reason == "day"

    fake.advance(24 * 60 * 60 + 1)

    assert limiter.acquire("1.1.1.1", "chat", 100).allowed


def test_global_daily_cap_and_utc_midnight_reset() -> None:
    """The shared quota refuses new work and comes back on a new UTC date."""
    fake = FakeTime()
    limiter = make_limiter(fake, global_daily_cap=1)

    assert limiter.acquire("1.1.1.1", "chat", 100).allowed
    blocked = limiter.acquire("2.2.2.2", "chat", 100)

    assert blocked.reason == "global"
    assert blocked.retry_after > 0

    fake.advance(24 * 60 * 60)

    assert limiter.acquire("2.2.2.2", "chat", 100).allowed


def test_expired_buckets_are_cleaned_up() -> None:
    """Old empty buckets are dropped so memory does not grow forever."""
    fake = FakeTime()
    limiter = make_limiter(fake, global_daily_cap=10_000)

    for index in range(50):
        assert limiter.acquire(f"10.0.0.{index}", "chat", 100).allowed

    assert limiter.tracked_buckets() == (50, 50)

    fake.advance(24 * 60 * 60 + 120)
    assert limiter.acquire("10.9.9.9", "chat", 100).allowed

    minute_buckets, day_buckets = limiter.tracked_buckets()
    assert minute_buckets == 1
    assert day_buckets == 1


# ---------------------------------------------------------------------------
# HTTP-level limits
# ---------------------------------------------------------------------------


def test_chat_minute_limit_returns_429_with_retry_after() -> None:
    """The endpoint refuses the third request when the limit is two."""
    client = TestClient(make_app(make_settings(rate_limit_per_minute=2)))

    assert client.post("/api/chat", json={"messages": [user()]}).status_code == 200
    assert client.post("/api/chat", json={"messages": [user()]}).status_code == 200

    response = client.post("/api/chat", json={"messages": [user()]})

    assert response.status_code == 429
    assert response.json()["code"] == "rate_limited"
    assert 1 <= int(response.headers["retry-after"]) <= 60


def test_fit_minute_limit_is_stricter_than_chat() -> None:
    """Fit uses its own lower allowance."""
    client = TestClient(
        make_app(make_settings(rate_limit_per_minute=10, rate_limit_fit_per_minute=1))
    )

    assert client.post("/api/fit", json={"jd_text": "x"}).status_code == 404
    response = client.post("/api/fit", json={"jd_text": "x"})

    assert response.status_code == 429


def test_daily_cap_returns_a_friendly_503() -> None:
    """Exhausting the shared cap stops work until the next UTC day."""
    client = TestClient(
        make_app(
            make_settings(
                global_daily_request_cap=1,
                rate_limit_per_day_per_ip=100,
            )
        )
    )

    assert client.post("/api/chat", json={"messages": [user()]}).status_code == 200

    response = client.post("/api/chat", json={"messages": [user()]})

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "busy"
    assert "today's limit" in body["message"]
    assert "tisha@example.com" in body["message"]
    assert int(response.headers["retry-after"]) > 0


def test_per_ip_day_limit_returns_429_on_the_endpoint() -> None:
    """The per-visitor day budget is enforced after the minute budget."""
    client = TestClient(
        make_app(
            make_settings(
                rate_limit_per_minute=100,
                rate_limit_per_day_per_ip=1,
            )
        )
    )

    assert client.post("/api/chat", json={"messages": [user()]}).status_code == 200

    response = client.post("/api/chat", json={"messages": [user()]})

    assert response.status_code == 429
    assert response.json()["code"] == "rate_limited"


# ---------------------------------------------------------------------------
# Client IP and proxy trust
# ---------------------------------------------------------------------------


def scope_with_forwarded(value: str) -> dict[str, Any]:
    """Build a minimal ASGI scope carrying one forwarded header."""
    return {
        "client": ("203.0.113.9", 5000),
        "headers": [(b"x-forwarded-for", value.encode("ascii"))],
    }


def test_direct_peer_is_used_when_proxy_is_not_trusted() -> None:
    """A spoofed header is ignored by default."""
    assert (
        client_ip(scope_with_forwarded("198.51.100.7"), trust_proxy=False)
        == "203.0.113.9"
    )


def test_right_most_valid_forwarded_hop_is_used_when_trusted() -> None:
    """The hop appended by the trusted proxy wins, not the left-most claim."""
    assert (
        client_ip(scope_with_forwarded("198.51.100.7, 192.0.2.4"), trust_proxy=True)
        == "192.0.2.4"
    )


def test_invalid_forwarded_hops_are_skipped() -> None:
    """Garbage in the header does not crash or become a bucket key."""
    assert (
        client_ip(scope_with_forwarded("not-an-ip, 192.0.2.4, nope"), trust_proxy=True)
        == "192.0.2.4"
    )


def test_forwarded_header_is_ignored_unless_trusted() -> None:
    """Spoofing X-Forwarded-For cannot reset the per-IP budget."""
    settings = make_settings(rate_limit_per_minute=1, trusted_proxy=False)
    client = TestClient(make_app(settings))

    first = client.post(
        "/api/chat",
        json={"messages": [user()]},
        headers={"x-forwarded-for": "198.51.100.7"},
    )
    second = client.post(
        "/api/chat",
        json={"messages": [user()]},
        headers={"x-forwarded-for": "198.51.100.8"},
    )

    assert first.status_code == 200
    assert second.status_code == 429


def test_forwarded_header_separates_visitors_when_trusted() -> None:
    """Behind a trusted proxy, distinct real IPs get distinct budgets."""
    settings = make_settings(rate_limit_per_minute=1, trusted_proxy=True)
    client = TestClient(make_app(settings))

    first = client.post(
        "/api/chat",
        json={"messages": [user()]},
        headers={"x-forwarded-for": "198.51.100.7"},
    )
    second = client.post(
        "/api/chat",
        json={"messages": [user()]},
        headers={"x-forwarded-for": "198.51.100.8"},
    )

    assert first.status_code == 200
    assert second.status_code == 200


# ---------------------------------------------------------------------------
# Body limits
# ---------------------------------------------------------------------------


def test_oversized_chat_body_is_rejected_before_routing() -> None:
    """A body over 64 KB gets a 413 without reaching the model."""
    llm = FakeLLM()
    client = TestClient(make_app(make_settings(), llm))

    response = client.post("/api/chat", content=b"x" * (64 * 1024 + 1))

    assert response.status_code == 413
    assert response.json()["code"] == "invalid"
    assert llm.calls == []


def test_oversized_fit_upload_uses_the_upload_limit() -> None:
    """Fit requests are bounded by MAX_UPLOAD_BYTES."""
    client = TestClient(make_app(make_settings(max_upload_bytes=64)))

    response = client.post("/api/fit", content=b"x" * 65)

    assert response.status_code == 413
    assert response.json()["code"] == "invalid"


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


def assert_security_headers(response: Any) -> None:
    """Every response carries the fixed header set and a request id."""
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value, name
    assert response.headers["x-request-id"]


def test_security_headers_are_on_success_and_error_responses() -> None:
    """Static, missing, and rate-limited responses all get the headers."""
    client = TestClient(make_app(make_settings(rate_limit_per_minute=1)))

    ok = client.get("/health")
    missing = client.get("/no-such-page")
    assert client.post("/api/chat", json={"messages": [user()]}).status_code == 200
    limited = client.post("/api/chat", json={"messages": [user()]})

    assert_security_headers(ok)
    assert_security_headers(missing)
    assert_security_headers(limited)
    assert limited.status_code == 429


def test_csp_disallows_inline_scripts_and_styles() -> None:
    """The CSP keeps script-src and style-src at 'self' only."""
    client = TestClient(make_app(make_settings()))

    csp = client.get("/health").headers["content-security-policy"]

    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp


# ---------------------------------------------------------------------------
# Errors and logs
# ---------------------------------------------------------------------------


def test_unknown_error_returns_a_generic_body_with_a_request_id() -> None:
    """An unexpected exception never leaks its message or traceback."""
    application = make_app(make_settings())

    @application.get("/boom")
    def boom() -> None:
        raise RuntimeError("LEAK-THIS-INTERNAL-DETAIL")

    client = TestClient(application)

    response = client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal"
    assert body["message"] == "Something went wrong."
    assert "LEAK-THIS-INTERNAL-DETAIL" not in response.text
    assert body["request_id"] == response.headers["x-request-id"]
    assert_security_headers(response)


def test_logs_contain_metadata_but_no_visitor_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Message content never reaches the log stream."""
    caplog.set_level(logging.INFO)
    secret = "UNIQUE-VISITOR-SECRET-PHRASE"
    client = TestClient(make_app(make_settings()))

    response = client.post("/api/chat", json={"messages": [user(secret)]})

    assert response.status_code == 200
    assert secret not in caplog.text

    request_records = [
        record for record in caplog.records if hasattr(record, "request_id")
    ]
    assert request_records, "the request should be logged with a request id"
    record = request_records[-1]
    assert record.route == "/api/chat"
    assert record.status == 200
    assert record.message_count == 1
    assert isinstance(record.duration_ms, float)
    assert secret not in json.dumps(record.__dict__, default=str)


def test_json_formatter_whitelists_metadata_fields() -> None:
    """Only known metadata fields are serialised, never arbitrary extras."""
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="http_request",
        args=(),
        exc_info=None,
    )
    record.request_id = "abc123"
    record.route = "/api/chat"
    record.status = 200
    record.message_count = 2
    record.visitor_text = "SHOULD-NOT-APPEAR"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "http_request"
    assert payload["request_id"] == "abc123"
    assert payload["route"] == "/api/chat"
    assert payload["status"] == 200
    assert payload["message_count"] == 2
    assert "visitor_text" not in payload
    assert "SHOULD-NOT-APPEAR" not in json.dumps(payload)


# ---------------------------------------------------------------------------
# Maintenance mode and input hygiene
# ---------------------------------------------------------------------------


def test_maintenance_mode_blocks_api_but_not_health() -> None:
    """The site and health probe stay useful while the API is paused."""
    client = TestClient(make_app(make_settings(maintenance_mode=True)))

    chat = client.post("/api/chat", json={"messages": [user()]})
    fit = client.post("/api/fit", json={"jd_text": "x"})
    health = client.get("/health")

    assert chat.status_code == 503
    assert chat.json()["code"] == "busy"
    assert "short break" in chat.json()["message"]
    assert fit.status_code == 503
    assert health.status_code == 200
    assert_security_headers(chat)


def test_maintenance_requests_are_not_counted_toward_quotas() -> None:
    """Paused traffic cannot drain the daily allowance."""
    import asyncio

    from app.security import SecurityMiddleware

    settings = make_settings(
        maintenance_mode=True,
        global_daily_request_cap=1,
        rate_limit_per_minute=100,
        rate_limit_per_day_per_ip=100,
    )
    limiter = RateLimiter(per_ip_day_limit=100, global_daily_cap=1)

    async def do_request(maintenance: bool) -> list[dict[str, Any]]:
        settings_copy = settings.model_copy(update={"maintenance_mode": maintenance})
        messages: list[dict[str, Any]] = []

        async def app(scope: Any, receive: Any, send: Any) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        middleware = SecurityMiddleware(
            app, settings_copy, limiter=limiter
        )
        scope = {
            "type": "http",
            "path": "/api/chat",
            "method": "POST",
            "headers": [],
            "client": ("1.1.1.1", 1234),
            "state": {},
        }
        await middleware(scope, receive, send)
        return messages

    async def scenario() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        paused = [await do_request(True) for _ in range(5)]
        live = await do_request(False)
        return paused, live

    paused, live = asyncio.run(scenario())

    assert all(messages[0]["status"] == 503 for messages in paused)
    assert live[0]["status"] == 200


def test_clean_text_strips_zero_width_and_control_characters() -> None:
    """Invisible characters are removed without mangling normal Unicode."""
    assert clean_text("he\u200bllo\u200d\ufeff world\x00\x07") == "hello world"
    assert clean_text("  café\r\n") == "café"


def test_clean_text_keeps_newlines_and_tabs() -> None:
    """Multi-line questions survive cleaning."""
    assert clean_text("line one\nline two\tend") == "line one\nline two\tend"


def test_message_content_is_cleaned_by_the_schema() -> None:
    """The schema applies the same hygiene before the route sees a message."""
    message = ChatMessage(role="user", content="a\u200bb\u0000")

    assert message.content == "ab"
