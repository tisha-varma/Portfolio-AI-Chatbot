"""Tests for configuration loading and system prompt building."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import config
from app.config import Settings
from app.prompt_builder import (
    ProfileParseError,
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

PROFILE = {
    "identity": {"name": "Tisha"},
    "projects": [{"name": "Project Alpha", "status": "Completed"}],
    "known_gaps": ["No production experience yet."],
}


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


def test_build_system_prompt_fills_all_placeholders(tmp_path: Path) -> None:
    """Every placeholder is replaced and the knowledge base is injected."""
    prompt_path, profile_path = write_project_files(tmp_path)

    result = build_system_prompt(
        prompt_path=prompt_path,
        profile_path=profile_path,
        settings=make_settings(),
        use_cache=False,
    )

    assert "{{" not in result
    assert "}}" not in result
    assert "Owner: Tisha" in result
    assert "Email: tisha@example.com" in result
    assert '"projects"' in result
    assert "Project Alpha" in result


def test_output_contains_owner_name(tmp_path: Path) -> None:
    """The configured owner name appears in the rendered prompt."""
    prompt_path, profile_path = write_project_files(tmp_path)

    result = build_system_prompt(
        prompt_path=prompt_path,
        profile_path=profile_path,
        settings=make_settings(owner_name="Asha Verma"),
        use_cache=False,
    )

    assert "Asha Verma" in result


def test_invalid_json_raises_profile_parse_error(tmp_path: Path) -> None:
    """Malformed knowledge JSON fails loudly with the file path."""
    prompt_path, profile_path = write_project_files(tmp_path)
    profile_path.write_text('{"identity": ', encoding="utf-8")

    with pytest.raises(ProfileParseError, match="Invalid JSON"):
        load_profile(profile_path)

    with pytest.raises(ProfileParseError, match="Invalid JSON"):
        build_system_prompt(
            prompt_path=prompt_path,
            profile_path=profile_path,
            settings=make_settings(),
            use_cache=False,
        )


def test_unknown_placeholder_raises(tmp_path: Path) -> None:
    """A placeholder without a value raises a clear error naming it."""
    prompt_path, profile_path = write_project_files(
        tmp_path, template="Owner: {{OWNER_NAME}}\nSecret: {{SECRET_VALUE}}\n"
    )

    with pytest.raises(PromptBuildError, match="SECRET_VALUE"):
        build_system_prompt(
            prompt_path=prompt_path,
            profile_path=profile_path,
            settings=make_settings(),
            use_cache=False,
        )


def test_placeholder_with_whitespace_is_filled(tmp_path: Path) -> None:
    """Spaced placeholders such as {{ OWNER_NAME }} are supported."""
    prompt_path, profile_path = write_project_files(
        tmp_path, template="Owner: {{ OWNER_NAME }} / {{CONTACT_EMAIL}}"
    )

    result = build_system_prompt(
        prompt_path=prompt_path,
        profile_path=profile_path,
        settings=make_settings(),
        use_cache=False,
    )

    assert result == "Owner: Tisha / tisha@example.com"


def test_placeholder_values_are_logged_as_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Leftover PLACEHOLDER: data is logged, not printed."""
    profile = {"identity": {"name": "PLACEHOLDER: Tisha"}}
    prompt_path, profile_path = write_project_files(tmp_path, profile=profile)

    with caplog.at_level(logging.WARNING, logger="app.prompt_builder"):
        build_system_prompt(
            prompt_path=prompt_path,
            profile_path=profile_path,
            settings=make_settings(),
            use_cache=False,
        )

    assert "PLACEHOLDER:" in caplog.text


def test_prompt_is_cached_and_reload_refreshes(tmp_path: Path) -> None:
    """Repeated builds reuse the cache; reload picks up new content."""
    prompt_path, profile_path = write_project_files(tmp_path)

    first = build_system_prompt(
        prompt_path=prompt_path,
        profile_path=profile_path,
        settings=make_settings(owner_name="First"),
    )
    cached = build_system_prompt(
        prompt_path=prompt_path,
        profile_path=profile_path,
        settings=make_settings(owner_name="Second"),
    )
    assert first == cached
    assert "First" in cached

    refreshed = reload_system_prompt(
        prompt_path=prompt_path,
        profile_path=profile_path,
        settings=make_settings(owner_name="Third"),
    )
    assert "Third" in refreshed
    assert "First" not in refreshed


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
    assert settings.allowed_origins_list == ["http://a.test", "http://b.test"]


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
