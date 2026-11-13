"""Tests for key rotation, model fallback, streaming failover, and secrecy."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from app.config import Settings
from app.llm import (
    LLMClient,
    LLMError,
    LLMUnavailableError,
    StreamInterrupted,
)

KEYS = ("test-key-one", "test-key-two")


def make_settings(
    keys: tuple[str, ...] = KEYS,
    *,
    model: str = "primary-model",
    fallbacks: tuple[str, ...] = (),
    **overrides: Any,
) -> Settings:
    """Build settings without touching the environment or a .env file."""
    values: dict[str, Any] = {
        "groq_api_keys": list(keys),
        "groq_model": model,
        "groq_fallback_models": list(fallbacks),
        "contact_email": "tisha@example.com",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


class FakeStatusError(Exception):
    """Stand-in for a Groq API status error, with optional Retry-After."""

    def __init__(self, status_code: int, retry_after: str | None = None) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code
        headers = {} if retry_after is None else {"retry-after": retry_after}
        self.response = SimpleNamespace(status_code=status_code, headers=headers)


def chunk(text: str) -> Any:
    """Build one streaming chunk."""
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
    )


def completion(text: str) -> Any:
    """Build one non-streaming completion."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


async def stream_of(*texts: str) -> Any:
    """Yield text chunks."""
    for text in texts:
        yield chunk(text)


async def broken_after(text: str, exc: Exception) -> Any:
    """Yield one chunk, then fail as a real upstream stream might."""
    yield chunk(text)
    raise exc


class FakeCompletions:
    """Record calls and delegate to the harness behavior."""

    def __init__(self, harness: "Harness", key_index: int) -> None:
        self.harness = harness
        self.key_index = key_index
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        self.harness.calls.append((self.key_index, kwargs["model"]))
        result = self.harness.behavior(self.key_index, kwargs)
        return result


class FakeGroqClient:
    """Minimal async Groq-shaped client."""

    def __init__(self, harness: "Harness", key_index: int) -> None:
        self.completions = FakeCompletions(harness, key_index)
        self.chat = SimpleNamespace(completions=self.completions)


