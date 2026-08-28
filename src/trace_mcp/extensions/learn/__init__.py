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
from typing import TYPE_CHECKING, Any, cast, get_args

from trace_mcp import extension_status
from trace_mcp import project_identity as pident
from trace_mcp.extension_hooks import register_extract_hook, register_recall_hook
from trace_mcp.extensions.learn import extraction, matching, store
from trace_mcp.extensions.learn.config import effective_learn_config, load_config
from trace_mcp.extensions.learn.egress import egress_project
from trace_mcp.extensions.learn.embeddings import get_embedding_provider
from trace_mcp.extensions.learn.matching import DecayParams
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning, LearningCategory

if TYPE_CHECKING:
    from trace_mcp.extensions.learn.config import LearnConfig
    from trace_mcp.extensions.learn.embeddings import EmbeddingProvider
    from trace_mcp.extensions.learn.matching import MatchingBackend

_VALID_CATEGORIES = get_args(LearningCategory)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from trace_mcp.storage.base import TraceStorage

logger = logging.getLogger(__name__)


def _aliased_store_error(exc: Exception, project: str | None) -> str:
    """Response for a write refused because the store's learning ids are aliased.

    The refusal composes a message naming the repair command; a bare
    ``except Exception`` would discard it and hand back a generic failure, so the
    first person to meet the guard would never learn a fix exists (INV-12).
    """
    logger.error("Refused a write to %r: %s", project, exc)
    return json.dumps({"error": "duplicate_learning_ids", "detail": str(exc), "project": project})


def _persist_recall_bookkeeping(ks: KnowledgeStore, project: str) -> str | None:
    """Persist recall counts and lazily-computed embeddings; return a notice if skipped.

    Recall is a read for its caller but writes bookkeeping back. On a store whose
    ids are aliased that write is refused (INV-12) — and failing the recall there
    would invert the guard's own guarantee, since reads are meant to keep working
    so the store stays searchable until it is repaired. The bookkeeping is
    therefore dropped rather than forced, and the reason is returned for the
    response instead of being swallowed.

    Side effect: writes the store unless the write is refused.
    """
    try:
        store.save_store(ks)
        return None
    except store.DuplicateLearningIdError as exc:
        logger.warning("Recall bookkeeping not persisted for %r: %s", project, exc)
        return f"Recall counts and embeddings were not saved: {exc}"


def _resolve_project(project: str | None) -> tuple[str, str | None]:
    """Resolve *project* against the process pin; ``(label, None)`` or ``("", error-JSON)``.

    The scope gate for every learn tool (INV-9, ADR-006): pin resolution
    (``None`` → the pin), foreign-label rejection on a pinned server (the error
    names both keys), and the reserved-key usage ban all happen here — before
    any knowledge store is touched. Unpinned, an explicit non-empty label is
    required.

    The returned label is STORE-SAFE: knowledge-store filenames key on
    ``canonical_project_key(label)`` (registry-independent), so when a pinned
    call is accepted through a registry ALIAS whose canonical form differs from
    the pinned key (or the pin's display label itself diverges), the pinned KEY
    is returned instead — otherwise the accepted alias would silently open a
    different store file than the project it was authorized against.
    """
    try:
        label = pident.resolve_scoped_project(project)
        bound = pident.get_bound_project()
        if bound is not None:
            if pident.canonical_project_key(label) != bound.key:
                label = bound.key
        elif pident.key_for_label(label) in pident.RESERVED_KEYS:
            # Defense in depth: a registry alias must not smuggle a benign-looking
            # label into a reserved quarantine store on an unpinned server.
            raise pident.ProjectKeyError(f"{label!r} resolves to a reserved project key and cannot be used.")
        return label, None
    except pident.ProjectKeyError as exc:
        return "", json.dumps({"error": str(exc), "project": project or ""})


