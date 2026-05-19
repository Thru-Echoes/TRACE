"""File I/O for the trace-learn knowledge store.

Stores per-project knowledge as JSON in ~/.trace/knowledge/{project}.json.
Uses atomic writes (write to temp file, then rename) to prevent corruption.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from trace_mcp.extensions.learn.models import KnowledgeStore, Learning

if TYPE_CHECKING:
    import numpy as np

    from trace_mcp.extensions.learn.models import LearningCategory

logger = logging.getLogger(__name__)

_DEFAULT_DIR = os.path.expanduser("~/.trace/knowledge")


def _get_directory(directory: str | None = None) -> Path:
    return Path(directory or os.environ.get("TRACE_KNOWLEDGE_DIR", _DEFAULT_DIR))


def _store_path(project: str, directory: str | None = None) -> Path:
    from trace_mcp.storage.json_file import sanitize_name

    return _get_directory(directory) / f"{sanitize_name(project)}.json"


_warned_no_filelock = False


@contextmanager
def project_lock(project: str, directory: str | None = None) -> Iterator[None]:
    """Per-project cross-process lock around a load→mutate→save span.

    P9(b) / Round-3 amendment A-R3-2. The shared knowledge store is
    read-modify-write; without a lock, two TRACE sessions mutating the
    SAME project concurrently silently lose one update (last-writer-wins).
    The lock is keyed per-project (not whole-directory) so unrelated
    projects never contend — important with many concurrent sessions.

    Degrades gracefully: if the optional ``filelock`` dependency is not
    installed, this is a no-op (with a one-time warning) rather than a
    hard failure — a missing lock lib must not break the extension. On
    lock-acquire timeout it proceeds (warned) rather than blocking an
    interactive tool indefinitely.
    """
    global _warned_no_filelock
    try:
        from filelock import FileLock, Timeout
    except Exception:
        if not _warned_no_filelock:
            logger.warning(
                "filelock not installed — knowledge-store writes are not "
                "cross-process locked; concurrent multi-session writes to the "
                "same project can lose updates. Install the trace-mcp 'all' or "
                "'embeddings' extra (or `pip install filelock`) to enable."
            )
            _warned_no_filelock = True
        yield
        return

    timeout = float(os.environ.get("TRACE_LOCK_TIMEOUT", "15"))
    lock_path = str(_store_path(project, directory)) + ".lock"
    try:
        with FileLock(lock_path, timeout=timeout):
            yield
    except Timeout:
        logger.warning(
            "Timed out after %.0fs acquiring knowledge-store lock for "
            "project %r; proceeding without the lock to avoid blocking.",
            timeout,
            project,
        )
        yield


class StoreLoadError(Exception):
    """Raised when a knowledge store file exists but cannot be parsed."""


def load_store(
    project: str,
    directory: str | None = None,
    *,
    strict: bool = False,
) -> KnowledgeStore:
    """Load a project's knowledge store from disk.

    If *strict* is True, raises StoreLoadError on parse failures instead
    of silently returning a fresh store (useful for testing / diagnostics).
    """
    path = _store_path(project, directory)
    if not path.exists():
        return KnowledgeStore(project=project)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return KnowledgeStore.model_validate(raw)
    except json.JSONDecodeError as exc:
        msg = f"Corrupt JSON in knowledge store {path}: {exc}"
        logger.warning(msg)
        if strict:
            raise StoreLoadError(msg) from exc
        return KnowledgeStore(project=project)
    except Exception as exc:
        msg = f"Failed to validate knowledge store {path}: {exc}"
        logger.warning(msg)
        if strict:
            raise StoreLoadError(msg) from exc
        return KnowledgeStore(project=project)


def save_store(store: KnowledgeStore, directory: str | None = None) -> Path:
    """Save a knowledge store to disk using atomic write.

    Writes to a temporary file in the same directory then renames, so a
    crash mid-write can never leave a half-written store file.
    """
    path = _store_path(store.project, directory)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Refresh the updated timestamp on every save
    store.updated = datetime.now(UTC)

    data = json.dumps(store.model_dump(mode="json"), indent=2)

    # Atomic write: temp file in same dir → rename
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_path, str(path))
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # Best-effort sidecar cache for fast embedding search
    save_embeddings_cache(store, directory)

    return path


def add_learning(
    store: KnowledgeStore,
    content: str,
    category: LearningCategory = "learning",
    source_session: str | None = None,
    source_event: str | None = None,
    corrects_event_ids: list[str] | None = None,
    tags: list[str] | None = None,
) -> Learning:
    """Add a learning to the store and return it."""
    learning = Learning(
        id=store.next_learning_id(),
        content=content,
        category=category,
        source_session=source_session,
        source_event=source_event,
        corrects_event_ids=corrects_event_ids or [],
        tags=tags or [],
    )
    store.learnings.append(learning)
    return learning


def remove_learning(store: KnowledgeStore, learning_id: str) -> bool:
    """Remove a learning by ID. Returns True if found and removed."""
    for i, lrn in enumerate(store.learnings):
        if lrn.id == learning_id:
            store.learnings.pop(i)
            return True
    return False


_TOOL_RESPONSE_EXCLUDE = {"embedding", "embedding_model"}


def learning_to_dict(lrn: Learning) -> dict:
    """Serialize a learning for tool/API responses (excludes bulky embedding)."""
    return lrn.model_dump(mode="json", exclude=_TOOL_RESPONSE_EXCLUDE)


def list_learnings(
    store: KnowledgeStore,
    category: str | None = None,
) -> list[dict]:
    """List learnings, optionally filtered by category."""
    learnings = store.learnings
    if category:
        learnings = [lrn for lrn in learnings if lrn.category == category]
    return [learning_to_dict(lrn) for lrn in learnings]


# ── Deduplication ────────────────────────────────────────────────────────


@dataclass
class DedupResult:
    """Result of a deduplicated add attempt."""

    learning: Learning
    is_duplicate: bool
    duplicate_of: str | None = None


def find_duplicate(
    store: KnowledgeStore,
    content: str,
    threshold: float = 0.85,
) -> Learning | None:
    """Find an existing learning that is near-duplicate of *content*.

    Uses Jaccard token-overlap similarity, which returns 1.0 for exact
    matches and naturally handles near-duplicates.  More appropriate for
    dedup than BM25 (which is a retrieval ranker, not a similarity metric).

    Returns the best match above *threshold*, or None.
    """
    if not store.learnings or not content.strip():
        return None

    from trace_mcp.extensions.learn.matching import jaccard_similarity

    best_score = 0.0
    best_idx = -1
    for i, lrn in enumerate(store.learnings):
        score = jaccard_similarity(lrn.content, content)
        if score > best_score:
            best_score = score
            best_idx = i

    if best_score >= threshold and best_idx >= 0:
        return store.learnings[best_idx]
    return None


def add_learning_dedup(
    store: KnowledgeStore,
    content: str,
    category: LearningCategory = "learning",
    source_session: str | None = None,
    source_event: str | None = None,
    corrects_event_ids: list[str] | None = None,
    tags: list[str] | None = None,
    dedup_threshold: float = 0.85,
) -> DedupResult:
    """Add a learning with content deduplication.

    If an existing learning scores above *dedup_threshold*, returns the
    existing one as a duplicate without adding.  Otherwise adds normally.
    """
    existing = find_duplicate(store, content, threshold=dedup_threshold)
    if existing is not None:
        return DedupResult(learning=existing, is_duplicate=True, duplicate_of=existing.id)

    lrn = add_learning(
        store,
        content=content,
        category=category,
        source_session=source_session,
        source_event=source_event,
        corrects_event_ids=corrects_event_ids,
        tags=tags,
    )
    return DedupResult(learning=lrn, is_duplicate=False)


# ── Embedding sidecar cache ─────────────────────────────────────────────


def _embeddings_cache_path(project: str, directory: str | None = None) -> Path:
    """Path to the ``.npy`` sidecar embedding cache."""
    json_path = _store_path(project, directory)
    return json_path.with_suffix(".embeddings.npy")


def save_embeddings_cache(store: KnowledgeStore, directory: str | None = None) -> Path | None:
    """Save embedding matrix as a ``.npy`` sidecar file (best-effort).

    Only saves if ``numpy`` is available and at least one learning has an
    embedding.  Returns the path on success, ``None`` if skipped.
    """
    try:
        import numpy as np
    except ImportError:
        return None

    embeddings_present = [lrn for lrn in store.learnings if lrn.embedding is not None]
    if not embeddings_present:
        return None

    first_emb = embeddings_present[0].embedding
    assert first_emb is not None  # guaranteed by list comprehension filter above
    dim = len(first_emb)
    matrix = np.full((len(store.learnings), dim), np.nan, dtype=np.float32)
    for i, lrn in enumerate(store.learnings):
        if lrn.embedding is not None:
            matrix[i] = lrn.embedding

    path = _embeddings_cache_path(store.project, directory)
    # P9(c) / Round-3 A-R3-8: atomic write — a crash or a concurrent reader
    # must never observe a torn .npy sidecar. Mirrors the temp + os.replace
    # atomic pattern used for the JSON store. Same-directory temp guarantees
    # os.replace is an atomic rename on the same filesystem.
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".npy.tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            np.save(fh, matrix)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def load_embeddings_cache(
    store: KnowledgeStore,
    directory: str | None = None,
) -> "np.ndarray | None":  # noqa: UP037 — string quote needed for optional dep
    """Load the ``.npy`` sidecar if it exists and matches the store size."""
    try:
        import numpy as np
    except ImportError:
        return None

    path = _embeddings_cache_path(store.project, directory)
    if not path.exists():
        return None

    matrix = np.load(str(path))
    if matrix.shape[0] != len(store.learnings):
        # Stale cache — store was modified since cache was written
        return None
    return matrix