class Harness:
    """Wire a real LLMClient to per-key fake Groq clients."""

    def __init__(
        self,
        behavior: Callable[[int, dict[str, Any]], Any],
        *,
        key_count: int = 2,
        model: str = "primary-model",
        fallbacks: tuple[str, ...] = (),
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.behavior = behavior
        self.calls: list[tuple[int, str]] = []
        self.clients: dict[int, FakeGroqClient] = {}
        kwargs: dict[str, Any] = {}
        if clock is not None:
            kwargs["clock"] = clock
        self.client = LLMClient(
            make_settings(
                KEYS[:key_count], model=model, fallbacks=fallbacks
            ),
            client_factory=self._factory,
            **kwargs,
        )

    def _factory(self, key_index: int, _key: Any) -> FakeGroqClient:
        client = FakeGroqClient(self, key_index)
        self.clients[key_index] = client
        return client


def test_429_on_first_key_fails_over_before_any_chunk() -> None:
    """A rejected key rotates to the next key without producing output."""

    def behavior(index: int, _kwargs: dict[str, Any]) -> Any:
        if index == 0:
            raise FakeStatusError(429, retry_after="0")
        return stream_of("hello", " world")

    async def scenario() -> tuple[list[str], Harness]:
        harness = Harness(behavior)
        chunks = [
            text
            async for text in harness.client.stream_chat(
                [{"role": "user", "content": "PROMPT-MARKER"}], 100, 0.2
            )
        ]
        return chunks, harness

    chunks, harness = asyncio.run(scenario())

    assert chunks == ["hello", " world"]
    assert harness.calls == [(0, "primary-model"), (1, "primary-model")]


def test_all_keys_429_on_primary_falls_back_to_next_model() -> None:
    """Exhausting every key on the primary model moves to the fallback model."""

    def behavior(_index: int, kwargs: dict[str, Any]) -> Any:
        if kwargs["model"] == "primary-model":
            raise FakeStatusError(429)
        return stream_of("fallback reply")

    async def scenario() -> tuple[list[str], Harness]:
        harness = Harness(behavior, fallbacks=("fallback-model",))
        chunks = [
            text
            async for text in harness.client.stream_chat(
                [{"role": "user", "content": "hi"}], 50, 0.2
            )
        ]
        return chunks, harness

    chunks, harness = asyncio.run(scenario())

    assert chunks == ["fallback reply"]
    assert harness.calls == [
        (0, "primary-model"),
        (1, "primary-model"),
        (0, "fallback-model"),
    ]


def test_401_disables_only_that_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An auth failure retires one key but leaves the others usable."""

    def behavior(index: int, _kwargs: dict[str, Any]) -> Any:
        if index == 0:
            raise FakeStatusError(401)
        return completion("ok")

    async def scenario() -> tuple[list[str], Harness]:
        harness = Harness(behavior)
        replies = [
            await harness.client.complete_chat(
                [{"role": "user", "content": "hi"}], 20, 0.0
            )
            for _ in range(3)
        ]
        return replies, harness

    with caplog.at_level(logging.ERROR, logger="app.llm"):
        replies, harness = asyncio.run(scenario())

    assert replies == ["ok", "ok", "ok"]
    attempted_keys = [index for index, _ in harness.calls]
    assert attempted_keys.count(0) == 1
    assert attempted_keys.count(1) == 3
    assert "Groq key 0" in caplog.text


def test_failure_after_first_chunk_raises_and_does_not_retry() -> None:
    """Mid-answer failures surface instead of restarting on another key."""

    def behavior(index: int, _kwargs: dict[str, Any]) -> Any:
        if index == 0:
            return broken_after("partial", FakeStatusError(500))
        return stream_of("should not be used")

    async def scenario() -> tuple[list[str], Harness]:
        harness = Harness(behavior)
        received: list[str] = []
        with pytest.raises(StreamInterrupted):
            async for text in harness.client.stream_chat(
                [{"role": "user", "content": "hi"}], 50, 0.2
            ):
                received.append(text)
        return received, harness

    received, harness = asyncio.run(scenario())

    assert received == ["partial"]
    assert harness.calls == [(0, "primary-model")]


def test_round_robin_spreads_consecutive_calls() -> None:
    """Successive calls start with successive keys."""

    def behavior(_index: int, _kwargs: dict[str, Any]) -> Any:
        return completion("ok")

    async def scenario() -> Harness:
        harness = Harness(behavior)
        for _ in range(3):
            await harness.client.complete_chat(
                [{"role": "user", "content": "hi"}], 20, 0.0
            )
        return harness

    harness = asyncio.run(scenario())

    assert harness.calls == [
        (0, "primary-model"),
        (1, "primary-model"),
        (0, "primary-model"),
    ]


def test_all_pairs_unavailable_raises_llm_unavailable() -> None:
    """Every pair cooling down means a clear unavailability error."""

    def behavior(_index: int, _kwargs: dict[str, Any]) -> Any:
        raise FakeStatusError(429)

    async def scenario() -> Harness:
        harness = Harness(behavior, fallbacks=("fallback-model",))
        with pytest.raises(LLMUnavailableError):
            await harness.client.complete_chat(
                [{"role": "user", "content": "hi"}], 20, 0.0
            )
        return harness

    harness = asyncio.run(scenario())

    assert harness.calls == [
        (0, "primary-model"),
        (1, "primary-model"),
        (0, "fallback-model"),
        (1, "fallback-model"),
    ]


def test_logs_never_contain_key_text_or_prompts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only the key index may appear in logs, never secrets or content."""
    secret_keys = ("alpha-secret-value", "beta-secret-value")

    def behavior(index: int, _kwargs: dict[str, Any]) -> Any:
        if index == 0:
            raise FakeStatusError(401)
        return completion("ok")

    async def scenario() -> None:
        harness = Harness(behavior, key_count=2)
        # Rebuild with the secret-looking keys used by this test.
        harness.client = LLMClient(
            make_settings(secret_keys),
            client_factory=harness._factory,
        )
        await harness.client.complete_chat(
            [{"role": "user", "content": "PROMPT-MARKER"}], 20, 0.0
        )

    with caplog.at_level(logging.DEBUG, logger="app.llm"):
        asyncio.run(scenario())

    for secret in secret_keys:
        assert secret not in caplog.text
    assert "PROMPT-MARKER" not in caplog.text
    assert "Groq key 0" in caplog.text


def test_json_mode_passes_the_response_format() -> None:
    """``json_mode`` maps to Groq's JSON object response format."""

    def behavior(_index: int, _kwargs: dict[str, Any]) -> Any:
        return completion('{"ok": true}')

    async def scenario() -> tuple[str, dict[str, Any]]:
        harness = Harness(behavior)
        reply = await harness.client.complete_chat(
            [{"role": "user", "content": "hi"}],
            40,
            0.0,
            json_mode=True,
        )
        return reply, harness.clients[0].completions.calls[0]

    reply, sent = asyncio.run(scenario())

    assert reply == '{"ok": true}'
    assert sent["response_format"] == {"type": "json_object"}


def test_plain_mode_omits_the_response_format() -> None:
    """Normal completions do not force JSON."""

    def behavior(_index: int, _kwargs: dict[str, Any]) -> Any:
        return completion("plain")

    async def scenario() -> dict[str, Any]:
        harness = Harness(behavior)
        await harness.client.complete_chat(
            [{"role": "user", "content": "hi"}], 40, 0.0
        )
        return harness.clients[0].completions.calls[0]

    sent = asyncio.run(scenario())

    assert "response_format" not in sent


def test_cooldown_defaults_to_sixty_seconds() -> None:
    """A 429 without Retry-After parks the pair for a minute."""
    now = [0.0]
    successes = [False]

    def behavior(_index: int, _kwargs: dict[str, Any]) -> Any:
        if not successes[0]:
            raise FakeStatusError(429)
        return completion("recovered")

    async def scenario() -> tuple[Harness, str | None]:
        harness = Harness(behavior, key_count=1, clock=lambda: now[0])
        with pytest.raises(LLMUnavailableError):
            await harness.client.complete_chat(
                [{"role": "user", "content": "hi"}], 20, 0.0
            )
        attempts_after_first = len(harness.calls)

        with pytest.raises(LLMUnavailableError):
            await harness.client.complete_chat(
                [{"role": "user", "content": "hi"}], 20, 0.0
            )
        assert len(harness.calls) == attempts_after_first

        now[0] = 59.0
        with pytest.raises(LLMUnavailableError):
            await harness.client.complete_chat(
                [{"role": "user", "content": "hi"}], 20, 0.0
            )
        assert len(harness.calls) == attempts_after_first

        now[0] = 61.0
        successes[0] = True
        reply = await harness.client.complete_chat(
            [{"role": "user", "content": "hi"}], 20, 0.0
        )
        return harness, reply

    harness, reply = asyncio.run(scenario())

    assert reply == "recovered"
    assert len(harness.calls) == 2


def test_retry_after_header_overrides_the_default() -> None:
    """A numeric Retry-After is honoured instead of the 60-second default."""
    now = [0.0]

    def behavior(_index: int, _kwargs: dict[str, Any]) -> Any:
        if now[0] < 5.0:
            raise FakeStatusError(429, retry_after="5")
        return completion("recovered")

    async def scenario() -> str:
        harness = Harness(behavior, key_count=1, clock=lambda: now[0])
        with pytest.raises(LLMUnavailableError):
            await harness.client.complete_chat(
                [{"role": "user", "content": "hi"}], 20, 0.0
            )
        now[0] = 5.1
        return await harness.client.complete_chat(
            [{"role": "user", "content": "hi"}], 20, 0.0
        )

    assert asyncio.run(scenario()) == "recovered"


def test_non_retryable_error_is_not_retried() -> None:
    """A 400-style failure surfaces immediately instead of exhausting pairs."""

    def behavior(_index: int, _kwargs: dict[str, Any]) -> Any:
        raise FakeStatusError(400)

    async def scenario() -> Harness:
        harness = Harness(behavior)
        with pytest.raises(LLMError) as caught:
            await harness.client.complete_chat(
                [{"role": "user", "content": "hi"}], 20, 0.0
            )
        assert not isinstance(caught.value, LLMUnavailableError)
        return harness

    harness = asyncio.run(scenario())

    assert harness.calls == [(0, "primary-model")]
