"""Tests for configuration loading and system prompt building."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app import config
from app.config import Settings
from app.prompt_builder import (
    DEEP_DIVE_HEADING,
    VISITOR_CONTEXT_HEADING,
    ProfileParseError,
    ProfileValidationError,
    PromptBuildError,
    SectionNotFoundError,
    build_system_prompt,
    load_core,
    reload_system_prompt,
)

PROMPT_TEMPLATE = (
    "Owner: {{OWNER_NAME}}\n"
    "Email: {{CONTACT_EMAIL}}\n"
    "<knowledge>\n{{KNOWLEDGE_BASE}}\n</knowledge>\n"
)

CORE: dict[str, Any] = {
    "identity": {
        "name": "Tisha",
        "pronouns": "she/her",
        "location": "Ahmedabad, Gujarat, India",
        "public_contact": {"email": "tisha@example.com"},
    },
    "summary": "A final-year IT student who builds working AI projects.",
    "career_goals": ["Find a backend or applied-AI internship."],
    "education": {"b_e": {"degree": "B.E. Information Technology"}},
    "full_time_employment": "None.",
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
        "skills_listed_unrated": {
            "note": "listed on her profile; proficiency level not specified",
            "backend": ["FastAPI"],
        },
    },
    "achievements": ["Won a small hackathon."],
    "known_gaps": ["No production experience yet."],
    "private_do_not_share": ["phone number"],
    "data_freshness": [{"item": "CPI", "value": "9.01", "as_of": "so far"}],
}

SECTION_TEXT = "ALPHA DEEP DIVE TEXT: the detailed story behind Project Alpha."
SECTION_INDEX: dict[str, Any] = {
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


def make_settings(**overrides: Any) -> Settings:
    """Build settings without touching the environment or a .env file."""
    values: dict[str, Any] = {
        "groq_api_keys": ["test-groq-key"],
        "groq_model": "test-groq-model",
        "contact_email": "tisha@example.com",
        "owner_name": "Tisha",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def write_project_files(
    tmp_path: Path,
    template: str = PROMPT_TEMPLATE,
    core: dict[str, Any] | None = None,
    section_text: str = SECTION_TEXT,
) -> tuple[Path, Path, Path, Path]:
    """Write a template, core knowledge base, index, and one deep dive."""
    prompt_path = tmp_path / "system_prompt.md"
    prompt_path.write_text(template, encoding="utf-8")

    core_path = tmp_path / "core.json"
    core_path.write_text(
        json.dumps(core if core is not None else CORE),
        encoding="utf-8",
    )

    sections_dir = tmp_path / "sections"
    sections_dir.mkdir()
    (sections_dir / "alpha.md").write_text(section_text, encoding="utf-8")

    index_path = sections_dir / "index.json"
    index_path.write_text(json.dumps(SECTION_INDEX), encoding="utf-8")
    return prompt_path, core_path, sections_dir, index_path


def build(
    prompt_path: Path,
    core_path: Path,
    sections_dir: Path,
    index_path: Path,
    section_ids: list[str] | None = None,
    settings: Settings | None = None,
    *,
    extra_context: str | None = None,
    use_cache: bool = True,
) -> str:
    """Build a prompt for a temp project with default test settings."""
    return build_system_prompt(
        section_ids,
        extra_context,
        prompt_path=prompt_path,
        core_path=core_path,
        sections_dir=sections_dir,
        index_path=index_path,
        settings=settings if settings is not None else make_settings(),
        use_cache=use_cache,
    )


def test_build_system_prompt_fills_all_placeholders(tmp_path: Path) -> None:
    """Every placeholder is replaced and the core knowledge base is injected."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    result = build(prompt_path, core_path, sections_dir, index_path, use_cache=False)

    assert "{{OWNER_NAME}}" not in result
    assert "{{CONTACT_EMAIL}}" not in result
    assert "{{KNOWLEDGE_BASE}}" not in result
    assert "Owner: Tisha" in result
    assert "Email: tisha@example.com" in result
    assert '"projects"' in result
    assert "Project Alpha" in result


