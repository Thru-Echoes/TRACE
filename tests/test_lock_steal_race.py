"""A lock file is removed only by a writer entitled to remove it (INV-17).

The interleavings are forced by pausing one contender at the exact step where the
race happens. The multi-process test then runs the real contention with crashed
holders and checks that no two processes are ever inside the lock together.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import signal
import subprocess
import sys
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from trace_mcp import lockfile
from trace_mcp.project_identity import exclusive_file_lock
from trace_mcp.storage.json_file import JsonFileStorage

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="these interleavings rely on POSIX process probes")

SESSION_ID = "trace_20260101_abcdef"
WAIT = 10.0


def _dead_token() -> bytes:
    process = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, check=True)
    return f"{int(process.stdout)}:1".encode()


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / "held.lock"


@pytest.fixture
def stale_lock(lock_path: Path) -> Path:
    lock_path.write_bytes(_dead_token())
    return lock_path


def _wait_until_gone(path: Path) -> None:
    deadline = threading.Event()
    for _ in range(int(WAIT / 0.01)):
        if not path.exists():
            return
        deadline.wait(0.01)
    pytest.fail(f"{path} was never removed")


def _in_background(target: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    results: list[Any] = []
    thread = threading.Thread(target=lambda: results.append(target()))
    thread.start()
    return thread, results


class TestStealing:
    def test_a_dead_holders_lock_is_removed(self, stale_lock: Path) -> None:
        assert lockfile.steal_if_stale(stale_lock, 60.0) is True
        assert not stale_lock.exists()

    def test_a_running_holders_lock_is_kept_however_old(self, lock_path: Path) -> None:
        lock_path.write_bytes(lockfile.new_lock_token())
        os.utime(lock_path, (1, 1))
        assert lockfile.steal_if_stale(lock_path, 60.0) is False
        assert lock_path.exists()

    def test_an_unknown_holder_is_judged_by_age(self, lock_path: Path) -> None:
        lock_path.write_bytes(b"not a token")
        assert lockfile.steal_if_stale(lock_path, 60.0) is False
        os.utime(lock_path, (1, 1))
        assert lockfile.steal_if_stale(lock_path, 60.0) is True

    def test_a_stealer_paused_after_judging_cannot_remove_a_newer_lock(
        self, stale_lock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The race: both judge the lock stale, one wins and relocks, the other wakes."""
        judged, resume = threading.Event(), threading.Event()
        real_guard = lockfile._removal_guard
        main = threading.current_thread()

        @contextlib.contextmanager
        def pause_before_guard(directory: Path) -> Iterator[bool]:
            if threading.current_thread() is not main:
                judged.set()
                assert resume.wait(WAIT)
            with real_guard(directory) as held:
                yield held

        monkeypatch.setattr(lockfile, "_removal_guard", pause_before_guard)
        thread, results = _in_background(lambda: lockfile.steal_if_stale(stale_lock, 60.0))
        assert judged.wait(WAIT)

        assert lockfile.steal_if_stale(stale_lock, 60.0) is True
        newer = lockfile.new_lock_token()
        assert lockfile.create_lock(stale_lock, newer) is True

        resume.set()
        thread.join(WAIT)
        assert results == [False]
        assert stale_lock.read_bytes() == newer

    def test_a_lock_replaced_while_being_judged_is_kept(
        self, stale_lock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A judgment about the old lock must not be applied to the one that replaced it."""
        newer = lockfile.new_lock_token()

        def replace_then_report_dead(_token: bytes) -> str:
            stale_lock.write_bytes(newer)
            return "dead"

        monkeypatch.setattr(lockfile, "get_holder_status", replace_then_report_dead)
        assert lockfile.steal_if_stale(stale_lock, 60.0) is False
        assert stale_lock.read_bytes() == newer

    def test_a_replaced_guard_file_does_not_count_as_held(
        self, stale_lock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_try = lockfile._try_lock_guard
        guard = stale_lock.parent / lockfile.GUARD_FILE_NAME

        def lock_then_replace(fd: int) -> bool:
            result = real_try(fd)
            guard.unlink()
            guard.write_bytes(b"")
            return result

        monkeypatch.setattr(lockfile, "_try_lock_guard", lock_then_replace)
        assert lockfile.steal_if_stale(stale_lock, 60.0) is False
        assert stale_lock.exists()
        monkeypatch.undo()
        assert lockfile.steal_if_stale(stale_lock, 60.0) is True

    def test_without_a_process_probe_age_decides(self, lock_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Where a process cannot be probed, as on Windows, only the lock's age counts."""
        monkeypatch.setattr(lockfile, "_PID_PROBE_IS_SAFE", False)
        lock_path.write_bytes(_dead_token())
        assert lockfile.steal_if_stale(lock_path, 60.0) is False
        os.utime(lock_path, (1, 1))
        assert lockfile.steal_if_stale(lock_path, 60.0) is True

    def test_only_one_stealer_can_be_removing_at_a_time(
        self, stale_lock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        about_to_remove, resume = threading.Event(), threading.Event()
        real_unlink = os.unlink
        main = threading.current_thread()

        def pause_before_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
            if threading.current_thread() is not main and str(path) == str(stale_lock):
                about_to_remove.set()
                assert resume.wait(WAIT)
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(lockfile.os, "unlink", pause_before_unlink)
        thread, results = _in_background(lambda: lockfile.steal_if_stale(stale_lock, 60.0))
        assert about_to_remove.wait(WAIT)

        assert lockfile.steal_if_stale(stale_lock, 60.0) is False
        assert stale_lock.exists()

        resume.set()
        thread.join(WAIT)
        assert results == [True]
        assert not stale_lock.exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="kills a process holding a POSIX file lock")
    def test_a_crashed_guard_holder_does_not_block_stealing(
        self, stale_lock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import fcntl, os, sys, time\n"
                "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                "print('held', flush=True)\n"
                "time.sleep(60)\n",
                str(stale_lock.parent / lockfile.GUARD_FILE_NAME),
            ],
            stdout=subprocess.PIPE,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == b"held"
            assert lockfile.steal_if_stale(stale_lock, 60.0) is False
            holder.send_signal(signal.SIGKILL)
            holder.wait(WAIT)
            assert lockfile.steal_if_stale(stale_lock, 60.0) is True
        finally:
            holder.kill()
            holder.wait(WAIT)
            assert holder.stdout is not None
            holder.stdout.close()


def _refuse_guard(_fd: int) -> bool:
    raise OSError(errno.ENOLCK, "no locks available")


class TestWithoutTheGuard:
    def test_nothing_is_stolen(self, stale_lock: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(lockfile, "_try_lock_guard", _refuse_guard)
        assert lockfile.steal_if_stale(stale_lock, 60.0) is False
        assert stale_lock.exists()

    async def test_a_lock_is_refused_before_any_write(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(lockfile, "_try_lock_guard", _refuse_guard)
        storage = JsonFileStorage(directory=str(tmp_path))
        with pytest.raises(TimeoutError, match="lock guard"):
            async with storage.lock(SESSION_ID):
                pytest.fail("took a lock that could not be removed safely")
        assert not list(tmp_path.glob("*.lock"))

    def test_a_relative_path_is_verified_per_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()
        monkeypatch.chdir(first)
        assert lockfile.create_lock(Path("held.lock"), lockfile.new_lock_token())
        monkeypatch.setattr(lockfile, "_try_lock_guard", _refuse_guard)
        monkeypatch.chdir(second)
        with pytest.raises(lockfile.LockGuardError, match="lock guard"):
            lockfile.create_lock(Path("held.lock"), lockfile.new_lock_token())

    def test_a_cleanup_failure_does_not_change_the_result(
        self, stale_lock: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def failing_unlock(_fd: int) -> None:
            raise OSError(errno.EIO, "unlock failed")

        monkeypatch.setattr(lockfile, "_unlock_guard", failing_unlock)
        assert lockfile.steal_if_stale(stale_lock, 60.0) is True

    def test_a_holder_never_removes_a_lock_unguarded(self, lock_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        token = lockfile.new_lock_token()
        assert lockfile.create_lock(lock_path, token)
        monkeypatch.setattr(lockfile, "_try_lock_guard", _refuse_guard)
        lockfile.release_lock(lock_path, token)
        threading.Event().wait(0.05)
        assert lock_path.read_bytes() == token


class TestRelease:
    def test_a_holder_removes_its_own_lock(self, lock_path: Path) -> None:
        token = lockfile.new_lock_token()
        assert lockfile.create_lock(lock_path, token)
        lockfile.release_lock(lock_path, token)
        assert not lock_path.exists()

    def test_a_busy_guard_delays_release_without_blocking_or_losing_it(self, lock_path: Path) -> None:
        token = lockfile.new_lock_token()
        assert lockfile.create_lock(lock_path, token)
        with lockfile._removal_guard(lock_path.parent) as held:
            assert held
            thread, _ = _in_background(lambda: lockfile.release_lock(lock_path, token))
            thread.join(WAIT)
            assert not thread.is_alive(), "release blocked while the guard was busy"
            assert lock_path.read_bytes() == token
        _wait_until_gone(lock_path)

    def test_a_background_release_is_not_redirected_by_a_directory_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()
        token = lockfile.new_lock_token()
        (second / "held.lock").write_bytes(token)  # same name and token, wrong directory
        monkeypatch.chdir(first)
        assert lockfile.create_lock(Path("held.lock"), token)
        with lockfile._removal_guard(first) as held:
            assert held
            lockfile.release_lock(Path("held.lock"), token)
            monkeypatch.chdir(second)
        _wait_until_gone(first / "held.lock")
        assert (second / "held.lock").read_bytes() == token

    def test_an_unexpected_removal_failure_is_reported_and_retried(
        self, lock_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        token = lockfile.new_lock_token()
        assert lockfile.create_lock(lock_path, token)
        real_unlink = os.unlink
        failures = [OSError(errno.EIO, "disk error")]

        def fail_once(path: Any, *args: Any, **kwargs: Any) -> None:
            if failures and str(path) == str(lock_path):
                raise failures.pop()
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(lockfile.os, "unlink", fail_once)
        with caplog.at_level(logging.ERROR):
            lockfile.release_lock(lock_path, token)
            _wait_until_gone(lock_path)
        assert "Could not release" in caplog.text

    def test_a_lock_that_is_already_gone_is_not_an_error(self, lock_path: Path) -> None:
        lockfile.release_lock(lock_path, lockfile.new_lock_token())

    def test_tokens_differ_between_acquisitions(self) -> None:
        assert len({lockfile.new_lock_token() for _ in range(1000)}) == 1000


@contextlib.asynccontextmanager
async def _session_lock(lock_dir: Path) -> AsyncIterator[Path]:
    async with JsonFileStorage(directory=str(lock_dir)).lock(SESSION_ID):
        yield lock_dir / f"{SESSION_ID}.lock"


@contextlib.asynccontextmanager
async def _file_lock(lock_dir: Path) -> AsyncIterator[Path]:
    path = lock_dir / "registry.json.lock"
    with exclusive_file_lock(path):
        yield path


LockFactory = Callable[[Path], contextlib._AsyncGeneratorContextManager[Path]]


@pytest.mark.parametrize("hold", [_session_lock, _file_lock], ids=["session-lock", "file-lock"])
class TestEveryLockHonorsOwnership:
    async def test_a_lock_lost_while_held_is_left_for_its_new_holder(self, hold: LockFactory, tmp_path: Path) -> None:
        successor = lockfile.new_lock_token()
        async with hold(tmp_path) as path:
            path.write_bytes(successor)
        assert path.read_bytes() == successor

    async def test_a_normal_release_leaves_no_lock(self, hold: LockFactory, tmp_path: Path) -> None:
        async with hold(tmp_path) as path:
            assert path.exists()
        assert not path.exists()


async def test_the_guard_file_is_not_mistaken_for_data(tmp_path: Path) -> None:
    storage = JsonFileStorage(directory=str(tmp_path))
    async with storage.lock(SESSION_ID):
        pass
    assert (tmp_path / lockfile.GUARD_FILE_NAME).exists()
    assert not list(tmp_path.glob("*.lock"))
    assert await storage.list_sessions() == []


_CONTENDER = """
import os, sys, time
from pathlib import Path
from trace_mcp.lockfile import create_lock, new_lock_token, release_lock, steal_if_stale

lock, inside, dead_token, rounds = Path(sys.argv[1]), sys.argv[2], sys.argv[3].encode(), int(sys.argv[4])
overlaps = 0
for number in range(rounds):
    token = new_lock_token()
    deadline = time.monotonic() + 60
    while not create_lock(lock, token):
        if time.monotonic() > deadline:
            sys.exit("timed out waiting for the lock")
        steal_if_stale(lock, 60.0)
    try:
        os.close(os.open(inside, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        overlaps += 1
    else:
        time.sleep(0.0005)
        os.unlink(inside)
    if number == rounds - 1:
        print(overlaps, flush=True)
        os._exit(0)  # a real crash: the next generation finds a lock whose holder has died
    if number % 2:
        lock.write_bytes(dead_token)  # abandon the lock instead of releasing it
    else:
        release_lock(lock, token)
"""


def test_contenders_recovering_from_crashes_never_overlap(tmp_path: Path) -> None:
    """Every process ends by crashing inside the lock, so each generation must steal."""
    lock, inside = tmp_path / "shared.lock", tmp_path / "inside"
    dead = _dead_token().decode()
    for _generation in range(3):
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", _CONTENDER, str(lock), str(inside), dead, "20"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(8)
        ]
        # Collected concurrently: an exited child that has not been reaped still answers
        # a liveness probe, so its lock could not be stolen.
        with ThreadPoolExecutor(len(processes)) as pool:
            outcomes = list(pool.map(lambda process: process.communicate(timeout=120), processes))
        assert all(process.returncode == 0 for process in processes), outcomes
        assert sum(int(out) for out, _ in outcomes) == 0
