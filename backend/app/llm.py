"""Resilient async Groq client with key rotation and model fallback.

Every call tries model/key pairs in a deterministic order: each model from
``GROQ_MODEL`` followed by ``GROQ_FALLBACK_MODELS``, and for each model every
API key starting from a shared round-robin pointer. Rate limits and transient
failures cool down only the affected pair; authentication failures disable the
key for the lifetime of the process.

Secrets, prompts, and model output are never logged. Log messages may name a
key index and a model, nothing more.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from typing import Any, Protocol

import httpx
from pydantic import SecretStr

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5.0
FIRST_TOKEN_TIMEOUT_SECONDS = 20.0
DEFAULT_COOLDOWN_SECONDS = 60.0

_AUTH_STATUS_CODES = frozenset({401, 403})
_TRANSIENT_STATUS_CODES = frozenset({408, 429})


class LLMError(RuntimeError):
    """Base class for language-model failures."""


class LLMUnavailableError(LLMError):
    """Raised when every model/key pair is disabled or cooling down."""


class StreamInterrupted(LLMError):
    """Raised when a stream fails after at least one text chunk was yielded."""


class CompletionsProtocol(Protocol):
    """The slice of the Groq completions API this module uses."""

    async def create(self, **kwargs: Any) -> Any:
        """Create a completion or a streaming completion."""
        ...


class ChatProtocol(Protocol):
    """The slice of the Groq chat namespace this module uses."""

    completions: CompletionsProtocol


class LLMClientProtocol(Protocol):
    """Small injectable interface matching the async Groq client."""

    chat: ChatProtocol


ClientFactory = Callable[[int, SecretStr], LLMClientProtocol]
Clock = Callable[[], float]


class LLMClient:
    """Talk to Groq with key rotation, cooldowns, and model fallback."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client_factory: ClientFactory | None = None,
        clock: Clock = time.monotonic,
        first_token_timeout: float = FIRST_TOKEN_TIMEOUT_SECONDS,
    ) -> None:
        """Build a client from settings, optionally with an injected factory.

        Args:
            settings: Pre-built settings; falls back to the cached application
                settings.
            client_factory: Builds one Groq-compatible client per key index.
                Tests use this to inject fakes; the key itself is passed only so
                the default factory can authenticate.
            clock: Monotonic time source, injectable for cooldown tests.
            first_token_timeout: Seconds allowed for the first response chunk.
        """
        resolved = settings if settings is not None else get_settings()
        self._models: tuple[str, ...] = tuple(
            dict.fromkeys([resolved.groq_model, *resolved.groq_fallback_models])
        )
        self._keys: tuple[SecretStr, ...] = tuple(resolved.groq_api_keys)
        self._client_factory: ClientFactory = client_factory or self._build_groq_client
        self._clock: Clock = clock
        self._first_token_timeout = first_token_timeout
        self._semaphore = asyncio.Semaphore(resolved.max_concurrent_llm_calls)

        self._lock = asyncio.Lock()
        self._pointer = 0
        self._disabled_keys: set[int] = set()
        self._cooldowns: dict[tuple[int, str], float] = {}
        self._clients: dict[int, LLMClientProtocol] = {}

    def __repr__(self) -> str:
        """Never reveal key text through a representation."""
        return (
            f"LLMClient(keys={len(self._keys)}, models={self._models!r}, "
            f"disabled_keys={sorted(self._disabled_keys)})"
        )

    def _build_groq_client(self, key_index: int, key: SecretStr) -> LLMClientProtocol:
        """Create the real async Groq client with the required timeouts."""
        from groq import AsyncGroq

        return AsyncGroq(
            api_key=key.get_secret_value(),
            timeout=httpx.Timeout(
                self._first_token_timeout, connect=CONNECT_TIMEOUT_SECONDS
            ),
            max_retries=0,
        )

    async def complete_chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int,
        temperature: float,
        json_mode: bool = False,
    ) -> str:
        """Return one complete reply, failing over across keys and models."""
        async with self._semaphore:
            async for key_index, model in self._attempts():
                client = await self._client_for(key_index)
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": list(messages),
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}

                try:
                    async with asyncio.timeout(self._first_token_timeout):
                        completion = await client.chat.completions.create(**kwargs)
                except Exception as exc:  # noqa: BLE001 - upstream errors vary
                    if await self._handle_failure(exc, key_index, model):
                        continue
                    raise LLMError(
                        "The language model request failed."
                    ) from exc

                return _completion_text(completion)

        raise LLMUnavailableError(
            "No Groq model/key combination is currently available."
        )

    async def _attempts(self) -> AsyncGenerator[tuple[int, str], None]:
        """Yield available ``(key_index, model)`` pairs in attempt order."""
        key_order = await self._claim_key_order()
        for model in self._models:
            for key_index in key_order:
                if await self._pair_is_ready(key_index, model):
                    yield key_index, model

    async def _claim_key_order(self) -> list[int]:
        """Return this call's key order and advance the round-robin pointer."""
        async with self._lock:
            count = len(self._keys)
            start = self._pointer % count
            order = [(start + offset) % count for offset in range(count)]
            self._pointer = (start + 1) % count
            disabled = set(self._disabled_keys)
        return [key_index for key_index in order if key_index not in disabled]

    async def _client_for(self, key_index: int) -> LLMClientProtocol:
        """Return the cached client for a key, creating it at most once."""
        async with self._lock:
            client = self._clients.get(key_index)
            if client is None:
                client = self._client_factory(key_index, self._keys[key_index])
                self._clients[key_index] = client
            return client

    async def _pair_is_ready(self, key_index: int, model: str) -> bool:
        """Return True when the key is enabled and the pair is not cooling."""
        async with self._lock:
            if key_index in self._disabled_keys:
                return False
            until = self._cooldowns.get((key_index, model))
            if until is None:
                return True
            if until <= self._clock():
                self._cooldowns.pop((key_index, model), None)
                return True
            return False

    async def _handle_failure(
        self, exc: BaseException, key_index: int, model: str
    ) -> bool:
        """Record a failure and report whether another pair should be tried.

        Returns:
            True when the caller should continue with the next pair.
        """
        status = _status_code(exc)

        if status in _AUTH_STATUS_CODES:
            async with self._lock:
                newly_disabled = key_index not in self._disabled_keys
                self._disabled_keys.add(key_index)
            if newly_disabled:
                logger.error(
                    "Groq key %d was rejected by the API; disabling it for this "
                    "process.",
                    key_index,
                )
            return True

        if status in _TRANSIENT_STATUS_CODES or (
            status is not None and 500 <= status <= 599
        ) or _looks_like_connection_error(exc):
            retry_after = _retry_after_seconds(exc)
            cooldown = (
                DEFAULT_COOLDOWN_SECONDS if retry_after is None else retry_after
            )
            async with self._lock:
                self._cooldowns[(key_index, model)] = self._clock() + cooldown
            logger.warning(
                "Groq key %d and model %r are cooling down for %.0f seconds "
                "after %s.",
                key_index,
                model,
                cooldown,
                f"HTTP {status}" if status is not None else "a connection error",
            )
            return True

        logger.error(
            "Groq key %d failed for model %r with %s; not retrying.",
            key_index,
            model,
            f"HTTP {status}" if status is not None else "an unrecognised error",
        )
        return False


