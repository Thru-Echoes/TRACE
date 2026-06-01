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
from pathlib import Path

import pytest

from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import logging_tools, session_tools


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