def test_output_contains_owner_name(tmp_path: Path) -> None:
    """The configured owner name appears in the rendered prompt."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    result = build(
        prompt_path,
        core_path,
        sections_dir,
        index_path,
        settings=make_settings(owner_name="Asha Verma"),
    )

    assert "Asha Verma" in result


def test_knowledge_base_may_contain_braces(tmp_path: Path) -> None:
    """Template syntax inside the core knowledge base is not re-substituted."""
    project = dict(CORE["projects"][0])
    project["one_line_summary"] = "A Vue app that renders {{ item.name }} rows."
    prompt_path, core_path, sections_dir, index_path = write_project_files(
        tmp_path, core=make_core(projects=[project])
    )

    result = build(prompt_path, core_path, sections_dir, index_path, use_cache=False)

    assert "{{ item.name }}" in result
    assert '"projects"' in result


def test_invalid_json_raises_profile_parse_error(tmp_path: Path) -> None:
    """Malformed core JSON fails loudly with the file path."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)
    core_path.write_text('{"identity": ', encoding="utf-8")

    with pytest.raises(ProfileParseError, match="Invalid JSON"):
        load_core(core_path)

    with pytest.raises(ProfileParseError, match="Invalid JSON"):
        build(prompt_path, core_path, sections_dir, index_path, use_cache=False)


def test_missing_required_section_raises(tmp_path: Path) -> None:
    """A core without known_gaps is rejected with a section-focused error."""
    core = make_core()
    del core["known_gaps"]
    prompt_path, core_path, sections_dir, index_path = write_project_files(
        tmp_path, core=core
    )

    with pytest.raises(ProfileValidationError, match="known_gaps"):
        build(prompt_path, core_path, sections_dir, index_path, use_cache=False)


def test_malformed_project_raises(tmp_path: Path) -> None:
    """A project missing one_line_summary is rejected."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(
        tmp_path,
        core=make_core(projects=[{"name": "Incomplete", "deep_dive_id": "alpha"}]),
    )

    with pytest.raises(ProfileValidationError, match="one_line_summary"):
        build(prompt_path, core_path, sections_dir, index_path, use_cache=False)


def test_production_rejects_placeholder_data(tmp_path: Path) -> None:
    """ENVIRONMENT=production refuses to ship fake knowledge."""
    identity = {**CORE["identity"], "name": "PLACEHOLDER: Tisha"}
    prompt_path, core_path, sections_dir, index_path = write_project_files(
        tmp_path, core=make_core(identity=identity)
    )

    with pytest.raises(ProfileValidationError, match="PLACEHOLDER"):
        build(
            prompt_path,
            core_path,
            sections_dir,
            index_path,
            settings=make_settings(environment="production"),
            use_cache=False,
        )


def test_production_rejects_empty_projects(tmp_path: Path) -> None:
    """Production requires at least one project."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(
        tmp_path, core=make_core(projects=[])
    )

    with pytest.raises(ProfileValidationError, match="no projects"):
        build(
            prompt_path,
            core_path,
            sections_dir,
            index_path,
            settings=make_settings(environment="production"),
            use_cache=False,
        )


def test_production_rejects_empty_skills(tmp_path: Path) -> None:
    """Production requires at least one recorded skill."""
    empty_skills = {"proficiency_stated": {}, "skills_listed_unrated": {}}
    prompt_path, core_path, sections_dir, index_path = write_project_files(
        tmp_path, core=make_core(skills=empty_skills)
    )

    with pytest.raises(ProfileValidationError, match="no skills"):
        build(
            prompt_path,
            core_path,
            sections_dir,
            index_path,
            settings=make_settings(environment="production"),
            use_cache=False,
        )


def test_unknown_placeholder_raises(tmp_path: Path) -> None:
    """A placeholder without a value raises a clear error naming it."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(
        tmp_path, template="Owner: {{OWNER_NAME}}\nSecret: {{SECRET_VALUE}}\n"
    )

    with pytest.raises(PromptBuildError, match="SECRET_VALUE"):
        build(prompt_path, core_path, sections_dir, index_path, use_cache=False)


def test_placeholder_with_whitespace_is_filled(tmp_path: Path) -> None:
    """Spaced placeholders such as {{ OWNER_NAME }} are supported."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(
        tmp_path, template="Owner: {{ OWNER_NAME }} / {{CONTACT_EMAIL}}"
    )

    result = build(prompt_path, core_path, sections_dir, index_path, use_cache=False)

    assert result == "Owner: Tisha / tisha@example.com"


