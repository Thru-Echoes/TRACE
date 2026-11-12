"""Read the person's words saved by the host hooks (ADR 008 D3).

The hooks append one JSON line per typed prompt or dialog answer to
``<shadow root>/<project-key>/words/<host-session-id>.jsonl``. Words are matched
to events by time within the project, so every file of the project is read and
merged.

Exports: load_words

Side effects: reads files only.
"""

from __future__ import annotations

from pydantic import ValidationError

from trace_mcp.extensions.learn.shadow.state import get_project_dir
from trace_mcp.provenance_marks import WordsEntry


def load_words(project_key: str) -> list[WordsEntry]:
    """Return every words entry recorded for a project, sorted by time.

    Lines that are not a valid entry (garbage, an unknown ``kind``, a time with
    no timezone) are skipped, as are files that cannot be read. A missing directory gives ``[]``.
    """
    words_dir = get_project_dir(project_key) / "words"
    if not words_dir.is_dir():
        return []
    entries: list[WordsEntry] = []
    for path in sorted(words_dir.glob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                entry = WordsEntry.model_validate_json(line)
            except ValidationError:
                continue
            if entry.ts.tzinfo is not None:
                entries.append(entry)
    return sorted(entries, key=lambda w: w.ts)
