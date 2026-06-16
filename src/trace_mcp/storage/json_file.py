"""JSON file storage backend for TRACE sessions.

One JSON file per session, stored in ~/.trace/sessions/ by default.
Each file is a self-contained, valid TRACE document.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from trace_mcp.schema import SCHEMA_VERSION, Session
from trace_mcp.storage.base import TraceStorage

logger = logging.getLogger(__name__)

_DEFAULT_DIR = os.path.expanduser("~/.trace/sessions")


def _minor_version(v: str) -> tuple[int, int]:
    """Parse the (major, minor) tuple from a dotted version string."""
    major, _, rest = v.partition(".")
    minor, _, _ = rest.partition(".")
    return (int(major), int(minor or 0))


def _is_future_schema_version(file_version: str | None) -> bool:
    """True if a session file declares a (major, minor) newer than this server.

    Used to warn on version skew at read time. Combined with the schema models'
    ``extra='allow'`` (forward-compat preservation), this surfaces the skew that
    used to cause silent field-stripping on rewrite. Returns False on any
    unparseable/missing version (treat as same-or-older).
    """
    if not file_version:
        return False
    try:
        return _minor_version(file_version) > _minor_version(SCHEMA_VERSION)
    except ValueError:
        return False


def _holder_status(token: bytes) -> str:
    """Liveness of the process named in a lock token (``<pid>:<time_ns>``).

    Returns ``"dead"`` (holder process is gone — safe to steal), ``"alive"``
    (holder is running — must NOT steal, however old the lock), or
    ``"unknown"`` (token unparseable / legacy-empty, or liveness not
    determinable on this platform — fall back to the time-based steal). Valid
    only under the single-host advisory-lock assumption the lock documents.
    """
    try:
        pid = int(token.split(b":", 1)[0])
    except (ValueError, IndexError):
        return "unknown"
    if pid <= 0:
        return "unknown"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        return "alive"  # process exists, owned by another user
    except OSError:
        return "unknown"  # e.g. signal-0 liveness unsupported on this platform
    return "alive"


def sanitize_name(name: str) -> str:
    """Sanitize a session ID or project name for safe filesystem use."""
    cleaned = re.sub(r"[^\w\-.]", "_", name)  # only alphanum, _, -, .
    cleaned = cleaned.replace("..", "_")  # no parent traversal
    cleaned = cleaned.lstrip(".")  # no hidden files
    # Raise if the original had no usable characters (e.g. "////")
    if not cleaned or not re.search(r"[\w\-.]", name):
        raise ValueError(f"Name sanitizes to empty string: {name!r}")
    return cleaned


class JsonFileStorage(TraceStorage):
    """Stores TRACE sessions as individual JSON files."""

    def __init__(self, directory: str | None = None) -> None:
        self._dir = Path(directory or os.environ.get("TRACE_SESSIONS_DIR", _DEFAULT_DIR))

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self._dir / f"{sanitize_name(session_id)}.json"

    def location(self) -> str:
        """The sessions directory backing this store."""
        return str(self._dir)

    def session_location(self, session_id: str) -> str:
        """The on-disk path of a session's JSON file."""
        return str(self._session_path(session_id))

    def _write_file(self, path: Path, data: str) -> None:
        """Write data to file using atomic write (temp file + fsync + rename).

        The fsync forces the bytes to disk before the atomic os.replace, so a
        crash cannot leave a truncated/empty session file — durability for the
        provenance record. Cross-platform: no fcntl.
        """
        self._ensure_dir()
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @contextlib.asynccontextmanager
    async def lock(
        self,
        session_id: str,
        *,
        timeout: float = 10.0,
        steal_after: float = 60.0,
        poll: float = 0.02,
    ) -> AsyncIterator[None]:
        """Portable per-session advisory lock for read-modify-write appends.

        Implemented with an exclusive lock file (``os.O_CREAT | os.O_EXCL``) —
        cross-platform, no fcntl/filelock dependency (keeps core deps = mcp +
        pydantic). A lock older than ``steal_after`` is treated as stale (holder
        crashed) and stolen.

        **Fail-closed (PR D):** if the lock cannot be acquired within
        ``timeout`` we ``raise TimeoutError`` rather than proceed unlocked. An
        audit store must never silently degrade to an unsynchronized write —
        that reopens the lost-update / duplicate-evt_id window the lock exists
        to close, invisibly to the caller and the record. The lock is
        single-host advisory only (``O_EXCL`` does not synchronize across NFS /
        distinct mounts); TRACE's deployment is a local per-user ``~/.trace``.

        The lock file carries an identifying token (``<pid>:<time_ns>``) so the
        stale-steal path can re-verify the lock is byte- and mtime-identical to
        the one it judged stale before unlinking it — closing the TOCTOU window
        where a writer could delete another writer's *fresh* lock.

        Side effects: creates/removes ``<session>.lock`` in the sessions dir
        (excluded from list/scan globs, which match ``trace_*.json``).

        Raises:
            TimeoutError: the lock could not be acquired within ``timeout``.
        """
        self._ensure_dir()
        lock_path = self._dir / f"{sanitize_name(session_id)}.lock"
        token = f"{os.getpid()}:{time.time_ns()}".encode()
        acquired = False
        waited = 0.0
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, token)
                finally:
                    os.close(fd)
                acquired = True
                break
            except FileExistsError:
                pass  # someone holds it — decide below whether to steal

            # Steal the lock only if its holder is provably DEAD (single-host
            # PID liveness), or — for an unparseable/legacy token whose holder
            # cannot be checked — if it is older than ``steal_after`` (time
            # backstop). A LIVE holder is never stolen, however old its lock.
            # The byte+mtime re-verify before unlink avoids deleting a different,
            # fresher lock (the TOCTOU the empty pre-fix lock file allowed).
            stole = False
            try:
                token_seen = lock_path.read_bytes()
                before = os.stat(lock_path)
                holder = _holder_status(token_seen)
                stale_by_time = (time.time() - before.st_mtime) > steal_after
                if holder == "dead" or (holder == "unknown" and stale_by_time):
                    after = os.stat(lock_path)
                    if after.st_mtime_ns == before.st_mtime_ns and lock_path.read_bytes() == token_seen:
                        os.unlink(lock_path)
                        stole = True
            except OSError:
                pass  # lock vanished mid-check — fall through to retry (counts vs budget)
            if stole:
                continue  # freed it — retry the create immediately
            if waited >= timeout:
                raise TimeoutError(
                    f"Could not acquire the per-session lock for '{session_id}' "
                    f"within {timeout}s; another writer is holding it. Refusing "
                    "to write unlocked (fail-closed) to protect provenance "
                    "integrity. Retry, or investigate a stuck/leaked lock file."
                )
            await asyncio.sleep(poll)
            waited += poll
        try:
            yield
        finally:
            if acquired:
                try:
                    os.unlink(lock_path)
                except OSError:
                    pass

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
        file_version = raw.get("trace_version") if isinstance(raw, dict) else None
        if _is_future_schema_version(file_version):
            logger.warning(
                "Session %s declares schema version %s, newer than this server's "
                "%s. Unknown fields are preserved (extra='allow'), but this server "
                "may not understand newer semantics — upgrade trace-mcp to read it "
                "fully.",
                session_id,
                file_version,
                SCHEMA_VERSION,
            )
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

    async def session_brief(self, project: str | None = None, scan_cap: int = 25) -> dict[str, Any]:
        """Cheap, BOUNDED session orientation for the start_session bootstrap.

        Scans at most ``scan_cap`` most-recent session files (never the whole
        history) so the opening assistant turn is not tempted to fan out into
        ``trace_list_sessions``/``trace_get_events``/``trace_health_check`` —
        the per-turn block-count inflation behind the Claude Code thinking-block
        400 (see docs/upstream-claude-code-thinking-block-400.md).

        Returns: ``matched`` (matching sessions found within the scan window),
        ``most_recent`` (brief of the newest match, or None), ``scanned`` (files
        read), and ``capped`` (True when more files exist beyond the window).

        Side effects: reads up to ``scan_cap`` files from the sessions directory.
        """
        self._ensure_dir()
        files = sorted(self._dir.glob("trace_*.json"), reverse=True)
        total = len(files)
        scanned = 0
        matched = 0
        most_recent: dict[str, Any] | None = None
        for path in files[:scan_cap]:
            scanned += 1
            try:
                with open(path) as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            proj = raw.get("metadata", {}).get("project", "")
            if project and project.lower() not in proj.lower():
                continue
            matched += 1
            if most_recent is None:
                most_recent = {
                    "id": raw.get("id", path.stem),
                    "project": proj,
                    "status": raw.get("status", "unknown"),
                    "created": raw.get("created", ""),
                    "event_count": len(raw.get("events", [])),
                }
        return {
            "matched": matched,
            "most_recent": most_recent,
            "scanned": scanned,
            "capped": total > scan_cap,
        }

    async def delete_session(self, session_id: str) -> None:
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        path.unlink()
        logger.info("Deleted session file: %s", path)
