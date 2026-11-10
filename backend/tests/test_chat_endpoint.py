"""Tests for the POST /chat wiring (no real Groq calls)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.config import Settings  # noqa: E402


def make_settings(**overrides: Any) -> Settings:
    """Build settings without reading the project .env."""
    values: dict[str, Any] = {
        "groq_api_keys": ["test-groq-key"],
        "groq_model": "test-groq-model",
        "contact_email": "tisha@example.com",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


class FakeCompletions:
    """Capture calls instead of contacting Groq."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))]
        )


class FakeClient:
    """Minimal stand-in for groq.Groq."""

    def __init__(self, reply: str = "A reply.") -> None:
        self.completions = FakeCompletions(reply)
        self.chat = SimpleNamespace(completions=self.completions)


def test_chat_selects_sections_and_sends_the_built_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real router and prompt builder feed the Groq request."""
    fake_client = FakeClient()

    monkeypatch.setattr(main, "get_settings", lambda: make_settings())
    monkeypatch.setattr(main, "_get_client", lambda _settings: fake_client)

    response = TestClient(main.app).post(
        "/chat",
        json={"messages": [{"role": "user", "content": "tell me about RazorFlow payments"}]},
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "A reply.", "sections": ["razorflow_ai"]}

    sent = fake_client.completions.calls[0]
    assert sent["model"] == "test-groq-model"
    system_prompt = sent["messages"][0]["content"]
    assert "DEEP DIVE DETAILS" in system_prompt
    assert "RazorFlow" in system_prompt
    assert sent["messages"][1]["content"] == "tell me about RazorFlow payments"


def test_chat_rejects_an_over_long_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAX_MESSAGE_CHARS is enforced before any model call."""
    monkeypatch.setattr(main, "get_settings", lambda: make_settings(max_message_chars=5))

    response = TestClient(main.app).post(
        "/chat",
        json={"messages": [{"role": "user", "content": "this is too long"}]},
    )

    assert response.status_code == 413


def test_health_endpoint() -> None:
    """The liveness endpoint needs no configuration."""
    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
