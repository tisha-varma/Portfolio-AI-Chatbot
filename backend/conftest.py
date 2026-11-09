"""Pytest bootstrap: make the backend package importable from tests."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def _clear_caches() -> Iterator[None]:
    """Stop one test's cached settings or prompt from leaking into the next."""
    from app.config import get_settings
    from app.prompt_builder import clear_prompt_cache

    get_settings.cache_clear()
    clear_prompt_cache()
    yield
    get_settings.cache_clear()
    clear_prompt_cache()
