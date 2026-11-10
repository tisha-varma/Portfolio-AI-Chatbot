"""Tests for the validated knowledge loader."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app.knowledge_loader import (
    REQUIRED_CORE_SECTIONS,
    ProfileParseError,
    ProfileValidationError,
    SectionIndexError,
    enforce_no_placeholders,
    load_core,
    load_core_data,
    load_sections_index,
    validate_core,
)

CORE: dict[str, Any] = {
    "identity": {
        "name": "Tisha",
        "public_contact": {"email": "tisha@example.com"},
    },
    "summary": "A final-year IT student who builds working AI projects.",
    "education": {"b_e": {"degree": "B.E. Information Technology"}},
    "experience": [
        {
            "role": "Machine Learning Intern",
            "organisation": "Acme",
            "dates": "2024",
            "details": ["Built a small model."],
        }
    ],
    "projects": [
        {
            "name": "Project Alpha",
            "one_line_summary": "Summarises long documents.",
            "my_role": "Built solo.",
            "tech_stack": ["Python"],
            "status": "Public repository.",
            "links": {},
            "key_facts": ["1 automated test"],
            "deep_dive_id": "alpha",
        }
    ],
    "skills": {
        "proficiency_stated": {"Python": "working level"},
        "skills_listed_unrated": {"backend": ["FastAPI"]},
    },
    "known_gaps": ["No production experience yet."],
    "private_do_not_share": ["phone number"],
    "data_freshness": [{"item": "CPI", "value": "9.01", "as_of": "so far"}],
}

INDEX: dict[str, Any] = {
    "alpha": {
        "title": "Project Alpha Deep Dive",
        "description": "Detailed notes about Project Alpha.",
        "keywords": ["alpha", "document"],
    }
}


def make_core(**overrides: Any) -> dict[str, Any]:
    """Return a deep copy of the fixture core with top-level overrides."""
    core: dict[str, Any] = json.loads(json.dumps(CORE))
    core.update(overrides)
    return core


def write_json(path: Path, data: object) -> Path:
    """Write JSON to disk and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_core_returns_a_validated_model(tmp_path: Path) -> None:
    """A valid core loads as a typed model with the expected fields."""
    core_path = write_json(tmp_path / "core.json", CORE)

    core = load_core(core_path)

    assert core.identity.name == "Tisha"
    assert core.projects[0].name == "Project Alpha"
    assert core.skills.has_any


def test_load_core_data_returns_the_raw_mapping(tmp_path: Path) -> None:
    """The raw loader preserves fields that are not modelled explicitly."""
    core_path = write_json(
        tmp_path / "core.json", make_core(extra_section={"kept": True})
    )

    data = load_core_data(core_path)

    assert data["extra_section"] == {"kept": True}


def test_missing_core_file_raises_profile_parse_error(tmp_path: Path) -> None:
    """A missing file fails with its path in the message."""
    missing = tmp_path / "core.json"

    with pytest.raises(ProfileParseError, match="not found"):
        load_core(missing)


def test_invalid_json_raises_profile_parse_error(tmp_path: Path) -> None:
    """Malformed JSON names the file and the parse position."""
    core_path = tmp_path / "core.json"
    core_path.write_text('{"identity": ', encoding="utf-8")

    with pytest.raises(ProfileParseError, match="Invalid JSON"):
        load_core(core_path)


def test_non_object_json_raises_profile_parse_error(tmp_path: Path) -> None:
    """A JSON array is not a knowledge base."""
    core_path = write_json(tmp_path / "core.json", [])

    with pytest.raises(ProfileParseError, match="JSON object"):
        load_core(core_path)


@pytest.mark.parametrize("section", REQUIRED_CORE_SECTIONS)
def test_every_required_section_is_named_when_missing(
    tmp_path: Path, section: str
) -> None:
    """Each missing required section produces a clear error naming it."""
    core = make_core()
    del core[section]
    core_path = write_json(tmp_path / "core.json", core)

    with pytest.raises(ProfileValidationError, match=section):
        load_core(core_path)


def test_missing_section_is_also_a_profile_parse_error(tmp_path: Path) -> None:
    """Callers can catch the broader parse error for schema failures."""
    core = make_core()
    del core["data_freshness"]
    core_path = write_json(tmp_path / "core.json", core)

    with pytest.raises(ProfileParseError, match="data_freshness"):
        load_core(core_path)


def test_malformed_project_raises_profile_validation_error(tmp_path: Path) -> None:
    """A project missing one_line_summary is rejected by name."""
    core_path = write_json(
        tmp_path / "core.json",
        make_core(projects=[{"name": "Incomplete", "deep_dive_id": "alpha"}]),
    )

    with pytest.raises(ProfileValidationError, match="one_line_summary"):
        load_core(core_path)


