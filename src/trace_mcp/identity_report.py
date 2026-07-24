"""Shared project-identity drift detection (ADR-006).

Core module — stdlib + the core identity types only, with **zero imports from
``extensions/``** (ADR-003). It is the single implementation of "which knowledge
stores are not backed by a registered project" and "what state is the registry
in", called by BOTH the ``trace_health_check`` MCP tool and the ``identity check``
CLI so the two can never report different verdicts about the same store.

Deliberately filesystem-only for the store scan: it globs the knowledge directory
by name rather than importing the trace-learn store module, so it works with the
extension absent and never triggers a store load.

Exports:
    ``registry_status`` — ``"ok"`` / ``"absent"`` / ``"unavailable"``.
    ``find_stray_stores`` — knowledge-store stems not backed by a registered key.
    ``knowledge_dir`` — the knowledge directory path (env-aware, no side effects).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from trace_mcp import project_identity as pident

RegistryStatus = Literal["ok", "absent", "unavailable"]


def knowledge_dir(directory: str | None = None) -> Path:
    """The knowledge directory, resolved the same way the extension resolves it.

    Mirrors ``extensions.learn.store._get_directory`` without importing it, so a
    core caller never depends on the extension. No side effects.
    """
    import os

    return Path(directory or os.environ.get("TRACE_KNOWLEDGE_DIR", "~/.trace/knowledge")).expanduser()


def registry_status() -> RegistryStatus:
    """Classify the project registry: usable, not-yet-created, or damaged.

    ``absent`` (no file) is a normal pre-migration state that degrades
    gracefully; ``unavailable`` (corrupt or unknown-major) is the fail-closed
    state that isolation-bearing paths must refuse to proceed past.
    """
    try:
        registry = pident.get_registry_cached()
    except pident.RegistryUnavailableError:
        return "unavailable"
    return "ok" if registry is not None else "absent"


def find_stray_stores(
    registry: pident.ProjectRegistry | None,
    directory: str | None = None,
) -> list[str]:
    """Return knowledge-store stems that no registered project accounts for.

    A stray store is one whose canonical stem is neither a registered key nor a
    reserved key (``auto``/``shared`` are quarantine/​reserved, not drift). It is
    the signal that a project's learnings exist on disk under a key the registry
    has never heard of — either an un-enrolled project or a laggard server that
    re-minted a raw-label store after a consolidation.

    Filesystem artifacts are excluded by construction: the ``*.json`` glob never
    matches the ``.embeddings.npy`` sidecar, the ``.json.lock`` lock file, or a
    ``.json.premerge-<date>`` merge backup.

    Returns ``[]`` when *registry* is ``None`` — without a registry nothing can be
    classified as stray, and the caller reports the ``absent``/``unavailable``
    registry state separately rather than flagging every store at once.
    """
    if registry is None:
        return []
    kdir = knowledge_dir(directory)
    if not kdir.is_dir():
        return []
    stray: list[str] = []
    for path in kdir.glob("*.json"):
        # Classify by the store's CANONICAL key — a legacy uppercase or
        # separator-bearing filename (REAP.json, created before canonical
        # keying) belongs to key 'reap' and is NOT stray once 'reap' is
        # registered. Comparing the raw stem to registry keys would flag such a
        # file forever, breaking the `identity check` exit-0 verification. The
        # reported value stays the actual filename stem, so the operator can
        # find the file.
        try:
            key = pident.canonical_project_key(path.stem)
        except pident.ProjectKeyError:
            stray.append(path.stem)  # a filename yielding no key belongs to no project
            continue
        if key in pident.RESERVED_KEYS:
            continue
        if key in registry.projects:
            continue
        stray.append(path.stem)
    return sorted(stray)
