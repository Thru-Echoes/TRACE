"""Configuration for the trace-learn extension.

Loads settings from environment variables and ~/.trace/.env.

Resolution order for OPENAI_API_KEY (first match wins):
  1. Environment variable (already set in shell)
  2. ~/.trace/.env (shared across all TRACE projects)
  3. .env in current working directory (project-level override)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_TRACE_ENV_PATH = Path.home() / ".trace" / ".env"


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file. Ignores comments and blank lines."""
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        if not _:
            continue
        key = key.strip()
        value = value.strip().strip("'\"")
        result[key] = value
    return result


@dataclass(frozen=True)
class LearnConfig:
    """Configuration for trace-learn matching and extraction backends."""

    openai_api_key: str | None = None
    llm_model: str = "gpt-5-nano"
    llm_extraction_model: str = "gpt-5-mini"
    llm_enabled: bool = True
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    tag_weight: float = 0.3


def load_config() -> LearnConfig:
    """Load trace-learn config from env vars and .env files.

    The user puts their OpenAI key in ONE place — ``~/.trace/.env`` — and
    every TRACE project picks it up automatically.  Env vars take precedence
    so CI / containers can override.
    """
    # Low-priority → high-priority merge
    project_env = _parse_dotenv(Path.cwd() / ".env")
    global_env = _parse_dotenv(_TRACE_ENV_PATH)

    merged = {**project_env, **global_env}
    for key in (
        "OPENAI_API_KEY",
        "TRACE_LLM_MODEL",
        "TRACE_LLM_EXTRACTION_MODEL",
        "TRACE_LLM_ENABLED",
        "TRACE_BM25_K1",
        "TRACE_BM25_B",
        "TRACE_TAG_WEIGHT",
    ):
        env_val = os.environ.get(key)
        if env_val is not None:
            merged[key] = env_val

    api_key = merged.get("OPENAI_API_KEY") or None
    llm_enabled = merged.get("TRACE_LLM_ENABLED", "true").lower() in ("true", "1", "yes")

    if llm_enabled and not api_key:
        logger.info(
            "No OPENAI_API_KEY found (checked env, ~/.trace/.env, ./.env) "
            "— LLM matching disabled, using BM25 fallback"
        )
        llm_enabled = False

    return LearnConfig(
        openai_api_key=api_key,
        llm_model=merged.get("TRACE_LLM_MODEL", "gpt-5-nano"),
        llm_extraction_model=merged.get("TRACE_LLM_EXTRACTION_MODEL", "gpt-5-mini"),
        llm_enabled=llm_enabled,
        bm25_k1=float(merged.get("TRACE_BM25_K1", "1.5")),
        bm25_b=float(merged.get("TRACE_BM25_B", "0.75")),
        tag_weight=float(merged.get("TRACE_TAG_WEIGHT", "0.3")),
    )
