"""SSE chat endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import Settings, get_settings
from app.knowledge_router import KnowledgeRouterError, select_sections
from app.llm import (
    LLMClient,
    LLMError,
    LLMUnavailableError,
    StreamInterrupted,
    get_llm_client,
)
from app.prompt_builder import PromptBuildError, build_system_prompt
from app.schemas import (
    ChatRequest,
    MessageLengthError,
    enforce_message_limits,
    trim_history,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

HEARTBEAT_SECONDS = 15.0


def _friendly_message(code: str, contact_email: str) -> str:
    """Return a generic visitor-facing error message for an SSE code."""
    if code == "busy":
        return (
            "The assistant is busy right now. Please try again in a minute, "
            f"or email {contact_email}."
        )
    if code == "rate_limited":
        return "Too many requests right now. Please wait a moment and try again."
    if code == "invalid":
        return "I couldn't read that request. Please check your message and try again."
    if code == "interrupted":
        return "The answer was interrupted. Please try again."
    return (
        "Something went wrong. Please try again, or email "
        f"{contact_email}."
    )


def _data_event(text: str) -> str:
    """Encode one assistant text chunk as an SSE data event."""
    payload = json.dumps({"t": text}, ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\n"


def _done_event() -> str:
    """Encode the terminal done event."""
    return "event: done\ndata: {}\n\n"


def _error_event(code: str, message: str) -> str:
    """Encode a friendly terminal error event."""
    payload = json.dumps(
        {"code": code, "message": message}, ensure_ascii=False, separators=(",", ":")
    )
    return f"event: error\ndata: {payload}\n\n"


def _log_extra(request_id: str, error_type: str) -> dict[str, str]:
    """Build content-free metadata for a chat error log."""
    return {
        "request_id": request_id,
        "route": "/api/chat",
        "error_type": error_type,
    }


@router.post("/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
    settings: Settings = Depends(get_settings),
    llm: LLMClient = Depends(get_llm_client),
) -> StreamingResponse:
    """Stream an answer about Tisha as server-sent events."""
    request_id = getattr(http_request.state, "request_id", None) or uuid.uuid4().hex
    try:
        enforce_message_limits(request.messages, settings.max_message_chars)
    except MessageLengthError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    history = trim_history(
        request.messages,
        settings.max_history_messages,
        settings.max_history_chars,
    )
    messages = [message.model_dump() for message in history]

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "X-Request-ID": request_id,
    }
    return StreamingResponse(
        _event_stream(messages, settings, llm, request_id),
        media_type="text/event-stream",
        headers=headers,
    )


async def _event_stream(
    messages: list[dict[str, Any]],
    settings: Settings,
    llm: LLMClient,
    request_id: str,
) -> AsyncGenerator[str, None]:
    """Prepare the prompt and relay model chunks as SSE events.

    A producer task owns the LLM stream while the response generator waits on a
    queue. That lets heartbeats flow during quiet periods without cancelling the
    upstream request. Cancelling the response (a client disconnect) cancels the
    producer and stops the model call.
    """
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=64)

    async def produce() -> None:
        try:
            section_ids = select_sections(messages)
            system_prompt = build_system_prompt(section_ids)
            groq_messages = [
                {"role": "system", "content": system_prompt},
                *messages,
            ]
            async for text in llm.stream_chat(
                groq_messages,
                settings.max_output_tokens_chat,
                settings.temperature,
            ):
                await queue.put(("chunk", text))
            await queue.put(("done", None))
        except StreamInterrupted:
            logger.warning(
                "Chat stream interrupted",
                extra=_log_extra(request_id, "StreamInterrupted"),
            )
            await queue.put(
                (
                    "error",
                    (
                        "interrupted",
                        _friendly_message("interrupted", settings.contact_email),
                    ),
                )
            )
        except LLMUnavailableError:
            logger.warning(
                "Chat LLM unavailable",
                extra=_log_extra(request_id, "LLMUnavailableError"),
            )
            await queue.put(
                ("error", ("busy", _friendly_message("busy", settings.contact_email)))
            )
        except (KnowledgeRouterError, PromptBuildError):
            logger.exception(
                "Chat prompt preparation failed",
                extra=_log_extra(request_id, "PromptPreparationError"),
            )
            await queue.put(
                (
                    "error",
                    ("internal", _friendly_message("internal", settings.contact_email)),
                )
            )
        except LLMError:
            logger.exception(
                "Chat LLM request failed",
                extra=_log_extra(request_id, "LLMError"),
            )
            await queue.put(
                (
                    "error",
                    ("internal", _friendly_message("internal", settings.contact_email)),
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never expose internals to visitors
            logger.exception(
                "Unexpected chat failure",
                extra=_log_extra(request_id, "UnexpectedError"),
            )
            await queue.put(
                (
                    "error",
                    ("internal", _friendly_message("internal", settings.contact_email)),
                )
            )

    producer = asyncio.create_task(produce())
    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(
                    queue.get(), timeout=HEARTBEAT_SECONDS
                )
            except TimeoutError:
                yield ": ping\n\n"
                continue

            if kind == "chunk":
                yield _data_event(payload)
            elif kind == "done":
                yield _done_event()
                return
            else:
                code, message = payload
                yield _error_event(code, message)
                return
    finally:
        producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer
