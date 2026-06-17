"""Regression tests for the session write/read data-integrity properties.

Cover four properties of the session storage paths:

  #1  The per-session lock must FAIL CLOSED on timeout (raise) instead of
      silently yielding unlocked, and the stale-lock steal must verify an
      identifying token before unlinking (close the TOCTOU window).
  #2  append_event must REFUSE to mint an event id that already exists in the
      reloaded on-disk session (no silent reference-aliasing), keeping the
      human-readable positional evt_NNN format.
  #3  Read aggregates (project_summary / health_check) must skip-and-report a
      schema-invalid session file (in a `skipped_sessions` list) instead of
      aborting the whole aggregate.
  #4  Loading a future-schema-version session must not silently strip and
      durably delete unknown fields on the next rewrite.

The final section covers additional hardening: lock holder-liveness steal, a
true timeout ceiling, extra='allow' at the event/nested level, version-skew
negative cases, and the under-lock end_session guard.

These are TDD regression tests: each was written RED first.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from trace_mcp.schema import SCHEMA_VERSION
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import logging_tools, query_tools, session_tools


def _write_invalid_session(directory, session_id: str, project: str) -> Path:
    """Write a VALID-JSON but SCHEMA-INVALID session file (bad status enum)."""
    path = Path(str(directory)) / f"{session_id}.json"
    path.write_text(json.dumps({
        "id": session_id,
        "status": "not-a-valid-status",  # invalid enum -> pydantic ValidationError
        "created": "2026-06-16T00:00:00Z",
        "metadata": {"project": project},
        "events": [],
    }))
    return path


@pytest.fixture
def storage(tmp_path):
    return JsonFileStorage(directory=str(tmp_path))


# ── #1 fail-closed lock ──────────────────────────────────────────────────


async def test_lock_raises_on_timeout_instead_of_proceeding_unlocked(storage, tmp_path):
    """A held (non-stale) lock must make lock() RAISE on timeout, not yield unlocked.

    The pre-fix behaviour logged a warning to stderr and proceeded unlocked —
    reopening the exact lost-update / duplicate-id window the lock exists to
    close, invisibly to the LLM and the audit record. An audit store must fail
    closed: a missed lock has to be visible to the caller.
    """
    session_id = "trace_20260616_lock01"
    storage._ensure_dir()
    lock_path = Path(str(tmp_path)) / f"{session_id}.lock"
    lock_path.write_text("held-by-another-writer")  # fresh, not stale

    with pytest.raises(TimeoutError):
        async with storage.lock(session_id, timeout=0.1, steal_after=600.0, poll=0.02):
            pass


async def test_lock_writes_identifying_token_for_steal_safety(storage, tmp_path):
    """While held, the lock file must carry an identifying token (this PID).

    An empty lock file (the pre-fix state) makes the stale-steal os.unlink a
    blind TOCTOU: the stealer cannot verify it is deleting the same lock it
    observed. A token lets the steal path re-verify before unlinking.
    """
    session_id = "trace_20260616_tok01"
    lock_path = Path(str(tmp_path)) / f"{session_id}.lock"

    async with storage.lock(session_id):
        content = lock_path.read_text()
    assert str(os.getpid()) in content, f"lock file had no PID token: {content!r}"


async def test_stale_lock_is_still_stolen_and_released(storage, tmp_path):
    """A genuinely stale lock (older than steal_after) must still be stolen.

    Guards the fail-closed change against over-correcting into a deadlock after
    a crashed holder leaks a lock file.
    """
    session_id = "trace_20260616_stale1"
    storage._ensure_dir()
    lock_path = Path(str(tmp_path)) / f"{session_id}.lock"
    lock_path.write_text("crashed-holder")
    old = time.time() - 10_000
    os.utime(lock_path, (old, old))

    async with storage.lock(session_id, timeout=0.1, steal_after=1.0):
        pass  # must not raise — the stale lock is stolen

    assert not lock_path.exists()  # released cleanly


# ── #2 duplicate-id guard ────────────────────────────────────────────────


async def test_append_refuses_to_mint_a_duplicate_event_id(storage):
    """append_event must refuse to assign an id already present on disk.

    If the on-disk session is in an aliased state (e.g. ids evt_001 + evt_003
    with len==2 so next_event_id() recomputes evt_003), minting that id again
    would silently alias revises_event_id / parent_event_id / corrects_event_ids
    references. The append must raise instead.
    """
    active: dict = {}
    session = await session_tools.create_session(storage, active, project="cc")
    await logging_tools.log_annotation(storage, session, category="gotcha", content="a")  # evt_001
    await logging_tools.log_annotation(storage, session, category="gotcha", content="b")  # evt_002

    # Corrupt the on-disk state so events are evt_001 + evt_003 (len 2 ->
    # next_event_id() == evt_003, which already exists).
    path = storage._session_path(session.id)
    raw = json.loads(path.read_text())
    raw["events"][1]["id"] = "evt_003"
    path.write_text(json.dumps(raw))

    fresh = await storage.get_session(session.id)
    with pytest.raises(ValueError, match="evt_003"):
        await logging_tools.log_annotation(storage, fresh, category="gotcha", content="c")


# ── #3 skip-and-report read aggregates ───────────────────────────────────


async def test_project_summary_skips_and_reports_schema_invalid_session(storage, tmp_path):
    """One schema-invalid file must not abort the whole project aggregate."""
    active: dict = {}
    good = await session_tools.create_session(storage, active, project="proj1")
    await logging_tools.log_annotation(storage, good, category="gotcha", content="ok")
    _write_invalid_session(tmp_path, "trace_20260616_badbad", "proj1")

    result = await query_tools.project_summary(storage, project="proj1")

    assert result["skipped_sessions"] == ["trace_20260616_badbad"]
    assert result["session_count"] == 1  # the good session is still counted


async def test_health_check_skips_and_reports_schema_invalid_session(storage, tmp_path):
    """health_check must skip-and-report a schema-invalid file, not raise."""
    active: dict = {}
    await session_tools.create_session(storage, active, project="proj2")
    _write_invalid_session(tmp_path, "trace_20260616_baddd2", "proj2")

    result = await query_tools.health_check(storage, project="proj2")

    assert result["skipped_sessions"] == ["trace_20260616_baddd2"]
    assert result["session_count"] == 1  # the good session still loaded


# ── #4 schema-version gate ───────────────────────────────────────────────


async def test_future_version_session_preserves_unknown_fields_on_rewrite(storage, tmp_path):
    """A newer-schema session's unknown fields must survive a rewrite.

    Pre-fix, Pydantic's default extra='ignore' silently stripped unknown
    top-level / nested fields on model_validate, so the next update_session
    durably deleted them — the documented upgrade path's silent-data-loss mode.
    """
    sid = "trace_20260616_futur1"
    path = Path(str(tmp_path)) / f"{sid}.json"
    path.write_text(json.dumps({
        "id": sid,
        "trace_version": "0.9.9",
        "status": "active",
        "created": "2026-06-16T00:00:00Z",
        "metadata": {"project": "p", "future_meta_field": "keep-meta"},
        "events": [],
        "future_top_level_field": "keep-top",
    }))

    s = await storage.get_session(sid)
    await storage.update_session(s)  # full-file rewrite

    raw = json.loads(path.read_text())
    assert raw.get("future_top_level_field") == "keep-top"
    assert raw.get("metadata", {}).get("future_meta_field") == "keep-meta"


async def test_get_session_warns_on_future_schema_version(storage, tmp_path, caplog):
    """Loading a newer-schema session must surface a version-skew warning."""
    import logging

    sid = "trace_20260616_futur2"
    path = Path(str(tmp_path)) / f"{sid}.json"
    path.write_text(json.dumps({
        "id": sid,
        "trace_version": "0.9.9",
        "status": "active",
        "created": "2026-06-16T00:00:00Z",
        "metadata": {"project": "p"},
        "events": [],
    }))

    with caplog.at_level(logging.WARNING):
        await storage.get_session(sid)

    assert any("0.9.9" in r.message or "version" in r.message.lower() for r in caplog.records)


# ── additional hardening ────────────────────────────────────────────────


def _dead_pid() -> int:
    """A PID that is guaranteed dead: spawn a trivial child and reap it."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


