"""Fidelity, size, and structure checks for the two-tier knowledge base."""

from __future__ import annotations

import json
import re
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


def test_knowledge_files_contain_no_placeholder_text() -> None:
    """A shipped knowledge base must not contain fake PLACEHOLDER: values."""
    paths = [CORE_PATH, INDEX_PATH, *sorted(SECTIONS_DIR.glob("*.md"))]

    for path in paths:
        assert "PLACEHOLDER:" not in path.read_text(encoding="utf-8"), (
            f"{path.name} contains PLACEHOLDER:"
        )


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


SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def test_every_project_has_a_unique_stable_id() -> None:
    """Project ids are stable slugs and cannot collide."""
    projects = load_core(CORE_PATH)["projects"]
    project_ids = [project["id"] for project in projects]

    assert len(project_ids) == len(set(project_ids)), f"Duplicate project id(s): {project_ids}"
    for project_id in project_ids:
        assert SLUG_PATTERN.match(project_id), f"Project id is not a stable slug: {project_id!r}"


def test_every_experience_item_has_a_unique_stable_id() -> None:
    """Every internship, contribution, and founder role has its own id."""
    experience = load_core(CORE_PATH)["experience"]
    experience_ids = [item["id"] for item in experience]

    assert len(experience_ids) == len(set(experience_ids)), (
        f"Duplicate experience id(s): {experience_ids}"
    )
    for experience_id in experience_ids:
        assert SLUG_PATTERN.match(experience_id), (
            f"Experience id is not a stable slug: {experience_id!r}"
        )


def test_every_skill_group_has_a_unique_stable_id() -> None:
    """Each skill group in the source is addressable by one stable id."""
    skills = load_core(CORE_PATH)["skills"]
    group_ids = skills["group_ids"]
    unrated_groups = set(skills["skills_listed_unrated"]) - {"note"}

    assert set(group_ids) == {"proficiency_stated", *unrated_groups}
    assert len(set(group_ids.values())) == len(group_ids), (
        f"Duplicate skill group id(s): {group_ids}"
    )
    for group_id in group_ids.values():
        assert SLUG_PATTERN.match(group_id), f"Skill group id is not a stable slug: {group_id!r}"


def test_ids_are_unique_across_projects_experience_and_skills() -> None:
    """One id namespace, so a future lookup can never be ambiguous."""
    core = load_core(CORE_PATH)
    all_ids = (
        [project["id"] for project in core["projects"]]
        + [item["id"] for item in core["experience"]]
        + list(core["skills"]["group_ids"].values())
    )

    assert len(all_ids) == len(set(all_ids)), f"Duplicate id(s) across sections: {all_ids}"


def test_projects_have_at_most_four_key_facts() -> None:
    """Tier 1 stays a summary; detail belongs in the deep dives."""
    for project in load_core(CORE_PATH)["projects"]:
        assert len(project["key_facts"]) <= 4, (
            f"{project['id']} has {len(project['key_facts'])} key facts; the cap is 4."
        )


def test_every_known_gap_points_to_a_source_section() -> None:
    """Gaps are paraphrases of stated limits, each tied to the source."""
    for gap in load_core(CORE_PATH)["known_gaps"]:
        assert re.search(r"\[src \d", gap), f"Gap has no source pointer: {gap!r}"


def test_data_freshness_covers_every_dated_source_fact() -> None:
    """Dated statuses and usage numbers carry their as-of date."""
    entries = {entry["item"]: entry for entry in load_core(CORE_PATH)["data_freshness"]}

    assert entries["B.E. CPI"]["value"] == "9.01 / 10"
    assert entries["B.E. CPI"]["as_of"] == "so far"

    for item in ("INCF PR #44 status", "INCF PR #61 status"):
        assert entries[item]["value"] == "OPEN"
        assert entries[item]["as_of"] == "6 Nov 2026"

    assert entries["INCF pull-request tally"]["as_of"] == "6 Nov 2026"
    assert entries["Career Copilot usage"]["value"] == (
        "18 users, 31 resumes analyzed, 11 feedback entries"
    )
    assert entries["Career Copilot usage"]["as_of"] == "7 Nov 2026"

