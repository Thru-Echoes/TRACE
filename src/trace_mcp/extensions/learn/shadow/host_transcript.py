"""Count a host transcript's typed prompts and dialog answers, for the capture comparison only (ADR 008 D3, D7).

The report compares what the hooks recorded for a host session with what the
host's own transcript holds, while that transcript still exists. The transcript
format is not a documented interface: this reads the shape observed on one
Claude Code version (one JSON object per line) and skips anything else rather
than guessing. It is never a source of the person's words.

Exports: HostInputs, is_plain_prompt, read_host_inputs

Side effects: ``read_host_inputs`` reads one file.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_CONTINUATION_PREFIX = "This session is being continued"
# The host's own answer text when only a note was typed; not counted, matching the dialog hook.
_NOTES_ONLY = "(notes only)"


class HostInputs(BaseModel):
    """What a transcript holds in a time window."""

    prompts: int = Field(default=0, description="Typed prompts (plain prompts only).")
    dialog_answers: int = Field(default=0, description="Non-empty question-dialog answers.")


def is_plain_prompt(text: str) -> bool:
    """True unless the text, left-stripped, is empty, a typed command (``/``), or a tag other than pasted content."""
    stripped = text.lstrip()
    if not stripped or stripped.startswith("/"):
        return False
    return not (stripped.startswith("<") and not stripped.startswith("<pasted_content"))


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _prompt_text(message: Any) -> str | None:
    """The typed text of a user message, or None when it is a tool result or another shape."""
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    blocks = [block for block in content if isinstance(block, dict)]
    if any(block.get("type") == "tool_result" for block in blocks):
        return None
    texts = [block.get("text") for block in blocks if block.get("type") == "text"]
    return "".join(text for text in texts if isinstance(text, str))


def read_host_inputs(path: Path, *, since: datetime, until: datetime) -> HostInputs | None:
    """Count the typed prompts and dialog answers in ``path`` timestamped within ``[since, until]``.

    Returns None when the file cannot be opened or no line parses. Never raises.
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None
    parsed_any = False
    inputs = HostInputs()
    for line in lines:
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue
        parsed_any = True
        try:
            if entry.get("isSidechain") is True:
                continue
            ts = _parse_time(entry.get("timestamp"))
            if ts is None or not (since <= ts <= until):
                continue
            if (
                entry.get("type") == "user"
                and entry.get("isMeta") is not True
                and entry.get("isCompactSummary") is not True
            ):
                text = _prompt_text(entry.get("message"))
                if text is not None and not text.startswith(_CONTINUATION_PREFIX) and is_plain_prompt(text):
                    inputs.prompts += 1
            result = entry.get("toolUseResult")
            if isinstance(result, dict) and isinstance(result.get("answers"), dict):
                inputs.dialog_answers += sum(
                    1 for v in result["answers"].values() if isinstance(v, str) and v and v != _NOTES_ONLY
                )
        except Exception:
            continue
    return inputs if parsed_any else None
