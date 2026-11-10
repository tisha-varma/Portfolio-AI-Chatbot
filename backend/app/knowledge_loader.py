"""Load and validate the two-tier knowledge base.

Tier 1 is ``knowledge/core.json``; tier 2 is the Markdown deep dives and their
index. This module is the only place that reads those files from disk, so the
schema checks and the production placeholder guard stay consistent across the
loader, router, and prompt builder.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

# app/knowledge_loader.py -> app -> backend -> project root
BACKEND_DIR: Path = Path(__file__).resolve().parents[1]
PROJECT_ROOT: Path = BACKEND_DIR.parent
DEFAULT_KNOWLEDGE_DIR: Path = BACKEND_DIR / "knowledge"
DEFAULT_CORE_PATH: Path = DEFAULT_KNOWLEDGE_DIR / "core.json"
DEFAULT_SECTIONS_DIR: Path = DEFAULT_KNOWLEDGE_DIR / "sections"
DEFAULT_INDEX_PATH: Path = DEFAULT_SECTIONS_DIR / "index.json"

PLACEHOLDER_MARKER = "PLACEHOLDER:"

REQUIRED_CORE_SECTIONS: tuple[str, ...] = (
    "identity",
    "summary",
    "education",
    "experience",
    "projects",
    "skills",
    "known_gaps",
    "private_do_not_share",
    "data_freshness",
)


def knowledge_dir() -> Path:
    """Return the knowledge root, honouring the ``KNOWLEDGE_DIR`` env var."""
    override = os.environ.get("KNOWLEDGE_DIR", "").strip()
    if not override:
        return DEFAULT_KNOWLEDGE_DIR
    path = Path(override).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def default_core_path() -> Path:
    """Return the default tier-1 knowledge file."""
    return knowledge_dir() / "core.json"


def default_sections_dir() -> Path:
    """Return the default deep-dive directory."""
    return knowledge_dir() / "sections"


def default_index_path() -> Path:
    """Return the default deep-dive index file."""
    return default_sections_dir() / "index.json"


class PromptBuildError(RuntimeError):
    """Base class for knowledge loading and prompt assembly failures."""


class ProfileParseError(PromptBuildError):
    """Raised when a knowledge file is missing, unreadable, or invalid JSON."""


class ProfileValidationError(ProfileParseError):
    """Raised when core.json is missing a required section or is malformed."""


class SectionIndexError(PromptBuildError):
    """Raised when sections/index.json is missing or malformed."""


class SectionNotFoundError(PromptBuildError):
    """Raised when a requested deep-dive section has no index entry or file."""


class CoreIdentity(BaseModel):
    """Identity facts required by the prompt."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    public_contact: dict[str, str] = Field(default_factory=dict)


class CoreExperience(BaseModel):
    """One internship, open-source contribution, or founder role."""

    model_config = ConfigDict(extra="allow")

    role: str = Field(min_length=1)
    organisation: str = Field(min_length=1)
    dates: str = Field(min_length=1)
    details: list[str] = Field(default_factory=list)


