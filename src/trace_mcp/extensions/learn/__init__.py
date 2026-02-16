"""trace-learn: Cross-session knowledge persistence for TRACE.

Registers 5 MCP tools on the existing TRACE server:
- trace_learn_recall
- trace_learn_add
- trace_learn_list
- trace_learn_forget
- trace_learn_extract
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from trace_mcp.extensions.learn import extraction, matching, store

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from trace_mcp.storage.base import TraceStorage

logger = logging.getLogger(__name__)


def register(mcp: FastMCP, storage: TraceStorage) -> None:
    """Register trace-learn tools on the MCP server."""

    @mcp.tool()
    async def trace_learn_recall(
        project: str,
        context: str | None = None,
        tags: list[str] | None = None,
        threshold: float = 0.1,
        limit: int = 10,
    ) -> str:
        """Find relevant past learnings for a given context.

        Searches the project's knowledge store using text similarity
        and tag matching. Returns scored results above the threshold.
        """
        try:
            ks = store.load_store(project)
            if not ks.learnings:
                return json.dumps({"project": project, "results": [], "total": 0})
            if not context and not tags:
                results = store.list_learnings(ks)
                return json.dumps({"project": project, "results": results[:limit], "total": len(results)})
            results = matching.recall_learnings(
                ks.learnings,
                context=context or "",
                context_tags=tags,
                threshold=threshold,
                limit=limit,
            )
            return json.dumps({"project": project, "results": results, "total": len(results)})
        except Exception as e:
            logger.exception("Error recalling learnings")
            return f"Error recalling learnings: {e}"

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
            ks = store.load_store(project)
            lrn = store.add_learning(
                ks,
                content=content,
                category=category,
                source_session=source_session,
                source_event=source_event,
                tags=tags,
            )
            store.save_store(ks)
            return json.dumps({"added": lrn.model_dump(mode="json")})
        except Exception as e:
            logger.exception("Error adding learning")
            return f"Error adding learning: {e}"

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
        except Exception as e:
            logger.exception("Error listing learnings")
            return f"Error listing learnings: {e}"

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
        except Exception as e:
            logger.exception("Error removing learning")
            return f"Error removing learning: {e}"

    @mcp.tool()
    async def trace_learn_extract(
        project: str,
        session_id: str | None = None,
    ) -> str:
        """Extract learnings from session annotations and decisions.

        Processes learning/correction/gotcha annotations and rejected/revised
        decisions into persistent knowledge entries. Idempotent — running twice
        on the same session produces no duplicates.

        If session_id is provided, extracts from that session only.
        Otherwise, extracts from all sessions for the project.
        """
        try:
            ks = store.load_store(project)
            all_new_ids: list[str] = []

            if session_id:
                session = await storage.get_session(session_id)
                new_ids = extraction.extract_from_session(ks, session)
                all_new_ids.extend(new_ids)
            else:
                summaries = await storage.list_sessions(project=project, limit=1000)
                for s in summaries:
                    try:
                        session = await storage.get_session(s["id"])
                        new_ids = extraction.extract_from_session(ks, session)
                        all_new_ids.extend(new_ids)
                    except FileNotFoundError:
                        continue

            if all_new_ids:
                store.save_store(ks)

            return json.dumps(
                {
                    "project": project,
                    "new_learnings": len(all_new_ids),
                    "new_ids": all_new_ids,
                    "total_learnings": len(ks.learnings),
                }
            )
        except Exception as e:
            logger.exception("Error extracting learnings")
            return f"Error extracting learnings: {e}"
