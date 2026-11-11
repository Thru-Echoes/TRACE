"""Local record of Jev advisory results, kept so they can be checked later.

An advisory result can only be compared with human labels if it still exists. Each
project has one append-only JSON Lines file. A record holds answers, digests, and
event ids, but not event text. Session files are never touched.

Exports: ``get_results_path``, ``append_result``, ``get_assessed_event_ids``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from trace_mcp.durability import ensure_durable_dir, fsync_dir


def get_results_path(project_key: str) -> Path:
    """Return the results file for a project.

    Args:
        project_key: the canonical project key.

    Returns:
        ``<dir>/<project_key>.jsonl``, where ``<dir>`` is ``TRACE_JEV_RESULTS_DIR``
        or ``~/.trace/jev``.
    """
    configured = os.environ.get("TRACE_JEV_RESULTS_DIR")
    directory = Path(configured).expanduser() if configured else Path.home() / ".trace" / "jev"
    return directory / f"{project_key}.jsonl"


def append_result(project_key: str, result: dict[str, Any]) -> Path:
    """Append one advisory result durably.

    Args:
        project_key: the canonical project key.
        result: the advisory result.

    Returns:
        The file written.

    Raises:
        OSError: the record could not be written in full.

    Side effects: creates the directory and file if needed; appends one line.
    """
    path = get_results_path(project_key)
    ensure_durable_dir(path.parent)
    line = (json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n").encode()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if os.write(fd, line) != len(line):
            raise OSError(f"short write to {path}")
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_dir(path.parent)
    return path


def get_assessed_event_ids(project_key: str, session_id: str, model: str) -> set[str]:
    """Return the events of a session already assessed with this model.

    A result from another model does not count, since a different model is a
    different instrument. An unreadable line is skipped.

    Args:
        project_key: the canonical project key.
        session_id: the session.
        model: the requested model.

    Returns:
        The ids of events with a saved result.

    Side effects: reads the results file.
    """
    path = get_results_path(project_key)
    if not path.is_file():
        return set()
    assessed: set[str] = set()
    with open(path, encoding="utf-8", errors="replace") as lines:
        for line in lines:
            try:
                record = json.loads(line)
                if record["requested_model"] == model and record["source"]["session_id"] == session_id:
                    assessed.add(record["source"]["event_id"])
            except (ValueError, KeyError, TypeError):
                continue
    return assessed
