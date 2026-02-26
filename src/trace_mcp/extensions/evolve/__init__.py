"""trace-evolve: Cross-session adaptive persistence for TRACE.

Uses evolution-themed terminology — genomes contain adaptations that
are mutated (added), expressed (recalled), selected (extracted from
sessions), and go extinct (removed).

Registers 5 MCP tools on the existing TRACE server:
- trace_evolve_express   (recall relevant adaptations)
- trace_evolve_mutate    (add a new adaptation)
- trace_evolve_list      (list all adaptations)
- trace_evolve_extinct   (remove an adaptation)
- trace_evolve_select    (extract adaptations from sessions)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from trace_mcp.extensions.evolve import fitness, selection, store

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from trace_mcp.storage.base import TraceStorage

logger = logging.getLogger(__name__)


def register(mcp: FastMCP, storage: TraceStorage) -> None:
    """Register trace-evolve tools on the MCP server."""

    @mcp.tool()
    async def trace_evolve_express(
        project: str,
        context: str | None = None,
        tags: list[str] | None = None,
        threshold: float = 0.1,
        limit: int = 10,
    ) -> str:
        """Find relevant past adaptations for a given context.

        Searches the project's genome using text similarity
        and tag matching. Returns scored results above the threshold.
        """
        try:
            genome = store.load_genome(project)
            if not genome.adaptations:
                return json.dumps({"project": project, "results": [], "total": 0})
            if not context and not tags:
                results = store.list_adaptations(genome)
                return json.dumps({"project": project, "results": results[:limit], "total": len(results)})
            results = fitness.express_adaptations(
                genome.adaptations,
                context=context or "",
                context_tags=tags,
                threshold=threshold,
                limit=limit,
            )
            return json.dumps({"project": project, "results": results, "total": len(results)})
        except Exception as e:
            logger.exception("Error expressing adaptations")
            return f"Error expressing adaptations: {e}"

    @mcp.tool()
    async def trace_evolve_mutate(
        project: str,
        content: str,
        source_session: str | None = None,
        source_event: str | None = None,
        category: str = "learning",
        tags: list[str] | None = None,
    ) -> str:
        """Manually add an adaptation (mutation) to the project's genome.

        Use this to record insights, patterns, or corrections that should
        persist across sessions.
        """
        try:
            genome = store.load_genome(project)
            adp = store.add_adaptation(
                genome,
                content=content,
                category=category,
                source_session=source_session,
                source_event=source_event,
                tags=tags,
            )
            store.save_genome(genome)
            return json.dumps({"mutated": adp.model_dump(mode="json")})
        except Exception as e:
            logger.exception("Error mutating genome")
            return f"Error mutating genome: {e}"

    @mcp.tool()
    async def trace_evolve_list(
        project: str,
        category: str | None = None,
    ) -> str:
        """List all adaptations in a project's genome.

        Optionally filter by category (learning, correction, gotcha, decision).
        """
        try:
            genome = store.load_genome(project)
            results = store.list_adaptations(genome, category=category)
            return json.dumps({"project": project, "adaptations": results, "total": len(results)})
        except Exception as e:
            logger.exception("Error listing adaptations")
            return f"Error listing adaptations: {e}"

    @mcp.tool()
    async def trace_evolve_extinct(
        project: str,
        adaptation_id: str,
    ) -> str:
        """Remove an adaptation from the project's genome (extinction).

        Use this when an adaptation is outdated, wrong, or no longer relevant.
        """
        try:
            genome = store.load_genome(project)
            removed = store.remove_adaptation(genome, adaptation_id)
            if not removed:
                return json.dumps({"removed": False, "error": f"Adaptation '{adaptation_id}' not found"})
            store.save_genome(genome)
            return json.dumps({"removed": True, "adaptation_id": adaptation_id})
        except Exception as e:
            logger.exception("Error removing adaptation")
            return f"Error removing adaptation: {e}"

    @mcp.tool()
    async def trace_evolve_select(
        project: str,
        session_id: str | None = None,
    ) -> str:
        """Select adaptations from session annotations and decisions (natural selection).

        Processes learning/correction/gotcha annotations and rejected/revised
        decisions into persistent genome entries. Idempotent — running twice
        on the same session produces no duplicates.

        If session_id is provided, selects from that session only.
        Otherwise, selects from all sessions for the project.
        """
        try:
            genome = store.load_genome(project)
            all_new_ids: list[str] = []

            if session_id:
                session = await storage.get_session(session_id)
                new_ids = selection.select_from_session(genome, session)
                all_new_ids.extend(new_ids)
            else:
                summaries = await storage.list_sessions(project=project, limit=1000)
                for s in summaries:
                    try:
                        session = await storage.get_session(s["id"])
                        new_ids = selection.select_from_session(genome, session)
                        all_new_ids.extend(new_ids)
                    except FileNotFoundError:
                        continue

            if all_new_ids:
                store.save_genome(genome)

            return json.dumps(
                {
                    "project": project,
                    "new_adaptations": len(all_new_ids),
                    "new_ids": all_new_ids,
                    "total_adaptations": len(genome.adaptations),
                }
            )
        except Exception as e:
            logger.exception("Error selecting adaptations")
            return f"Error selecting adaptations: {e}"
