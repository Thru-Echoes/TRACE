"""File I/O for the trace-learn knowledge store.

Stores per-project knowledge as JSON in ~/.trace/knowledge/{project}.json.
Uses atomic writes (write to temp file, then rename) to prevent corruption.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from trace_mcp.extensions.learn.models import KnowledgeStore, Learning

if TYPE_CHECKING:
    from trace_mcp.extensions.learn.models import LearningCategory

logger = logging.getLogger(__name__)

_DEFAULT_DIR = os.path.expanduser("~/.trace/knowledge")


def _get_directory(directory: str | None = None) -> Path:
    return Path(directory or os.environ.get("TRACE_KNOWLEDGE_DIR", _DEFAULT_DIR))


def _store_path(project: str, directory: str | None = None) -> Path:
    return _get_directory(directory) / f"{project}.json"


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


def list_learnings(
    store: KnowledgeStore,
    category: str | None = None,
) -> list[dict]:
    """List learnings, optionally filtered by category."""
    learnings = store.learnings
    if category:
        learnings = [lrn for lrn in learnings if lrn.category == category]
    return [lrn.model_dump(mode="json") for lrn in learnings]
