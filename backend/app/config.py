"""Environment-backed configuration for the Portfolio AI Chatbot backend.

Only ``PROJECT_ROOT / ".env"`` is loaded. Environment variables override the
file, and fields are validated at startup so a bad deployment fails fast.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> app -> backend -> project root
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

DEFAULT_ALLOWED_ORIGINS = "http://localhost:5173,http://localhost:3000"


def _split_csv(value: str) -> list[str]:
    """Split a comma-separated string, trimming whitespace and dropping empties."""
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and the project .env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        enable_decoding=False,
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    groq_api_keys: list[SecretStr] = Field(
        validation_alias=AliasChoices("GROQ_API_KEYS", "GROQ_API_KEY"),
        repr=False,
        description="One or more Groq API keys, comma-separated or as a list.",
    )
    groq_model: str = Field(
        min_length=1,
        description="Primary Groq model id. Required and never hardcoded.",
    )
    allowed_origins: str = Field(
        default=DEFAULT_ALLOWED_ORIGINS,
        description="Comma-separated origins allowed to call the API.",
    )
    max_jd_chars: int = Field(default=8000, gt=0, description="Maximum job description length.")
    max_message_chars: int = Field(default=1500, gt=0, description="Maximum user message length.")
    contact_email: str = Field(min_length=3, description="Public contact email shown to visitors.")
    owner_name: str = Field(default="Tisha", min_length=1, description="Name used in the prompt.")
    environment: Literal["development", "production"] = Field(
        default="development",
        description="Deployment stage; production rejects placeholder knowledge.",
    )

    @field_validator("groq_api_keys", mode="before")
    @classmethod
    def _parse_api_keys(cls, value: object) -> list[SecretStr]:
        """Parse keys from a comma-separated string or a list.

        Error messages deliberately never include key text.
        """
        if isinstance(value, str):
            candidates = value.split(",")
        elif isinstance(value, (list, tuple)):
            candidates = []
            for item in value:
                if isinstance(item, SecretStr):
                    candidates.append(item.get_secret_value())
                elif isinstance(item, str):
                    candidates.append(item)
                else:
                    raise ValueError("each Groq API key must be text")
        elif value is None:
            raise ValueError("at least one Groq API key is required")
        else:
            raise ValueError("Groq API keys must be a comma-separated string or a list")

        keys = [candidate.strip() for candidate in candidates if candidate.strip()]
        if not keys:
            raise ValueError("at least one Groq API key is required")
        if any(any(character.isspace() for character in key) for key in keys):
            raise ValueError("Groq API keys must not contain spaces")
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate Groq API keys are not allowed")
        return [SecretStr(key) for key in keys]

    @field_validator("environment", mode="before")
    @classmethod
    def _normalise_environment(cls, value: object) -> str:
        """Accept case-insensitive ENVIRONMENT values."""
        return str(value).strip().lower()

    @field_validator("groq_model", "owner_name", mode="after")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        """Trim whitespace and reject blank values."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("contact_email")
    @classmethod
    def _require_valid_email(cls, value: str) -> str:
        """Require one @, a dotted domain, and no whitespace."""
        cleaned = value.strip()
        if any(character.isspace() for character in cleaned):
            raise ValueError("must be a valid email address without spaces")
        if cleaned.count("@") != 1:
            raise ValueError("must be a valid email address with exactly one @")
        local_part, domain = cleaned.split("@")
        if not local_part or not domain:
            raise ValueError("must be a valid email address")
        if (
            "." not in domain
            or domain.startswith(".")
            or domain.endswith(".")
            or ".." in domain
        ):
            raise ValueError("must be a valid email address with a dotted domain")
        if len(domain.rsplit(".", 1)[1]) < 2:
            raise ValueError("must be a valid email address with a valid domain")
        return cleaned

    @property
    def allowed_origins_list(self) -> list[str]:
        """Return ALLOWED_ORIGINS split into a clean list for CORS setup."""
        return _split_csv(self.allowed_origins)

    def __repr__(self) -> str:
        """Return a safe representation that never includes key text."""
        return f"Settings({settings_summary(self)!r})"

    def __str__(self) -> str:
        """Never reveal key text when settings are stringified."""
        return repr(self)


def settings_summary(settings: Settings | None = None) -> dict[str, object]:
    """Return a log-safe snapshot: key count and names, never key text."""
    resolved = settings if settings is not None else get_settings()
    return {
        "environment": resolved.environment,
        "groq_api_key_count": len(resolved.groq_api_keys),
        "groq_model": resolved.groq_model,
        "allowed_origins": resolved.allowed_origins_list,
        "max_jd_chars": resolved.max_jd_chars,
        "max_message_chars": resolved.max_message_chars,
        "owner_name": resolved.owner_name,
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load, validate, and cache settings once per process.

    Raises:
        RuntimeError: If required variables are missing or any value is invalid.
            Messages name the variables but never include secret values.
    """
    try:
        return Settings()
    except ValidationError as exc:
        missing = sorted(
            {str(error["loc"][0]).upper() for error in exc.errors() if error["type"] == "missing"}
        )
        if missing:
            raise RuntimeError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Set GROQ_API_KEYS (comma-separated) or the legacy GROQ_API_KEY, "
                "and copy .env.example to .env in the project root."
            ) from exc

        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise RuntimeError(f"Invalid configuration: {details}") from exc
