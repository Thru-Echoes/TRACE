"""User-facing TRACE extension-status banner.

A brief, obvious, consistent one-line notification of which learning-
extension mode is active, plus a short upgrade hint. Surfaced in the
``trace_start_session`` response so it is visible in the host (e.g.
Claude Code) at session start and recorded in the session JSON.

Lives in core (not under ``extensions/``) and probes the OPTIONAL
trace-learn extension defensively via guarded imports — this keeps the
core/extension boundary intact (governance: TRACE decision evt_002) and
makes the probe fail-safe: a status check must never break session start.
"""

from __future__ import annotations

_BANNER = "TRACE active"


def get_extension_status() -> str:
    """Return a one-line extension-status banner + brief upgrade hint.

    Modes:
      * no learning extension installed/importable
      * learning extension, LLM embeddings (OpenAI)
      * learning extension, local embeddings (model2vec, no LLM)
      * learning extension, no embeddings (keyword recall only)

    Never raises — any failure degrades to the "no learning extension"
    message rather than propagating into session start.
    """
    try:
        from trace_mcp.extensions.learn.config import load_config
        from trace_mcp.extensions.learn.embeddings import get_embedding_provider
    except Exception:
        return (
            f"{_BANNER} — no learning extension. "
            "Enable the trace-learn extension for cross-session knowledge recall."
        )

    try:
        provider = get_embedding_provider(load_config())
    except Exception:
        provider = None

    name = type(provider).__name__ if provider is not None else None

    if name == "OpenAIEmbeddingProvider":
        return f"{_BANNER} — learning extension (LLM embeddings via OpenAI)."
    if name == "Model2VecEmbeddingProvider":
        return (
            f"{_BANNER} — learning extension (local embeddings, no LLM). "
            "To upgrade: set OPENAI_API_KEY and embedding_backend=openai for "
            "LLM-grade semantic recall."
        )
    return (
        f"{_BANNER} — learning extension (keyword recall only, no embeddings). "
        "To upgrade: install `model2vec` (local, no LLM) or set OPENAI_API_KEY "
        "for semantic recall."
    )
