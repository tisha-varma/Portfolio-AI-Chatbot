"""Focused tests for configuration parsing, validation, and secrecy."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app import config
from app.config import PROJECT_ROOT, Settings, get_settings, settings_summary


def make_settings(**overrides: object) -> Settings:
    """Build validated settings without reading the project .env file."""
    values: dict[str, object] = {
        "groq_api_keys": ["test-groq-key"],
        "groq_model": "test-groq-model",
        "contact_email": "tisha@example.com",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Prevent cached settings from leaking between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_documented_defaults() -> None:
    """Every budget and flag matches the documented default."""
    settings = make_settings()

    assert settings.groq_fallback_models == []
    assert settings.temperature == 0.2
    assert settings.max_output_tokens_chat == 600
    assert settings.max_output_tokens_fit == 1400
    assert settings.max_message_chars == 1500
    assert settings.max_history_messages == 8
    assert settings.max_history_chars == 6000
    assert settings.max_jd_chars == 8000
    assert settings.max_upload_bytes == 2_000_000
    assert settings.rate_limit_per_minute == 10
    assert settings.rate_limit_fit_per_minute == 3
    assert settings.rate_limit_per_day_per_ip == 60
    assert settings.global_daily_request_cap == 400
    assert settings.max_concurrent_llm_calls == 4
    assert settings.environment == "development"
    assert settings.trusted_proxy is False
    assert settings.maintenance_mode is False
    assert settings.serve_frontend is True
    assert settings.knowledge_dir == (PROJECT_ROOT / "backend" / "knowledge").resolve()
    assert settings.public_url == ""


def test_comma_separated_keys_are_parsed() -> None:
    """Whitespace is stripped, empty entries dropped, and SecretStr used."""
    settings = make_settings(groq_api_keys=" first, second , , third ")

    assert [key.get_secret_value() for key in settings.groq_api_keys] == [
        "first",
        "second",
        "third",
    ]
    assert all(isinstance(key, SecretStr) for key in settings.groq_api_keys)


def test_legacy_single_key_env_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The legacy GROQ_API_KEY also accepts comma-separated values."""
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", " legacy-one , legacy-two , legacy-three ")

    settings = Settings(
        _env_file=None,
        groq_model="test-groq-model",
        contact_email="tisha@example.com",
    )

    assert [key.get_secret_value() for key in settings.groq_api_keys] == [
        "legacy-one",
        "legacy-two",
        "legacy-three",
    ]


def test_duplicate_keys_are_rejected() -> None:
    """Two identical keys fail validation without echoing the value."""
    with pytest.raises(ValidationError, match="duplicate"):
        make_settings(groq_api_keys=["same-key", "same-key"])


def test_empty_key_list_is_rejected() -> None:
    """A list of blank entries fails fast."""
    with pytest.raises(ValidationError, match="at least one"):
        make_settings(groq_api_keys=" , , ")


def test_key_text_never_appears_in_repr_or_str() -> None:
    """Neither repr nor str may reveal a key, and SecretStr stays masked."""
    settings = make_settings(groq_api_keys=["super-secret-key-value"])

    assert "super-secret-key-value" not in repr(settings)
    assert "super-secret-key-value" not in str(settings)
    assert str(settings.groq_api_keys[0]) == "**********"


def test_settings_summary_is_log_safe() -> None:
    """The summary reports a key count and limits, never key text."""
    settings = make_settings(
        groq_api_keys=["first-secret", "second-secret"],
        groq_fallback_models="fallback-a, fallback-b",
    )

    summary = settings_summary(settings)

    assert summary["groq_api_key_count"] == 2
    assert summary["groq_model"] == "test-groq-model"
    assert summary["groq_fallback_models"] == ["fallback-a", "fallback-b"]
    assert summary["max_message_chars"] == 1500
    assert summary["rate_limit_per_day_per_ip"] == 60
    assert "first-secret" not in repr(summary)
    assert "second-secret" not in repr(summary)


