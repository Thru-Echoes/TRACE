"""Tests for the provenance-marks trial state, its append-only records, and the words reader.

Every test points TRACE_SHADOW_DIR at its own temporary directory.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from trace_mcp.extensions.learn.shadow.state import (
    ShadowState,
    ShadowStateError,
    append_record,
    get_project_dir,
    get_shadow_root,
    get_store_dir,
    is_active,
    load_state,
    record_error,
    set_excluded,
    start_trial,
    stop_trial,
)
from trace_mcp.extensions.learn.shadow.words import load_words

T0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def shadow_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shadow"
    monkeypatch.setenv("TRACE_SHADOW_DIR", str(root))
    return root


class _Line(BaseModel):
    n: int


def test_paths_follow_the_env(shadow_root: Path) -> None:
    assert get_shadow_root() == shadow_root
    assert get_project_dir("acme") == shadow_root / "acme"
    assert get_store_dir() == shadow_root / "stores"


def test_default_root_is_under_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACE_SHADOW_DIR", raising=False)
    assert get_shadow_root() == Path.home() / ".trace" / "shadow"


def test_fresh_root(shadow_root: Path) -> None:
    assert load_state() == ShadowState()
    assert is_active("acme") is False


def test_start_makes_every_project_active(shadow_root: Path) -> None:
    state = start_trial(now=T0)
    assert state.started_at == T0
    assert is_active("acme") is True
    assert (shadow_root / "STATE.json").exists()


def test_exclude_and_include(shadow_root: Path) -> None:
    start_trial(now=T0)
    set_excluded("Acme Co", True)
    assert is_active("acme-co") is False
    assert is_active("collab-a") is True
    set_excluded("acme-co", False)
    assert is_active("acme-co") is True


def test_stop(shadow_root: Path) -> None:
    start_trial(now=T0)
    state = stop_trial(now=T0 + timedelta(days=40))
    assert is_active("acme") is False
    assert state.stopped_at == T0 + timedelta(days=40)


def test_start_twice_is_refused(shadow_root: Path) -> None:
    start_trial(now=T0)
    with pytest.raises(ShadowStateError):
        start_trial(now=T0)


def test_restart_after_stop_is_refused_naming_the_stop_date(shadow_root: Path) -> None:
    start_trial(now=T0)
    stop_trial(now=datetime(2026, 12, 15, 9, 0, tzinfo=UTC))
    with pytest.raises(ShadowStateError, match="2026-12-15"):
        start_trial(now=T0)


def test_stop_when_never_started_is_refused(shadow_root: Path) -> None:
    with pytest.raises(ShadowStateError):
        stop_trial()


def test_corrupt_state_reads_as_off_and_is_left_alone(shadow_root: Path) -> None:
    shadow_root.mkdir(parents=True)
    path = shadow_root / "STATE.json"
    path.write_bytes(b"{not json")
    assert is_active("acme") is False
    with pytest.raises(ShadowStateError):
        load_state()
    assert path.read_bytes() == b"{not json"


def test_state_with_wrong_shape_reads_as_off(shadow_root: Path) -> None:
    shadow_root.mkdir(parents=True)
    (shadow_root / "STATE.json").write_text('{"started_at": "not a date"}')
    assert is_active("acme") is False
    with pytest.raises(ShadowStateError):
        load_state()


def test_append_record_writes_one_line_per_record_with_private_mode(shadow_root: Path) -> None:
    path = get_project_dir("acme") / "extraction.jsonl"
    append_record(path, _Line(n=1))
    append_record(path, _Line(n=2))
    lines = path.read_text().splitlines()
    assert [json.loads(line) for line in lines] == [{"n": 1}, {"n": 2}]
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_record_error_appends_to_the_project_errors_file(shadow_root: Path) -> None:
    record_error("acme", "extract", ValueError("x" * 900))
    lines = (get_project_dir("acme") / "errors.jsonl").read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["project_key"] == "acme"
    assert entry["step"] == "extract"
    assert entry["error_type"] == "ValueError"
    assert len(entry["message"]) == 500


def test_record_error_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "shadow"
    blocker.write_text("a file where the root should be")
    monkeypatch.setenv("TRACE_SHADOW_DIR", str(blocker))
    assert record_error("acme", "extract", RuntimeError("boom")) is None


def _write_words(path: Path, entries: list[dict | str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for entry in entries:
            fh.write((entry if isinstance(entry, str) else json.dumps(entry)) + "\n")


def _entry(minutes: int, text: str, **extra) -> dict:
    return {"ts": (T0 + timedelta(minutes=minutes)).isoformat(), "host_session_id": "h", "text": text, **extra}


def test_load_words_merges_files_and_skips_bad_lines(shadow_root: Path) -> None:
    words_dir = get_project_dir("acme") / "words"
    _write_words(words_dir / "host-a.jsonl", [_entry(0, "first"), "garbage line", _entry(4, "fourth")])
    _write_words(
        words_dir / "host-b.jsonl",
        [
            _entry(2, "second", kind="dialog_answer", question="Which cache?"),
            _entry(3, "third", kind="something-else"),
        ],
    )
    words = load_words("acme")
    assert [w.text for w in words] == ["first", "second", "fourth"]
    assert words[0].kind == "prompt"
    assert words[1].kind == "dialog_answer"
    assert words[1].question == "Which cache?"


def test_load_words_missing_directory(shadow_root: Path) -> None:
    assert load_words("acme") == []


def test_load_words_skips_times_without_a_timezone(shadow_root: Path) -> None:
    words_dir = get_project_dir("acme") / "words"
    naive = {"ts": "2026-11-01T10:00:00", "host_session_id": "h", "text": "no zone"}
    _write_words(words_dir / "host-a.jsonl", [naive, _entry(1, "zoned")])
    assert [w.text for w in load_words("acme")] == ["zoned"]


def test_append_record_creates_every_missing_directory_private(shadow_root: Path) -> None:
    path = get_project_dir("acme") / "words" / "host-1.jsonl"
    append_record(path, _Line(n=1))
    for directory in (shadow_root, shadow_root / "acme", shadow_root / "acme" / "words"):
        assert directory.stat().st_mode & 0o777 == 0o700, directory


def test_starting_creates_a_private_root(shadow_root: Path) -> None:
    start_trial(now=T0)
    assert shadow_root.stat().st_mode & 0o777 == 0o700