async def test_dead_holder_lock_is_stolen_immediately(storage, tmp_path):
    """A lock whose holder PID is dead is reclaimed at once, regardless of age.

    Steal must key on holder LIVENESS, not just wall-clock age: the default
    steal_after (60s) would otherwise force a long wait (or a TimeoutError on a
    short timeout) to reclaim a freshly-crashed holder's lock.
    """
    session_id = "trace_20260616_deadpid"
    storage._ensure_dir()
    lock_path = Path(str(tmp_path)) / f"{session_id}.lock"
    lock_path.write_text(f"{_dead_pid()}:0")  # fresh mtime, but holder is dead

    async with storage.lock(session_id, timeout=2.0):  # << steal_after (60s)
        pass

    assert not lock_path.exists()


async def test_live_holder_lock_is_never_stolen(storage, tmp_path):
    """A lock whose holder PID is alive must NEVER be stolen, even if old.

    The pre-hardening code stole any lock older than steal_after purely on
    mtime — silently evicting a legitimately long-running holder.
    """
    session_id = "trace_20260616_livepid"
    storage._ensure_dir()
    lock_path = Path(str(tmp_path)) / f"{session_id}.lock"
    lock_path.write_text(f"{os.getpid()}:0")  # this very test process is alive
    old = time.time() - 10_000
    os.utime(lock_path, (old, old))  # and the lock looks very old

    with pytest.raises(TimeoutError):
        async with storage.lock(session_id, timeout=0.1, steal_after=1.0):
            pass

    assert lock_path.exists()  # the live holder's lock was left intact


