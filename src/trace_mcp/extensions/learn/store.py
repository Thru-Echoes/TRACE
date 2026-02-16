"""File I/O for the trace-learn knowledge store.

Stores per-project knowledge as JSON in ~/.trace/knowledge/{project}.json.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from pathlib import Path

from trace_mcp.extensions.learn.models import KnowledgeStore, Learning

logger = logging.getLogger(__name__)

_DEFAULT_DIR = os.path.expanduser("~/.trace/knowledge")


def _get_directory(directory: str | None = None) -> Path:
    return Path(directory or os.environ.get("TRACE_KNOWLEDGE_DIR", _DEFAULT_DIR))


def _store_path(project: str, directory: str | None = None) -> Path:
    return _get_directory(directory) / f"{project}.json"


def load_store(project: str, directory: str | None = None) -> KnowledgeStore:
    """Load a project's knowledge store, returning empty store on any error."""
    path = _store_path(project, directory)
    if not path.exists():
        return KnowledgeStore(project=project)
    try:
        with open(path) as f:
            raw = json.load(f)
        return KnowledgeStore.model_validate(raw)
    except Exception:
        logger.warning("Failed to load knowledge store: %s, starting fresh", path)
        return KnowledgeStore(project=project)


def save_store(store: KnowledgeStore, directory: str | None = None) -> Path:
    """Save a knowledge store to disk with file locking."""
    path = _store_path(store.project, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(store.model_dump(mode="json"), indent=2)
    with open(path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(data)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return path


def add_learning(
    store: KnowledgeStore,
    content: str,
    category: str = "learning",
    source_session: str | None = None,
    source_event: str | None = None,
    tags: list[str] | None = None,
) -> Learning:
    """Add a learning to the store and return it."""
    learning = Learning(
        id=store.next_learning_id(),
        content=content,
        category=category,
        source_session=source_session,
        source_event=source_event,
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
        learnings = [l for l in learnings if l.category == category]
    return [l.model_dump(mode="json") for l in learnings]
