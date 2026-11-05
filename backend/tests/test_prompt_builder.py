"""Tests for configuration loading and system prompt building."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import config
from app.config import Settings
from app.prompt_builder import (
    ProfileParseError,
    ProfileValidationError,
    PromptBuildError,
    build_system_prompt,
    load_profile,
    reload_system_prompt,
)

PROMPT_TEMPLATE = (
    "Owner: {{OWNER_NAME}}\n"
    "Email: {{CONTACT_EMAIL}}\n"
    "<knowledge>\n{{KNOWLEDGE_BASE}}\n</knowledge>\n"
)

PROFILE: dict[str, object] = {
    "identity": {
        "name": "Tisha",
        "college": "Example Institute of Technology",
        "degree": "B.Tech",
        "branch": "Computer Science and Engineering",
        "expected_graduation": "May 2027",
    },
    "education": {"cgpa": "8.40", "semester": "5", "scale": "10"},
    "skills": {
        "strong": ["Python"],
        "working_knowledge": ["FastAPI"],
        "familiar": [],
        "not_experienced": ["Kubernetes"],
    },
    "projects": [
        {
            "name": "Project Alpha",
            "one_line_summary": "Summarises long documents.",
            "tech_stack": ["Python"],
            "status": "Completed",
        }
    ],
    "contact": {"public_contact_only": ["tisha@example.com"]},
    "private_do_not_share": ["phone_number"],
    "known_gaps": ["No production experience yet."],
}


def make_profile(**overrides: object) -> dict[str, object]:
    """Return a deep copy of the fixture profile with top-level overrides."""
    profile: dict[str, object] = json.loads(json.dumps(PROFILE))
    profile.update(overrides)
    return profile


def make_settings(**overrides: object) -> Settings:
    """Build settings without touching the environment or a .env file."""
    values: dict[str, object] = {
        "groq_api_key": "test-groq-key",
        "groq_model": "test-groq-model",
        "contact_email": "tisha@example.com",
        "owner_name": "Tisha",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def write_project_files(
    tmp_path: Path,
    template: str = PROMPT_TEMPLATE,
    profile: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    """Write a prompt template and knowledge base into a temp directory."""
    prompt_path = tmp_path / "system_prompt.md"
    prompt_path.write_text(template, encoding="utf-8")

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(profile if profile is not None else PROFILE),
        encoding="utf-8",
    )
    return prompt_path, profile_path


def build(
    prompt_path: Path,
    profile_path: Path,
    settings: Settings | None = None,
    *,
    use_cache: bool = True,
) -> str:
    """Build a prompt for a temp project with default test settings."""
    return build_system_prompt(
        prompt_path=prompt_path,
        profile_path=profile_path,
        settings=settings if settings is not None else make_settings(),
        use_cache=use_cache,
    )


def test_build_system_prompt_fills_all_placeholders(tmp_path: Path) -> None:
    """Every placeholder is replaced and the knowledge base is injected."""
    prompt_path, profile_path = write_project_files(tmp_path)

    result = build(prompt_path, profile_path, use_cache=False)

    assert "{{" not in result
    assert "}}" not in result
    assert "Owner: Tisha" in result
    assert "Email: tisha@example.com" in result
    assert '"projects"' in result
    assert "Project Alpha" in result


def test_output_contains_owner_name(tmp_path: Path) -> None:
    """The configured owner name appears in the rendered prompt."""
    prompt_path, profile_path = write_project_files(tmp_path)

    result = build(prompt_path, profile_path, make_settings(owner_name="Asha Verma"))

    assert "Asha Verma" in result


def test_knowledge_base_may_contain_braces(tmp_path: Path) -> None:
    """Template syntax inside the profile is not treated as a placeholder."""
    project = dict(PROFILE["projects"][0])  # type: ignore[index]
    project["one_line_summary"] = "A Vue app that renders {{ item.name }} rows."
    prompt_path, profile_path = write_project_files(
        tmp_path, profile=make_profile(projects=[project])
    )

    result = build(prompt_path, profile_path, use_cache=False)

    assert "{{ item.name }}" in result
    assert '"projects"' in result


def test_invalid_json_raises_profile_parse_error(tmp_path: Path) -> None:
    """Malformed knowledge JSON fails loudly with the file path."""
    prompt_path, profile_path = write_project_files(tmp_path)
    profile_path.write_text('{"identity": ', encoding="utf-8")

    with pytest.raises(ProfileParseError, match="Invalid JSON"):
        load_profile(profile_path)

    with pytest.raises(ProfileParseError, match="Invalid JSON"):
        build(prompt_path, profile_path, use_cache=False)


def test_missing_required_section_raises(tmp_path: Path) -> None:
    """A profile without known_gaps is rejected with a section-focused error."""
    profile = make_profile()
    del profile["known_gaps"]
    prompt_path, profile_path = write_project_files(tmp_path, profile=profile)

    with pytest.raises(ProfileValidationError, match="known_gaps"):
        build(prompt_path, profile_path, use_cache=False)


def test_malformed_project_raises(tmp_path: Path) -> None:
    """A project missing one_line_summary is rejected."""
    prompt_path, profile_path = write_project_files(
        tmp_path, profile=make_profile(projects=[{"name": "Incomplete", "status": "Draft"}])
    )

    with pytest.raises(ProfileValidationError, match="one_line_summary"):
        build(prompt_path, profile_path, use_cache=False)


def test_production_rejects_placeholder_data(tmp_path: Path) -> None:
    """ENVIRONMENT=production refuses to ship fake knowledge."""
    profile = make_profile(identity={**PROFILE["identity"], "name": "PLACEHOLDER: Tisha"})  # type: ignore[dict-item]
    prompt_path, profile_path = write_project_files(tmp_path, profile=profile)

    with pytest.raises(ProfileValidationError, match="PLACEHOLDER"):
        build(
            prompt_path,
            profile_path,
            make_settings(environment="production"),
            use_cache=False,
        )


def test_production_rejects_empty_projects(tmp_path: Path) -> None:
    """Production requires at least one project."""
    prompt_path, profile_path = write_project_files(tmp_path, profile=make_profile(projects=[]))

    with pytest.raises(ProfileValidationError, match="no projects"):
        build(
            prompt_path,
            profile_path,
            make_settings(environment="production"),
            use_cache=False,
        )


def test_production_rejects_empty_skills(tmp_path: Path) -> None:
    """Production requires at least one recorded skill."""
    empty_skills = {
        "strong": [],
        "working_knowledge": [],
        "familiar": [],
        "not_experienced": [],
    }
    prompt_path, profile_path = write_project_files(
        tmp_path, profile=make_profile(skills=empty_skills)
    )

    with pytest.raises(ProfileValidationError, match="no skills"):
        build(
            prompt_path,
            profile_path,
            make_settings(environment="production"),
            use_cache=False,
        )


def test_unknown_placeholder_raises(tmp_path: Path) -> None:
    """A placeholder without a value raises a clear error naming it."""
    prompt_path, profile_path = write_project_files(
        tmp_path, template="Owner: {{OWNER_NAME}}\nSecret: {{SECRET_VALUE}}\n"
    )

    with pytest.raises(PromptBuildError, match="SECRET_VALUE"):
        build(prompt_path, profile_path, use_cache=False)


def test_placeholder_with_whitespace_is_filled(tmp_path: Path) -> None:
    """Spaced placeholders such as {{ OWNER_NAME }} are supported."""
    prompt_path, profile_path = write_project_files(
        tmp_path, template="Owner: {{ OWNER_NAME }} / {{CONTACT_EMAIL}}"
    )

    result = build(prompt_path, profile_path, use_cache=False)

    assert result == "Owner: Tisha / tisha@example.com"


def test_placeholder_values_are_logged_as_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Leftover PLACEHOLDER: data is logged, not printed."""
    profile = make_profile(identity={**PROFILE["identity"], "name": "PLACEHOLDER: Tisha"})  # type: ignore[dict-item]
    prompt_path, profile_path = write_project_files(tmp_path, profile=profile)

    with caplog.at_level(logging.WARNING, logger="app.prompt_builder"):
        build(prompt_path, profile_path, use_cache=False)

    assert "PLACEHOLDER:" in caplog.text


