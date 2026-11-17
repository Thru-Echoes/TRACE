"""Read a host transcript's typed prompts and dialog answers, for the capture comparison only (ADR 008 D3, D7).

The report compares what the hooks recorded for a host session with what the
host's own transcript holds, while that transcript still exists. The transcript
format is not a documented interface: this reads the shape observed on one
Claude Code version (one JSON object per line) and skips anything else rather
than guessing. A typed prompt is either a user turn or a message the person sent
while the assistant was still working, which the host stores as a
``queued_command`` attachment rather than a user turn.

The transcript is never a source of the person's words for a quote check: a
quote is verified only against what the hooks recorded. The report uses
``read_host_prompts`` only to say that a missed quote matches a prompt the hooks
did not record, which points at a gap in capture, not at an invented quote.

Exports: HostInputs, is_plain_prompt, read_host_inputs, read_host_prompts

Side effects: ``read_host_inputs`` and ``read_host_prompts`` read one file.
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

    prompts: int = Field(
        default=0, description="Typed prompts (plain prompts only), including those sent while the assistant worked."
    )
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


def _queued_prompt(entry: dict[str, Any]) -> str | None:
    """The text of a prompt the person sent while the assistant was working, or None for anything else."""
    if entry.get("type") != "attachment":
        return None
    attachment = entry.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != "queued_command":
        return None
    if attachment.get("commandMode") != "prompt":
        return None
    origin = attachment.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") != "human":
        return None
    text = attachment.get("prompt")
    return text if isinstance(text, str) else None


def _entry_prompt(entry: dict[str, Any]) -> str | None:
    """The plain typed prompt an entry holds (a user turn or a queued prompt), or None."""
    if entry.get("type") == "user" and entry.get("isMeta") is not True and entry.get("isCompactSummary") is not True:
        text = _prompt_text(entry.get("message"))
    else:
        text = _queued_prompt(entry)
    if text is None or text.startswith(_CONTINUATION_PREFIX) or not is_plain_prompt(text):
        return None
    return text


def _read_entries(path: Path) -> list[dict[str, Any]] | None:
    """The transcript's parsed JSON objects, skipping sidechain lines; None when unreadable or nothing parses."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None
    entries: list[dict[str, Any]] = []
    parsed_any = False
    for line in lines:
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue
        parsed_any = True
        if entry.get("isSidechain") is not True:
            entries.append(entry)
    return entries if parsed_any else None


def read_host_prompts(path: Path) -> list[tuple[datetime, str]] | None:
    """Return the transcript's plain typed prompts with their times, oldest first.

    Includes prompts sent while the assistant was working. Returns None when the
    file cannot be opened or no line parses. Never raises.
    """
    entries = _read_entries(path)
    if entries is None:
        return None
    prompts: list[tuple[datetime, str]] = []
    for entry in entries:
        try:
            ts = _parse_time(entry.get("timestamp"))
            text = _entry_prompt(entry)
        except Exception:
            continue
        if ts is not None and text is not None:
            prompts.append((ts, text))
    return sorted(prompts, key=lambda item: item[0])


def read_host_inputs(path: Path, *, since: datetime, until: datetime) -> HostInputs | None:
    """Count the typed prompts and dialog answers in ``path`` timestamped within ``[since, until]``.

    Returns None when the file cannot be opened or no line parses. Never raises.
    """
    entries = _read_entries(path)
    if entries is None:
        return None
    inputs = HostInputs()
    for entry in entries:
        try:
            ts = _parse_time(entry.get("timestamp"))
            if ts is None or not (since <= ts <= until):
                continue
            if _entry_prompt(entry) is not None:
                inputs.prompts += 1
            result = entry.get("toolUseResult")
            if isinstance(result, dict) and isinstance(result.get("answers"), dict):
                inputs.dialog_answers += sum(
                    1 for v in result["answers"].values() if isinstance(v, str) and v and v != _NOTES_ONLY
                )
        except Exception:
            continue
    return inputs
