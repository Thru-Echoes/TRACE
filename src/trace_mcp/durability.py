"""Directory durability for files TRACE publishes by name (INV-14).

``fsync`` on a file makes its bytes durable. The name that points at those bytes
lives in the parent directory, so until that directory is synced a power loss can
undo a create, a replace, or a delete the caller was told had succeeded.

Platform contract:

- Where ``os.O_DIRECTORY`` exists (Linux, macOS, the BSDs), the directory is synced
  and a failure is raised to the caller.
- A filesystem that reports it cannot sync directories is tolerated with one
  warning per directory, because no stronger guarantee is available there.
- Windows has no directory ``fsync``, so the sync is skipped.
- The guarantee is whatever ``os.fsync`` provides on the host. On macOS that does
  not include a drive-cache flush.

Exports: ``fsync_dir``, ``ensure_durable_dir``. This module imports nothing from
TRACE, so every store can use it without an import cycle.
"""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DIRECTORY_FSYNC_AVAILABLE = hasattr(os, "O_DIRECTORY")

# fsync errors meaning this filesystem cannot sync a directory at all. Anything
# else (an I/O error, a full journal) is a real failure and must reach the caller.
_DIRECTORY_FSYNC_UNSUPPORTED = frozenset({errno.EBADF, errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP})

# Errors meaning an ancestor directory cannot have just gained a name from us.
_ANCESTOR_NOT_SYNCABLE = frozenset({errno.EACCES, errno.EPERM, errno.EROFS})

_warned_directories: set[str] = set()

# (path, st_dev, st_ino) of directories whose ancestors this process has synced.
_durable_directories: set[tuple[str, int, int]] = set()


def fsync_dir(directory: Path) -> None:
    """Make the entries of ``directory`` durable.

    Args:
        directory: the directory whose entries changed.

    Side effects: syncs the directory to disk; may log one warning per directory.

    Raises:
        OSError: the directory could not be opened or synced, other than because
            the filesystem does not support syncing directories.
    """
    if not _DIRECTORY_FSYNC_AVAILABLE:
        return
    fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    except OSError as e:
        if e.errno not in _DIRECTORY_FSYNC_UNSUPPORTED:
            raise
        if str(directory) not in _warned_directories:
            _warned_directories.add(str(directory))
            logger.warning("Directory fsync is unsupported in %s; new names may not survive power loss", directory)
    finally:
        os.close(fd)


def ensure_durable_dir(directory: Path) -> None:
    """Create ``directory`` if needed and make the path to it durable.

    That a directory exists does not show its name is durable: another process may
    have created it and not synced yet, or an earlier sync may have failed. So each
    process syncs every ancestor once per directory, and only records success. After
    that a call costs one ``mkdir`` and one ``stat``. Both the path as given and its
    resolved form are walked, so the directory holding a symlink is synced too.

    A store reached through a new path is synced again. Not covered: moving one of
    its ancestors while the process runs, or deleting and recreating the store at
    the same path if the filesystem reuses the inode.

    Args:
        directory: the directory that must exist.

    Side effects: creates missing directories; syncs ancestors on first use.

    Raises:
        FileExistsError: ``directory`` exists and is not a directory.
        OSError: creation or a directory sync failed.
    """
    directory.mkdir(parents=True, exist_ok=True)
    info = directory.stat()
    identity = (str(directory), info.st_dev, info.st_ino)
    if identity in _durable_directories:
        return
    for ancestor in {*directory.absolute().parents, *directory.resolve().parents}:
        try:
            fsync_dir(ancestor)
        except OSError as e:
            if e.errno not in _ANCESTOR_NOT_SYNCABLE:
                raise
    _durable_directories.add(identity)