def test_prompt_is_cached_per_settings(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Repeat builds with identical settings hit the cache."""
    prompt_path, profile_path = write_project_files(tmp_path)
    settings = make_settings(owner_name="Cached Owner")

    with caplog.at_level(logging.INFO, logger="app.prompt_builder"):
        first = build(prompt_path, profile_path, settings)
        second = build(prompt_path, profile_path, settings)

    assert first == second
    builds = [message for message in caplog.messages if "Built system prompt" in message]
    assert len(builds) == 1


def test_cache_reflects_changed_settings(tmp_path: Path) -> None:
    """A different owner name must not reuse the previous cached prompt."""
    prompt_path, profile_path = write_project_files(tmp_path)

    first = build(prompt_path, profile_path, make_settings(owner_name="First"))
    second = build(prompt_path, profile_path, make_settings(owner_name="Second"))

    assert "First" in first
    assert "Second" in second
    assert "First" not in second


def test_cache_is_invalidated_by_profile_edits(tmp_path: Path) -> None:
    """Editing profile.json is picked up without a restart."""
    prompt_path, profile_path = write_project_files(tmp_path)

    first = build(prompt_path, profile_path)
    assert "No production experience yet." in first

    profile_path.write_text(
        json.dumps(make_profile(known_gaps=["Updated gap from disk."])),
        encoding="utf-8",
    )
    bumped = profile_path.stat().st_mtime_ns + 1_000_000_000
    os.utime(profile_path, ns=(bumped, bumped))

    second = build(prompt_path, profile_path)
    assert "Updated gap from disk." in second
    assert "No production experience yet." not in second


def test_reload_system_prompt_refreshes_owner(tmp_path: Path) -> None:
    """reload_system_prompt clears the cache and rebuilds with new settings."""
    prompt_path, profile_path = write_project_files(tmp_path)

    assert "Old Owner" in build(prompt_path, profile_path, make_settings(owner_name="Old Owner"))
    refreshed = reload_system_prompt(
        prompt_path=prompt_path,
        profile_path=profile_path,
        settings=make_settings(owner_name="New Owner"),
    )

    assert "New Owner" in refreshed
    assert "Old Owner" not in refreshed


def test_get_settings_fails_fast_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing GROQ_API_KEY produces one clear RuntimeError."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_MODEL", "test-groq-model")
    monkeypatch.setenv("CONTACT_EMAIL", "tisha@example.com")
    monkeypatch.setitem(config.Settings.model_config, "env_file", ())

    config.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
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

    assert settings.groq_api_key.get_secret_value() == "key-from-dotenv"
    assert "key-from-dotenv" not in repr(settings)
    assert str(settings.groq_api_key) == "**********"


def test_blank_api_key_is_rejected() -> None:
    """A whitespace-only key fails validation."""
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            groq_api_key="   ",
            groq_model="test-groq-model",
            contact_email="tisha@example.com",
        )


def test_email_validation_rejects_double_at() -> None:
    """The tightened email check rejects malformed addresses."""
    with pytest.raises(ValidationError, match="email address"):
        make_settings(contact_email="a@@b.c")
