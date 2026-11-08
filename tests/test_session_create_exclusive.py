"""Session creation never replaces an existing session record (INV-13).

An id collision used to reach ``os.replace`` and destroy the earlier session
without leaving any sign that it had existed. These tests run against the real
JSON backend in a temporary directory; the only substitutions are a fixed id
generator (to force a collision that is otherwise improbable) and a failing
``os.link`` (to reach the code path for filesystems without hard links).
"""

from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path
from typing import Any

import pytest

from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.storage.base import SessionIdTakenError
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import session_tools

TAKEN_ID = "trace_20260101_abc123"
FRESH_ID = "trace_20260101_def456"


def _session(session_id: str, project: str) -> Session:
    return Session(id=session_id, metadata=SessionMetadata(project=project))


def _no_link(*_args: Any, **_kwargs: Any) -> None:
    raise OSError(errno.EPERM, "hard links are not supported on this filesystem")


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(directory=str(tmp_path))


async def test_second_create_with_same_id_is_refused_and_first_record_is_untouched(
    storage: JsonFileStorage, tmp_path: Path
) -> None:
    await storage.create_session(_session(TAKEN_ID, "collab-a"))
    path = tmp_path / f"{TAKEN_ID}.json"
    original = path.read_bytes()

    with pytest.raises(FileExistsError):
        await storage.create_session(_session(TAKEN_ID, "collab-b"))

    assert path.read_bytes() == original


async def test_refused_create_leaves_no_temp_files(storage: JsonFileStorage, tmp_path: Path) -> None:
    await storage.create_session(_session(TAKEN_ID, "collab-a"))

    with pytest.raises(FileExistsError):
        await storage.create_session(_session(TAKEN_ID, "collab-b"))

    assert [p.name for p in tmp_path.iterdir()] == [f"{TAKEN_ID}.json"]


async def test_ids_that_sanitize_to_one_filename_cannot_overwrite_each_other(
    storage: JsonFileStorage, tmp_path: Path
) -> None:
    await storage.create_session(_session("trace_a_b", "collab-a"))
    path = tmp_path / "trace_a_b.json"
    original = path.read_bytes()

    with pytest.raises(FileExistsError):
        await storage.create_session(_session("trace_a/b", "collab-b"))

    assert path.read_bytes() == original


async def test_concurrent_creators_of_one_id_produce_exactly_one_winner(
    storage: JsonFileStorage, tmp_path: Path
) -> None:
    contenders = [_session(TAKEN_ID, f"collab-{i}") for i in range(16)]

    results = await asyncio.gather(
        *(asyncio.to_thread(asyncio.run, storage.create_session(s)) for s in contenders),
        return_exceptions=True,
    )

    wins = [i for i, r in enumerate(results) if r == TAKEN_ID]
    assert len(wins) == 1
    assert all(isinstance(r, FileExistsError) for i, r in enumerate(results) if i != wins[0])
    stored = await storage.get_session(TAKEN_ID)
    assert stored.metadata.project == f"collab-{wins[0]}"
    assert [p.name for p in tmp_path.iterdir()] == [f"{TAKEN_ID}.json"]


async def test_create_is_a_complete_valid_record_without_hard_links(
    storage: JsonFileStorage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "link", _no_link)

    await storage.create_session(_session(TAKEN_ID, "collab-a"))

    assert (await storage.get_session(TAKEN_ID)).metadata.project == "collab-a"
    assert [p.name for p in tmp_path.iterdir()] == [f"{TAKEN_ID}.json"]


async def test_refusal_holds_without_hard_links(
    storage: JsonFileStorage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "link", _no_link)
    await storage.create_session(_session(TAKEN_ID, "collab-a"))
    path = tmp_path / f"{TAKEN_ID}.json"
    original = path.read_bytes()

    with pytest.raises(FileExistsError):
        await storage.create_session(_session(TAKEN_ID, "collab-b"))

    assert path.read_bytes() == original
    assert [p.name for p in tmp_path.iterdir()] == [f"{TAKEN_ID}.json"]


async def test_a_real_link_failure_is_raised_and_does_not_fall_back(
    storage: JsonFileStorage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def disk_full(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr(os, "link", disk_full)

    with pytest.raises(OSError) as raised:
        await storage.create_session(_session(TAKEN_ID, "collab-a"))

    assert raised.value.errno == errno.ENOSPC
    assert list(tmp_path.iterdir()) == []


async def test_update_still_replaces_an_existing_record(storage: JsonFileStorage) -> None:
    session = _session(TAKEN_ID, "collab-a")
    await storage.create_session(session)

    session.metadata.description = "edited"
    await storage.update_session(session)

    assert (await storage.get_session(TAKEN_ID)).metadata.description == "edited"


async def test_tool_path_mints_a_fresh_id_on_collision(
    storage: JsonFileStorage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await storage.create_session(_session(TAKEN_ID, "collab-a"))
    path = tmp_path / f"{TAKEN_ID}.json"
    original = path.read_bytes()
    minted = iter([TAKEN_ID, FRESH_ID])
    monkeypatch.setattr(session_tools, "_generate_session_id", lambda: next(minted))
    active: dict[str, Session] = {}

    session = await session_tools.create_session(storage, active, project="collab-b")

    assert session.id == FRESH_ID
    assert path.read_bytes() == original
    assert (await storage.get_session(FRESH_ID)).metadata.project == "collab-b"
    assert list(active) == [FRESH_ID]


async def test_tool_path_fails_loudly_when_every_minted_id_is_taken(
    storage: JsonFileStorage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await storage.create_session(_session(TAKEN_ID, "collab-a"))
    path = tmp_path / f"{TAKEN_ID}.json"
    original = path.read_bytes()
    calls: list[str] = []

    def always_taken() -> str:
        calls.append(TAKEN_ID)
        return TAKEN_ID

    monkeypatch.setattr(session_tools, "_generate_session_id", always_taken)
    active: dict[str, Session] = {}

    with pytest.raises(SessionIdTakenError, match="unused session id"):
        await session_tools.create_session(storage, active, project="collab-b")

    assert len(calls) == session_tools.SESSION_ID_ATTEMPTS
    assert path.read_bytes() == original
    assert active == {}


async def test_a_store_path_that_is_a_file_is_not_reported_as_a_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_a_directory = tmp_path / "sessions"
    not_a_directory.write_text("occupied")
    calls: list[str] = []

    def mint() -> str:
        calls.append(FRESH_ID)
        return FRESH_ID

    monkeypatch.setattr(session_tools, "_generate_session_id", mint)
    active: dict[str, Session] = {}

    with pytest.raises(OSError) as raised:
        await session_tools.create_session(JsonFileStorage(directory=str(not_a_directory)), active, project="collab-a")

    assert not isinstance(raised.value, SessionIdTakenError)
    assert len(calls) == 1
    assert active == {}
