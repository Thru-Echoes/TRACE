"""Counting a host transcript's typed prompts and dialog answers, for the capture comparison only.

The transcript format is not a documented interface: these lines follow the
shape observed on one host version, and anything else must be skipped, never
an error.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trace_mcp.extensions.learn.shadow.host_transcript import is_plain_prompt, read_host_inputs

SINCE = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)
UNTIL = datetime(2026, 11, 12, 23, 59, tzinfo=UTC)
TS = "2026-11-12T01:00:00.000Z"


def _user(content: object, **extra: object) -> dict:
    return {"type": "user", "timestamp": TS, "message": {"role": "user", "content": content}, **extra}


ONE_OF_EACH = [
    _user("keep the retry limit at three"),
    _user([{"type": "text", "text": "and log every retry"}]),
    _user("<task-notification>\n<status>done</status>\n</task-notification>"),
    _user("<command-name>/deploy</command-name>"),
    _user("caveat injected by the host", isMeta=True),
    _user("This session is being continued from a previous conversation.", isCompactSummary=True),
    _user("a subagent prompt", isSidechain=True),
    _user('<pasted_content id="ab">\nthree lines\n</pasted_content id="ab">\nuse this'),
    _user([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
    {
        **_user([{"type": "tool_result", "tool_use_id": "t2", "content": "answered"}]),
        "toolUseResult": {
            "questions": [],
            "answers": {"Which cache?": "Use the local cache", "Anything else?": "no"},
            "annotations": {},
        },
    },
]


def _write(path: Path, lines: Sequence[object]) -> Path:
    path.write_text("\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines) + "\n")
    return path


def test_one_of_each(tmp_path: Path) -> None:
    inputs = read_host_inputs(_write(tmp_path / "t.jsonl", ONE_OF_EACH), since=SINCE, until=UNTIL)
    assert inputs is not None
    assert (inputs.prompts, inputs.dialog_answers) == (3, 2)


def test_lines_outside_the_window_are_not_counted(tmp_path: Path) -> None:
    early = {**_user("an early prompt"), "timestamp": "2026-11-11T23:00:00.000Z"}
    late = {**_user("a late prompt"), "timestamp": "2026-11-13T01:00:00.000Z"}
    no_time = {k: v for k, v in _user("no timestamp").items() if k != "timestamp"}
    inputs = read_host_inputs(
        _write(tmp_path / "t.jsonl", [early, late, no_time, _user("inside")]), since=SINCE, until=UNTIL
    )
    assert inputs is not None and inputs.prompts == 1


def test_garbage_lines_are_skipped(tmp_path: Path) -> None:
    inputs = read_host_inputs(
        _write(tmp_path / "t.jsonl", ["{not json", _user("a prompt"), "[]"]), since=SINCE, until=UNTIL
    )
    assert inputs is not None and inputs.prompts == 1


def test_a_string_tool_use_result_is_ignored(tmp_path: Path) -> None:
    line = {**_user([{"type": "tool_result", "tool_use_id": "t", "content": "x"}]), "toolUseResult": "Error: denied"}
    inputs = read_host_inputs(_write(tmp_path / "t.jsonl", [line]), since=SINCE, until=UNTIL)
    assert inputs is not None and (inputs.prompts, inputs.dialog_answers) == (0, 0)


def test_missing_or_garbage_files_give_none(tmp_path: Path) -> None:
    assert read_host_inputs(tmp_path / "absent.jsonl", since=SINCE, until=UNTIL) is None
    assert read_host_inputs(_write(tmp_path / "g.jsonl", ["{not json", "nope"]), since=SINCE, until=UNTIL) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("hello", True), ('<pasted_content id="a">x', True), ("", False), ("  /deploy", False), ("<div>", False)],
)
def test_is_plain_prompt(text: str, expected: bool) -> None:
    assert is_plain_prompt(text) is expected
