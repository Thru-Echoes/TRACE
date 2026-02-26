"""File I/O for the trace-evolve genome store.

Stores per-project genomes as JSON in ~/.trace/evolution/{project}.json.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from pathlib import Path

from trace_mcp.extensions.evolve.models import Adaptation, Genome

logger = logging.getLogger(__name__)

_DEFAULT_DIR = os.path.expanduser("~/.trace/evolution")


def _get_directory(directory: str | None = None) -> Path:
    return Path(directory or os.environ.get("TRACE_EVOLUTION_DIR", _DEFAULT_DIR))


def _store_path(project: str, directory: str | None = None) -> Path:
    return _get_directory(directory) / f"{project}.json"


def load_genome(project: str, directory: str | None = None) -> Genome:
    """Load a project's genome, returning empty genome on any error."""
    path = _store_path(project, directory)
    if not path.exists():
        return Genome(project=project)
    try:
        with open(path) as f:
            raw = json.load(f)
        return Genome.model_validate(raw)
    except Exception:
        logger.warning("Failed to load genome: %s, starting fresh", path)
        return Genome(project=project)


def save_genome(genome: Genome, directory: str | None = None) -> Path:
    """Save a genome to disk with file locking."""
    path = _store_path(genome.project, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(genome.model_dump(mode="json"), indent=2)
    with open(path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(data)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return path


def add_adaptation(
    genome: Genome,
    content: str,
    category: str = "learning",
    source_session: str | None = None,
    source_event: str | None = None,
    tags: list[str] | None = None,
) -> Adaptation:
    """Add an adaptation (mutation) to the genome and return it."""
    adaptation = Adaptation(
        id=genome.next_adaptation_id(),
        content=content,
        category=category,
        source_session=source_session,
        source_event=source_event,
        tags=tags or [],
    )
    genome.adaptations.append(adaptation)
    return adaptation


def remove_adaptation(genome: Genome, adaptation_id: str) -> bool:
    """Remove an adaptation by ID (extinction). Returns True if found and removed."""
    for i, adp in enumerate(genome.adaptations):
        if adp.id == adaptation_id:
            genome.adaptations.pop(i)
            return True
    return False


def list_adaptations(
    genome: Genome,
    category: str | None = None,
) -> list[dict]:
    """List adaptations, optionally filtered by category."""
    adaptations = genome.adaptations
    if category:
        adaptations = [adp for adp in adaptations if adp.category == category]
    return [adp.model_dump(mode="json") for adp in adaptations]
