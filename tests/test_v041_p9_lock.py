"""P9(b) / Round-3 A-R3-2: per-project cross-process knowledge-store lock.

The shared store is read-modify-write. Without a lock around the full
load→mutate→save span, two concurrent sessions mutating the SAME project
silently lose one update (last-writer-wins). store.project_lock() must
serialize that span; and it must degrade gracefully (no-op, no crash) if
the optional `filelock` dependency is unavailable.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from trace_mcp.extensions.learn import store


def _add_under_lock(project: str, directory: str, content: str) -> None:
    with store.project_lock(project, directory=directory):
        ks = store.load_store(project, directory=directory)
        store.add_learning(ks, content=content, tags=["t"])
        time.sleep(0.05)  # widen the race window
        store.save_store(ks, directory=directory)


def test_concurrent_adds_do_not_lose_updates(tmp_path: Path) -> None:
    """Two threads adding to the same project concurrently: WITH the lock
    serializing the load→mutate→save span, BOTH learnings must persist."""
    pytest.importorskip("filelock")
    d = str(tmp_path)
    t1 = threading.Thread(target=_add_under_lock, args=("concur", d, "alpha"))
    t2 = threading.Thread(target=_add_under_lock, args=("concur", d, "beta"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    ks = store.load_store("concur", directory=d)
    contents = {lrn.content for lrn in ks.learnings}
    assert contents == {"alpha", "beta"}, (
        f"lost update — lock did not serialize the RMW span: {contents}"
    )


def test_project_lock_graceful_without_filelock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If filelock is unavailable the context manager is a safe no-op."""
    monkeypatch.setitem(sys.modules, "filelock", None)
    # Reset the one-time-warning flag so the fallback path is exercised.
    monkeypatch.setattr(store, "_warned_no_filelock", False, raising=False)
    entered = False
    with store.project_lock("p", directory=str(tmp_path)):
        entered = True
    assert entered  # yielded exactly once, no exception


def test_project_lock_is_per_project(tmp_path: Path) -> None:
    """Different projects use distinct lock files (no cross-project
    contention with many concurrent sessions)."""
    pytest.importorskip("filelock")
    d = str(tmp_path)
    with store.project_lock("proj-a", directory=d):
        # A different project must be independently lockable while proj-a
        # is held (would deadlock/raise if it were one global lock).
        with store.project_lock("proj-b", directory=d):
            pass