def test_placeholder_values_are_logged_as_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Leftover PLACEHOLDER: data is logged, not printed."""
    identity = {**CORE["identity"], "name": "PLACEHOLDER: Tisha"}
    prompt_path, core_path, sections_dir, index_path = write_project_files(
        tmp_path, core=make_core(identity=identity)
    )

    with caplog.at_level(logging.WARNING, logger="app.knowledge_loader"):
        build(prompt_path, core_path, sections_dir, index_path, use_cache=False)

    assert "PLACEHOLDER:" in caplog.text


def test_prompt_is_cached_per_settings(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Repeat builds with identical settings hit the cache."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)
    settings = make_settings(owner_name="Cached Owner")

    with caplog.at_level(logging.INFO, logger="app.prompt_builder"):
        first = build(prompt_path, core_path, sections_dir, index_path, settings=settings)
        second = build(prompt_path, core_path, sections_dir, index_path, settings=settings)

    assert first == second
    builds = [message for message in caplog.messages if "Built system prompt" in message]
    assert len(builds) == 1


def test_cache_reflects_changed_settings(tmp_path: Path) -> None:
    """A different owner name must not reuse the previous cached prompt."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    first = build(
        prompt_path,
        core_path,
        sections_dir,
        index_path,
        settings=make_settings(owner_name="First"),
    )
    second = build(
        prompt_path,
        core_path,
        sections_dir,
        index_path,
        settings=make_settings(owner_name="Second"),
    )

    assert "First" in first
    assert "Second" in second
    assert "First" not in second


def test_cache_is_invalidated_by_core_edits(tmp_path: Path) -> None:
    """Editing core.json is picked up without a restart."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    first = build(prompt_path, core_path, sections_dir, index_path)
    assert "No production experience yet." in first

    core_path.write_text(
        json.dumps(make_core(known_gaps=["Updated gap from disk."])),
        encoding="utf-8",
    )
    bumped = core_path.stat().st_mtime_ns + 1_000_000_000
    os.utime(core_path, ns=(bumped, bumped))

    second = build(prompt_path, core_path, sections_dir, index_path)
    assert "Updated gap from disk." in second
    assert "No production experience yet." not in second


def test_cache_is_invalidated_by_section_edits(tmp_path: Path) -> None:
    """Editing a selected deep dive is picked up without a restart."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    first = build(prompt_path, core_path, sections_dir, index_path, ["alpha"])
    assert SECTION_TEXT in first

    section_path = sections_dir / "alpha.md"
    section_path.write_text("UPDATED ALPHA DEEP DIVE.", encoding="utf-8")
    bumped = section_path.stat().st_mtime_ns + 1_000_000_000
    os.utime(section_path, ns=(bumped, bumped))

    second = build(prompt_path, core_path, sections_dir, index_path, ["alpha"])
    assert "UPDATED ALPHA DEEP DIVE." in second
    assert SECTION_TEXT not in second


def test_reload_system_prompt_refreshes_owner(tmp_path: Path) -> None:
    """reload_system_prompt clears the cache and rebuilds with new settings."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    assert "Old Owner" in build(
        prompt_path,
        core_path,
        sections_dir,
        index_path,
        settings=make_settings(owner_name="Old Owner"),
    )
    refreshed = reload_system_prompt(
        prompt_path=prompt_path,
        core_path=core_path,
        sections_dir=sections_dir,
        index_path=index_path,
        settings=make_settings(owner_name="New Owner"),
    )

    assert "New Owner" in refreshed
    assert "Old Owner" not in refreshed


def test_selected_section_is_appended_under_a_heading(tmp_path: Path) -> None:
    """A selected deep dive appears after the core under its own heading."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    result = build(prompt_path, core_path, sections_dir, index_path, ["alpha"])

    assert DEEP_DIVE_HEADING in result
    assert "## Project Alpha Deep Dive" in result
    assert SECTION_TEXT in result
    assert result.index(DEEP_DIVE_HEADING) > result.index("Project Alpha")


def test_unselected_section_text_is_absent(tmp_path: Path) -> None:
    """Without a selection the prompt contains no deep-dive text."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    result = build(prompt_path, core_path, sections_dir, index_path, [])

    assert SECTION_TEXT not in result
    assert DEEP_DIVE_HEADING not in result


def test_unknown_section_id_raises(tmp_path: Path) -> None:
    """An id with no index entry fails with a clear error."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    with pytest.raises(SectionNotFoundError, match="missing_section"):
        build(
            prompt_path,
            core_path,
            sections_dir,
            index_path,
            ["missing_section"],
            use_cache=False,
        )


