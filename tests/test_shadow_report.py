"""The read-only comparison report and review sheet (ADR 008 D7).

The input is built by running the real steps: the trial is started, two of the
person's words are recorded, a session is extracted by both arms with scripted
replies (the live arm names evt_002 and evt_003, the shadow arm evt_002 only),
and one recall runs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _shadow_helpers import ScriptedClient, learn_env, make_session, reply

from trace_mcp import extension_hooks
from trace_mcp.extensions.learn import extraction
from trace_mcp.extensions.learn.shadow.extract import ArmRecord, ExtractionRecord, LearningAtExtraction
from trace_mcp.extensions.learn.shadow.report import (
    ShadowReport,
    build_report,
    format_report,
    get_review_rows,
    write_review_csv,
)
from trace_mcp.extensions.learn.shadow.state import append_record, get_project_dir, start_trial
from trace_mcp.provenance_marks import WordsEntry
from trace_mcp.schema import Session
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import decision_tools

PROJECT = "collab-a"
WORDS = ("please keep the retry limit at three", "yes, go with three retries")


def _iso_z(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _write_transcript(path: Path, at: datetime) -> None:
    lines = [
        {"type": "user", "timestamp": _iso_z(at), "message": {"role": "user", "content": WORDS[0]}},
        {
            "type": "user",
            "timestamp": _iso_z(at + timedelta(seconds=1)),
            "message": {"role": "user", "content": WORDS[1]},
        },
        {
            "type": "user",
            "timestamp": _iso_z(at + timedelta(seconds=2)),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
            "toolUseResult": {"questions": [], "answers": {"Which cache?": "local", "Anything else?": "no"}},
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def _words_file() -> Path:
    return get_project_dir(PROJECT) / "words" / "host-1.jsonl"


async def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    resolution_quote: str = "yes, go with three retries",
    contribution_snippet: str | None = None,
) -> tuple[JsonFileStorage, Session]:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    transcript = tmp_path / "host" / "host-1.jsonl"
    began = datetime.now(UTC) - timedelta(seconds=3)
    _write_transcript(transcript, began)
    for i, text in enumerate(WORDS):
        append_record(
            _words_file(),
            WordsEntry(
                ts=began + timedelta(seconds=i), host_session_id="host-1", text=text, transcript=str(transcript)
            ),
        )
    client = ScriptedClient(
        [
            reply(
                ("Cap retries at three for uploads.", "evt_002"), ("Back off exponentially between retries.", "evt_003")
            ),
            reply(("It was proposed that retries are capped at three.", "evt_002")),
        ]
    )
    monkeypatch.setattr(extraction, "AsyncOpenAI", lambda **_kw: client)
    session = await make_session(storage, resolution_quote=resolution_quote, contribution_snippet=contribution_snippet)
    result = await extension_hooks.extract_if_available(PROJECT, session.id)
    assert result.error is None
    # Recall on the local ranker: re-register with cloud extraction off (the LLM ranker would try a real request).
    storage = learn_env(tmp_path, monkeypatch, llm=False)
    await extension_hooks.recall_if_available(PROJECT, "upload retries", None, 5)
    return storage, session


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


# ── measures from the real fixture ────────────────────────────────────────


async def test_the_report_of_one_session_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _fixture(tmp_path, monkeypatch)
    total = build_report().total

    assert total.session_ends == 1
    assert total.cost["live"].calls == 1
    assert total.cost["shadow"].prompt_tokens == 120
    assert total.learnings_by_mark["shadow"] == {"[proposed]": 1}
    assert total.learnings_by_mark["live"] == {"[proposed]": 1, "[accepted]": 1}
    assert (total.paired, total.live_only, total.shadow_only) == (1, 1, 0)

    (recall,) = _jsonl(get_project_dir(PROJECT) / "recall.jsonl")
    assert total.recall_calls == 1
    assert (total.recall_chars_without, total.recall_chars_with) == (recall["chars_without"], recall["chars_with"])

    assert total.resolution == {"verified": 1, "absent": 1}
    assert total.resolution_by_quote == {"verified": {"absent": 1}, "absent": {"absent": 1}}
    assert total.resolution_timing == {"same_turn": 2}
    assert total.resolution_position == {"turn": 1}
    # The session holds evt_001 (annotation) and evt_002..evt_004 (decisions); none carries a snippet.
    assert total.quote == {"absent": 4}
    assert total.quote_miss_share == {}
    assert total.quote_position == {}

    assert set(total.similarity) == {"live", "shadow"}
    assert total.similarity == {"live": {"no_vector": 2}, "shadow": {"no_vector": 1}}
    assert total.words_by_kind == {"prompt": 2}
    assert total.words_entries == 2
    assert total.suspect_entries == 0
    assert total.resolved_since_extraction == {"live": 0, "shadow": 0}
    assert total.errors_by_step == {}
    assert total.unreadable_sessions == 0
    assert build_report().projects_without_words == []


async def test_a_quote_that_is_not_found_is_split_by_word_share(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _fixture(tmp_path, monkeypatch, contribution_snippet="keep the retry limit at four")
    total = build_report().total
    assert total.quote == {"absent": 4, "not_found": 1}
    assert total.quote_miss_share == {"0.5-0.9": 1}
    assert any(row.kind == "quote_not_found" and row.event_id == "evt_005" for row in get_review_rows())


async def test_a_quote_found_only_in_older_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _fixture(tmp_path, monkeypatch, contribution_snippet="please keep the retry limit")
    assert build_report().total.quote_position == {"older": 1}


async def test_a_resolution_quote_found_only_in_older_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _fixture(tmp_path, monkeypatch, resolution_quote="please keep the retry limit")
    assert build_report().total.resolution_position == {"older": 1}
    rows = [row for row in get_review_rows() if row.kind == "resolution_older"]
    assert [(row.event_id, row.session_id != "") for row in rows] == [("evt_003", True)]


def test_similarity_bands_from_hand_written_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_SHADOW_DIR", str(tmp_path / "shadow"))
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    start_trial()
    cosines = [0.97, 0.95, 0.91, 0.85, 0.4, None]
    shadow = ArmRecord(
        path="llm",
        learnings=[
            LearningAtExtraction(
                id=f"slrn_{i:03d}", category="learning", tags=["[accepted]"], nearest_existing_cosine=c
            )
            for i, c in enumerate(cosines, start=1)
        ],
    )
    append_record(
        get_project_dir(PROJECT) / "extraction.jsonl",
        ExtractionRecord(
            ts=datetime.now(UTC),
            project_key=PROJECT,
            session_id="trace_20261112_aaaaaa",
            live=ArmRecord(path="skipped"),
            shadow=shadow,
            nearest_n=50,
            store_size=0,
            selection="all",
            query_chars=0,
            query_truncated=False,
            seeded_now=True,
        ),
    )
    similarity = build_report().total.similarity
    assert similarity["shadow"] == {"0.95+": 2, "0.90-0.95": 1, "0.85-0.90": 1, "<0.85": 1, "no_vector": 1}
    assert similarity["live"] == {}


async def test_words_kinds_and_suspect_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _fixture(tmp_path, monkeypatch)
    now = datetime.now(UTC)
    append_record(
        _words_file(),
        WordsEntry(ts=now, host_session_id="host-1", text="local", kind="dialog_answer", question="Which cache?"),
    )
    assert build_report().total.words_by_kind == {"prompt": 2, "dialog_answer": 1}
    append_record(_words_file(), WordsEntry(ts=now, host_session_id="host-1", text="<system-reminder>x"))
    assert build_report().total.suspect_entries == 1


async def test_capture_against_the_host_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _fixture(tmp_path, monkeypatch)
    capture = build_report().total.capture
    assert capture.host_sessions == 1
    assert (capture.prompts_in_transcript, capture.prompts_recorded) == (2, 2)
    assert (capture.dialog_answers_in_transcript, capture.dialog_answers_recorded) == (2, 0)

    transcript = tmp_path / "host" / "host-1.jsonl"
    transcript.write_text("{not json\n")
    assert build_report().total.capture.transcripts_unreadable == 1

    transcript.unlink()
    capture = build_report().total.capture
    assert capture.transcripts_missing == 1
    assert (capture.prompts_in_transcript, capture.prompts_recorded) == (0, 0)
    assert (capture.dialog_answers_in_transcript, capture.dialog_answers_recorded) == (0, 0)


async def test_a_decision_resolved_after_extraction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage, session = await _fixture(tmp_path, monkeypatch)
    before = build_report().total
    assert before.resolved_since_extraction == {"live": 0, "shadow": 0}

    stored = await storage.get_session(session.id)
    await decision_tools.resolve_decision(
        storage, stored, event_id="evt_002", disposition="accepted", resolved_by_type="human", resolved_by_id="alice"
    )
    after = build_report().total
    assert after.resolved_since_extraction == {"live": 1, "shadow": 1}
    assert after.learnings_by_mark == before.learnings_by_mark


async def test_projects_without_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _fixture(tmp_path, monkeypatch)
    shutil.rmtree(get_project_dir(PROJECT) / "words")
    assert build_report().projects_without_words == [PROJECT]


async def test_format_report_uses_counts_with_denominators(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _fixture(tmp_path, monkeypatch)
    text = format_report(build_report())
    assert "TOTAL" in text
    assert "1/2" in text
    assert "%" not in text
    assert "not captured by any hook: text typed when declining a tool call" in text


async def test_review_rows_and_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _fixture(tmp_path, monkeypatch)
    rows = get_review_rows()
    kinds = [row.kind for row in rows]
    unaccepted = [row for row in rows if row.kind == "unaccepted_source"]
    assert len(unaccepted) == 2 and {row.event_id for row in unaccepted} == {"evt_002"}
    assert {(row.live_id is not None, row.shadow_id is not None) for row in unaccepted} == {
        (True, False),
        (False, True),
    }
    assert [row.event_id for row in rows if row.kind == "resolution_unquoted"] == ["evt_004"]
    assert "live_only" in kinds and "shadow_only" not in kinds

    path = tmp_path / "review.csv"
    assert write_review_csv(path, rows) == len(rows)
    with path.open(newline="") as fh:
        table = list(csv.reader(fh))
    assert table[0][:2] == ["project", "kind"]
    assert table[0][-4:] == ["reviewer", "mark_correct", "useful", "reworded_as_directive"]
    assert len(table) == len(rows) + 1
    assert all(line[-4:] == ["", "", "", ""] for line in table[1:])


def _digest(*roots: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


async def test_the_report_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _fixture(tmp_path, monkeypatch)
    roots = (tmp_path / "shadow", tmp_path / "sessions", tmp_path / "knowledge")
    before = _digest(*roots)
    assert isinstance(build_report(), ShadowReport)
    get_review_rows()
    assert _digest(*roots) == before


async def test_an_unreadable_session_file_is_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _fixture(tmp_path, monkeypatch)
    (tmp_path / "sessions" / "trace_20261112_broken.json").write_text("{not json")
    assert build_report().total.unreadable_sessions == 1