class CoreProject(BaseModel):
    """One project summary; the full story lives in its deep dive."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    one_line_summary: str = Field(min_length=1)
    my_role: str = Field(min_length=1)
    tech_stack: list[str] = Field(default_factory=list)
    status: str = Field(min_length=1)
    links: dict[str, str] = Field(default_factory=dict)
    key_facts: list[str] = Field(default_factory=list)
    deep_dive_id: str = Field(min_length=1)


class CoreSkills(BaseModel):
    """Skills split into source-stated levels and unrated listings."""

    model_config = ConfigDict(extra="allow")

    proficiency_stated: dict[str, str] = Field(default_factory=dict)
    skills_listed_unrated: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_any(self) -> bool:
        """Return True when at least one skill is recorded."""
        return bool(self.proficiency_stated or self.skills_listed_unrated)


class CoreKnowledge(BaseModel):
    """Validated shape of the tier-1 knowledge base."""

    model_config = ConfigDict(extra="allow")

    identity: CoreIdentity
    summary: str = Field(min_length=1)
    career_goals: list[str] = Field(default_factory=list)
    education: dict[str, Any]
    experience: list[CoreExperience] = Field(default_factory=list)
    projects: list[CoreProject] = Field(default_factory=list)
    skills: CoreSkills
    achievements: list[str] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)
    private_do_not_share: list[str] = Field(default_factory=list)
    data_freshness: list[dict[str, Any]] = Field(default_factory=list)


def load_core_data(path: str | Path | None = None) -> dict[str, Any]:
    """Read and parse core.json without schema validation.

    Args:
        path: Override for ``KNOWLEDGE_DIR/core.json``.

    Raises:
        ProfileParseError: If the file is missing, unreadable, or invalid JSON.
    """
    core_path = Path(path) if path is not None else default_core_path()
    if not core_path.is_file():
        raise ProfileParseError(f"Core knowledge base not found: {core_path}")

    try:
        data = json.loads(core_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileParseError(
            f"Invalid JSON in core knowledge base {core_path} "
            f"(line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise ProfileParseError(f"Core knowledge base {core_path} must contain a JSON object.")
    return data


def _contains_placeholder(data: object) -> bool:
    """Return True when serialised knowledge still contains a fake marker."""
    return PLACEHOLDER_MARKER in json.dumps(data, ensure_ascii=False)


def validate_core(
    data: dict[str, Any], *, environment: str = "development"
) -> CoreKnowledge:
    """Validate required sections, then apply environment-only checks.

    Raises:
        ProfileValidationError: If a required section is missing, a field is
            malformed, production has no projects or skills, or production
            knowledge still contains a ``PLACEHOLDER:`` value.
    """
    missing = [section for section in REQUIRED_CORE_SECTIONS if section not in data]
    if missing:
        raise ProfileValidationError(
            f"core.json is missing required section(s): {', '.join(missing)}"
        )

    try:
        core = CoreKnowledge.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ProfileValidationError(f"core.json is invalid: {details}") from exc

    if _contains_placeholder(data):
        if environment == "production":
            raise ProfileValidationError(
                "Core knowledge base still contains PLACEHOLDER: values but "
                "ENVIRONMENT=production. Replace every placeholder in "
                "knowledge/core.json before deploying."
            )
        logger.warning(
            "Core knowledge base still contains %r values; replace them with real "
            "data before deploying.",
            PLACEHOLDER_MARKER,
        )

    if environment == "production":
        if not core.projects:
            raise ProfileValidationError(
                "core.json has no projects, which production requires."
            )
        if not core.skills.has_any:
            raise ProfileValidationError(
                "core.json has no skills recorded, which production requires."
            )
    return core


def load_core(
    path: str | Path | None = None, *, environment: str = "development"
) -> CoreKnowledge:
    """Read, validate, and return core.json as a typed model."""
    return validate_core(load_core_data(path), environment=environment)


def load_sections_index(
    path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Read and validate the deep-dive index.

    Args:
        path: Override for ``KNOWLEDGE_DIR/sections/index.json``.

    Raises:
        SectionIndexError: If the file is missing, invalid JSON, or an entry is
            missing its title, description, or keywords.
    """
    index_path = Path(path) if path is not None else default_index_path()
    if not index_path.is_file():
        raise SectionIndexError(f"Section index not found: {index_path}")

    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SectionIndexError(
            f"Invalid JSON in section index {index_path} "
            f"(line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc

    if not isinstance(data, dict) or not data:
        raise SectionIndexError(f"Section index {index_path} must be a non-empty JSON object.")

    for section_id, entry in data.items():
        if not isinstance(entry, dict):
            raise SectionIndexError(f"Section index entry {section_id!r} must be an object.")
        if not isinstance(entry.get("title"), str) or not entry["title"].strip():
            raise SectionIndexError(f"Section index entry {section_id!r} has no title.")
        if not isinstance(entry.get("description"), str) or not entry["description"].strip():
            raise SectionIndexError(f"Section index entry {section_id!r} has no description.")
        keywords = entry.get("keywords")
        if not isinstance(keywords, list) or not all(
            isinstance(keyword, str) and keyword.strip() for keyword in keywords
        ):
            raise SectionIndexError(
                f"Section index entry {section_id!r} must have a list of non-empty keywords."
            )

    return data


def enforce_no_placeholders(
    paths: Iterable[str | Path], *, environment: str = "development"
) -> list[Path]:
    """Reject shipped placeholder values in production; warn in development.

    Returns the offending paths so callers can log or report them.

    Raises:
        ProfileValidationError: In production, when any path contains
            ``PLACEHOLDER:``.
    """
    offenders: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file() and PLACEHOLDER_MARKER in path.read_text(encoding="utf-8"):
            offenders.append(path)

    if offenders:
        names = ", ".join(str(path) for path in offenders)
        if environment == "production":
            raise ProfileValidationError(
                f"PLACEHOLDER: values remain in {names}, but ENVIRONMENT=production. "
                "Replace them before deploying."
            )
        logger.warning(
            "PLACEHOLDER: values remain in %s; replace them before deploying.", names
        )
    return offenders
