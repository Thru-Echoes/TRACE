"""v0.4.2 Phase 4: storage lost-update / event-ID-collision regression.

append_event did an unsynchronized read-modify-write of the whole session file
with positional evt_{len+1} IDs. Two writers (separate processes, or one process
holding a stale in-memory Session) each wrote N+1 events, so the later write
clobbered the other's event AND both were assigned the same evt_id — silent
provenance loss, the exact thing TRACE promises never to do.

Fix: append_event reloads the authoritative on-disk events under a per-session
file lock before appending, and writes are fsynced. These tests pin that the
data-loss and ID-collision can no longer happen.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import decision_tools, logging_tools, session_tools

# A self-contained worker run in a separate OS process (via `python -c`) to
# exercise the per-session lock under the real cross-process threat model.
_CROSS_PROCESS_WORKER = """
import asyncio
import sys

from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import logging_tools


async def main() -> None:
    directory, session_id, worker_id, n_events = (
        sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    )
    storage = JsonFileStorage(directory=directory)
    session = await storage.get_session(session_id)
    for i in range(n_events):
        await logging_tools.log_annotation(
            storage, session, category="gotcha", content=f"w{worker_id}-e{i}",
        )


asyncio.run(main())
"""


@pytest.fixture
def storage(tmp_path):
    return JsonFileStorage(directory=str(tmp_path))


async def test_append_reloads_disk_state_no_lost_update(storage):
    """A writer with a STALE in-memory view must not clobber on-disk events."""
    active: dict = {}
    session = await session_tools.create_session(storage, active, project="cc")
    await logging_tools.log_annotation(storage, session, category="gotcha", content="from A")

    # Simulate a second writer that never observed A's event.
    stale = await storage.get_session(session.id)
    stale.events = []
    await logging_tools.log_annotation(storage, stale, category="gotcha", content="from B")

    final = await storage.get_session(session.id)
    contents = [e.annotation.content for e in final.events if e.annotation]
    assert "from A" in contents and "from B" in contents
    assert len(final.events) == 2
    assert len({e.id for e in final.events}) == 2  # no ID collision


async def test_concurrent_appends_no_loss_or_collision(storage):
    """Two concurrent appends on independent Session views both survive."""
    active: dict = {}
    session = await session_tools.create_session(storage, active, project="cc")
    s1 = await storage.get_session(session.id)
    s2 = await storage.get_session(session.id)

    await asyncio.gather(
        logging_tools.log_annotation(storage, s1, category="gotcha", content="A"),
        logging_tools.log_annotation(storage, s2, category="gotcha", content="B"),
    )

    final = await storage.get_session(session.id)
    assert len(final.events) == 2
    assert len({e.id for e in final.events}) == 2


async def test_lock_file_is_released(storage, tmp_path):
    active: dict = {}
    session = await session_tools.create_session(storage, active, project="cc")
    await logging_tools.log_annotation(storage, session, category="gotcha", content="x")
    assert list(Path(tmp_path).glob("*.lock")) == []  # no leftover lock


async def test_completed_session_still_immutable(storage):
    active: dict = {}
    session = await session_tools.create_session(storage, active, project="cc")
    await session_tools.end_session(storage, active, session_id=session.id)
    reloaded = await storage.get_session(session.id)
    with pytest.raises(ValueError, match="completed"):
        await logging_tools.log_annotation(storage, reloaded, category="gotcha", content="late")


async def test_end_session_preserves_concurrent_append(storage):
    """end_session writing a stale in-memory view must not clobber a concurrent append.

    Symmetry with append_event: the v0.4.2 lock closed append<->append, this
    pins append<->end. The active-session view held by end_session is stale
    (missing an event another writer persisted); end_session must reload the
    authoritative disk events under the lock before writing the completed state.
    """
    active: dict = {}
    session = await session_tools.create_session(storage, active, project="cc")
    await logging_tools.log_annotation(storage, session, category="gotcha", content="A")

    # A second writer appends B to disk; the active `session` view never saw it.
    stale = await storage.get_session(session.id)
    await logging_tools.log_annotation(storage, stale, category="gotcha", content="B")

    # End using the original (now stale) active-session view.
    await session_tools.end_session(storage, active, session_id=session.id)

    final = await storage.get_session(session.id)
    contents = [e.annotation.content for e in final.events if e.annotation]
    assert "A" in contents and "B" in contents
    assert len(final.events) == 2
    assert len({e.id for e in final.events}) == 2  # no ID collision
    assert final.status == "completed"


async def test_resolve_decision_preserves_concurrent_append(storage):
    """resolve_decision writing a stale view must not clobber a concurrent append."""
    active: dict = {}
    session = await session_tools.create_session(storage, active, project="cc")
    d_id = await decision_tools.propose_decision(
        storage,
        session,
        description="use method X",
        proposed_by_type="ai",
        proposed_by_id="claude",
    )

    # Another writer appends an annotation to disk; `session` view doesn't see it.
    stale = await storage.get_session(session.id)
    await logging_tools.log_annotation(storage, stale, category="gotcha", content="concurrent")

    # Resolve the decision using the (now stale) session view.
    await decision_tools.resolve_decision(
        storage,
        session,
        event_id=d_id,
        disposition="accepted",
        resolved_by_type="human",
        resolved_by_id="researcher",
    )

    final = await storage.get_session(session.id)
    contents = [e.annotation.content for e in final.events if e.annotation]
    assert "concurrent" in contents  # the concurrent append survived
    decision_evt = next(e for e in final.events if e.id == d_id)
    assert decision_evt.decision is not None
    assert decision_evt.decision.disposition == "accepted"
    assert len(final.events) == 2


def test_cross_process_concurrent_appends_no_loss(tmp_path):
    """Real OS processes appending concurrently to one session lose nothing.

    The single-process asyncio tests above cannot exercise the actual deployment
    threat model — separate processes contending for the same session file. This
    spawns N real subprocesses that each append M events through the storage API
    and asserts the O_CREAT|O_EXCL per-session lock serialized them with zero
    lost writes and zero positional-id collisions.
    """
    directory = str(tmp_path)

    async def _create() -> str:
        storage = JsonFileStorage(directory=directory)
        active: dict = {}
        session = await session_tools.create_session(storage, active, project="cc")
        return session.id

    session_id = asyncio.run(_create())

    src = str(Path(__file__).resolve().parent.parent / "src")
    env = os.environ.copy()
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    # Keep the workers offline/fast — annotations never touch embeddings, but be explicit.
    env["TRACE_EMBEDDING_BACKEND"] = "none"
    env["TRACE_LLM_ENABLED"] = "false"

    n_workers, n_events = 4, 10
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _CROSS_PROCESS_WORKER, directory, session_id, str(w), str(n_events)],
            env=env,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True,
        )
        for w in range(n_workers)
    ]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, f"worker process failed:\n{err}"

    async def _load():
        return await JsonFileStorage(directory=directory).get_session(session_id)

    final = asyncio.run(_load())
    assert len(final.events) == n_workers * n_events  # no lost writes across processes
    assert len({e.id for e in final.events}) == n_workers * n_events  # no positional-id collision
