"""Fidelity, size, and structure checks for the two-tier knowledge base."""

from __future__ import annotations

import json
from pathlib import Path

from app.prompt_builder import (
    REQUIRED_CORE_SECTIONS,
    load_core,
    load_sections_index,
    validate_core,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = BACKEND_DIR / "knowledge"
CORE_PATH = KNOWLEDGE_DIR / "core.json"
SECTIONS_DIR = KNOWLEDGE_DIR / "sections"
INDEX_PATH = SECTIONS_DIR / "index.json"

TEAMMATE_NAMES = ("Mugdha Bhangale", "Jay Raval")
EXPECTED_SECTION_IDS = {
    "razorflow_ai",
    "career_copilot",
    "amazon_recommender",
    "pharmamate",
    "ai_study_buddy",
    "workchain",
    "open_source_incf",
    "other_projects",
}


def compact_json(data: object) -> str:
    """Serialise exactly as the prompt builder does."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def test_core_json_validates() -> None:
    """The real core passes the same validation used at build time."""
    core = load_core(CORE_PATH)

    validated = validate_core(core)

    assert validated.identity.name == "Tisha Varma"
    assert validated.projects


def test_core_has_every_required_section() -> None:
    """No required tier-1 section is missing."""
    core = load_core(CORE_PATH)

    missing = [section for section in REQUIRED_CORE_SECTIONS if section not in core]

    assert not missing, f"core.json is missing: {', '.join(missing)}"


def test_core_contains_no_placeholder_text() -> None:
    """A shipped knowledge base must not contain fake PLACEHOLDER: values."""
    text = CORE_PATH.read_text(encoding="utf-8")

    assert "PLACEHOLDER:" not in text


def test_core_token_estimate_stays_under_the_hard_limit() -> None:
    """The always-loaded core must stay under 3,000 estimated tokens."""
    tokens = len(compact_json(load_core(CORE_PATH))) // 4

    assert tokens < 3_000, (
        f"core.json is about {tokens} tokens; the hard limit is 3,000. "
        "Move detail into knowledge/sections/ or trim wording."
    )


def test_teammate_names_are_absent_from_core_and_sections() -> None:
    """The Daily Planner teammates are referred to as 'two teammates'."""
    paths = [CORE_PATH, INDEX_PATH, *sorted(SECTIONS_DIR.glob("*.md"))]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        for name in TEAMMATE_NAMES:
            assert name not in text, f"{name} appears in {path}"


def test_every_deep_dive_id_exists_in_the_index() -> None:
    """Each project points at a real section id."""
    core = load_core(CORE_PATH)
    index = load_sections_index(INDEX_PATH)

    deep_dive_ids = {project["deep_dive_id"] for project in core["projects"]}

    assert deep_dive_ids <= set(index), (
        f"Unknown deep_dive_id(s): {sorted(deep_dive_ids - set(index))}"
    )


def test_index_contains_exactly_the_expected_sections() -> None:
    """The deep-dive set matches the phase-3 specification."""
    index = load_sections_index(INDEX_PATH)

    assert set(index) == EXPECTED_SECTION_IDS


def test_every_indexed_section_has_a_markdown_file() -> None:
    """The index and the files on disk cannot drift apart."""
    index = load_sections_index(INDEX_PATH)

    missing = [section_id for section_id in index if not (SECTIONS_DIR / f"{section_id}.md").is_file()]

    assert not missing, f"Sections in index.json without a file: {', '.join(missing)}"


def test_index_keywords_are_lowercase() -> None:
    """Keywords are matched in lowercase, so the index stores them that way."""
    index = load_sections_index(INDEX_PATH)

    for section_id, entry in index.items():
        for keyword in entry["keywords"]:
            assert keyword == keyword.lower(), f"{section_id!r} has a non-lowercase keyword: {keyword!r}"
