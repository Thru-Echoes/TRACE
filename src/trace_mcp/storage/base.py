"""Abstract storage interface for TRACE sessions.

Defines the contract that all storage backends must implement.
Swap in SQLite, S3, etc. without changing tool code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from trace_mcp.schema import Session


class TraceStorage(ABC):
    """Abstract storage interface for TRACE sessions."""

    @abstractmethod
    async def create_session(self, session: Session) -> str:
        """Create a new session. Returns session ID."""
        ...

    @abstractmethod
    async def update_session(self, session: Session) -> None:
        """Write updated session to storage."""
        ...

    @abstractmethod
    async def get_session(self, session_id: str) -> Session:
        """Load a session by ID."""
        ...

    @abstractmethod
    async def list_sessions(self, project: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List sessions. Returns lightweight summaries."""
        ...

    @abstractmethod
    async def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        ...

    def location(self) -> str:
        """Human-readable location of this backend (e.g. a directory path).

        Diagnostics/reporting only — never control flow. Backends without a
        filesystem location return ``"unknown"``.
        """
        return "unknown"

    def session_location(self, session_id: str) -> str:
        """Human-readable location of a single session's record.

        Default is a generic ``"session:<id>"`` label; file-based backends
        override with the concrete path.
        """
        return f"session:{session_id}"

    async def session_brief(
        self, project: str | None = None, scan_cap: int = 25, read_ceiling: int = 200
    ) -> dict[str, Any]:
        """Cheap, BOUNDED session orientation for the start_session bootstrap.

        Part of the storage contract (not an accident of the JSON backend) so
        every backend answers "has this project been here before?" honestly:
        the answer feeds the bootstrap's orientation line, and an unbounded or
        falsely-absolute answer is what this method exists to prevent.

        Returns a dict with ``matched``, ``most_recent`` (lightweight brief of
        the newest match, or None), ``scanned``, ``capped`` (results may exist
        beyond what was examined), ``window_exhausted`` (a bounded backend hit
        its read ceiling with more records beyond it), and ``read_ceiling``.

        This default rides ``list_sessions`` and never claims completeness:
        ``capped`` is True whenever the limit came back full, and
        ``window_exhausted`` mirrors it — a generic backend cannot cheaply know
        the store's true extent, and claiming "none exist" without knowing is
        the false absolute the JSON backend's override was built to retire.
        """
        summaries = await self.list_sessions(project=project, limit=scan_cap)
        hit_limit = len(summaries) >= scan_cap
        return {
            "matched": len(summaries),
            "most_recent": summaries[0] if summaries else None,
            "scanned": len(summaries),
            "capped": hit_limit,
            "window_exhausted": hit_limit,
            "read_ceiling": read_ceiling,
        }
