"""Build the chatbot system prompt from the template and two-tier knowledge base.

Tier 1 is ``knowledge/core.json`` and is always injected into the prompt. Tier 2
is a set of Markdown deep dives under ``knowledge/sections/`` that the router
selects on demand.
"""

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
KNOWLEDGE_DIR: Path = BACKEND_DIR / "knowledge"
DEFAULT_PROMPT_PATH: Path = BACKEND_DIR / "prompts" / "system_prompt.md"
DEFAULT_CORE_PATH: Path = KNOWLEDGE_DIR / "core.json"
DEFAULT_SECTIONS_DIR: Path = KNOWLEDGE_DIR / "sections"
DEFAULT_INDEX_PATH: Path = DEFAULT_SECTIONS_DIR / "index.json"

DEEP_DIVE_HEADING = "DEEP DIVE DETAILS (also part of the knowledge base)"

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
PLACEHOLDER_MARKER = "PLACEHOLDER:"

REQUIRED_CORE_SECTIONS: tuple[str, ...] = (
    "identity",
    "summary",
    "career_goals",
    "education",
    "experience",
    "projects",
    "skills",
    "achievements",
    "known_gaps",
    "private_do_not_share",
    "data_freshness",
)

# Any JSON-serialisable values that affect the rendered prompt.
CacheKey = tuple[object, ...]

_CACHE: dict[CacheKey, str] = {}
_CACHE_LOCK = threading.Lock()


class PromptBuildError(RuntimeError):
    """Raised when the system prompt cannot be built."""


class ProfileParseError(PromptBuildError):
    """Raised when knowledge/core.json is missing or is not valid JSON."""


class ProfileValidationError(PromptBuildError):
    """Raised when knowledge/core.json is missing required structure."""


class SectionIndexError(PromptBuildError):
    """Raised when knowledge/sections/index.json is missing or malformed."""


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


def load_template(path: str | Path = DEFAULT_PROMPT_PATH) -> str:
    """Read the system prompt template from disk."""
    template_path = Path(path)
    if not template_path.is_file():
        raise PromptBuildError(f"System prompt template not found: {template_path}")
    return template_path.read_text(encoding="utf-8")


def load_core(path: str | Path = DEFAULT_CORE_PATH) -> dict[str, Any]:
    """Read and parse the tier-1 knowledge base.

    Raises:
        ProfileParseError: If the file is missing, unreadable, or invalid JSON.
    """
    core_path = Path(path)
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


def validate_core(data: dict[str, Any], *, environment: str = "development") -> CoreKnowledge:
    """Validate required sections, then apply production-only checks.

    Raises:
        ProfileValidationError: If a section is missing, malformed, or empty
            where production requires content.
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


def load_sections_index(path: str | Path = DEFAULT_INDEX_PATH) -> dict[str, dict[str, Any]]:
    """Read and validate the deep-dive index.

    Raises:
        SectionIndexError: If the file is missing, invalid JSON, or an entry is
            missing its title, description, or keywords.
    """
    index_path = Path(path)
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


def resolve_section_ids(
    section_ids: list[str] | None,
    index: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Deduplicate and sort section ids, rejecting unknown ones.

    Raises:
        SectionNotFoundError: If any id has no entry in the index.
    """
    if not section_ids:
        return []

    resolved = sorted(set(section_ids))
    missing = [section_id for section_id in resolved if section_id not in index]
    if missing:
        known = ", ".join(sorted(index))
        raise SectionNotFoundError(
            f"Unknown section id(s): {', '.join(missing)}. Known ids: {known}."
        )
    return resolved


def load_section(section_id: str, sections_dir: str | Path = DEFAULT_SECTIONS_DIR) -> str:
    """Read one deep-dive Markdown file.

    Raises:
        SectionNotFoundError: If the file does not exist.
    """
    section_path = Path(sections_dir) / f"{section_id}.md"
    if not section_path.is_file():
        raise SectionNotFoundError(f"Deep-dive file not found: {section_path}")
    return section_path.read_text(encoding="utf-8").strip()


def render_sections(
    section_ids: list[str],
    index: Mapping[str, Mapping[str, Any]],
    sections_dir: str | Path = DEFAULT_SECTIONS_DIR,
) -> str:
    """Render the selected deep dives under their own heading."""
    if not section_ids:
        return ""

    blocks = [
        f"## {index[section_id]['title']}\n\n{load_section(section_id, sections_dir)}"
        for section_id in section_ids
    ]
    return f"\n\n{DEEP_DIVE_HEADING}\n\n" + "\n\n".join(blocks) + "\n"


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
    core_path: Path,
    index_path: Path,
    sections_dir: Path,
    section_ids: list[str],
    settings: Settings,
) -> CacheKey:
    """Build a cache key from paths, mtimes, section ids, and settings."""
    section_stamps = tuple(
        (section_id, _file_stamp(sections_dir / f"{section_id}.md"))
        for section_id in section_ids
    )
    return (
        prompt_path.resolve(),
        core_path.resolve(),
        index_path.resolve(),
        sections_dir.resolve(),
        _file_stamp(prompt_path),
        _file_stamp(core_path),
        _file_stamp(index_path),
        section_stamps,
        tuple(section_ids),
        settings.owner_name,
        settings.contact_email,
        settings.environment,
    )


