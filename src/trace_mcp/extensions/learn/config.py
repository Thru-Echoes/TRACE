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
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_TRACE_ENV_PATH = Path.home() / ".trace" / ".env"


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file.

    Handles:
    - Blank lines and full-line comments (``# comment``)
    - Inline comments (``KEY=value  # comment``) — stripped from the value
    - Quoted values (``KEY="value with spaces"``) — quotes preserved,
      inline ``#`` inside quotes is NOT treated as a comment
    """
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        # Strip inline comments. For quoted values, the quote terminates
        # the value and anything after (including `#`) is comment.
        if value and value[0] in ('"', "'"):
            quote = value[0]
            end = value.find(quote, 1)
            if end >= 0:
                value = value[1:end]  # content between quotes
            else:
                value = value[1:]  # unterminated — take rest as-is
        else:
            comment_idx = value.find("#")
            if comment_idx >= 0:
                value = value[:comment_idx].rstrip()
        result[key] = value
    return result


class LLMFallbackError(RuntimeError):
    """Raised when an LLM operation fails and strict mode forbids falling back.

    In strict mode, the user has signalled that LLM features must work.
    Silent fallback to BM25/rule-based would hide degraded quality, so
    we surface the failure instead.
    """


@dataclass(frozen=True)
class LearnConfig:
    """Configuration for trace-learn matching and extraction backends."""

    # repr=False: the key must never leak into pytest failure output, logs,
    # or debugger dumps (all of which render the dataclass repr). It remains
    # reachable via direct attribute access and dataclasses.asdict() — code
    # and tests must never log/assert on a real key value through those.
    openai_api_key: str | None = field(default=None, repr=False)
    llm_model: str = "gpt-5.4-mini"
    llm_extraction_model: str = "gpt-5.4-mini"
    llm_enabled: bool = True
    # Strict mode: if True, LLM failures raise instead of falling back to
    # BM25/rule-based. Auto-defaults to True when an API key is present — the
    # assumption being "if you configured it, you expect it to work."
    strict_llm: bool = True
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    tag_weight: float = 0.3
    decay_enabled: bool = True
    decay_half_life_days: float = 365.0
    evergreen_recall_threshold: int = 3
    evergreen_floor: float = 0.8
    dedup_enabled: bool = True
    dedup_threshold: float = 0.85
    embedding_backend: str = "auto"  # "auto" | "fastembed" | "model2vec" | "openai" | "none"
    embedding_model: str = "text-embedding-3-small"
    # Custom OpenAI-compatible endpoint for the "openai" backend, letting a user
    # point it at any local server (Ollama / LM Studio / text-embeddings-inference
    # / vLLM). None → the SDK default (api.openai.com or its own OPENAI_BASE_URL).
    embedding_base_url: str | None = None


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
        "TRACE_STRICT_LLM",
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
        "OPENAI_BASE_URL",
        "TRACE_OPENAI_BASE_URL",
    ):
        env_val = os.environ.get(key)
        if env_val is not None:
            merged[key] = env_val

    api_key = merged.get("OPENAI_API_KEY") or None
    llm_enabled = merged.get("TRACE_LLM_ENABLED", "true").lower() in ("true", "1", "yes")

    # Strict mode: default ON when API key is present.
    # If the user bothered to configure an API key, fall-backs should fail
    # loudly rather than silently degrade to BM25.
    strict_default = "true" if api_key else "false"
    strict_llm = merged.get("TRACE_STRICT_LLM", strict_default).lower() in ("true", "1", "yes")

    if llm_enabled and not api_key:
        # No key at all — this is a legitimate config, not a failure.
        logger.info(
            "No OPENAI_API_KEY found (checked env, ~/.trace/.env, ./.env). "
            "LLM features disabled — using BM25/rule-based matching."
        )
        llm_enabled = False
    elif llm_enabled and api_key and strict_llm:
        logger.warning(
            "TRACE strict LLM mode is ON (model=%s). "
            "LLM failures will raise errors instead of silently falling back to BM25. "
            "Set TRACE_STRICT_LLM=false to allow silent fallback.",
            merged.get("TRACE_LLM_MODEL", "gpt-5.4-mini"),
        )

    decay_enabled = merged.get("TRACE_DECAY_ENABLED", "true").lower() in ("true", "1", "yes")
    dedup_enabled = merged.get("TRACE_DEDUP_ENABLED", "true").lower() in ("true", "1", "yes")

    return LearnConfig(
        openai_api_key=api_key,
        llm_model=merged.get("TRACE_LLM_MODEL", "gpt-5.4-mini"),
        llm_extraction_model=merged.get("TRACE_LLM_EXTRACTION_MODEL", "gpt-5.4-mini"),
        llm_enabled=llm_enabled,
        strict_llm=strict_llm,
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
        embedding_base_url=merged.get("TRACE_OPENAI_BASE_URL") or merged.get("OPENAI_BASE_URL") or None,
    )
