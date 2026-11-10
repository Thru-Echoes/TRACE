"""Durable publication (INV-14), one record per session id (INV-15), and recency.

These tests run the real JSON backend in a temporary directory. A power loss cannot
be staged, so durability is checked at the one observable point: ``os.fsync`` is
wrapped, still runs, and records the directory's state each time a directory is
synced. A sync that happens before the name changes would record the old state.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from trace_mcp import durability, lockfile, project_identity
from trace_mcp.extensions.learn import store as learn_store
from trace_mcp.extensions.learn.models import KnowledgeStore
from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.storage import json_file
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.storage.locked import locked_disk_session
from trace_mcp.tools import session_tools

SESSION_ID = "trace_20260101_abc123"
NOON = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _session(session_id: str = SESSION_ID, project: str = "collab-a", created: datetime = NOON) -> Session:
    return Session(id=session_id, metadata=SessionMetadata(project=project), created=created)


def _no_link(*_args: Any, **_kwargs: Any) -> None:
    raise OSError(errno.EPERM, "hard links are not supported on this filesystem")


needs_directory_sync = pytest.mark.skipif(
    not durability._DIRECTORY_FSYNC_AVAILABLE, reason="this platform has no directory fsync"
)


@pytest.fixture(autouse=True)
def _fresh_durability_record() -> None:
    """Inode numbers can repeat between temporary directories."""
    durability._durable_directories.clear()


class FsyncSpy:
    """What was on disk each time a directory or file was synced."""

    def __init__(self) -> None:
        self.directory_inodes: list[int] = []
        self.directory_states: list[dict[str, bytes]] = []
        self.file_syncs = 0


@pytest.fixture
def fsyncs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FsyncSpy:
    spy = FsyncSpy()
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            spy.directory_inodes.append(info.st_ino)
            spy.directory_states.append(
                {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file() and p.suffix == ".json"}
            )
        else:
            spy.file_syncs += 1
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    return spy


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(directory=str(tmp_path))


@pytest.fixture(params=["hard_link", "no_hard_link"])
def publication(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "no_hard_link":
        monkeypatch.setattr(os, "link", _no_link)
    return request.param


@needs_directory_sync
class TestDirectoryDurability:
    async def test_create_syncs_the_directory_after_the_name_exists(
        self, storage: JsonFileStorage, fsyncs: FsyncSpy, publication: str
    ) -> None:
        await storage.create_session(_session())
        assert f"{SESSION_ID}.json" in fsyncs.directory_states[-1], publication

    async def test_update_syncs_the_directory_after_the_replace(
        self, storage: JsonFileStorage, fsyncs: FsyncSpy
    ) -> None:
        session = _session()
        await storage.create_session(session)
        session.status = "completed"
        await storage.update_session(session)
        assert json.loads(fsyncs.directory_states[-1][f"{SESSION_ID}.json"])["status"] == "completed"

    async def test_delete_syncs_the_directory_after_the_unlink(
        self, storage: JsonFileStorage, fsyncs: FsyncSpy
    ) -> None:
        await storage.create_session(_session())
        await storage.delete_session(SESSION_ID)
        assert f"{SESSION_ID}.json" not in fsyncs.directory_states[-1]

    async def test_a_new_store_makes_every_new_parent_durable(self, tmp_path: Path, fsyncs: FsyncSpy) -> None:
        deep = tmp_path / "home" / "sessions"
        await JsonFileStorage(directory=str(deep)).create_session(_session())
        expected = {os.stat(p).st_ino for p in (tmp_path, tmp_path / "home", deep)}
        assert expected <= set(fsyncs.directory_inodes)

    async def test_a_store_another_process_created_is_still_made_durable(
        self, tmp_path: Path, fsyncs: FsyncSpy
    ) -> None:
        deep = tmp_path / "home" / "sessions"
        deep.mkdir(parents=True)  # exists, but nothing shows its name was synced
        await JsonFileStorage(directory=str(deep)).create_session(_session())
        assert {os.stat(p).st_ino for p in (tmp_path, tmp_path / "home")} <= set(fsyncs.directory_inodes)

    async def test_a_failed_ancestor_sync_is_retried(
        self, tmp_path: Path, fsyncs: FsyncSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deep = tmp_path / "home" / "sessions"
        storage = JsonFileStorage(directory=str(deep))
        real_fsync_dir = durability.fsync_dir

        def failing(_directory: Path) -> None:
            raise OSError(errno.EIO, os.strerror(errno.EIO))

        monkeypatch.setattr(durability, "fsync_dir", failing)
        with pytest.raises(OSError):
            await storage.create_session(_session())
        monkeypatch.setattr(durability, "fsync_dir", real_fsync_dir)

        await storage.create_session(_session())
        assert os.stat(tmp_path / "home").st_ino in fsyncs.directory_inodes

    async def test_a_read_never_fails_on_a_durability_sync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def failing(_directory: Path) -> None:
            raise OSError(errno.EIO, os.strerror(errno.EIO))

        monkeypatch.setattr(durability, "fsync_dir", failing)
        storage = JsonFileStorage(directory=str(tmp_path / "new"))
        assert await storage.list_sessions() == []
        assert (await storage.session_brief())["matched"] == 0

    def test_the_directory_holding_a_symlinked_store_is_synced(self, tmp_path: Path, fsyncs: FsyncSpy) -> None:
        real = tmp_path / "real"
        real.mkdir()
        holder = tmp_path / "holder"
        holder.mkdir()
        (holder / "store").symlink_to(real)
        durability.ensure_durable_dir(holder / "store")
        assert {os.stat(holder).st_ino, os.stat(tmp_path).st_ino} <= set(fsyncs.directory_inodes)

    def test_a_store_reached_through_a_new_path_is_synced_again(self, tmp_path: Path, fsyncs: FsyncSpy) -> None:
        old = tmp_path / "old"
        durability.ensure_durable_dir(old)
        new_parent = tmp_path / "new"
        new_parent.mkdir()
        moved = new_parent / "store"
        old.rename(moved)
        fsyncs.directory_inodes.clear()
        durability.ensure_durable_dir(moved)
        assert os.stat(new_parent).st_ino in fsyncs.directory_inodes

    def test_an_ancestor_that_cannot_be_synced_stops_the_walk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def read_only(_directory: Path) -> None:
            raise OSError(errno.EROFS, os.strerror(errno.EROFS))

        monkeypatch.setattr(durability, "fsync_dir", read_only)
        durability.ensure_durable_dir(tmp_path / "store")
        assert (tmp_path / "store").is_dir()

    async def test_reading_a_store_syncs_nothing(self, storage: JsonFileStorage, fsyncs: FsyncSpy) -> None:
        await storage.list_sessions()
        await storage.session_brief()
        assert fsyncs.directory_inodes == []

    def test_registry_write_syncs_the_file_and_the_directory(self, tmp_path: Path, fsyncs: FsyncSpy) -> None:
        path = tmp_path / "projects.json"
        project_identity._atomic_write_registry(path, project_identity.ProjectRegistry())
        assert fsyncs.file_syncs == 1
        assert os.stat(tmp_path).st_ino in fsyncs.directory_inodes
        assert path.exists()

    def test_knowledge_store_write_syncs_the_file_and_the_directory(self, tmp_path: Path, fsyncs: FsyncSpy) -> None:
        store = KnowledgeStore(project="collab-a")
        learn_store.save_store(store, directory=str(tmp_path))
        assert fsyncs.file_syncs == 1
        assert os.stat(tmp_path).st_ino in fsyncs.directory_inodes


@needs_directory_sync
class TestDirectorySyncPlatformContract:
    @pytest.fixture
    def failing_directory_sync(self, monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
        real_fsync = os.fsync

        def install(error_number: int) -> None:
            def fail_on_directories(fd: int) -> None:
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise OSError(error_number, os.strerror(error_number))
                real_fsync(fd)

            monkeypatch.setattr(os, "fsync", fail_on_directories)

        return install

    @pytest.mark.parametrize("error_number", sorted(durability._DIRECTORY_FSYNC_UNSUPPORTED))
    async def test_an_unsupporting_filesystem_is_tolerated(
        self, storage: JsonFileStorage, failing_directory_sync: Callable[[int], None], error_number: int
    ) -> None:
        failing_directory_sync(error_number)
        await storage.create_session(_session())
        assert (await storage.get_session(SESSION_ID)).id == SESSION_ID

    async def test_a_real_sync_failure_reaches_the_caller_and_keeps_the_record(
        self, storage: JsonFileStorage, failing_directory_sync: Callable[[int], None]
    ) -> None:
        durability.ensure_durable_dir(Path(storage.location()))
        failing_directory_sync(errno.EIO)
        with pytest.raises(OSError) as raised:
            await storage.create_session(_session())
        assert raised.value.errno == errno.EIO
        assert (await storage.get_session(SESSION_ID)).id == SESSION_ID

    async def test_a_real_sync_failure_is_not_retried_as_a_collision(
        self, storage: JsonFileStorage, failing_directory_sync: Callable[[int], None], tmp_path: Path
    ) -> None:
        durability.ensure_durable_dir(Path(storage.location()))
        failing_directory_sync(errno.EIO)
        with pytest.raises(OSError):
            await session_tools.create_session(storage, {}, project="collab-a")
        assert len(list(tmp_path.glob("trace_*.json"))) == 1

    async def test_a_platform_without_directory_sync_still_writes(
        self, storage: JsonFileStorage, fsyncs: FsyncSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(durability, "_DIRECTORY_FSYNC_AVAILABLE", False)
        await storage.create_session(_session())
        assert fsyncs.directory_inodes == []
        assert (await storage.get_session(SESSION_ID)).id == SESSION_ID


class TestRecency:
    async def test_the_banner_names_the_session_created_last_not_the_highest_suffix(
        self, storage: JsonFileStorage, tmp_path: Path
    ) -> None:
        await storage.create_session(_session("trace_20260101_f12345", created=NOON - timedelta(hours=8)))
        await storage.create_session(_session("trace_20260101_012345", created=NOON))
        os.utime(tmp_path / "trace_20260101_f12345.json", (5_000, 5_000))  # older session, written to last
        os.utime(tmp_path / "trace_20260101_012345.json", (1_000, 1_000))
        brief = await storage.session_brief("collab-a")
        assert brief["most_recent"]["id"] == "trace_20260101_012345"

    async def test_the_scan_cap_still_reaches_the_newest_session_of_a_busy_day(
        self, storage: JsonFileStorage, tmp_path: Path
    ) -> None:
        for i in range(20):
            await storage.create_session(_session(f"trace_20260101_f{i:05d}", created=NOON + timedelta(minutes=i)))
            os.utime(tmp_path / f"trace_20260101_f{i:05d}.json", (1_000 + i, 1_000 + i))
        newest = "trace_20260101_000000"
        await storage.create_session(_session(newest, created=NOON + timedelta(hours=5)))
        os.utime(tmp_path / f"{newest}.json", (2_000, 2_000))

        brief = await storage.session_brief("collab-a", scan_cap=3)

        assert brief["most_recent"]["id"] == newest
        assert brief["scanned"] == 3, "the bound on files read must hold"

    async def test_a_later_touch_never_outranks_a_newer_day(self, storage: JsonFileStorage, tmp_path: Path) -> None:
        await storage.create_session(_session("trace_20260101_aaaaaa", created=NOON))
        await storage.create_session(_session("trace_20260102_aaaaaa", created=NOON + timedelta(days=1)))
        os.utime(tmp_path / "trace_20260101_aaaaaa.json", (9_000_000_000, 9_000_000_000))
        brief = await storage.session_brief("collab-a", scan_cap=1)
        assert brief["most_recent"]["id"] == "trace_20260102_aaaaaa"

    async def test_list_sessions_is_ordered_by_creation(self, storage: JsonFileStorage, tmp_path: Path) -> None:
        await storage.create_session(_session("trace_20260101_f12345", created=NOON - timedelta(hours=8)))
        await storage.create_session(_session("trace_20260101_012345", created=NOON))
        os.utime(tmp_path / "trace_20260101_f12345.json", (5_000, 5_000))  # older session, written to last
        os.utime(tmp_path / "trace_20260101_012345.json", (1_000, 1_000))
        listed = await storage.list_sessions("collab-a")
        assert [s["id"] for s in listed] == ["trace_20260101_012345", "trace_20260101_f12345"]

    async def test_the_limit_applies_after_a_full_day_is_ranked(self, storage: JsonFileStorage, tmp_path: Path) -> None:
        await storage.create_session(_session("trace_20260101_000001", created=NOON - timedelta(hours=4)))
        await storage.create_session(_session("trace_20260101_ffffff", created=NOON))
        os.utime(tmp_path / "trace_20260101_000001.json", (5_000, 5_000))  # older session, written to last
        os.utime(tmp_path / "trace_20260101_ffffff.json", (1_000, 1_000))
        listed = await storage.list_sessions("collab-a", limit=1)
        assert [s["id"] for s in listed] == ["trace_20260101_ffffff"]

    async def test_a_file_deleted_during_a_listing_is_skipped(
        self, storage: JsonFileStorage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await storage.create_session(_session())
        vanished = tmp_path / "trace_20260101_gone00.json"
        existing = storage._session_files_newest_first()
        monkeypatch.setattr(storage, "_session_files_newest_first", lambda: [vanished, *existing])
        assert [s["id"] for s in await storage.list_sessions()] == [SESSION_ID]

    @pytest.mark.parametrize("content", [b"[]", b'{"metadata": null}', b'{"events": null}', b"\xff\xfe", b"{not json"])
    async def test_one_malformed_file_does_not_take_down_listings(
        self, storage: JsonFileStorage, tmp_path: Path, content: bytes
    ) -> None:
        await storage.create_session(_session())
        (tmp_path / "trace_20260101_broken.json").write_bytes(content)
        assert [s["id"] for s in await storage.list_sessions()] == [SESSION_ID]
        assert (await storage.session_brief())["most_recent"]["id"] == SESSION_ID

    async def test_unusual_timestamps_rank_last_without_failing(self, storage: JsonFileStorage, tmp_path: Path) -> None:
        await storage.create_session(_session("trace_20260101_good00", created=NOON))
        for name, created in (("trace_20260101_zzzzz1", "not a time"), ("trace_20260101_zzzzz2", None)):
            (tmp_path / f"{name}.json").write_text(
                json.dumps({"id": name, "created": created, "metadata": {"project": "collab-a"}})
            )
        (tmp_path / "trace_20260101_naive0.json").write_text(
            json.dumps(
                {"id": "trace_20260101_naive0", "created": "2026-01-01T06:00:00", "metadata": {"project": "collab-a"}}
            )
        )
        brief = await storage.session_brief("collab-a")
        assert brief["most_recent"]["id"] == "trace_20260101_good00"
        assert len(await storage.list_sessions("collab-a")) == 4


@pytest.fixture
def ignores_case(tmp_path: Path) -> bool:
    probe = tmp_path / "case-probe"
    probe.touch()
    answer = (tmp_path / "CASE-PROBE").exists()
    probe.unlink()
    return answer


class TestOneRecordPerSessionId:
    async def test_a_lossy_id_cannot_read_another_record(self, storage: JsonFileStorage) -> None:
        await storage.create_session(_session("trace_a_b"))
        with pytest.raises(FileNotFoundError):
            await storage.get_session("trace_a/b")

    async def test_the_clean_id_cannot_read_a_stored_lossy_id(self, storage: JsonFileStorage) -> None:
        await storage.create_session(_session("trace_a/b"))
        with pytest.raises(FileNotFoundError):
            await storage.get_session("trace_a_b")
        assert (await storage.get_session("trace_a/b")).id == "trace_a/b"

    async def test_a_stored_id_too_long_for_a_filename_is_not_an_alias(
        self, storage: JsonFileStorage, tmp_path: Path
    ) -> None:
        payload = _session("x" * 400).model_dump(mode="json")
        (tmp_path / "trace_20250101_legacy.json").write_text(json.dumps(payload))
        assert (await storage.get_session("trace_20250101_legacy")).id == "x" * 400

    async def test_a_failed_identity_check_refuses_rather_than_overwrites(
        self, storage: JsonFileStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await storage.create_session(_session("trace_a_b", project="collab-a"))

        def failing_samefile(*_args: Any) -> bool:
            raise OSError(errno.EIO, os.strerror(errno.EIO))

        monkeypatch.setattr(Path, "samefile", failing_samefile)
        with pytest.raises(OSError):
            await storage.update_session(_session("trace_a/b", project="collab-b"))
        monkeypatch.undo()
        assert (await storage.get_session("trace_a_b")).metadata.project == "collab-a"

    async def test_a_case_variant_resolving_to_the_file_is_refused(
        self, storage: JsonFileStorage, ignores_case: bool
    ) -> None:
        if not ignores_case:
            pytest.skip("this filesystem keeps the two names apart")
        await storage.create_session(_session("trace_a_b"))
        with pytest.raises(FileNotFoundError):
            await storage.get_session("TRACE_A_B")

    async def test_a_case_difference_alone_does_not_strand_a_record(
        self, storage: JsonFileStorage, tmp_path: Path, ignores_case: bool
    ) -> None:
        if ignores_case:
            pytest.skip("this filesystem treats the two names as one file")
        payload = _session("TRACE_A_B").model_dump(mode="json")
        (tmp_path / "trace_a_b.json").write_text(json.dumps(payload))
        assert (await storage.get_session("trace_a_b")).id == "TRACE_A_B"

    @pytest.mark.skipif(getattr(os, "geteuid", lambda: 0)() == 0, reason="needs POSIX modes and a non-root user")
    async def test_an_unreadable_record_is_never_replaced_or_deleted_through_an_alias(
        self, storage: JsonFileStorage, tmp_path: Path
    ) -> None:
        await storage.create_session(_session("trace_a_b"))
        record = tmp_path / "trace_a_b.json"
        record.chmod(0o000)
        try:
            with pytest.raises(PermissionError):
                await storage.delete_session("trace_a/b")
            with pytest.raises(PermissionError):
                await storage.update_session(_session("trace_a/b"))
            assert record.exists()
        finally:
            record.chmod(0o600)
        assert (await storage.get_session("trace_a_b")).id == "trace_a_b"

    async def test_a_locked_write_refuses_a_file_holding_another_session(
        self, storage: JsonFileStorage, tmp_path: Path
    ) -> None:
        await storage.create_session(_session("trace_20260101_target", project="collab-b"))
        payload = _session("trace_20260101_target", project="collab-a").model_dump(mode="json")
        (tmp_path / "trace_20250101_legacy.json").write_text(json.dumps(payload))

        with pytest.raises(ValueError, match="holds session"):
            async with locked_disk_session(storage, "trace_20250101_legacy", fallback=_session()):
                pytest.fail("the caller was handed another session's record to write")

        assert (await storage.get_session("trace_20260101_target")).metadata.project == "collab-b"

    async def test_a_lossy_id_cannot_delete_another_record(self, storage: JsonFileStorage) -> None:
        await storage.create_session(_session("trace_a_b"))
        with pytest.raises(FileNotFoundError):
            await storage.delete_session("trace_a/b")
        assert (await storage.get_session("trace_a_b")).id == "trace_a_b"

    async def test_a_lossy_id_cannot_replace_another_record(self, storage: JsonFileStorage) -> None:
        await storage.create_session(_session("trace_a_b", project="collab-a"))
        with pytest.raises(FileNotFoundError):
            await storage.update_session(_session("trace_a/b", project="collab-b"))
        assert (await storage.get_session("trace_a_b")).metadata.project == "collab-a"

    async def test_a_file_whose_name_differs_from_its_id_stays_readable(
        self, storage: JsonFileStorage, tmp_path: Path
    ) -> None:
        """Older records are not aliases, so no migration is needed to keep them."""
        payload = _session("legacy-name").model_dump(mode="json")
        (tmp_path / "trace_20250101_legacy.json").write_text(json.dumps(payload))
        assert (await storage.get_session("trace_20250101_legacy")).id == "legacy-name"

    async def test_a_corrupt_file_can_still_be_repaired_by_update(
        self, storage: JsonFileStorage, tmp_path: Path
    ) -> None:
        await storage.create_session(_session())
        (tmp_path / f"{SESSION_ID}.json").write_text("{not json")
        await storage.update_session(_session())
        assert (await storage.get_session(SESSION_ID)).id == SESSION_ID


class TestLockProbe:
    def test_the_holder_is_never_signalled_where_a_probe_would_kill_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def must_not_run(*_args: Any) -> None:
            pytest.fail("os.kill terminates the target process on Windows")

        monkeypatch.setattr(lockfile, "_PID_PROBE_IS_SAFE", False)
        monkeypatch.setattr(os, "kill", must_not_run)
        assert lockfile.get_holder_status(b"1234:5678") == "unknown"


class TestDeleteTakesTheSessionLock:
    async def test_delete_waits_for_the_lock_holder(self, storage: JsonFileStorage, tmp_path: Path) -> None:
        await storage.create_session(_session())
        async with storage.lock(SESSION_ID):
            deleting = asyncio.create_task(storage.delete_session(SESSION_ID))
            await asyncio.sleep(0.1)
            assert not deleting.done(), "delete ran while another writer held the session lock"
            assert (tmp_path / f"{SESSION_ID}.json").exists()
        await deleting
        assert not (tmp_path / f"{SESSION_ID}.json").exists()

    async def test_the_lock_is_held_through_the_unlink_and_the_sync(
        self, storage: JsonFileStorage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await storage.create_session(_session())
        lock_file = tmp_path / f"{SESSION_ID}.lock"
        held: list[bool] = []
        real_unlink, real_sync = Path.unlink, json_file.fsync_dir

        def spying_unlink(path: Path, *args: Any, **kwargs: Any) -> None:
            if path.suffix == ".json":
                held.append(lock_file.exists())
            real_unlink(path, *args, **kwargs)

        def spying_sync(directory: Path) -> None:
            held.append(lock_file.exists())
            real_sync(directory)

        monkeypatch.setattr(Path, "unlink", spying_unlink)
        monkeypatch.setattr(json_file, "fsync_dir", spying_sync)
        await storage.delete_session(SESSION_ID)
        assert held == [True, True]

    async def test_a_stale_updater_cannot_overwrite_a_recreated_id(self, storage: JsonFileStorage) -> None:
        """The updater holds the lock, so a delete and recreate must wait until it has written."""
        await storage.create_session(_session(project="collab-a"))

        async def delete_then_recreate() -> None:
            await storage.delete_session(SESSION_ID)
            await storage.create_session(_session(project="collab-b"))

        async with storage.lock(SESSION_ID):
            stale = await storage.get_session(SESSION_ID)
            racing = asyncio.create_task(delete_then_recreate())
            await asyncio.sleep(0.1)
            await storage.update_session(stale)
        await racing

        assert (await storage.get_session(SESSION_ID)).metadata.project == "collab-b"
        assert not list(Path(storage.location()).glob("*.lock"))
