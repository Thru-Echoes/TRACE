"""Creating and removing TRACE's advisory lock files (INV-17).

A lock is a file created with ``O_CREAT | O_EXCL``, so only one writer can create
it. Creating is therefore safe on its own. Removing is not: a writer that decides
a lock is abandoned and then removes it by name can delete a newer lock that took
its place, and two writers then believe they hold the lock.

Every removal here happens under a removal guard, after reading the lock again.
While the guard is held no other process can remove the lock, and nothing can
create one at a name that is still taken, so the file that was read is the file
that gets removed.

The guard is an operating-system file lock on a ``.trace-lock-guard`` file beside
the locks. The operating system releases it when its holder exits, so a crash
cannot leave it stuck, which is the reason it is not another lock file. The guard
file must never be deleted or replaced while a server runs, because writers that
lock different files do not exclude each other.

A lock is never removed without the guard, and the guard is only ever tried, never
waited for, so it cannot stall an event loop or stretch a timeout. A steal that
finds it busy lets the caller's wait loop retry. A release that finds it busy
finishes on a background thread. A directory where the guard cannot work is refused
before a lock is taken.

A process running a build from before this module does not take the guard, so the
guarantee holds once every running server has been restarted. Reads are verified
again just before removal, which narrows the exposure until then.

Exports: ``LockGuardError``, ``new_lock_token``, ``create_lock``, ``steal_if_stale``,
``release_lock``, ``get_holder_status``. This module imports nothing from TRACE, so every store can
use it without an import cycle.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

GUARD_FILE_NAME = ".trace-lock-guard"

# Longest pause between attempts of a release that is finishing in the background.
_RELEASE_RETRY_MAX_SECONDS = 5.0

# Directories in which this process has confirmed the guard works.
_verified_guard_directories: set[str] = set()

# On Windows, os.kill(pid, 0) terminates the process instead of probing it.
_PID_PROBE_IS_SAFE = os.name != "nt"


class LockGuardError(TimeoutError):
    """The removal guard cannot work here, so no lock can be taken safely.

    A ``TimeoutError`` so that every caller already handling a refused lock reports
    this one the same way.
    """


if sys.platform == "win32":
    import msvcrt

    def _try_lock_guard(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EDEADLOCK):
                return False  # held by another process
            raise
        return True

    def _unlock_guard(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock_guard(fd: int) -> bool:
        # flock, not lockf: lockf belongs to the process, so it would not exclude
        # two threads of one server.
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock_guard(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def get_holder_status(token: bytes) -> str:
    """Report whether the process named in a lock token is running.

    Args:
        token: the lock file's contents, ``<pid>:<time_ns>`` or longer.

    Returns:
        ``"dead"`` when the holder is gone and its lock may be stolen, ``"alive"``
        when it is running and its lock must not be stolen however old, or
        ``"unknown"`` when the token cannot be parsed or this platform cannot probe
        a process, in which case callers fall back to the lock's age. Valid only
        under the single-host assumption these locks document.

    Side effects: signals the named process with signal 0, which delivers nothing.
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
        return "alive"  # the process exists under another user
    except OSError:
        return "unknown"
    return "alive"


def new_lock_token() -> bytes:
    """Return a token unique to one acquisition, ``<pid>:<time_ns>:<random>``.

    The random part keeps two acquisitions in one process distinct when the clock
    has coarse resolution, since release relies on tokens differing.
    """
    return f"{os.getpid()}:{time.time_ns()}:{uuid.uuid4().hex}".encode()


def _open_guard(directory: Path) -> int:
    """Open the guard file, creating it with the same permissions a lock file gets."""
    return os.open(str(directory / GUARD_FILE_NAME), os.O_CREAT | os.O_RDWR, 0o666)


def _require_guard(directory: Path) -> None:
    """Refuse a directory in which a lock could be taken but never safely removed.

    Contention proves the guard works, so only an error from the filesystem counts.

    Raises:
        LockGuardError: the guard file cannot be opened or locked here.

    Side effects: creates the guard file; caches success for this process.
    """
    absolute = str(directory.absolute())
    if absolute in _verified_guard_directories:
        return
    try:
        fd = _open_guard(directory)
        try:
            if _try_lock_guard(fd):
                _unlock_guard(fd)
        finally:
            os.close(fd)
    except OSError as e:
        raise LockGuardError(
            f"Cannot use the lock guard in {directory}: {e.strerror}. Refusing to take a lock "
            "that could not be removed safely (fail-closed)."
        ) from e
    _verified_guard_directories.add(absolute)


def create_lock(lock_path: Path, token: bytes) -> bool:
    """Create the lock file holding ``token``, unless it already exists.

    Args:
        lock_path: the lock file.
        token: this acquisition's token.

    Returns:
        True if this call created the lock, False if it already existed.

    Raises:
        LockGuardError: the removal guard cannot work in this directory.
        OSError: the lock could not be created.

    Side effects: creates ``lock_path`` and, on first use, the guard file.
    """
    _require_guard(lock_path.parent)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        os.write(fd, token)
    finally:
        os.close(fd)
    return True


