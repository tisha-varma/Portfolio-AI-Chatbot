"""Keyword-based section router for on-demand knowledge deep dives.

The router never calls an LLM. It scores the latest user message against the
keywords in ``knowledge/sections/index.json`` and falls back to the previous
user message when the latest one has no keyword hit, which keeps short
follow-ups on the same topic.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

BACKEND_DIR: Path = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_PATH: Path = BACKEND_DIR / "knowledge" / "sections" / "index.json"


class KnowledgeRouterError(RuntimeError):
    """Raised when the section index cannot be read or is malformed."""


def load_section_index(
    index_path: str | Path = DEFAULT_INDEX_PATH,
) -> dict[str, dict[str, Any]]:
    """Load and validate the section index.

    Raises:
        KnowledgeRouterError: If the file is missing, invalid JSON, or an entry
            is missing its title, keywords, or description.
    """
    path = Path(index_path)
    if not path.is_file():
        raise KnowledgeRouterError(f"Section index not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KnowledgeRouterError(
            f"Invalid JSON in section index {path} "
            f"(line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc

    if not isinstance(data, dict) or not data:
        raise KnowledgeRouterError(f"Section index {path} must be a non-empty JSON object.")

    for section_id, entry in data.items():
        if not isinstance(entry, dict):
            raise KnowledgeRouterError(f"Section index entry {section_id!r} must be an object.")
        if not isinstance(entry.get("title"), str) or not entry["title"].strip():
            raise KnowledgeRouterError(f"Section index entry {section_id!r} has no title.")
        if not isinstance(entry.get("description"), str) or not entry["description"].strip():
            raise KnowledgeRouterError(f"Section index entry {section_id!r} has no description.")
        keywords = entry.get("keywords")
        if not isinstance(keywords, list) or not all(
            isinstance(keyword, str) and keyword.strip() for keyword in keywords
        ):
            raise KnowledgeRouterError(
                f"Section index entry {section_id!r} must have a list of non-empty keywords."
            )

    return data


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
    index_path: str | Path = DEFAULT_INDEX_PATH,
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
