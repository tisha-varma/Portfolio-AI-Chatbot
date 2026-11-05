"""Build the chatbot system prompt from the Markdown template and knowledge base."""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

BACKEND_DIR: Path = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT_PATH: Path = BACKEND_DIR / "prompts" / "system_prompt.md"
DEFAULT_PROFILE_PATH: Path = BACKEND_DIR / "knowledge" / "profile.json"

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
PLACEHOLDER_MARKER = "PLACEHOLDER:"

REQUIRED_PROFILE_SECTIONS: tuple[str, ...] = (
    "identity",
    "education",
    "skills",
    "projects",
    "contact",
    "private_do_not_share",
    "known_gaps",
)

# (template path, profile path, both mtimes, owner, email, environment)
CacheKey = tuple[Path, Path, int, int, str, str, str]

_CACHE: dict[CacheKey, str] = {}
_CACHE_LOCK = threading.Lock()


class PromptBuildError(RuntimeError):
    """Raised when the system prompt cannot be built."""


class ProfileParseError(PromptBuildError):
    """Raised when knowledge/profile.json is not valid JSON."""


class ProfileValidationError(PromptBuildError):
    """Raised when knowledge/profile.json is missing required structure."""


class ProfileIdentity(BaseModel):
    """Minimal identity facts the prompt relies on."""

    model_config = ConfigDict(extra="allow")

    name: str
    college: str
    degree: str
    branch: str
    expected_graduation: str


class ProfileSkills(BaseModel):
    """Skill levels, from strongest to not experienced."""

    model_config = ConfigDict(extra="allow")

    strong: list[str] = Field(default_factory=list)
    working_knowledge: list[str] = Field(default_factory=list)
    familiar: list[str] = Field(default_factory=list)
    not_experienced: list[str] = Field(default_factory=list)


class ProfileProject(BaseModel):
    """A project entry; extra fields such as outcome and links are kept."""

    model_config = ConfigDict(extra="allow")

    name: str
    one_line_summary: str
    tech_stack: list[str] = Field(default_factory=list)
    status: str


class ProfileContact(BaseModel):
    """The only contact details the bot may share."""

    model_config = ConfigDict(extra="allow")

    public_contact_only: list[str]


class Profile(BaseModel):
    """Validated shape of the knowledge base."""

    model_config = ConfigDict(extra="allow")

    identity: ProfileIdentity
    education: dict[str, Any]
    skills: ProfileSkills
    projects: list[ProfileProject]
    experience: list[Any] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    certifications: list[Any] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    availability: dict[str, Any] = Field(default_factory=dict)
    contact: ProfileContact
    private_do_not_share: list[str]
    known_gaps: list[str]

    @property
    def has_any_skill(self) -> bool:
        """Return True when at least one skill is recorded at any level."""
        return any(
            (self.skills.strong, self.skills.working_knowledge,
             self.skills.familiar, self.skills.not_experienced)
        )


def load_template(path: str | Path = DEFAULT_PROMPT_PATH) -> str:
    """Read the system prompt template from disk."""
    template_path = Path(path)
    if not template_path.is_file():
        raise PromptBuildError(f"System prompt template not found: {template_path}")
    return template_path.read_text(encoding="utf-8")