def test_get_settings_missing_key_fails_readably(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing key produces one clear RuntimeError naming GROQ_API_KEYS."""
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_MODEL", "test-groq-model")
    monkeypatch.setenv("CONTACT_EMAIL", "tisha@example.com")
    monkeypatch.setitem(Settings.model_config, "env_file", ())

    with pytest.raises(RuntimeError, match="GROQ_API_KEYS"):
        get_settings()


def test_get_settings_error_never_includes_key_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation errors name settings but never echo secret values."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEYS", "very-secret-key-one, very-secret-key-two")
    monkeypatch.setenv("GROQ_MODEL", "test-groq-model")
    monkeypatch.setenv("CONTACT_EMAIL", "tisha@example.com")
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setitem(Settings.model_config, "env_file", ())

    with pytest.raises(RuntimeError) as excinfo:
        get_settings()

    message = str(excinfo.value)
    assert "very-secret-key-one" not in message
    assert "very-secret-key-two" not in message
    assert "environment" in message


def test_get_settings_loads_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Environment variables are validated into a Settings instance."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEYS", "one, two")
    monkeypatch.setenv("GROQ_MODEL", "model-from-env")
    monkeypatch.setenv("CONTACT_EMAIL", "tisha@example.com")
    monkeypatch.setitem(Settings.model_config, "env_file", ())

    settings = get_settings()

    assert [key.get_secret_value() for key in settings.groq_api_keys] == ["one", "two"]
    assert settings.groq_model == "model-from-env"


def test_get_settings_returns_a_cached_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_settings caches until cache_clear is called."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEYS", "one")
    monkeypatch.setenv("GROQ_MODEL", "test-groq-model")
    monkeypatch.setenv("CONTACT_EMAIL", "tisha@example.com")
    monkeypatch.setitem(Settings.model_config, "env_file", ())

    first = get_settings()
    second = get_settings()
    assert first is second

    get_settings.cache_clear()
    assert get_settings() is not first


def test_invalid_environment_is_rejected() -> None:
    """Only development and production are accepted."""
    with pytest.raises(ValidationError, match="development"):
        make_settings(environment="staging")


def test_environment_is_case_insensitive() -> None:
    """ENVIRONMENT accepts mixed case."""
    assert make_settings(environment="Production").environment == "production"


def test_email_requires_one_at_dotted_domain_and_no_spaces() -> None:
    """The email check is stricter than a bare @ check."""
    invalid = [
        "a@@b.com",
        "a@b",
        "a b@c.com",
        "a@b..com",
        "@b.com",
        "a@.com",
        "a@b.c",
    ]
    for value in invalid:
        with pytest.raises(ValidationError, match="email address"):
            make_settings(contact_email=value)

    assert make_settings(contact_email="tisha@example.com").contact_email == (
        "tisha@example.com"
    )


def test_positive_budgets_reject_zero_and_negative() -> None:
    """Every numeric budget must be a positive integer."""
    fields = [
        "max_output_tokens_chat",
        "max_output_tokens_fit",
        "max_message_chars",
        "max_history_messages",
        "max_history_chars",
        "max_jd_chars",
        "max_upload_bytes",
        "rate_limit_per_minute",
        "rate_limit_fit_per_minute",
        "rate_limit_per_day_per_ip",
        "global_daily_request_cap",
        "max_concurrent_llm_calls",
    ]
    for field in fields:
        for bad in (0, -1):
            with pytest.raises(ValidationError):
                make_settings(**{field: bad})


def test_temperature_is_bounded() -> None:
    """Temperature stays within the 0-2 range."""
    with pytest.raises(ValidationError):
        make_settings(temperature=-0.1)
    with pytest.raises(ValidationError):
        make_settings(temperature=2.1)

    assert make_settings(temperature=0).temperature == 0
    assert make_settings(temperature=2).temperature == 2


def test_booleans_accept_only_explicit_values() -> None:
    """Booleans parse documented spellings and reject arbitrary text."""
    cases = [
        ("true", True),
        ("FALSE", False),
        (" yes ", True),
        ("off", False),
        ("1", True),
        ("0", False),
        ("on", True),
        ("no", False),
    ]
    for raw, expected in cases:
        assert make_settings(trusted_proxy=raw).trusted_proxy is expected

    with pytest.raises(ValidationError, match="boolean"):
        make_settings(maintenance_mode="maybe")
    with pytest.raises(ValidationError, match="boolean"):
        make_settings(serve_frontend=2)


def test_fallback_models_parse_and_dedupe() -> None:
    """Fallback models are trimmed, empty entries dropped, order preserved."""
    settings = make_settings(groq_fallback_models=" a , b , a ,, ")

    assert settings.groq_fallback_models == ["a", "b"]
    assert make_settings(groq_fallback_models="").groq_fallback_models == []


def test_public_url_validation() -> None:
    """PUBLIC_URL is empty or an http(s) origin without a trailing slash."""
    assert make_settings(public_url=" https://tisha.example/ ").public_url == (
        "https://tisha.example"
    )
    assert make_settings(public_url="").public_url == ""

    with pytest.raises(ValidationError, match="http"):
        make_settings(public_url="ftp://tisha.example")
    with pytest.raises(ValidationError, match="space"):
        make_settings(public_url="https://tisha example.com")


def test_knowledge_dir_resolves_relative_paths(tmp_path: Path) -> None:
    """Relative knowledge paths resolve against the project root."""
    relative = make_settings(knowledge_dir="backend/knowledge").knowledge_dir
    assert relative == (PROJECT_ROOT / "backend" / "knowledge").resolve()

    absolute = make_settings(knowledge_dir=tmp_path).knowledge_dir
    assert absolute == tmp_path.resolve()


def test_only_project_dotenv_is_configured() -> None:
    """The project .env is the sole dotenv file, with no old paths."""
    assert Settings.model_config["env_file"] == PROJECT_ROOT / ".env"

    source = Path(config.__file__).read_text(encoding="utf-8")
    assert "notepad" not in source.lower()
    assert "SHARED_ENV_FILE" not in source
