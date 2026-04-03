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
    decay_enabled: bool = True
    decay_half_life_days: float = 365.0
    evergreen_recall_threshold: int = 3
    evergreen_floor: float = 0.8
    dedup_enabled: bool = True
    dedup_threshold: float = 0.85
    embedding_backend: str = "auto"  # "openai" | "model2vec" | "none" | "auto"
    embedding_model: str = "text-embedding-3-small"


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
        "TRACE_DECAY_ENABLED",
        "TRACE_DECAY_HALF_LIFE_DAYS",
        "TRACE_EVERGREEN_RECALL_THRESHOLD",
        "TRACE_EVERGREEN_FLOOR",
        "TRACE_DEDUP_ENABLED",
        "TRACE_DEDUP_THRESHOLD",
        "TRACE_EMBEDDING_BACKEND",
        "TRACE_EMBEDDING_MODEL",
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

    decay_enabled = merged.get("TRACE_DECAY_ENABLED", "true").lower() in ("true", "1", "yes")
    dedup_enabled = merged.get("TRACE_DEDUP_ENABLED", "true").lower() in ("true", "1", "yes")

    return LearnConfig(
        openai_api_key=api_key,
        llm_model=merged.get("TRACE_LLM_MODEL", "gpt-5-nano"),
        llm_extraction_model=merged.get("TRACE_LLM_EXTRACTION_MODEL", "gpt-5-mini"),
        llm_enabled=llm_enabled,
        bm25_k1=float(merged.get("TRACE_BM25_K1", "1.5")),
        bm25_b=float(merged.get("TRACE_BM25_B", "0.75")),
        tag_weight=float(merged.get("TRACE_TAG_WEIGHT", "0.3")),
        decay_enabled=decay_enabled,
        decay_half_life_days=float(merged.get("TRACE_DECAY_HALF_LIFE_DAYS", "365.0")),
        evergreen_recall_threshold=int(merged.get("TRACE_EVERGREEN_RECALL_THRESHOLD", "3")),
        evergreen_floor=float(merged.get("TRACE_EVERGREEN_FLOOR", "0.8")),
        dedup_enabled=dedup_enabled,
        dedup_threshold=float(merged.get("TRACE_DEDUP_THRESHOLD", "0.85")),
        embedding_backend=merged.get("TRACE_EMBEDDING_BACKEND", "auto"),
        embedding_model=merged.get("TRACE_EMBEDDING_MODEL", "text-embedding-3-small"),
    )