def test_production_rejects_core_placeholders(tmp_path: Path) -> None:
    """ENVIRONMENT=production refuses fake values in core.json."""
    identity = {**CORE["identity"], "name": "PLACEHOLDER: Tisha"}
    core_path = write_json(
        tmp_path / "core.json", make_core(identity=identity)
    )

    with pytest.raises(ProfileValidationError, match="PLACEHOLDER"):
        load_core(core_path, environment="production")


def test_development_only_warns_about_placeholders(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Development logs the fake values instead of failing."""
    identity = {**CORE["identity"], "name": "PLACEHOLDER: Tisha"}
    core_path = write_json(
        tmp_path / "core.json", make_core(identity=identity)
    )

    with caplog.at_level(logging.WARNING, logger="app.knowledge_loader"):
        core = load_core(core_path)

    assert core.identity.name == "PLACEHOLDER: Tisha"
    assert "PLACEHOLDER:" in caplog.text


def test_production_requires_projects(tmp_path: Path) -> None:
    """Production will not start with no projects."""
    core_path = write_json(tmp_path / "core.json", make_core(projects=[]))

    with pytest.raises(ProfileValidationError, match="no projects"):
        load_core(core_path, environment="production")


def test_production_requires_skills(tmp_path: Path) -> None:
    """Production will not start with no skills."""
    core_path = write_json(
        tmp_path / "core.json",
        make_core(skills={"proficiency_stated": {}, "skills_listed_unrated": {}}),
    )

    with pytest.raises(ProfileValidationError, match="no skills"):
        load_core(core_path, environment="production")


def test_explicit_environment_can_be_overridden(tmp_path: Path) -> None:
    """The caller controls the environment used for placeholder checks."""
    identity = {**CORE["identity"], "name": "PLACEHOLDER: Tisha"}
    core_path = write_json(
        tmp_path / "core.json", make_core(identity=identity)
    )

    core = validate_core(load_core_data(core_path), environment="development")

    assert core.identity.name.startswith("PLACEHOLDER:")


def test_load_sections_index_validates_entries(tmp_path: Path) -> None:
    """A well-formed index loads as a mapping of entries."""
    index_path = write_json(tmp_path / "sections" / "index.json", INDEX)

    index = load_sections_index(index_path)

    assert index["alpha"]["title"] == "Project Alpha Deep Dive"
    assert index["alpha"]["keywords"] == ["alpha", "document"]


def test_missing_index_raises_section_index_error(tmp_path: Path) -> None:
    """A missing index fails with its path."""
    with pytest.raises(SectionIndexError, match="not found"):
        load_sections_index(tmp_path / "sections" / "index.json")


@pytest.mark.parametrize(
    "entry",
    [
        {"description": "No title.", "keywords": ["alpha"]},
        {"title": "No description.", "keywords": ["alpha"]},
        {"title": "No keywords.", "description": "Missing keywords."},
        {"title": "Bad keywords.", "description": "Not text.", "keywords": [1]},
    ],
)
def test_incomplete_index_entry_raises(
    tmp_path: Path, entry: dict[str, Any]
) -> None:
    """Every index entry must have a title, description, and keywords."""
    index_path = write_json(tmp_path / "index.json", {"alpha": entry})

    with pytest.raises(SectionIndexError):
        load_sections_index(index_path)


def test_enforce_no_placeholders_rejects_in_production(tmp_path: Path) -> None:
    """A section file with fake text blocks a production build."""
    section_path = tmp_path / "alpha.md"
    section_path.write_text("PLACEHOLDER: alpha story", encoding="utf-8")

    with pytest.raises(ProfileValidationError, match="PLACEHOLDER"):
        enforce_no_placeholders([section_path], environment="production")


def test_enforce_no_placeholders_warns_in_development(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Development reports the offending path without failing."""
    section_path = tmp_path / "alpha.md"
    section_path.write_text("PLACEHOLDER: alpha story", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="app.knowledge_loader"):
        offenders = enforce_no_placeholders([section_path])

    assert offenders == [section_path]
    assert "PLACEHOLDER:" in caplog.text


def test_knowledge_dir_env_controls_default_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KNOWLEDGE_DIR moves the default core and section index locations."""
    knowledge_dir = tmp_path / "custom-knowledge"
    write_json(knowledge_dir / "core.json", make_core(summary="From env dir."))
    write_json(knowledge_dir / "sections" / "index.json", INDEX)
    monkeypatch.setenv("KNOWLEDGE_DIR", str(knowledge_dir))

    core = load_core()
    index = load_sections_index()

    assert core.summary == "From env dir."
    assert "alpha" in index