def _render_from_files(
    prompt_path: Path,
    core_path: Path,
    sections_dir: Path,
    index_path: Path,
    section_ids: list[str],
    settings: Settings,
) -> str:
    """Load every input, validate it, and render the final prompt."""
    template = load_template(prompt_path)
    core_data = load_core(core_path)
    validate_core(core_data, environment=settings.environment)
    index = load_sections_index(index_path)
    resolved_ids = resolve_section_ids(section_ids, index)

    knowledge_base = json.dumps(core_data, ensure_ascii=False, separators=(",", ":"))

    if PLACEHOLDER_MARKER in knowledge_base:
        if settings.environment == "production":
            raise ProfileValidationError(
                "Core knowledge base still contains PLACEHOLDER: values but "
                "ENVIRONMENT=production. Replace every placeholder in "
                "knowledge/core.json before deploying."
            )
        logger.warning(
            "Core knowledge base %s still contains %r values; replace them with real "
            "data before deploying.",
            core_path,
            PLACEHOLDER_MARKER,
        )

    values = {
        "OWNER_NAME": settings.owner_name,
        "CONTACT_EMAIL": settings.contact_email,
        "KNOWLEDGE_BASE": knowledge_base,
    }
    deep_dive = render_sections(resolved_ids, index, sections_dir)
    prompt = render_template(template, values) + deep_dive

    core_tokens = len(knowledge_base) // 4
    total_tokens = (len(knowledge_base) + len(deep_dive)) // 4
    logger.info(
        "Built system prompt (%d chars): core ~%d tokens; core+sections ~%d tokens; "
        "sections: %s.",
        len(prompt),
        core_tokens,
        total_tokens,
        ", ".join(resolved_ids) or "none",
    )
    return prompt


def build_system_prompt(
    section_ids: list[str] | None = None,
    *,
    prompt_path: str | Path | None = None,
    core_path: str | Path | None = None,
    sections_dir: str | Path | None = None,
    index_path: str | Path | None = None,
    settings: Settings | None = None,
    use_cache: bool = True,
) -> str:
    """Build the system prompt, reusing the cache when nothing has changed.

    Args:
        section_ids: Deep dives to append; ``None`` or ``[]`` means core only.
        prompt_path: Override for the Markdown template location.
        core_path: Override for the tier-1 knowledge base location.
        sections_dir: Override for the deep-dive directory.
        index_path: Override for the section index location.
        settings: Pre-built settings; falls back to the cached get_settings().
        use_cache: Set False to force a fresh read (tests, tooling).

    Raises:
        PromptBuildError: On missing files, unknown placeholders, invalid JSON,
            invalid knowledge, or an unknown section id.
    """
    template_path = Path(prompt_path) if prompt_path is not None else DEFAULT_PROMPT_PATH
    knowledge_path = Path(core_path) if core_path is not None else DEFAULT_CORE_PATH
    deep_dives_dir = Path(sections_dir) if sections_dir is not None else DEFAULT_SECTIONS_DIR
    section_index_path = Path(index_path) if index_path is not None else DEFAULT_INDEX_PATH
    resolved_settings = settings if settings is not None else get_settings()

    index = load_sections_index(section_index_path)
    resolved_ids = resolve_section_ids(section_ids, index)
    key = _cache_key(
        template_path,
        knowledge_path,
        section_index_path,
        deep_dives_dir,
        resolved_ids,
        resolved_settings,
    )

    if use_cache:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        if cached is not None:
            return cached

    prompt = _render_from_files(
        template_path,
        knowledge_path,
        deep_dives_dir,
        section_index_path,
        resolved_ids,
        resolved_settings,
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
    section_ids: list[str] | None = None,
    *,
    prompt_path: str | Path | None = None,
    core_path: str | Path | None = None,
    sections_dir: str | Path | None = None,
    index_path: str | Path | None = None,
    settings: Settings | None = None,
) -> str:
    """Rebuild the prompt from disk, bypassing the cache (development helper)."""
    clear_prompt_cache()
    return build_system_prompt(
        section_ids,
        prompt_path=prompt_path,
        core_path=core_path,
        sections_dir=sections_dir,
        index_path=index_path,
        settings=settings,
        use_cache=True,
    )
