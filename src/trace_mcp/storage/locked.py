"""Shared locked read-modify-write helper for session write paths.

PR D: ``append_event``, ``end_session``, and ``resolve_decision`` each performed
the same acquire-lock → reload-authoritative-disk-state → mutate → write dance,
but as three hand-copied blocks (the consolidation deferred in PR #10). Three
copies is exactly how the H1/H2 immutability gaps arose in the first place — a
new write path copies two of the three guards and silently drops the third.
Routing every session write through ONE helper makes "write under the
per-session lock against the freshest on-disk truth" a single implementation,
registered as invariant INV-1 in docs/INVARIANTS.md.

Exports:
    locked_disk_session — async context manager yielding the disk-truth Session
        under the per-session lock.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from trace_mcp.schema import Session
from trace_mcp.storage.base import TraceStorage


@contextlib.asynccontextmanager
async def locked_disk_session(
    storage: TraceStorage,
    session_id: str,
    *,
    fallback: Session,
) -> AsyncIterator[Session]:
    """Acquire the per-session lock and yield the authoritative on-disk Session.

    Yields the freshest disk-loaded ``Session`` so every read-modify-write path
    sees disk truth: no stale in-memory copy can clobber a concurrent writer's
    events or resurrect a completed session (the H1 class). If the session is
    not yet persisted — a brand-new session whose first write creates the file —
    yields ``fallback`` (the caller's in-memory ``Session``).

    The lock fails closed (raises ``TimeoutError``) rather than degrading to an
    unsynchronized write; see ``JsonFileStorage.lock``. Storage backends without
    a ``lock`` method degrade to a no-op context (single-threaded use only).

    The caller owns its own status / immutability policy and MUST call
    ``storage.update_session(...)`` inside the ``with`` block for its mutation to
    persist. Use ``yielded is fallback`` to tell a not-yet-persisted session from
    a disk-loaded one.

    Args:
        storage: the session store.
        session_id: the session to lock and load.
        fallback: the in-memory Session to yield if nothing is on disk yet.

    Yields:
        The disk-loaded Session, or ``fallback`` if not yet persisted.

    Side effects: acquires/releases the ``<session>.lock`` file.

    Raises:
        TimeoutError: the per-session lock could not be acquired (fail-closed).
    """
    lock_factory = getattr(storage, "lock", None)
    lock_cm = lock_factory(session_id) if lock_factory is not None else contextlib.nullcontext()
    async with lock_cm:
        try:
            disk = await storage.get_session(session_id)
        except FileNotFoundError:
            disk = fallback
        yield disk
