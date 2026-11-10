"""Minimal FastAPI app exposing the chat endpoint.

The endpoint routes the conversation to the relevant deep dives, builds the
system prompt, and sends everything to the configured Groq model.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.knowledge_router import KnowledgeRouterError, select_sections
from app.prompt_builder import PromptBuildError, build_system_prompt

logger = logging.getLogger(__name__)

app = FastAPI(title="Portfolio AI Chatbot", version="0.1.0")


class ChatMessage(BaseModel):
    """One message in the conversation."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    """A conversation to answer."""

    messages: list[ChatMessage] = Field(min_length=1)


class ChatResponse(BaseModel):
    """The assistant's reply and the deep dives that were loaded."""

    reply: str
    sections: list[str]


def _get_client(settings: Settings) -> Any:
    """Create the Groq client lazily so importing this module needs no key."""
    from groq import Groq

    return Groq(api_key=settings.groq_api_keys[0].get_secret_value())


@app.get("/health")
def health() -> dict[str, str]:
    """Return a simple liveness response."""
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """Answer a visitor's question about Tisha."""
    settings = get_settings()

    for message in request.messages:
        if len(message.content) > settings.max_message_chars:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Each message must be at most "
                    f"{settings.max_message_chars} characters."
                ),
            )

    messages = [message.model_dump() for message in request.messages]

    try:
        section_ids = select_sections(messages)
    except KnowledgeRouterError as exc:
        logger.exception("Section routing failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        system_prompt = build_system_prompt(section_ids)
    except PromptBuildError as exc:
        logger.exception("System prompt build failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    groq_messages = [
        {"role": "system", "content": system_prompt},
        *messages,
    ]

    try:
        completion = _get_client(settings).chat.completions.create(
            model=settings.groq_model,
            messages=groq_messages,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001 - upstream SDK errors vary
        logger.exception("Groq request failed")
        raise HTTPException(
            status_code=502,
            detail="The language model is unavailable right now.",
        ) from exc

    choices = getattr(completion, "choices", None)
    if not choices:
        raise HTTPException(status_code=502, detail="The language model returned no reply.")

    reply = choices[0].message.content
    if not reply:
        raise HTTPException(status_code=502, detail="The language model returned an empty reply.")

    return ChatResponse(reply=reply, sections=section_ids)