def test_duplicate_section_ids_are_rendered_once(tmp_path: Path) -> None:
    """Repeated ids are deduplicated."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    result = build(prompt_path, core_path, sections_dir, index_path, ["alpha", "alpha"])

    assert result.count(DEEP_DIVE_HEADING) == 1
    assert result.count(SECTION_TEXT) == 1


def test_token_estimates_are_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Both the core-alone and core-plus-sections estimates are logged."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    with caplog.at_level(logging.INFO, logger="app.prompt_builder"):
        build(prompt_path, core_path, sections_dir, index_path, ["alpha"])

    assert "core ~" in caplog.text
    assert "core+sections ~" in caplog.text


def test_extra_context_is_appended_under_untrusted_heading(tmp_path: Path) -> None:
    """Visitor text is appended literally under an explicitly untrusted heading."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)
    context = "Job description: needs {{OWNER_NAME}} and Python."

    result = build(
        prompt_path,
        core_path,
        sections_dir,
        index_path,
        extra_context=context,
        use_cache=False,
    )

    assert VISITOR_CONTEXT_HEADING in result
    assert result.index(VISITOR_CONTEXT_HEADING) > result.index("Project Alpha")
    assert context in result
    # Visitor text is never templated, so the placeholder survives untouched.
    assert result.count("Owner: Tisha") == 1


def test_no_visitor_heading_without_extra_context(tmp_path: Path) -> None:
    """Core-only prompts carry no untrusted-context section."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    result = build(prompt_path, core_path, sections_dir, index_path, use_cache=False)

    assert VISITOR_CONTEXT_HEADING not in result


def test_extra_context_is_part_of_the_cache_key(tmp_path: Path) -> None:
    """Two different job descriptions never share a cached prompt."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    first = build(
        prompt_path,
        core_path,
        sections_dir,
        index_path,
        extra_context="CTX-ALPHA role",
    )
    second = build(
        prompt_path,
        core_path,
        sections_dir,
        index_path,
        extra_context="CTX-BETA role",
    )

    assert "CTX-ALPHA role" in first
    assert "CTX-BETA role" not in first
    assert "CTX-BETA role" in second
    assert "CTX-ALPHA role" not in second


def test_cache_reflects_changed_contact_email(tmp_path: Path) -> None:
    """A different public contact email must rebuild the prompt."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    first = build(
        prompt_path,
        core_path,
        sections_dir,
        index_path,
        settings=make_settings(contact_email="first@example.com"),
    )
    second = build(
        prompt_path,
        core_path,
        sections_dir,
        index_path,
        settings=make_settings(contact_email="second@example.com"),
    )

    assert "first@example.com" in first
    assert "second@example.com" in second
    assert "first@example.com" not in second


def test_cache_is_invalidated_by_index_edits(tmp_path: Path) -> None:
    """Editing the section index is picked up without a restart."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    first = build(prompt_path, core_path, sections_dir, index_path, ["alpha"])
    assert "## Project Alpha Deep Dive" in first

    renamed = {
        "alpha": {
            **SECTION_INDEX["alpha"],
            "title": "Renamed Alpha Deep Dive",
        }
    }
    index_path.write_text(json.dumps(renamed), encoding="utf-8")
    bumped = index_path.stat().st_mtime_ns + 1_000_000_000
    os.utime(index_path, ns=(bumped, bumped))

    second = build(prompt_path, core_path, sections_dir, index_path, ["alpha"])
    assert "## Renamed Alpha Deep Dive" in second
    assert "## Project Alpha Deep Dive" not in second


