"""JSON file storage backend for TRACE sessions.

One JSON file per session, stored in ~/.trace/sessions/ by default.
Each file is a self-contained, valid TRACE document.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from pathlib import Path
from typing import Any

from trace_mcp.schema import Session
from trace_mcp.storage.base import TraceStorage

logger = logging.getLogger(__name__)

_DEFAULT_DIR = os.path.expanduser("~/.trace/sessions")


class JsonFileStorage(TraceStorage):
    """Stores TRACE sessions as individual JSON files."""

    def __init__(self, directory: str | None = None) -> None:
        self._dir = Path(directory or os.environ.get("TRACE_SESSIONS_DIR", _DEFAULT_DIR))

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    def _write_file(self, path: Path, data: str) -> None:
        """Write data to file with file locking."""
        self._ensure_dir()
        with open(path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(data)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    async def create_session(self, session: Session) -> str:
        path = self._session_path(session.id)
        data = json.dumps(session.model_dump(mode="json"), indent=2)
        self._write_file(path, data)
        logger.info("Created session file: %s", path)
        return session.id

    async def update_session(self, session: Session) -> None:
        path = self._session_path(session.id)
        if not path.exists():
            raise FileNotFoundError(f"Session file not found: {path}")
        data = json.dumps(session.model_dump(mode="json"), indent=2)
        self._write_file(path, data)

    async def get_session(self, session_id: str) -> Session:
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        with open(path) as f:
            raw = json.load(f)
        return Session.model_validate(raw)

    async def list_sessions(self, project: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure_dir()
        summaries: list[dict[str, Any]] = []
        files = sorted(self._dir.glob("trace_*.json"), reverse=True)
        for path in files:
            if len(summaries) >= limit:
                break
            try:
                with open(path) as f:
                    raw = json.load(f)
                proj = raw.get("metadata", {}).get("project", "")
                if project and project.lower() not in proj.lower():
                    continue
                summaries.append(
                    {
                        "id": raw.get("id", path.stem),
                        "project": proj,
                        "status": raw.get("status", "unknown"),
                        "created": raw.get("created", ""),
                        "event_count": len(raw.get("events", [])),
                    }
                )
            except (json.JSONDecodeError, KeyError):
                logger.warning("Skipping malformed session file: %s", path)
        return summaries

    async def delete_session(self, session_id: str) -> None:
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        path.unlink()
        logger.info("Deleted session file: %s", path)