async def test_lock_state_after_timeout_then_clean_reacquire(storage, tmp_path):
    """After a fail-closed timeout the foreign lock survives; a later acquire works."""
    session_id = "trace_20260616_react1"
    storage._ensure_dir()
    lock_path = Path(str(tmp_path)) / f"{session_id}.lock"
    lock_path.write_text(f"{os.getpid()}:0")  # alive -> not stolen

    with pytest.raises(TimeoutError):
        async with storage.lock(session_id, timeout=0.1, steal_after=600.0):
            pass
    assert lock_path.exists()  # untouched

    lock_path.unlink()  # holder releases
    async with storage.lock(session_id, timeout=0.1):
        pass
    assert not lock_path.exists()  # reacquired and released cleanly


async def test_extra_allow_preserves_unknown_event_and_nested_fields(storage, tmp_path):
    """extra='allow' must preserve unknown fields at the EVENT and NESTED-data
    level (not just top-level / metadata) through a rewrite."""
    sid = "trace_20260616_xtraev"
    path = Path(str(tmp_path)) / f"{sid}.json"
    path.write_text(json.dumps({
        "id": sid,
        "trace_version": "0.9.9",
        "status": "active",
        "created": "2026-06-16T00:00:00Z",
        "metadata": {"project": "p"},
        "events": [{
            "id": "evt_001",
            "session_id": sid,
            "type": "annotation",
            "timestamp": "2026-06-16T00:00:00Z",
            "actor": {"type": "ai", "id": "claude"},
            "annotation": {"category": "gotcha", "content": "x", "future_anno_field": "keep-anno"},
            "future_event_field": "keep-evt",
        }],
    }))

    s = await storage.get_session(sid)
    await storage.update_session(s)

    raw = json.loads(path.read_text())
    ev = raw["events"][0]
    assert ev.get("future_event_field") == "keep-evt"
    assert ev["annotation"].get("future_anno_field") == "keep-anno"


async def test_no_version_skew_warning_for_equal_or_older_version(storage, tmp_path, caplog):
    """A same-version or older session must NOT trigger the skew warning."""
    import logging

    for ver in (SCHEMA_VERSION, "0.3.0"):
        sid = f"trace_20260616_v{ver.replace('.', '')}"
        path = Path(str(tmp_path)) / f"{sid}.json"
        path.write_text(json.dumps({
            "id": sid,
            "trace_version": ver,
            "status": "active",
            "created": "2026-06-16T00:00:00Z",
            "metadata": {"project": "p"},
            "events": [],
        }))
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            await storage.get_session(sid)
        assert not any("newer than this server" in r.message for r in caplog.records), (
            f"unexpected version-skew warning for {ver}"
        )


async def test_end_session_refuses_when_disk_already_completed(storage):
    """The under-lock disk-truth guard must block ending an already-completed
    session via a stale in-memory handle (and must not overwrite the summary)."""
    active: dict = {}
    session = await session_tools.create_session(storage, active, project="cc")
    await session_tools.end_session(storage, active, session_id=session.id, summary="first")

    # A stale handle that still believes the session is active.
    stale = await storage.get_session(session.id)
    stale.status = "active"
    stale.ended = None
    active2 = {session.id: stale}

    result = await session_tools.end_session(
        storage, active2, session_id=session.id, summary="SECOND"
    )

    assert "already ended" in result
    disk = await storage.get_session(session.id)
    assert disk.summary == "first"  # the second end did not clobber it


async def test_locked_disk_session_without_lock_method():
    """The helper degrades to a no-op context for a storage backend with no lock."""
    from trace_mcp.schema import Session, SessionMetadata
    from trace_mcp.storage.locked import locked_disk_session

    class NoLockStore:
        def __init__(self) -> None:
            self._store: dict = {}

        async def get_session(self, sid: str) -> Session:
            if sid not in self._store:
                raise FileNotFoundError(sid)
            return self._store[sid]

        async def update_session(self, s: Session) -> None:
            self._store[s.id] = s

    store = NoLockStore()
    mem = Session(id="trace_nolock", metadata=SessionMetadata(project="p"))

    # Not persisted -> yields the fallback (by identity).
    async with locked_disk_session(store, "trace_nolock", fallback=mem) as disk:
        assert disk is mem
    store._store["trace_nolock"] = mem
    # Persisted -> yields the disk-loaded object.
    async with locked_disk_session(store, "trace_nolock", fallback=mem) as disk:
        assert disk is mem  # same object the stub returns