def test_cache_is_invalidated_by_template_edits(tmp_path: Path) -> None:
    """Editing the Markdown template is picked up without a restart."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(tmp_path)

    first = build(prompt_path, core_path, sections_dir, index_path)
    assert "Template v1" not in first

    prompt_path.write_text(
        PROMPT_TEMPLATE + "Template v2\n", encoding="utf-8"
    )
    bumped = prompt_path.stat().st_mtime_ns + 1_000_000_000
    os.utime(prompt_path, ns=(bumped, bumped))

    second = build(prompt_path, core_path, sections_dir, index_path)
    assert "Template v2" in second


def test_missing_section_is_a_profile_parse_error(tmp_path: Path) -> None:
    """The missing-section failure is catchable as a profile parse error."""
    core = make_core()
    del core["known_gaps"]
    prompt_path, core_path, sections_dir, index_path = write_project_files(
        tmp_path, core=core
    )

    with pytest.raises(ProfileParseError, match="known_gaps"):
        build(prompt_path, core_path, sections_dir, index_path, use_cache=False)


def test_production_rejects_placeholder_in_a_section(tmp_path: Path) -> None:
    """A deep dive cannot ship with PLACEHOLDER: text in production."""
    prompt_path, core_path, sections_dir, index_path = write_project_files(
        tmp_path, section_text="PLACEHOLDER: alpha story"
    )

    with pytest.raises(ProfileValidationError, match="PLACEHOLDER"):
        build(
            prompt_path,
            core_path,
            sections_dir,
            index_path,
            ["alpha"],
            settings=make_settings(environment="production"),
            use_cache=False,
        )


def test_knowledge_dir_setting_controls_the_default_paths(tmp_path: Path) -> None:
    """KNOWLEDGE_DIR replaces the default core, index, and section locations."""
    knowledge_dir = tmp_path / "knowledge"
    sections_dir = knowledge_dir / "sections"
    sections_dir.mkdir(parents=True)
    (knowledge_dir / "core.json").write_text(
        json.dumps(make_core(summary="Core loaded from KNOWLEDGE_DIR.")),
        encoding="utf-8",
    )
    (sections_dir / "index.json").write_text(json.dumps(SECTION_INDEX), encoding="utf-8")
    (sections_dir / "alpha.md").write_text(
        "ALPHA FROM KNOWLEDGE_DIR", encoding="utf-8"
    )
    prompt_path = tmp_path / "system_prompt.md"
    prompt_path.write_text(PROMPT_TEMPLATE, encoding="utf-8")

    result = build_system_prompt(
        ["alpha"],
        prompt_path=prompt_path,
        settings=make_settings(knowledge_dir=knowledge_dir),
        use_cache=False,
    )

    assert "Core loaded from KNOWLEDGE_DIR." in result
    assert "ALPHA FROM KNOWLEDGE_DIR" in result


def test_get_settings_fails_fast_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing key produces one clear RuntimeError naming GROQ_API_KEYS."""
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_MODEL", "test-groq-model")
    monkeypatch.setenv("CONTACT_EMAIL", "tisha@example.com")
    monkeypatch.setitem(config.Settings.model_config, "env_file", ())

    config.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="GROQ_API_KEYS"):
            config.get_settings()
    finally:
        config.get_settings.cache_clear()


def test_defaults_and_origin_parsing() -> None:
    """Defaults apply and ALLOWED_ORIGINS is split into a list."""
    settings = make_settings(allowed_origins="http://a.test, http://b.test ,")

    assert settings.max_jd_chars == 8000
    assert settings.max_message_chars == 1500
    assert settings.owner_name == "Tisha"
    assert settings.environment == "development"
    assert settings.allowed_origins_list == ["http://a.test", "http://b.test"]


def test_environment_is_normalised() -> None:
    """ENVIRONMENT accepts mixed case."""
    assert make_settings(environment="Production").environment == "production"


def test_api_key_loads_from_dotenv_as_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GROQ_API_KEY in a dotenv file is read without being exposed."""
    dotenv_file = tmp_path / "test.env"
    dotenv_file.write_text("GROQ_API_KEY=key-from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    settings = Settings(
        _env_file=dotenv_file,
        groq_model="test-groq-model",
        contact_email="tisha@example.com",
    )

    assert settings.groq_api_keys[0].get_secret_value() == "key-from-dotenv"
    assert "key-from-dotenv" not in repr(settings)
    assert str(settings.groq_api_keys[0]) == "**********"


def test_blank_api_key_is_rejected() -> None:
    """A whitespace-only key fails validation."""
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            groq_api_keys="   ",
            groq_model="test-groq-model",
            contact_email="tisha@example.com",
        )


def test_email_validation_rejects_double_at() -> None:
    """The tightened email check rejects malformed addresses."""
    with pytest.raises(ValidationError, match="email address"):
        make_settings(contact_email="a@@b.c")