@contextlib.contextmanager
def _removal_guard(directory: Path) -> Iterator[bool]:
    """Try once to hold the removal guard for the duration of the block.

    A replaced guard file does not count: a writer locking the new file would not
    exclude one still holding the old, so the locked file must be the current one.

    Args:
        directory: the directory holding the locks.

    Yields:
        True if the guard is held; False if it is busy or could not be taken.

    Side effects: creates the guard file if absent.
    """
    fd: int | None = None
    locked = held = False
    try:
        fd = _open_guard(directory)
        locked = _try_lock_guard(fd)
        if locked:
            opened, current = os.fstat(fd), os.stat(directory / GUARD_FILE_NAME)
            held = (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)
    except OSError as e:
        logger.warning("Lock guard unavailable in %s: %s", directory, e)
    try:
        yield held
    finally:
        # A cleanup failure must not replace the block's result. Closing the
        # descriptor releases the guard even if the explicit unlock fails.
        if fd is not None:
            if locked:
                with contextlib.suppress(OSError):
                    _unlock_guard(fd)
            with contextlib.suppress(OSError):
                os.close(fd)


def _is_stale(lock_path: Path, steal_after: float) -> bool:
    """Return True if the lock's holder is dead, or unknowable and the lock is old.

    The file is compared again after the holder is judged, so a judgment made from
    one lock cannot be applied to a different lock that has since taken its name.
    """
    token = lock_path.read_bytes()
    before = lock_path.stat()
    status = get_holder_status(token)
    stale = status == "dead" or (status == "unknown" and time.time() - before.st_mtime > steal_after)
    return stale and lock_path.stat().st_mtime_ns == before.st_mtime_ns and lock_path.read_bytes() == token


def steal_if_stale(lock_path: Path, steal_after: float) -> bool:
    """Remove an abandoned lock so the caller can try to create it again.

    A lock is abandoned if its holder is dead, or if the holder cannot be determined
    and the lock is older than ``steal_after``. A running holder's lock is never
    removed. The guard is tried once: if it is busy nothing is removed and the caller
    retries from its own wait loop, timing out visibly if it never succeeds.

    Args:
        lock_path: the lock file.
        steal_after: age in seconds beyond which a lock with an unknown holder is
            treated as abandoned.

    Returns:
        True if this call removed the lock.

    Side effects: may remove ``lock_path``.
    """
    try:
        if not _is_stale(lock_path, steal_after):
            return False
        with _removal_guard(lock_path.parent) as held:
            # The first check ran unguarded, so another writer may have stolen the
            # lock and created its own since. Only this second reading counts.
            if not held or not _is_stale(lock_path, steal_after):
                return False
            os.unlink(lock_path)
            return True
    except OSError:
        return False  # the lock vanished mid-check; the caller retries


def _try_release(lock_path: Path, token: bytes) -> bool:
    """Attempt one guarded release.

    Returns:
        True when nothing further is needed, False if the release should be retried.

    Side effects: may remove ``lock_path``; logs a takeover or an unexpected failure.
    """
    with _removal_guard(lock_path.parent) as held:
        if not held:
            return False
        try:
            if lock_path.read_bytes() != token:
                logger.error("Lock %s was taken over while held; leaving the new holder's lock", lock_path)
                return True
            os.unlink(lock_path)
        except FileNotFoundError:
            return True
        except OSError as e:
            logger.error("Could not release lock %s: %s", lock_path, e)
            return False
        return True


def _finish_release(lock_path: Path, token: bytes) -> None:
    """Retry a release until it succeeds. Ends with the process if it never does."""
    delay = 0.001
    while not _try_release(lock_path, token):
        time.sleep(delay)
        delay = min(delay * 2, _RELEASE_RETRY_MAX_SECONDS)


def release_lock(lock_path: Path, token: bytes) -> None:
    """Remove the lock if it is still the one holding ``token``, without blocking.

    A lock with another token means this holder lost it, for example to the
    age-based steal on a platform that cannot probe processes. That lock now belongs
    to someone else and stays, and an error is logged. The protected work has already
    finished by then, so raising would report a completed write as failed.

    If the guard is busy the release finishes on a daemon thread, because removing
    the lock unguarded could invalidate a steal in progress. Writers wait until it
    completes. If this process exits first, its lock can be stolen.

    Args:
        lock_path: the lock file.
        token: the token this holder wrote when it created the lock.

    Side effects: may remove ``lock_path`` now or shortly after; may start a thread.
    """
    # Anchored now, because a background retry outlives the caller's working directory.
    lock_path = lock_path.absolute()
    if _try_release(lock_path, token):
        return
    try:
        threading.Thread(target=_finish_release, args=(lock_path, token), daemon=True).start()
    except RuntimeError as e:
        logger.error("Could not finish releasing lock %s: %s", lock_path, e)
