"""Keyword-based section router for on-demand knowledge deep dives.

The router never calls an LLM. It scores the latest user message against the
keywords in ``knowledge/sections/index.json`` and falls back to the previous
user message when the latest one has no keyword hit, which keeps short
follow-ups on the same topic. Assistant messages are never used for routing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.knowledge_loader import (
    SectionIndexError,
    load_sections_index,
)

# Kept for callers that caught the router's original error name.
KnowledgeRouterError = SectionIndexError


def load_section_index(
    index_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Read and validate the section index through the shared loader."""
    return load_sections_index(index_path)


def _compile_keyword_patterns(
    index: Mapping[str, Mapping[str, Any]],
) -> dict[str, tuple[re.Pattern[str], ...]]:
    """Compile each section's keywords with word-boundary lookarounds.

    Lookarounds are used instead of ``\\b`` so keywords containing apostrophes
    or hyphens still match, and short keywords such as ``rag`` or ``pr`` do not
    match inside larger words such as ``storage`` or ``profile``.
    """
    compiled: dict[str, tuple[re.Pattern[str], ...]] = {}
    for section_id, entry in index.items():
        patterns = []
        for keyword in entry["keywords"]:
            escaped = re.escape(keyword.lower())
            patterns.append(re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"))
        compiled[section_id] = tuple(patterns)
    return compiled


def _user_message_texts(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return the content of user messages only, in conversation order."""
    texts: list[str] = []
    for message in messages:
        if str(message.get("role", "")).lower() != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content)
    return texts


def _score_text(
    text: str,
    patterns: Mapping[str, tuple[re.Pattern[str], ...]],
) -> dict[str, int]:
    """Count keyword hits for every section in one message."""
    lowered = text.lower()
    return {
        section_id: sum(len(pattern.findall(lowered)) for pattern in section_patterns)
        for section_id, section_patterns in patterns.items()
    }


def select_sections(
    messages: Sequence[Mapping[str, Any]],
    max_sections: int = 2,
    *,
    index_path: str | Path | None = None,
) -> list[str]:
    """Return the best-matching deep-dive section ids for a conversation.

    Only user messages are scored. The latest user message is used first; if it
    contains no keyword hit, the previous user message is scored instead. The
    result is ordered by score (highest first, ties alphabetical) and capped at
    ``max_sections``. A general question returns an empty list because the core
    knowledge base already holds the project summaries.
    """
    if max_sections <= 0:
        return []

    index = load_section_index(index_path)
    patterns = _compile_keyword_patterns(index)
    texts = _user_message_texts(messages)
    if not texts:
        return []

    scores = _score_text(texts[-1], patterns)
    if not any(scores.values()) and len(texts) >= 2:
        scores = _score_text(texts[-2], patterns)

    ranked = sorted(
        ((score, section_id) for section_id, score in scores.items() if score >= 1),
        key=lambda item: (-item[0], item[1]),
    )
    return [section_id for _, section_id in ranked[:max_sections]]
