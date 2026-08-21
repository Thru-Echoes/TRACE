"""User-facing TRACE extension-status banner and OpenAI-credential warnings.

A brief, obvious, consistent one-line notification of which learning-
extension mode is active, plus a short upgrade hint. Surfaced in the
``trace_start_session`` response so it is visible in the host (e.g.
Claude Code) at session start and recorded in the session JSON.

Lives in core (not under ``extensions/``) and probes the OPTIONAL
trace-learn extension defensively via guarded imports — this keeps the
core/extension boundary intact (the optional extension must never be a
hard dependency of core; see docs/adr/003-core-extension-boundary.md) and
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
            f"{_BANNER} — no learning extension. Enable the trace-learn extension for cross-session knowledge recall."
        )

    try:
        config = load_config()
    except Exception:
        return f"{_BANNER} — learning extension (configuration unreadable)."

    try:
        provider = get_embedding_provider(config)
    except Exception:
        provider = None

    name = type(provider).__name__ if provider is not None else None

    if name == "OpenAIEmbeddingProvider":
        mode = f"{_BANNER} — learning extension (LLM embeddings via OpenAI)."
    elif name == "Model2VecEmbeddingProvider":
        mode = (
            f"{_BANNER} — learning extension (local embeddings, no LLM). "
            "To upgrade: put an OpenAI key in this project's .env and set "
            "TRACE_EMBEDDING_BACKEND=openai for LLM-grade semantic recall."
        )
    else:
        mode = (
            f"{_BANNER} — learning extension (keyword recall only, no embeddings). "
            "To upgrade: install `model2vec` (local, no LLM) or put an OpenAI key "
            "in this project's .env for semantic recall."
        )

    return " ".join([mode, *key_warnings(config)])


def key_warnings(config: object) -> list[str]:
    """Loud, user-facing notices about the OpenAI credential, or [] when fine.

    Shared by the session-start banner and the learn tools' responses so the
    same words reach the user through whichever surface they are looking at.
    A missing key is stated at the moment it matters rather than left to a
    server log the user never opens: the failure it causes — keyword results
    instead of semantic ones — looks exactly like success.

    Never raises: a status probe must not be able to break session start.
    """
    warnings: list[str] = []
    try:
        if getattr(config, "missing_key_for_cloud", False):
            warnings.append(
                "⚠️ NO OPENAI_API_KEY FOUND, but this project asks for a cloud path — "
                f"searched {config.key_search_description()}. "  # type: ignore[attr-defined]
                f"Recall and extraction are running on local keyword matching only. "
                f"Put this project's key in {getattr(config, 'project_env_path', None) or './.env'} "
                "and restart the MCP server."
            )
        elif getattr(config, "key_source", None) == "global":
            warnings.append(
                "⚠️ Using the machine-global OpenAI key at "
                f"{getattr(config, 'key_source_path', None) or '~/.trace/.env'} — this project has no key of its own. "
                f"Give it one in {getattr(config, 'project_env_path', None) or './.env'} so its cloud usage is "
                "scoped to this project."
            )
    except Exception:  # pragma: no cover - a status probe must never break session start
        return []
    return warnings
