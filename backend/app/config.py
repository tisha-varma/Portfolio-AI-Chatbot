"""Environment-backed configuration for the Portfolio AI Chatbot backend."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> app -> backend -> project root
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and the project .env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    groq_api_key: SecretStr = Field(description="Groq API key. Required.")
    groq_model: str = Field(
        min_length=1,
        description="Groq model id. Required and deliberately never hardcoded.",
    )
    allowed_origins: str = Field(
        default="http://localhost:5173,http://localhost:3000",
        description="Comma-separated origins allowed to call the API.",
    )
    max_jd_chars: int = Field(default=8000, gt=0, description="Maximum job description length.")
    max_message_chars: int = Field(default=1500, gt=0, description="Maximum user message length.")
    contact_email: str = Field(min_length=3, description="Public contact email shown to visitors.")
    owner_name: str = Field(default="Tisha", min_length=1, description="Name used in the prompt.")

    @field_validator("groq_model", "contact_email", "owner_name", mode="after")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        """Trim whitespace and reject blank values."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("groq_api_key", mode="after")
    @classmethod
    def _require_non_empty_key(cls, value: SecretStr) -> SecretStr:
        """Reject a blank API key without ever exposing its value."""
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("contact_email")
    @classmethod
    def _require_plausible_email(cls, value: str) -> str:
        """Reject values that clearly are not an email address."""
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("must look like an email address, e.g. you@example.com")
        return value

    @property
    def allowed_origins_list(self) -> list[str]:
        """Return ALLOWED_ORIGINS split into a clean list for CORS setup."""
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load, validate, and cache settings once per process.

    Raises:
        RuntimeError: If required variables are missing or any value is invalid.
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
                "Copy .env.example to .env in the project root and fill in real values."
            ) from exc

        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise RuntimeError(f"Invalid configuration: {details}") from exc