def load_profile(path: str | Path = DEFAULT_PROFILE_PATH) -> dict[str, Any]:
    """Read and parse the knowledge base JSON.

    Raises:
        ProfileParseError: If the file is missing, unreadable, or invalid JSON.
    """
    profile_path = Path(path)
    if not profile_path.is_file():
        raise ProfileParseError(f"Knowledge base not found: {profile_path}")

    raw = profile_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProfileParseError(
            f"Invalid JSON in knowledge base {profile_path} "
            f"(line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise ProfileParseError(f"Knowledge base {profile_path} must contain a JSON object.")
    return data


def validate_profile(data: dict[str, Any], *, environment: str = "development") -> Profile:
    """Validate required sections, then apply production-only checks.

    Raises:
        ProfileValidationError: If a section is missing, malformed, or empty
            where production requires content.
    """
    missing = [section for section in REQUIRED_PROFILE_SECTIONS if section not in data]
    if missing:
        raise ProfileValidationError(
            f"profile.json is missing required section(s): {', '.join(missing)}"
        )

    try:
        profile = Profile.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ProfileValidationError(f"profile.json is invalid: {details}") from exc

    if environment == "production":
        if not profile.projects:
            raise ProfileValidationError(
                "profile.json has no projects, which production requires."
            )
        if not profile.has_any_skill:
            raise ProfileValidationError(
                "profile.json has no skills recorded, which production requires."
            )
    return profile


def find_placeholders(text: str) -> list[str]:
    """Return the distinct placeholder names used in a template, sorted."""
    return sorted({match.group(1) for match in PLACEHOLDER_PATTERN.finditer(text)})


def render_template(template: str, values: Mapping[str, str]) -> str:
    """Replace {{PLACEHOLDER}} tokens, failing on unknown placeholders.

    Substitution is single-pass, so braces inside the injected knowledge base
    are left untouched and can never be re-substituted.
    """
    missing = [name for name in find_placeholders(template) if name not in values]
    if missing:
        formatted = ", ".join(f"{{{{{name}}}}}" for name in missing)
        raise PromptBuildError(f"No value provided for placeholder(s): {formatted}")

    return PLACEHOLDER_PATTERN.sub(lambda match: values[match.group(1)], template)


def _file_stamp(path: Path) -> int:
    """Return a file's modification time in nanoseconds, or -1 if missing."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _cache_key(
    prompt_path: Path,
    profile_path: Path,
    settings: Settings,
) -> CacheKey:
    """Build a cache key from paths, file mtimes, and prompt-affecting settings."""
    return (
        prompt_path.resolve(),
        profile_path.resolve(),
        _file_stamp(prompt_path),
        _file_stamp(profile_path),
        settings.owner_name,
        settings.contact_email,
        settings.environment,
    )


def _render_from_files(
    prompt_path: Path,
    profile_path: Path,
    settings: Settings,
) -> str:
    """Load both files, validate the profile, and render the final prompt."""
    template = load_template(prompt_path)
    profile_data = load_profile(profile_path)
    validate_profile(profile_data, environment=settings.environment)

    knowledge_base = json.dumps(profile_data, ensure_ascii=False, separators=(",", ":"))

    if PLACEHOLDER_MARKER in knowledge_base:
        if settings.environment == "production":
            raise ProfileValidationError(
                "Knowledge base still contains PLACEHOLDER: values but ENVIRONMENT=production. "
                "Replace every placeholder in knowledge/profile.json before deploying."
            )
        logger.warning(
            "Knowledge base %s still contains %r values; replace them with real data "
            "before deploying.",
            profile_path,
            PLACEHOLDER_MARKER,
        )

    values = {
        "OWNER_NAME": settings.owner_name,
        "CONTACT_EMAIL": settings.contact_email,
        "KNOWLEDGE_BASE": knowledge_base,
    }
    return render_template(template, values)


def build_system_prompt(
    *,
    prompt_path: str | Path | None = None,
    profile_path: str | Path | None = None,
    settings: Settings | None = None,
    use_cache: bool = True,
) -> str:
    """Build the system prompt, reusing the cache when nothing has changed.

    The cache key covers both file mtimes and the settings that appear in the
    prompt, so edits and config changes are picked up without a restart.

    Args:
        prompt_path: Override for the Markdown template location.
        profile_path: Override for the knowledge base location.
        settings: Pre-built settings; falls back to the cached get_settings().
        use_cache: Set False to force a fresh read (tests, tooling).

    Raises:
        PromptBuildError: On missing files, unknown placeholders, invalid JSON,
            or an invalid profile.
    """
    template_path = Path(prompt_path) if prompt_path is not None else DEFAULT_PROMPT_PATH
    knowledge_path = Path(profile_path) if profile_path is not None else DEFAULT_PROFILE_PATH
    resolved_settings = settings if settings is not None else get_settings()
    key = _cache_key(template_path, knowledge_path, resolved_settings)

    if use_cache:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        if cached is not None:
            return cached

    prompt = _render_from_files(template_path, knowledge_path, resolved_settings)
    logger.info(
        "Built system prompt (%d chars, roughly %d tokens).",
        len(prompt),
        len(prompt) // 4,
    )

    if use_cache:
        with _CACHE_LOCK:
            _CACHE[key] = prompt
    return prompt


def clear_prompt_cache() -> None:
    """Drop every cached system prompt."""
    with _CACHE_LOCK:
        _CACHE.clear()
    logger.debug("System prompt cache cleared.")


def reload_system_prompt(
    *,
    prompt_path: str | Path | None = None,
    profile_path: str | Path | None = None,
    settings: Settings | None = None,
) -> str:
    """Rebuild the prompt from disk, bypassing the cache (development helper)."""
    clear_prompt_cache()
    return build_system_prompt(
        prompt_path=prompt_path,
        profile_path=profile_path,
        settings=settings,
        use_cache=True,
    )