def _completion_text(completion: Any) -> str:
    """Extract text from a non-streaming completion without raising."""
    choices = getattr(completion, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    return content or ""


def _chunk_text(chunk: Any) -> str:
    """Extract text from one streaming chunk, tolerating empty deltas."""
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    content = getattr(delta, "content", None)
    return content or ""


def _status_code(exc: BaseException) -> int | None:
    """Read an HTTP status from an SDK error without touching its message."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _looks_like_connection_error(exc: BaseException) -> bool:
    """Return True for SDK connection, HTTP transport, and timeout failures."""
    try:
        from groq import APIConnectionError
    except ImportError:  # pragma: no cover - groq is a declared dependency
        sdk_connection_error: type[BaseException] = ConnectionError
    else:
        sdk_connection_error = APIConnectionError

    if isinstance(
        exc,
        (
            sdk_connection_error,
            httpx.TransportError,
            ConnectionError,
            TimeoutError,
            asyncio.TimeoutError,
        ),
    ):
        return True

    name = type(exc).__name__.lower()
    return "connection" in name or "timeout" in name


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Parse a Retry-After header in seconds or HTTP-date form."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None

    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None

    text = str(raw).strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass

    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """Return the process-wide client so cooldowns and rotation are shared."""
    return LLMClient(get_settings())


async def complete_chat(
    messages: Sequence[Mapping[str, Any]],
    max_tokens: int,
    temperature: float,
    json_mode: bool = False,
    *,
    client: LLMClient | None = None,
) -> str:
    """Return a complete reply using the shared client or an injected one."""
    active = client if client is not None else get_llm_client()
    return await active.complete_chat(
        messages, max_tokens, temperature, json_mode=json_mode
    )
