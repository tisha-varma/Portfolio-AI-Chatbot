"""Request schemas and history trimming for the chat API."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_JD_SUMMARY_CHARS = 1500


def clean_text(value: str) -> str:
    """Normalise Unicode and remove control and zero-width characters.

    Newlines and tabs are kept because chat messages may be multi-line.
    """
    normalised = unicodedata.normalize("NFC", value)
    kept: list[str] = []
    for character in normalised:
        if character in "\n\t":
            kept.append(character)
            continue
        if unicodedata.category(character) in {"Cc", "Cf"}:
            continue
        kept.append(character)
    return "".join(kept).strip()


class ChatMessage(BaseModel):
    """One validated conversation message.

    Only user and assistant messages are accepted. A client-sent system role is
    rejected before it can reach the model.
    """

    role: Literal["user", "assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        """Clean visitor text and reject anything empty after cleaning."""
        cleaned = clean_text(value)
        if not cleaned:
            raise ValueError("content must not be empty")
        return cleaned


class ChatRequest(BaseModel):
    """A chat request with optional JD context for later stages."""

    messages: list[ChatMessage] = Field(min_length=1)
    jd_summary: str | None = None

    @field_validator("jd_summary")
    @classmethod
    def _validate_jd_summary(cls, value: str | None) -> str | None:
        """Trim optional JD context and cap it at 1500 characters."""
        if value is None:
            return None
        cleaned = clean_text(value)
        if not cleaned:
            return None
        if len(cleaned) > MAX_JD_SUMMARY_CHARS:
            raise ValueError(
                f"jd_summary must be at most {MAX_JD_SUMMARY_CHARS} characters"
            )
        return cleaned

    @model_validator(mode="after")
    def _require_user_last(self) -> ChatRequest:
        """The model must answer a visitor message, not its own last turn."""
        if self.messages[-1].role != "user":
            raise ValueError("the last message must be from the user")
        return self


HistoryMessage = ChatMessage | Mapping[str, Any]


class MessageLengthError(ValueError):
    """Raised when one message exceeds the configured character limit."""

    def __init__(self, index: int, limit: int) -> None:
        super().__init__(f"message {index + 1} must be at most {limit} characters")
        self.index = index
        self.limit = limit


def enforce_message_limits(
    messages: Sequence[ChatMessage], max_chars: int
) -> None:
    """Reject any message longer than the configured limit.

    This runs on the submitted messages before history trimming, so a long
    message cannot hide by being old enough to be dropped.

    Raises:
        MessageLengthError: If any message exceeds ``max_chars``.
    """
    for index, message in enumerate(messages):
        if len(message.content) > max_chars:
            raise MessageLengthError(index, max_chars)


def _content_of(message: HistoryMessage) -> str:
    """Read content from a model or a plain mapping."""
    if isinstance(message, ChatMessage):
        return message.content
    content = message.get("content", "")
    return content if isinstance(content, str) else ""


def trim_history(
    messages: Sequence[HistoryMessage],
    max_messages: int,
    max_chars: int,
) -> list[HistoryMessage]:
    """Keep recent history within both limits, always keeping the last user turn.

    First the oldest messages beyond ``max_messages`` are discarded. Then the
    oldest remaining messages are dropped until the total character count fits
    ``max_chars``. The final message is never dropped, even when it alone exceeds
    the character budget, because it is the question being answered.
    """
    if max_messages <= 0:
        return []

    kept = list(messages)[-max_messages:]
    while len(kept) > 1 and sum(len(_content_of(item)) for item in kept) > max_chars:
        kept.pop(0)
    return kept
