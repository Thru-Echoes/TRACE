"""JSON file storage backend for TRACE sessions.

One JSON file per session, stored in ~/.trace/sessions/ by default.
Each file is a self-contained, valid TRACE document.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trace_mcp.durability import ensure_durable_dir, fsync_dir
from trace_mcp.schema import SCHEMA_VERSION, Session
from trace_mcp.storage.base import SessionIdTakenError, TraceStorage

logger = logging.getLogger(__name__)

_DEFAULT_DIR = os.path.expanduser("~/.trace/sessions")

# On Windows, os.kill(pid, 0) terminates the process instead of probing it.
_PID_PROBE_IS_SAFE = os.name != "nt"

# os.link errors meaning hard-link publication is unavailable here, because the
# filesystem lacks hard links or a policy denies them. Anything else (a full
# disk, an I/O error) is a real failure and must reach the caller.
_HARD_LINKS_UNSUPPORTED = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS})


def _write_and_fsync(fd: int, data: str) -> None:
    """Write ``data`` to an open descriptor and force it to disk.

    Side effects: writes to and closes ``fd``.
    """
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


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
    if pid <= 0 or not _PID_PROBE_IS_SAFE:
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
    """Sanitize a session ID or project name for safe filesystem use.

    The mapping is lossy: distinct names can produce one filename. Session reads
    and writes use ``_is_alias`` to keep those names from reaching each other's
    records (INV-15).
    """
    cleaned = re.sub(r"[^\w\-.]", "_", name)  # only alphanum, _, -, .
    cleaned = cleaned.replace("..", "_")  # no parent traversal
    cleaned = cleaned.lstrip(".")  # no hidden files
    # Raise if the original had no usable characters (e.g. "////")
    if not cleaned or not re.search(r"[\w\-.]", name):
        raise ValueError(f"Name sanitizes to empty string: {name!r}")
    return cleaned


def _read_stored_id(path: Path) -> object:
    """The ``id`` field of the session file at ``path``, or None if the file is corrupt.

    Raises:
        OSError: the file could not be read, so its owner is unknown.
    """
    try:
        with open(path) as f:
            raw = json.load(f)
    except ValueError:
        return None
    return raw.get("id") if isinstance(raw, dict) else None


def _read_listable_session(path: Path) -> dict[str, Any] | None:
    """The parsed session at ``path``, or None if a listing should leave it out.

    One bad file must not take down every listing, so a vanished, corrupt, or
    misshapen file is skipped.
    """
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        logger.warning("Skipping malformed or vanished session file: %s", path)
        return None
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("metadata", {}), dict)
        or not isinstance(raw.get("events", []), list)
    ):
        logger.warning("Skipping malformed session file: %s", path)
        return None
    return raw


def _created_at(raw: dict[str, Any]) -> datetime:
    """A session's stored creation time, or the earliest datetime if missing or invalid.

    A timestamp with no offset is read as UTC, the timezone TRACE records in.
    """
    try:
        parsed = datetime.fromisoformat(raw.get("created"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _session_day(path: Path) -> str:
    """The ``trace_<YYYYMMDD>`` prefix of a session filename."""
    return "_".join(path.stem.split("_", 2)[:2])


def _recency_key(path: Path) -> tuple[str, int, str]:
    """Sort key for reading the likely-newest session files first.

    A session id holds its creation day and then random hex, so the name orders
    days and nothing finer. Within a day the last write stands in for creation
    order, which lets a bounded scan start at the right files without opening
    them. A later update to an older session moves it ahead, so this is a proxy.
    The filename breaks exact ties so the order is deterministic.
    """
    day = _session_day(path)
    try:
        last_write = path.stat().st_mtime_ns
    except OSError:
        last_write = 0
    return day, last_write, path.name


class JsonFileStorage(TraceStorage):
    """Stores TRACE sessions as individual JSON files."""

    def __init__(self, directory: str | None = None) -> None:
        self._dir = Path(directory or os.environ.get("TRACE_SESSIONS_DIR", _DEFAULT_DIR))

    def _ensure_dir(self) -> None:
        ensure_durable_dir(self._dir)

    def _session_files_newest_first(self) -> list[Path]:
        return sorted(self._dir.glob("trace_*.json"), key=_recency_key, reverse=True)

    def _session_path(self, session_id: str) -> Path:
        return self._dir / f"{sanitize_name(session_id)}.json"

    def _is_alias(self, path: Path, stored_id: object, requested_id: str) -> bool:
        """True when the record at ``path`` holds a different id that resolves to the same file.

        The filesystem decides, so case and Unicode normalization count only where
        it treats those names as one file. A stored id with a file of its own is
        not an alias, so an older file whose name differs from its id stays readable.

        Args:
            path: the file ``requested_id`` resolved to.
            stored_id: the ``id`` read from it; anything but a string is ignored.
            requested_id: the id the caller asked for.

        Raises:
            OSError: the comparison failed, so the record's owner is unknown.
        """
        if not isinstance(stored_id, str) or stored_id == requested_id:
            return False
        try:
            return self._session_path(stored_id).samefile(path)
        except (FileNotFoundError, ValueError):
            return False  # the stored id has no file of its own, or no valid filename
        except OSError as e:
            if e.errno == errno.ENAMETOOLONG:
                return False  # no file can have that name
            raise

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
        provenance record. The directory is then synced so the new name survives
        a power loss (INV-14). Cross-platform: no fcntl.
        """
        self._ensure_dir()
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            _write_and_fsync(fd, data)
            os.replace(tmp_path, str(path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        fsync_dir(path.parent)

    def _write_file_exclusive(self, path: Path, data: str) -> None:
        """Create ``path`` holding ``data``, refusing to replace an existing file.

        The bytes are fsynced to a temp file and published with ``os.link``, so
        a reader never sees a partial record and the name is claimed atomically.
        Where hard-link publication is unavailable, this falls back to writing
        through an ``O_CREAT | O_EXCL`` descriptor, which claims the name just as
        atomically but exposes the file while it is being written.

        A failed fallback write leaves the claimed file in place: once the name is
        visible another writer may have replaced it, so unlinking by path could
        delete that writer's record. A failed directory sync leaves it for the same
        reason.

        Side effects: creates ``path``; removes the temp file; syncs the directory.

        Raises:
            SessionIdTakenError: ``path`` already exists; it is left untouched.
            OSError: the write or the directory sync failed for any other reason.
        """
        self._ensure_dir()
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        published = False
        try:
            _write_and_fsync(fd, data)
            try:
                os.link(tmp_path, str(path))
                published = True
            except FileExistsError:
                raise SessionIdTakenError(f"Session file already exists: {path}") from None
            except OSError as e:
                if e.errno not in _HARD_LINKS_UNSUPPORTED:
                    raise
                logger.warning("Hard links unavailable in %s; creating with O_EXCL", path.parent)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

        if not published:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise SessionIdTakenError(f"Session file already exists: {path}") from None
            _write_and_fsync(fd, data)
        fsync_dir(path.parent)

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

        **Fail closed:** if the lock cannot be acquired within
        ``timeout`` we ``raise TimeoutError`` rather than proceed unlocked. An
        audit store must never silently degrade to an unsynchronized write —
        that reopens the lost-update / duplicate-evt_id window the lock exists
        to close, invisibly to the caller and the record. The lock is
        single-host advisory only (``O_EXCL`` does not synchronize across NFS /
        distinct mounts); TRACE's deployment is a local per-user ``~/.trace``.

        The lock file carries an identifying token (``<pid>:<time_ns>``) so the
        stale-steal path can re-verify the lock is byte- and mtime-identical to
        the one it judged stale before unlinking it. That narrows the window where
        a writer could delete another writer's *fresh* lock but does not close it,
        because the check and the unlink are separate steps.

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
        """Persist a new session. Raises ``SessionIdTakenError`` if the id is taken (INV-13)."""
        path = self._session_path(session.id)
        data = json.dumps(session.model_dump(mode="json"), indent=2)
        self._write_file_exclusive(path, data)
        logger.info("Created session file: %s", path)
        return session.id

    async def update_session(self, session: Session) -> None:
        path = self._session_path(session.id)
        if not path.exists():
            raise FileNotFoundError(f"Session file not found: {path}")
        if self._is_alias(path, _read_stored_id(path), session.id):
            raise FileNotFoundError(f"Session not found: {session.id}")
        data = json.dumps(session.model_dump(mode="json"), indent=2)
        self._write_file(path, data)

    async def get_session(self, session_id: str) -> Session:
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        with open(path) as f:
            raw = json.load(f)
        if isinstance(raw, dict) and self._is_alias(path, raw.get("id"), session_id):
            raise FileNotFoundError(f"Session not found: {session_id}")
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
        # INV-4: filter by CANONICAL project key (not raw case-sensitive label),
        # so display-label drift (TRACE vs trace-mcp, coeqwal vs COEQWAL) resolves
        # to ONE project and cannot split one project or merge two. Lazy import
        # avoids a json_file <-> project_identity module cycle.
        from trace_mcp.project_identity import key_for_label, session_project_key

        self._dir.mkdir(parents=True, exist_ok=True)  # a read must not fail on a durability sync
        query_key = key_for_label(project) if project else None
        summaries: list[dict[str, Any]] = []
        previous_day: str | None = None
        for path in self._session_files_newest_first():
            # A filename orders days only, so finish a day before applying the limit.
            day = _session_day(path)
            if len(summaries) >= limit and day != previous_day:
                break
            previous_day = day
            raw = _read_listable_session(path)
            if raw is None:
                continue
            meta = raw.get("metadata", {})
            if query_key is not None and session_project_key(meta) != query_key:
                continue
            summaries.append(
                {
                    "id": raw.get("id", path.stem),
                    "project": meta.get("project", ""),
                    "status": raw.get("status", "unknown"),
                    "created": raw.get("created", ""),
                    "event_count": len(raw.get("events", [])),
                }
            )
        summaries.sort(key=_created_at, reverse=True)
        return summaries[:limit]

    async def session_brief(
        self, project: str | None = None, scan_cap: int = 25, read_ceiling: int = 200
    ) -> dict[str, Any]:
        """Cheap, BOUNDED session orientation for the start_session bootstrap.

        Newest-first scan that stops at ``scan_cap`` matches or after examining
        ``read_ceiling`` files, whichever comes first — never the whole history,
        so the opening assistant turn is not tempted to fan out into
        ``trace_list_sessions``/``trace_get_events``/``trace_health_check`` —
        the per-turn block-count inflation behind the Claude Code thinking-block
        400 (see docs/upstream-claude-code-thinking-block-400.md).

        The former single ``scan_cap``-file window made the brief assert a false
        absolute: a project whose newest session sat 26 files back in a busy
        shared store read as "no prior sessions". Matches are now sought through
        a much deeper (still bounded) window, and when even that window ends
        with more files beyond it, ``window_exhausted`` says so — the caller can
        report "none found in the newest N" instead of "none exist".

        ``most_recent`` is the match with the latest stored ``created`` time among
        the files read. A session id orders days and nothing finer, so within a day
        files are read most recently written first. If ``scan_cap`` matches were
        written after the newest session was, that session is not read. Only JSON
        reads are bounded; listing the directory and one ``stat`` per file are not.

        Returns: ``matched``, ``most_recent`` (brief of the newest match, or
        None), ``scanned`` (files read), ``capped`` (more files exist beyond
        what was scanned), ``window_exhausted`` (the read ceiling was hit with
        more files beyond it), and ``read_ceiling``.

        Side effects: reads up to ``read_ceiling`` files from the sessions dir.
        """
        # INV-4: canonical-key match, same as list_sessions (lazy import breaks
        # the json_file <-> project_identity cycle).
        from trace_mcp.project_identity import key_for_label, session_project_key

        self._dir.mkdir(parents=True, exist_ok=True)  # a read must not fail on a durability sync
        query_key = key_for_label(project) if project else None
        files = self._session_files_newest_first()
        total = len(files)
        scanned = 0
        matched = 0
        most_recent: dict[str, Any] | None = None
        newest_at: datetime | None = None
        for path in files[:read_ceiling]:
            if matched >= scan_cap:
                break
            scanned += 1
            raw = _read_listable_session(path)
            if raw is None:
                continue
            meta = raw.get("metadata", {})
            if query_key is not None and session_project_key(meta) != query_key:
                continue
            proj = meta.get("project", "")
            matched += 1
            created_at = _created_at(raw)
            if newest_at is None or created_at > newest_at:
                newest_at = created_at
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
            "capped": total > scanned,
            "window_exhausted": scanned >= read_ceiling and total > read_ceiling,
            "read_ceiling": read_ceiling,
        }

    async def delete_session(self, session_id: str) -> None:
        """Delete a session under its lock, so it cannot interleave with a locked update (INV-15).

        The lock is not reentrant: calling this while holding it times out.

        Side effects: removes the session file; syncs the directory.

        Raises:
            FileNotFoundError: no record holds ``session_id``.
            TimeoutError: the session lock could not be acquired.
        """
        async with self.lock(session_id):
            path = self._session_path(session_id)
            if not path.exists() or self._is_alias(path, _read_stored_id(path), session_id):
                raise FileNotFoundError(f"Session not found: {session_id}")
            path.unlink()
            fsync_dir(path.parent)
        logger.info("Deleted session file: %s", path)
