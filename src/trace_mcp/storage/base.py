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
