"""Shadow-period state, paths, and append-only records (ADR 008 D4).

The period is on when ``STATE.json`` under the shadow root records a start and
no stop. Only the operator's ``trace-mcp shadow`` commands write that file; the
hooks and the server only read it, and an unreadable file means "not running".

Exports:
    get_shadow_root, get_project_dir, get_store_dir
    ShadowStateError, ShadowState, ErrorRecord
    load_state, is_active, start_trial, stop_trial, set_excluded
    append_record, record_error

Side effects: ``start_trial``, ``stop_trial`` and ``set_excluded`` replace
``STATE.json`` atomically and durably; ``append_record`` and ``record_error``
append one line to a JSONL file. Everything else only reads.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from trace_mcp.durability import ensure_durable_dir, fsync_dir
from trace_mcp.project_identity import canonical_project_key

_STATE_FILE = "STATE.json"
_ERROR_MESSAGE_CHARS = 500


class ShadowStateError(Exception):
    """The shadow state is unreadable, or the requested change is not allowed."""


class ShadowState(BaseModel):
    """The contents of ``STATE.json``."""

    version: int = Field(default=1, description="State file format version.")
    started_at: datetime | None = Field(default=None, description="When the period was started.")
    stopped_at: datetime | None = Field(default=None, description="When the period was stopped.")
    excluded: list[str] = Field(default_factory=list, description="Canonical keys of projects left out.")

    def is_running(self) -> bool:
        """True when the period has started and has not been stopped."""
        return self.started_at is not None and self.stopped_at is None


class ErrorRecord(BaseModel):
    """One shadow-step failure, appended to ``<project>/errors.jsonl``."""

    ts: datetime = Field(..., description="UTC time of the failure.")
    project_key: str = Field(..., description="Canonical project key.")
    step: str = Field(..., description="The shadow step that failed.")
    error_type: str = Field(..., description="The exception's class name.")
    message: str = Field(..., description="The exception's message, at most 500 characters.")


def get_shadow_root() -> Path:
    """Return the shadow root: ``$TRACE_SHADOW_DIR``, else ``~/.trace/shadow``."""
    override = os.environ.get("TRACE_SHADOW_DIR")
    return Path(override) if override else Path.home() / ".trace" / "shadow"


def get_project_dir(project_key: str) -> Path:
    """Return the shadow directory of one project (the key is canonicalized, so it cannot leave the root)."""
    return get_shadow_root() / canonical_project_key(project_key)


def get_store_dir() -> Path:
    """Return the directory that holds the shadow knowledge stores."""
    return get_shadow_root() / "stores"


def load_state() -> ShadowState:
    """Read ``STATE.json``. A missing file is a fresh state.

    Raises:
        ShadowStateError: the file exists but cannot be read or validated.
    """
    path = get_shadow_root() / _STATE_FILE
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ShadowState()
    except OSError as exc:
        raise ShadowStateError(f"cannot read {path}: {exc}") from exc
    try:
        return ShadowState.model_validate_json(raw)
    except ValidationError as exc:
        raise ShadowStateError(f"{path} is not a valid shadow state file: {exc.error_count()} error(s)") from exc


def is_active(project_key: str) -> bool:
    """True when the period is running and ``project_key`` is not excluded. Never raises."""
    try:
        state = load_state()
        return state.is_running() and project_key not in state.excluded
    except Exception:
        return False


def _write_state(state: ShadowState) -> None:
    """Replace ``STATE.json`` atomically and durably.

    Side effects: creates the shadow root if needed; writes a temp file in the
    same directory, syncs it, renames it over ``STATE.json``, syncs the directory.
    """
    root = get_shadow_root()
    ensure_durable_dir(root)
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".STATE.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(state.model_dump_json(indent=2))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, root / _STATE_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    fsync_dir(root)


def start_trial(*, now: datetime | None = None) -> ShadowState:
    """Start the period for every project.

    Raises:
        ShadowStateError: the state is unreadable, or the period was already
            started (a stopped period cannot be restarted; the message names the stop date).
    """
    state = load_state()
    if state.started_at is not None:
        if state.stopped_at is not None:
            raise ShadowStateError(
                f"the trial was stopped on {state.stopped_at.date().isoformat()}; a second period is not supported"
            )
        raise ShadowStateError(f"the trial is already running (since {state.started_at.date().isoformat()})")
    new = state.model_copy(update={"started_at": now or datetime.now(UTC)})
    _write_state(new)
    return new


def stop_trial(*, now: datetime | None = None) -> ShadowState:
    """Stop the period.

    Raises:
        ShadowStateError: the state is unreadable, or the period is not running.
    """
    state = load_state()
    if not state.is_running():
        raise ShadowStateError("the trial is not running")
    new = state.model_copy(update={"stopped_at": now or datetime.now(UTC)})
    _write_state(new)
    return new


def set_excluded(project_key: str, excluded: bool) -> ShadowState:
    """Leave one project out of the period, or bring it back. The key is canonicalized.

    Raises:
        ShadowStateError: the state is unreadable.
    """
    key = canonical_project_key(project_key)
    state = load_state()
    keys = [k for k in state.excluded if k != key]
    if excluded:
        keys.append(key)
    new = state.model_copy(update={"excluded": sorted(keys)})
    _write_state(new)
    return new


def append_record(path: Path, record: BaseModel) -> None:
    """Append ``record`` as one JSON line to ``path``.

    Side effects: creates missing parent directories (mode 0700) and the file
    (mode 0600); writes the line in a single ``os.write`` on an ``O_APPEND``
    descriptor, so concurrent writers cannot interleave within a line.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = (record.model_dump_json() + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def record_error(project_key: str, step: str, exc: BaseException) -> None:
    """Append a shadow-step failure to the project's ``errors.jsonl``. Never raises.

    Side effects: appends one line under the shadow root; any failure of its own is swallowed.
    """
    try:
        record = ErrorRecord(
            ts=datetime.now(UTC),
            project_key=project_key,
            step=step,
            error_type=type(exc).__name__,
            message=str(exc)[:_ERROR_MESSAGE_CHARS],
        )
        append_record(get_project_dir(project_key) / "errors.jsonl", record)
    except Exception:
        return None