def register(mcp: FastMCP, storage: TraceStorage) -> None:
    """Register trace-learn tools and hooks on the MCP server."""

    _config = load_config()
    # Registration must survive a backend that cannot be built. `get_default_backend`
    # refuses to degrade to keyword matching in strict mode — correct for a caller
    # that can report it, but here the exception would escape the extension loader
    # and the server would come up with the 17 core tools and no explanation. That
    # is the silent-degradation failure this project treats as the worst outcome,
    # so the failure is captured and re-reported on every affected response instead.
    _startup_error: str | None = None
    try:
        _backend = matching.get_default_backend(_config)
        _embedding_provider = get_embedding_provider(_config)
    except Exception as exc:  # noqa: BLE001 - a broken backend must not unregister the tools
        _startup_error = str(exc)
        logger.error(
            "trace-learn: could not build the configured matching backend (%s). Registering with keyword "
            "matching only; every recall response will carry this notice.",
            exc,
        )
        _backend = matching.BM25Backend(k1=_config.bm25_k1, b=_config.bm25_b, tag_weight=_config.tag_weight)
        _embedding_provider = None

    def _key_notices(config: LearnConfig | None = None) -> list[str]:
        """Loud notices to attach to any response that would have used the cloud.

        Takes the EFFECTIVE config for the calling project, not the process-wide
        one: a project the registry ratchets offline asks for no cloud path and
        must not be told it is missing a key for one.
        """
        notices = list(extension_status.key_warnings(config if config is not None else _config))
        if _startup_error:
            notices.append(
                f"⚠️ The configured matching backend could not be built, so recall is running on local "
                f"keyword matching: {_startup_error}"
            )
        return notices

    _decay_params = DecayParams(
        enabled=_config.decay_enabled,
        half_life_days=_config.decay_half_life_days,
        evergreen_recall_threshold=_config.evergreen_recall_threshold,
        evergreen_floor=_config.evergreen_floor,
    )

    # Per-project ratcheted (config, backend, provider) triples. Keyed by the
    # canonical key PLUS the entry's posture fields, so an edited registry entry
    # takes effect on its next call rather than serving a stale triple for the
    # life of the process. Only ratcheted projects land here — the common
    # unrestricted path returns the process-wide globals via `eff is _config`.
    _ratchet_cache: dict[tuple, tuple[LearnConfig, MatchingBackend, EmbeddingProvider | None]] = {}

    def _effective(project: str) -> tuple[LearnConfig, MatchingBackend, EmbeddingProvider | None]:
        """The (config, matching backend, embedding provider) to use for *project*.

        Applies the registry's restrict-only privacy ratchet (ADR-006 §6).
        Raises ``RegistryUnavailableError`` when the registry exists but cannot
        be read — the caller's knowledge/egress path must fail closed, because
        the posture that would forbid the egress may live in the unreadable file.
        """
        eff = effective_learn_config(_config, project)
        if eff is _config:
            return _config, _backend, _embedding_provider
        cache_key = (
            pident.key_for_label(project),
            eff.local_only,
            eff.llm_enabled,
            eff.embedding_backend,
        )
        cached = _ratchet_cache.get(cache_key)
        if cached is None:
            cached = (eff, matching.get_default_backend(eff), get_embedding_provider(eff))
            _ratchet_cache[cache_key] = cached
        return cached

    async def _embed_learnings(
        learnings: list[Learning],
        provider: EmbeddingProvider | None = None,
    ) -> bool:
        """Generate embeddings for learnings that need them.  Returns True if any were updated.

        *provider* is the (possibly ratchet-downgraded) provider for the calling
        project; ``None`` falls back to the process-wide one.

        Raises ``LLMFallbackError`` in strict mode when the embedding provider
        fails, rather than silently saving un-embedded learnings (which would
        degrade future recall quality without any signal to the caller).
        """
        from trace_mcp.extensions.learn.config import LLMFallbackError

        active = provider if provider is not None else _embedding_provider
        if active is None or not learnings:
            return False
        try:
            texts = [lrn.content for lrn in learnings]
            vecs = await active.embed_texts(texts)
            for lrn, vec in zip(learnings, vecs, strict=True):
                lrn.embedding = vec
                lrn.embedding_model = active.model_name
            return True
        except Exception as exc:
            from trace_mcp.extensions.learn.config import ApiKeyRejectedError

            if isinstance(exc, ApiKeyRejectedError):
                # Strict mode decides whether degradation is acceptable, never
                # whether a refused credential is reported. Swallowing it here
                # is the one path that would leave the headline guarantee false:
                # recall's lazy-embed step would be skipped, everything would
                # fall to keyword scoring, and the response would carry no
                # notice because a key IS configured.
                logger.error("OpenAI rejected the API key while embedding: %s", exc)
                raise
            if _config.strict_llm:
                logger.error(
                    "Embedding generation failed in strict mode (provider=%s) — "
                    "refusing to silently save un-embedded learnings.",
                    getattr(active, "model_name", "unknown"),
                )
                raise LLMFallbackError(
                    f"Embedding generation failed "
                    f"(provider={getattr(active, 'model_name', 'unknown')}): "
                    f"{exc}. Strict mode is ON — set TRACE_STRICT_LLM=false to "
                    f"allow saving un-embedded learnings."
                ) from exc
            logger.warning(
                "Failed to generate embeddings (provider=%s) — "
                "saving learnings without embeddings. Strict mode is OFF.",
                getattr(active, "model_name", "unknown"),
                exc_info=True,
            )
            return False

    def _needs_embedding(ks: KnowledgeStore, provider: EmbeddingProvider | None = None) -> list[Learning]:
        """Return learnings that need (re-)embedding under *provider* (default: process-wide)."""
        active = provider if provider is not None else _embedding_provider
        if active is None:
            return []
        return [lrn for lrn in ks.learnings if lrn.embedding is None or lrn.embedding_model != active.model_name]

    def _registry_unavailable_error(project: str | None, exc: Exception) -> str:
        return json.dumps(
            {
                "error": "project registry unreadable — learn operations fail closed",
                "detail": str(exc),
                "project": project or "",
            }
        )

    async def _project_session_ids(project: str) -> list[str]:
        """Every session id belonging to *project*, by DIRECT GLOB of the store.

        The query layer's ``list_sessions`` caps its scan (500 files) and its
        result set, silently hiding the OLDEST sessions of a large store —
        exactly the ones a project-wide extraction exists to mine. Enumerate
        the directory instead and match by canonical key. Falls back to the
        query path for storage backends that expose no filesystem location.
        """
        from pathlib import Path

        loc = getattr(storage, "location", lambda: "unknown")()
        if loc == "unknown" or not Path(loc).is_dir():
            summaries = await storage.list_sessions(project=project, limit=1000)
            return [s["id"] for s in summaries]
        key = pident.key_for_label(project)
        ids: list[str] = []
        for path in sorted(Path(loc).glob("trace_*.json")):
            try:
                meta = json.loads(path.read_text(encoding="utf-8")).get("metadata") or {}
            except (json.JSONDecodeError, OSError):
                continue
            if pident.session_matches_project(meta, key):
                ids.append(path.stem)
        return ids

    # ── Register hooks so core tools can auto-recall/extract ──

    async def _recall_hook(
        project: str,
        context: str,
        tags: list[str] | None,
        limit: int,
    ) -> list[dict]:
        # Fail loudly-but-open on registry damage: hooks run inside session
        # capture (start/decision), and capture must never be blocked by a
        # damaged registry (ADR-006 §6 split) — but with the posture unreadable
        # no egress-capable backend may run, so recall is skipped entirely.
        try:
            _eff, backend, provider = _effective(project)
        except pident.RegistryUnavailableError as exc:
            logger.error("Registry unreadable — skipping auto-recall for %r (fail closed on egress): %s", project, exc)
            return []
        # Lock the full load→embed→save read-modify-write span. This is
        # the highest-frequency knowledge-store mutator (core auto-recall);
        # leaving it unlocked allowed a concurrent locked add to be
        # clobbered (a lost update).
        with store.project_lock(project), egress_project(pident.key_for_label(project) or None):
            ks = store.load_store(project)
            if not ks.learnings:
                return []
            stale = _needs_embedding(ks, provider)
            embedded = await _embed_learnings(stale, provider) if stale else False
            results = await matching.recall_learnings(
                ks.learnings,
                context=context,
                context_tags=tags,
                threshold=None,  # Use backend's default_threshold
                limit=limit,
                backend=backend,
                decay_config=_decay_params,
            )
            if results or embedded:
                _persist_recall_bookkeeping(ks, project)
            return results

    async def _extract_hook(project: str, session_id: str) -> list[str]:
        # INV-6: refuse to extract a session into a DIFFERENT project's store
        # (closes the cross-wired-extract bleed, which would also send one
        # project's store to the cloud as another's dedup context).
        await pident.validate_project_session(storage, project, session_id)
        try:
            eff, _backend_unused, provider = _effective(project)
        except pident.RegistryUnavailableError as exc:
            # end_session must complete (capture over attribution); with the
            # privacy posture unreadable, extraction — an egress-capable path —
            # is skipped loudly instead of running on the global config.
            logger.error("Registry unreadable — skipping extraction for %r (fail closed on egress): %s", project, exc)
            return []
        with store.project_lock(project), egress_project(pident.key_for_label(project) or None):
            ks = store.load_store(project)
            sess = await storage.get_session(session_id)
            new_ids = await extraction.extract_from_session_auto(ks, sess, eff)
            if new_ids:
                new_set = set(new_ids)
                to_embed = [lrn for lrn in ks.learnings if lrn.id in new_set and lrn.embedding is None]
                await _embed_learnings(to_embed, provider)
                store.save_store(ks)
        return new_ids

    register_recall_hook(_recall_hook)
    register_extract_hook(_extract_hook)

    @mcp.tool()
    async def trace_learn_recall(
        project: str | None = None,
        context: str | None = None,
        tags: list[str] | None = None,
        threshold: float | None = None,
        limit: int = 10,
        query: str | None = None,
    ) -> str:
        """Find relevant past learnings for a given context.

        Searches the project's knowledge store using text similarity
        and tag matching. Returns scored results above the threshold, plus a
        ``backend`` field naming what produced the ranking. ``query`` is an
        alias for ``context`` (either alone is fine; both with different
        values is an error). A call with neither context/query nor tags is an
        error — use ``trace_learn_list`` to browse the store unranked.

        ``project`` is optional when the server is pinned (TRACE_PROJECT):
        omit it to use the pin; a supplied label must resolve to the pinned
        project. Unpinned, an explicit project is required.

        When threshold is None, uses the backend's default (BM25: 0.15, LLM: 0.2).

        Data flow: with the OpenAI embedding/LLM backend configured, the query
        text is sent to OpenAI to score matches. Set ``TRACE_LOCAL_ONLY=1`` to
        keep recall fully local.
        """
        try:
            project, _err = _resolve_project(project)
            if _err:
                return _err
            # Normalize BEFORE alias-conflict detection: whitespace-only text is
            # no query (it must not rank, and must not count as a conflicting
            # alias value), and blank tag elements are no criteria.
            if context is not None:
                context = context.strip() or None
            if query is not None:
                query = query.strip() or None
            if tags is not None:
                tags = [t.strip() for t in tags if isinstance(t, str) and t.strip()] or None
            if query is not None and context is not None and query != context:
                return json.dumps(
                    {
                        "error": "query and context were both given and differ — pass a single query text",
                        "project": project,
                    }
                )
            if context is None:
                context = query
            if not context and not tags:
                return json.dumps(
                    {
                        "error": "recall needs context text or tags to rank against",
                        "hint": "To browse the store without a query, use trace_learn_list.",
                        "project": project,
                    }
                )
            _eff, backend, provider = _effective(project)
            # Lock the full span (recall may backfill
            # embeddings and save — a read-modify-write).
            with store.project_lock(project), egress_project(pident.key_for_label(project) or None):
                ks = store.load_store(project)
                if not ks.learnings:
                    empty: dict[str, Any] = {
                        "project": project,
                        "results": [],
                        "total": 0,
                        "backend": matching.describe_backend(backend),
                    }
                    # A first-run project is exactly where a missing key is most
                    # worth saying out loud, so this early return carries the
                    # notices too.
                    if notices := _key_notices(_eff):
                        empty["warnings"] = notices
                    return json.dumps(empty)
                # Lazy-embed: generate (or regenerate) embeddings for learnings that need them
                stale = _needs_embedding(ks, provider)
                embedded = await _embed_learnings(stale, provider) if stale else False
                results = await matching.recall_learnings(
                    ks.learnings,
                    context=context or "",
                    context_tags=tags,
                    threshold=threshold,
                    limit=limit,
                    backend=backend,
                    decay_config=_decay_params,
                )
                bookkeeping_notice: str | None = None
                if results or embedded:
                    bookkeeping_notice = _persist_recall_bookkeeping(ks, project)
                payload: dict[str, Any] = {
                    "project": project,
                    "results": results,
                    "total": len(results),
                    "backend": matching.describe_backend(backend),
                }
                notices = _key_notices(_eff)
                if bookkeeping_notice:
                    notices.append(bookkeeping_notice)
                if notices:
                    payload["warnings"] = notices
                return json.dumps(payload)
        except pident.RegistryUnavailableError as exc:
            return _registry_unavailable_error(project, exc)
        except Exception as exc:
            from trace_mcp.extensions.learn.config import ApiKeyRejectedError, LLMFallbackError

            if isinstance(exc, ApiKeyRejectedError):
                # Checked BEFORE LLMFallbackError, which it subclasses: labelling
                # a refused credential "strict mode blocked fallback" sends the
                # reader to TRACE_STRICT_LLM, where nothing they change will fix it.
                logger.error("OpenAI rejected the API key: %s", exc)
                return json.dumps({"error": "OpenAI API key rejected", "detail": str(exc), "project": project})
            if isinstance(exc, LLMFallbackError):
                logger.error("Strict LLM mode blocked fallback in trace_learn_recall: %s", exc)
                return json.dumps(
                    {
                        "error": "LLM strict mode: fallback blocked",
                        "detail": str(exc),
                        "project": project,
                    }
                )
            if isinstance(exc, store.DuplicateLearningIdError):
                return _aliased_store_error(exc, project)
            logger.exception("Error recalling learnings")
            return json.dumps({"error": "Failed to recall learnings", "project": project})

    @mcp.tool()
    async def trace_learn_add(
        content: str,
        project: str | None = None,
        source_session: str | None = None,
        source_event: str | None = None,
        category: str = "learning",
        tags: list[str] | None = None,
    ) -> str:
        """Manually add a learning to the project's knowledge store.

        Use this to record insights, patterns, or corrections that should
        persist across sessions.

        ``project`` is optional when the server is pinned (TRACE_PROJECT):
        omit it to use the pin; a supplied label must resolve to the pinned
        project. Unpinned, an explicit project is required.

        Data flow: with the OpenAI embedding backend configured, the learning
        content is embedded via OpenAI. Set ``TRACE_LOCAL_ONLY=1`` for local-only.
        """
        try:
            project, _err = _resolve_project(project)
            if _err:
                return _err
            if category not in _VALID_CATEGORIES:
                return json.dumps(
                    {
                        "error": f"Invalid category '{category}'. Must be one of: {_VALID_CATEGORIES}",
                    }
                )
            _eff, _bk, provider = _effective(project)
            # Lock the full load->mutate->save span so concurrent
            # multi-session adds to the same project don't lose updates
            # (last-writer-wins on the shared store).
            with store.project_lock(project), egress_project(pident.key_for_label(project) or None):
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
                        extraction_method="manual",
                    )
                    if result.is_duplicate:
                        return json.dumps(
                            {
                                "duplicate": True,
                                "similar_to": result.duplicate_of,
                                "existing": store.learning_to_dict(result.learning),
                            }
                        )
                    lrn = result.learning
                else:
                    lrn = store.add_learning(
                        ks,
                        content=content,
                        category=cast(LearningCategory, category),
                        source_session=source_session,
                        source_event=source_event,
                        tags=tags,
                        extraction_method="manual",
                    )
                await _embed_learnings([lrn], provider)
                store.save_store(ks)
                return json.dumps({"added": store.learning_to_dict(lrn)})
        except pident.RegistryUnavailableError as exc:
            return _registry_unavailable_error(project, exc)
        except Exception as exc:
            from trace_mcp.extensions.learn.config import ApiKeyRejectedError, LLMFallbackError

            if isinstance(exc, ApiKeyRejectedError):
                # Checked BEFORE LLMFallbackError, which it subclasses: labelling
                # a refused credential "strict mode blocked fallback" sends the
                # reader to TRACE_STRICT_LLM, where nothing they change will fix it.
                logger.error("OpenAI rejected the API key: %s", exc)
                return json.dumps({"error": "OpenAI API key rejected", "detail": str(exc), "project": project})
            if isinstance(exc, LLMFallbackError):
                logger.error("Strict LLM mode blocked fallback in trace_learn_add: %s", exc)
                return json.dumps(
                    {
                        "error": "LLM strict mode: fallback blocked",
                        "detail": str(exc),
                        "project": project,
                    }
                )
            if isinstance(exc, store.DuplicateLearningIdError):
                return _aliased_store_error(exc, project)
            logger.exception("Error adding learning")
            return json.dumps({"error": "Failed to add learning", "project": project})

    @mcp.tool()
    async def trace_learn_list(
        project: str | None = None,
        category: str | None = None,
    ) -> str:
        """List all learnings in a project's knowledge store.

        Optionally filter by category (learning, correction, gotcha, decision).

        ``project`` is optional when the server is pinned (TRACE_PROJECT):
        omit it to use the pin; a supplied label must resolve to the pinned
        project. Unpinned, an explicit project is required.
        """
        try:
            project, _err = _resolve_project(project)
            if _err:
                return _err
            ks = store.load_store(project)
            results = store.list_learnings(ks, category=category)
            return json.dumps({"project": project, "learnings": results, "total": len(results)})
        except Exception:
            logger.exception("Error listing learnings")
            return json.dumps({"error": "Failed to list learnings", "project": project})

    @mcp.tool()
    async def trace_learn_forget(
        learning_id: str,
        project: str | None = None,
    ) -> str:
        """Remove a learning from the project's knowledge store.

        Use this when a learning is outdated, wrong, or no longer relevant.

        ``project`` is optional when the server is pinned (TRACE_PROJECT):
        omit it to use the pin; a supplied label must resolve to the pinned
        project. Unpinned, an explicit project is required.
        """
        try:
            project, _err = _resolve_project(project)
            if _err:
                return _err
            with store.project_lock(project):  # lock the full read-modify-write span
                ks = store.load_store(project)
                removed = store.remove_learning(ks, learning_id)
                if not removed:
                    return json.dumps({"removed": False, "error": f"Learning '{learning_id}' not found"})
                store.save_store(ks)
                return json.dumps({"removed": True, "learning_id": learning_id})
        except store.DuplicateLearningIdError as exc:
            return _aliased_store_error(exc, project)
        except Exception:
            logger.exception("Error removing learning")
            return json.dumps({"error": "Failed to remove learning", "project": project})

    @mcp.tool()
    async def trace_learn_extract(
        project: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """Extract learnings from session annotations and decisions.

        ``project`` is optional when the server is pinned (TRACE_PROJECT):
        omit it to use the pin; a supplied label must resolve to the pinned
        project. Unpinned, an explicit project is required.

        Processes learning/correction/gotcha annotations and rejected/revised
        decisions into persistent knowledge entries. Idempotent — running twice
        on the same session produces no duplicates.

        Uses LLM-enhanced extraction when configured, otherwise rule-based.

        Data flow: when an OpenAI key is configured and LLM features are enabled,
        this sends session event content (and the existing knowledge store, as
        de-duplication context) to OpenAI for extraction. To keep everything on
        your machine, set ``TRACE_LOCAL_ONLY=1`` (forces local embeddings +
        rule-based extraction, no egress). See docs/embeddings.md.

        If session_id is provided, extracts from that session only.
        Otherwise, extracts from all sessions for the project.
        """
        try:
            project, _err = _resolve_project(project)
            if _err:
                return _err
            # INV-6: when extracting a specific session, refuse if it belongs to a
            # different project — the cross-wired-extract bleed (would send this
            # project's whole store to the cloud alongside another's events).
            if session_id:
                await pident.validate_project_session(storage, project, session_id)
            eff, _bk, provider = _effective(project)
            # Lock the full multi-session extract→embed→save span.
            with store.project_lock(project), egress_project(pident.key_for_label(project) or None):
                ks = store.load_store(project)
                all_new_ids: list[str] = []

                if session_id:
                    session = await storage.get_session(session_id)
                    new_ids = await extraction.extract_from_session_auto(ks, session, eff)
                    all_new_ids.extend(new_ids)
                else:
                    for sid in await _project_session_ids(project):
                        try:
                            session = await storage.get_session(sid)
                            new_ids = await extraction.extract_from_session_auto(ks, session, eff)
                            all_new_ids.extend(new_ids)
                        except FileNotFoundError:
                            continue

                # Batch-embed newly extracted learnings
                if all_new_ids:
                    new_set = set(all_new_ids)
                    to_embed = [lrn for lrn in ks.learnings if lrn.id in new_set and lrn.embedding is None]
                    await _embed_learnings(to_embed, provider)
                    store.save_store(ks)

                payload: dict[str, Any] = {
                    "project": project,
                    "new_learnings": len(all_new_ids),
                    "new_ids": all_new_ids,
                    "total_learnings": len(ks.learnings),
                }
                if notices := _key_notices(eff):
                    payload["warnings"] = notices
                return json.dumps(payload)
        except pident.RegistryUnavailableError as exc:
            return _registry_unavailable_error(project, exc)
        except (pident.ProjectMismatchError, pident.ProjectKeyError) as exc:
            return json.dumps(
                {"error": "project/session coherence check failed", "detail": str(exc), "project": project}
            )
        except Exception as exc:
            from trace_mcp.extensions.learn.config import ApiKeyRejectedError, LLMFallbackError

            if isinstance(exc, ApiKeyRejectedError):
                # Checked BEFORE LLMFallbackError, which it subclasses: labelling
                # a refused credential "strict mode blocked fallback" sends the
                # reader to TRACE_STRICT_LLM, where nothing they change will fix it.
                logger.error("OpenAI rejected the API key: %s", exc)
                return json.dumps({"error": "OpenAI API key rejected", "detail": str(exc), "project": project})
            if isinstance(exc, LLMFallbackError):
                logger.error("Strict LLM mode blocked fallback in trace_learn_extract: %s", exc)
                return json.dumps(
                    {
                        "error": "LLM strict mode: fallback blocked",
                        "detail": str(exc),
                        "project": project,
                    }
                )
            if isinstance(exc, store.DuplicateLearningIdError):
                return _aliased_store_error(exc, project)
            logger.exception("Error extracting learnings")
            return json.dumps({"error": "Failed to extract learnings", "project": project})
