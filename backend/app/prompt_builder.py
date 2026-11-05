"""Build the chatbot system prompt from the Markdown template and knowledge base."""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Mapping

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

BACKEND_DIR: Path = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT_PATH: Path = BACKEND_DIR / "prompts" / "system_prompt.md"
DEFAULT_PROFILE_PATH: Path = BACKEND_DIR / "knowledge" / "profile.json"

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
PLACEHOLDER_MARKER = "PLACEHOLDER:"

_CACHE: dict[tuple[Path, Path], str] = {}
_CACHE_LOCK = threading.Lock()


class PromptBuildError(RuntimeError):
    """Raised when the system prompt cannot be built."""


class ProfileParseError(PromptBuildError):
    """Raised when knowledge/profile.json is missing or not valid JSON."""


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


def find_placeholders(text: str) -> list[str]:
    """Return the distinct placeholder names used in a template, sorted."""
    return sorted({match.group(1) for match in PLACEHOLDER_PATTERN.finditer(text)})


def render_template(template: str, values: Mapping[str, str]) -> str:
    """Replace {{PLACEHOLDER}} tokens and fail on unknown or leftover placeholders."""
    missing = [name for name in find_placeholders(template) if name not in values]
    if missing:
        formatted = ", ".join(f"{{{{{name}}}}}" for name in missing)
        raise PromptBuildError(f"No value provided for placeholder(s): {formatted}")

    rendered = PLACEHOLDER_PATTERN.sub(lambda match: values[match.group(1)], template)

    leftover = find_placeholders(rendered)
    if leftover:
        formatted = ", ".join(f"{{{{{name}}}}}" for name in leftover)
        raise PromptBuildError(f"Placeholder(s) still unfilled after rendering: {formatted}")
    return rendered


def _render_from_files(
    prompt_path: Path,
    profile_path: Path,
    settings: Settings,
) -> str:
    """Load both files and render the final prompt."""
    template = load_template(prompt_path)
    profile = load_profile(profile_path)
    knowledge_base = json.dumps(profile, indent=2, ensure_ascii=False)

    if PLACEHOLDER_MARKER in knowledge_base:
        logger.warning(
            "Knowledge base %s still contains %r values; replace them with real data "
            "before exposing the chatbot publicly.",
            profile_path,
            PLACEHOLDER_MARKER,
        )

    values = {
        "OWNER_NAME": settings.owner_name,
        "CONTACT_EMAIL": settings.contact_email,
        "KNOWLEDGE_BASE": knowledge_base,
    }
    return render_template(template, values)


def _cache_key(prompt_path: Path, profile_path: Path) -> tuple[Path, Path]:
    """Build a stable cache key for a template/knowledge pair."""
    return prompt_path.resolve(), profile_path.resolve()


def build_system_prompt(
    *,
    prompt_path: str | Path | None = None,
    profile_path: str | Path | None = None,
    settings: Settings | None = None,
    use_cache: bool = True,
) -> str:
    """Build the system prompt, optionally reusing the in-memory cache.

    Args:
        prompt_path: Override for the Markdown template location.
        profile_path: Override for the knowledge base location.
        settings: Pre-built settings; falls back to the cached get_settings().
        use_cache: Set False to force a fresh read (tests, tooling).

    Raises:
        PromptBuildError: On missing files, missing placeholders, or invalid JSON.
    """
    template_path = Path(prompt_path) if prompt_path is not None else DEFAULT_PROMPT_PATH
    knowledge_path = Path(profile_path) if profile_path is not None else DEFAULT_PROFILE_PATH
    key = _cache_key(template_path, knowledge_path)

    if use_cache:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        if cached is not None:
            return cached

    prompt = _render_from_files(
        template_path,
        knowledge_path,
        settings if settings is not None else get_settings(),
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
