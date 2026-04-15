"""trace-learn: Cross-session knowledge persistence for TRACE.

Registers 5 MCP tools on the existing TRACE server:
- trace_learn_recall   — find relevant past learnings (LLM or BM25)
- trace_learn_add      — manually add a learning
- trace_learn_list     — list all learnings
- trace_learn_forget   — remove a learning
- trace_learn_extract  — extract learnings from session events (LLM or rule-based)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, cast, get_args

from trace_mcp.extensions.learn import extraction, matching, store
from trace_mcp.extensions.learn.config import load_config
from trace_mcp.extensions.learn.embeddings import get_embedding_provider
from trace_mcp.extensions.learn.matching import DecayParams
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning, LearningCategory
from trace_mcp.hooks import register_extract_hook, register_recall_hook

_VALID_CATEGORIES = get_args(LearningCategory)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from trace_mcp.storage.base import TraceStorage

logger = logging.getLogger(__name__)


def register(mcp: FastMCP, storage: TraceStorage) -> None:
    """Register trace-learn tools and hooks on the MCP server."""

    _config = load_config()
    _backend = matching.get_default_backend(_config)
    _embedding_provider = get_embedding_provider(_config)
    _decay_params = DecayParams(
        enabled=_config.decay_enabled,
        half_life_days=_config.decay_half_life_days,
        evergreen_recall_threshold=_config.evergreen_recall_threshold,
        evergreen_floor=_config.evergreen_floor,
    )

    async def _embed_learnings(learnings: list[Learning]) -> bool:
        """Generate embeddings for learnings that need them.  Returns True if any were updated.

        Raises ``LLMFallbackError`` in strict mode when the embedding provider
        fails, rather than silently saving un-embedded learnings (which would
        degrade future recall quality without any signal to the caller).
        """
        from trace_mcp.extensions.learn.config import LLMFallbackError

        if _embedding_provider is None or not learnings:
            return False
        try:
            texts = [lrn.content for lrn in learnings]
            vecs = await _embedding_provider.embed_texts(texts)
            for lrn, vec in zip(learnings, vecs, strict=True):
                lrn.embedding = vec
                lrn.embedding_model = _embedding_provider.model_name
            return True
        except Exception as exc:
            if _config.strict_llm:
                logger.error(
                    "Embedding generation failed in strict mode (provider=%s) — "
                    "refusing to silently save un-embedded learnings.",
                    getattr(_embedding_provider, "model_name", "unknown"),
                )
                raise LLMFallbackError(
                    f"Embedding generation failed "
                    f"(provider={getattr(_embedding_provider, 'model_name', 'unknown')}): "
                    f"{exc}. Strict mode is ON — set TRACE_STRICT_LLM=false to "
                    f"allow saving un-embedded learnings."
                ) from exc
            logger.warning(
                "Failed to generate embeddings (provider=%s) — "
                "saving learnings without embeddings. Strict mode is OFF.",
                getattr(_embedding_provider, "model_name", "unknown"),
                exc_info=True,
            )
            return False

    def _needs_embedding(ks: KnowledgeStore) -> list[Learning]:
        """Return learnings that need (re-)embedding."""
        if _embedding_provider is None:
            return []
        return [
            lrn for lrn in ks.learnings
            if lrn.embedding is None or lrn.embedding_model != _embedding_provider.model_name
        ]

    # ── Register hooks so core tools can auto-recall/extract ──

    async def _recall_hook(
        project: str,
        context: str,
        tags: list[str] | None,
        limit: int,
    ) -> list[dict]:
        ks = store.load_store(project)
        if not ks.learnings:
            return []
        stale = _needs_embedding(ks)
        embedded = await _embed_learnings(stale) if stale else False
        results = await matching.recall_learnings(
            ks.learnings,
            context=context,
            context_tags=tags,
            threshold=None,  # Use backend's default_threshold
            limit=limit,
            backend=_backend,
            decay_config=_decay_params,
        )
        if results or embedded:
            store.save_store(ks)
        return results

    async def _extract_hook(project: str, session_id: str) -> list[str]:
        ks = store.load_store(project)
        sess = await storage.get_session(session_id)
        new_ids = await extraction.extract_from_session_auto(ks, sess, _config)
        if new_ids:
            new_set = set(new_ids)
            to_embed = [lrn for lrn in ks.learnings if lrn.id in new_set and lrn.embedding is None]
            await _embed_learnings(to_embed)
            store.save_store(ks)
        return new_ids

    register_recall_hook(_recall_hook)
    register_extract_hook(_extract_hook)

    @mcp.tool()
    async def trace_learn_recall(
        project: str,
        context: str | None = None,
        tags: list[str] | None = None,
        threshold: float | None = None,
        limit: int = 10,
    ) -> str:
        """Find relevant past learnings for a given context.

        Searches the project's knowledge store using text similarity
        and tag matching. Returns scored results above the threshold.

        When threshold is None, uses the backend's default (BM25: 0.15, LLM: 0.2).
        """
        try:
            ks = store.load_store(project)
            if not ks.learnings:
                return json.dumps({"project": project, "results": [], "total": 0})
            if not context and not tags:
                results = store.list_learnings(ks)
                return json.dumps({"project": project, "results": results[:limit], "total": len(results)})
            # Lazy-embed: generate (or regenerate) embeddings for learnings that need them
            stale = _needs_embedding(ks)
            embedded = await _embed_learnings(stale) if stale else False
            results = await matching.recall_learnings(
                ks.learnings,
                context=context or "",
                context_tags=tags,
                threshold=threshold,
                limit=limit,
                backend=_backend,
                decay_config=_decay_params,
            )
            if results or embedded:
                store.save_store(ks)
            return json.dumps({"project": project, "results": results, "total": len(results)})
        except Exception as exc:
            from trace_mcp.extensions.learn.config import LLMFallbackError

            if isinstance(exc, LLMFallbackError):
                logger.error("Strict LLM mode blocked fallback in trace_learn_recall: %s", exc)
                return json.dumps({
                    "error": "LLM strict mode: fallback blocked",
                    "detail": str(exc),
                    "project": project,
                })
            logger.exception("Error recalling learnings")
            return json.dumps({"error": "Failed to recall learnings", "project": project})

    @mcp.tool()
    async def trace_learn_add(
        project: str,
        content: str,
        source_session: str | None = None,
        source_event: str | None = None,
        category: str = "learning",
        tags: list[str] | None = None,
    ) -> str:
        """Manually add a learning to the project's knowledge store.

        Use this to record insights, patterns, or corrections that should
        persist across sessions.
        """
        try:
            if category not in _VALID_CATEGORIES:
                return json.dumps({
                    "error": f"Invalid category '{category}'. Must be one of: {_VALID_CATEGORIES}",
                })
            ks = store.load_store(project)
            if _config.dedup_enabled:
                result = store.add_learning_dedup(
                    ks,
                    content=content,
                    category=cast(LearningCategory, category),
                    source_session=source_session,
                    source_event=source_event,
                    tags=tags,
                    dedup_threshold=_config.dedup_threshold,
                )
                if result.is_duplicate:
                    return json.dumps({
                        "duplicate": True,
                        "similar_to": result.duplicate_of,
                        "existing": store.learning_to_dict(result.learning),
                    })
                lrn = result.learning
            else:
                lrn = store.add_learning(
                    ks,
                    content=content,
                    category=cast(LearningCategory, category),
                    source_session=source_session,
                    source_event=source_event,
                    tags=tags,
                )
            await _embed_learnings([lrn])
            store.save_store(ks)
            return json.dumps({"added": store.learning_to_dict(lrn)})
        except Exception as exc:
            from trace_mcp.extensions.learn.config import LLMFallbackError

            if isinstance(exc, LLMFallbackError):
                logger.error("Strict LLM mode blocked fallback in trace_learn_add: %s", exc)
                return json.dumps({
                    "error": "LLM strict mode: fallback blocked",
                    "detail": str(exc),
                    "project": project,
                })
            logger.exception("Error adding learning")
            return json.dumps({"error": "Failed to add learning", "project": project})

    @mcp.tool()
    async def trace_learn_list(
        project: str,
        category: str | None = None,
    ) -> str:
        """List all learnings in a project's knowledge store.

        Optionally filter by category (learning, correction, gotcha, decision).
        """
        try:
            ks = store.load_store(project)
            results = store.list_learnings(ks, category=category)
            return json.dumps({"project": project, "learnings": results, "total": len(results)})
        except Exception:
            logger.exception("Error listing learnings")
            return json.dumps({"error": "Failed to list learnings", "project": project})

    @mcp.tool()
    async def trace_learn_forget(
        project: str,
        learning_id: str,
    ) -> str:
        """Remove a learning from the project's knowledge store.

        Use this when a learning is outdated, wrong, or no longer relevant.
        """
        try:
            ks = store.load_store(project)
            removed = store.remove_learning(ks, learning_id)
            if not removed:
                return json.dumps({"removed": False, "error": f"Learning '{learning_id}' not found"})
            store.save_store(ks)
            return json.dumps({"removed": True, "learning_id": learning_id})
        except Exception:
            logger.exception("Error removing learning")
            return json.dumps({"error": "Failed to remove learning", "project": project})

    @mcp.tool()
    async def trace_learn_extract(
        project: str,
        session_id: str | None = None,
    ) -> str:
        """Extract learnings from session annotations and decisions.

        Processes learning/correction/gotcha annotations and rejected/revised
        decisions into persistent knowledge entries. Idempotent — running twice
        on the same session produces no duplicates.

        Uses LLM-enhanced extraction when configured, otherwise rule-based.

        If session_id is provided, extracts from that session only.
        Otherwise, extracts from all sessions for the project.
        """
        try:
            ks = store.load_store(project)
            all_new_ids: list[str] = []

            if session_id:
                session = await storage.get_session(session_id)
                new_ids = await extraction.extract_from_session_auto(ks, session, _config)
                all_new_ids.extend(new_ids)
            else:
                summaries = await storage.list_sessions(project=project, limit=1000)
                for s in summaries:
                    try:
                        session = await storage.get_session(s["id"])
                        new_ids = await extraction.extract_from_session_auto(ks, session, _config)
                        all_new_ids.extend(new_ids)
                    except FileNotFoundError:
                        continue

            # Batch-embed newly extracted learnings
            if all_new_ids:
                new_set = set(all_new_ids)
                to_embed = [lrn for lrn in ks.learnings if lrn.id in new_set and lrn.embedding is None]
                await _embed_learnings(to_embed)
                store.save_store(ks)

            return json.dumps(
                {
                    "project": project,
                    "new_learnings": len(all_new_ids),
                    "new_ids": all_new_ids,
                    "total_learnings": len(ks.learnings),
                }
            )
        except Exception as exc:
            from trace_mcp.extensions.learn.config import LLMFallbackError

            if isinstance(exc, LLMFallbackError):
                logger.error("Strict LLM mode blocked fallback in trace_learn_extract: %s", exc)
                return json.dumps({
                    "error": "LLM strict mode: fallback blocked",
                    "detail": str(exc),
                    "project": project,
                })
            logger.exception("Error extracting learnings")
            return json.dumps({"error": "Failed to extract learnings", "project": project})
