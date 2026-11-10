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
    groq_fallback_models: list[str] = Field(
        default_factory=list, description="Fallback Groq model ids, tried in order."
    )
    temperature: float = Field(default=0.2, ge=0, le=2, description="Sampling temperature.")
    max_output_tokens_chat: int = Field(default=600, gt=0, description="Chat token cap.")
    max_output_tokens_fit: int = Field(default=1400, gt=0, description="JD fit token cap.")
    max_message_chars: int = Field(default=1500, gt=0, description="Maximum user message length.")
    max_history_messages: int = Field(default=8, gt=0, description="Messages sent to the model.")
    max_history_chars: int = Field(default=6000, gt=0, description="History character budget.")
    max_jd_chars: int = Field(default=8000, gt=0, description="Maximum job description length.")
    max_upload_bytes: int = Field(default=2_000_000, gt=0, description="Maximum upload size.")
    rate_limit_per_minute: int = Field(default=10, gt=0, description="Chat requests per IP/minute.")
    rate_limit_fit_per_minute: int = Field(
        default=3, gt=0, description="JD fit requests per IP/minute."
    )
    rate_limit_per_day_per_ip: int = Field(
        default=60, gt=0, description="Requests per IP per day."
    )
    global_daily_request_cap: int = Field(
        default=400, gt=0, description="Daily requests across all visitors."
    )
    max_concurrent_llm_calls: int = Field(default=4, gt=0, description="Concurrent model calls.")
    allowed_origins: str = Field(
        default=DEFAULT_ALLOWED_ORIGINS,
        description="Comma-separated origins allowed to call the API.",
    )
    contact_email: str = Field(min_length=3, description="Public contact email shown to visitors.")
    owner_name: str = Field(default="Tisha", min_length=1, description="Name used in the prompt.")
    environment: Literal["development", "production"] = Field(
        default="development", description="Deployment stage."
    )
    trusted_proxy: bool = Field(default=False, description="Trust X-Forwarded-For headers.")
    maintenance_mode: bool = Field(default=False, description="Reject requests with 503.")
    knowledge_dir: Path = Field(
        default=PROJECT_ROOT / "backend" / "knowledge", description="Knowledge base root."
    )
    serve_frontend: bool = Field(default=True, description="Serve the static frontend.")
    public_url: str = Field(default="", description="Public origin for social preview tags.")

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

    @field_validator("groq_fallback_models", mode="before")
    @classmethod
    def _parse_fallback_models(cls, value: object) -> list[str]:
        """Parse an optional comma-separated list, deduplicating in order."""
        if value is None:
            return []
        if isinstance(value, str):
            candidates: list[object] = value.split(",")
        elif isinstance(value, (list, tuple)):
            candidates = list(value)
        else:
            raise ValueError("fallback models must be a comma-separated string or a list")

        models: list[str] = []
        for candidate in candidates:
            if not isinstance(candidate, str):
                raise ValueError("each fallback model must be text")
            model = candidate.strip()
            if model and model not in models:
                models.append(model)
        return models

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

    @field_validator("trusted_proxy", "maintenance_mode", "serve_frontend", mode="before")
    @classmethod
    def _parse_bool(cls, value: object) -> bool:
        """Accept only explicit boolean spellings, never arbitrary truthy text."""
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalised = value.strip().lower()
            if normalised in {"true", "1", "yes", "on"}:
                return True
            if normalised in {"false", "0", "no", "off"}:
                return False
        raise ValueError("must be a boolean (true/false, yes/no, on/off, 1/0)")

    @field_validator("knowledge_dir", mode="after")
    @classmethod
    def _resolve_knowledge_dir(cls, value: Path) -> Path:
        """Resolve relative knowledge paths against the project root."""
        resolved = value if value.is_absolute() else PROJECT_ROOT / value
        return resolved.resolve()

    @field_validator("public_url", mode="after")
    @classmethod
    def _validate_public_url(cls, value: str) -> str:
        """Allow an empty value or an http(s) origin, without a trailing slash."""
        cleaned = value.strip()
        if not cleaned:
            return ""
        if any(character.isspace() for character in cleaned):
            raise ValueError("must be a URL without spaces")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return cleaned.rstrip("/")

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
        "groq_fallback_models": list(resolved.groq_fallback_models),
        "temperature": resolved.temperature,
        "max_output_tokens_chat": resolved.max_output_tokens_chat,
        "max_output_tokens_fit": resolved.max_output_tokens_fit,
        "max_message_chars": resolved.max_message_chars,
        "max_history_messages": resolved.max_history_messages,
        "max_history_chars": resolved.max_history_chars,
        "max_jd_chars": resolved.max_jd_chars,
        "max_upload_bytes": resolved.max_upload_bytes,
        "rate_limit_per_minute": resolved.rate_limit_per_minute,
        "rate_limit_fit_per_minute": resolved.rate_limit_fit_per_minute,
        "rate_limit_per_day_per_ip": resolved.rate_limit_per_day_per_ip,
        "global_daily_request_cap": resolved.global_daily_request_cap,
        "max_concurrent_llm_calls": resolved.max_concurrent_llm_calls,
        "allowed_origins": resolved.allowed_origins_list,
        "trusted_proxy": resolved.trusted_proxy,
        "maintenance_mode": resolved.maintenance_mode,
        "knowledge_dir": str(resolved.knowledge_dir),
        "serve_frontend": resolved.serve_frontend,
        "public_url": resolved.public_url,
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
