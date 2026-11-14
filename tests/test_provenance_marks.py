"""Tests for the provenance-marks core module (ADR 008 D1, D2).

Every rule of the decision table in ``trace_mcp.provenance_marks`` has a
parametrized case here, built from small in-memory events and words entries
at fixed times T0 < T1 < T2 < T3. No I/O.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from trace_mcp.provenance_marks import (
    MARK_LEGEND,
    MIN_FRAGMENT_CHARS,
    SOURCE_UNKNOWN_TAG,
    EventMarks,
    WordsEntry,
    get_base_mark,
    get_event_marks,
    get_quote_position,
    get_quote_status,
    get_quote_word_share,
    get_resolution_quote,
    get_resolution_timing,
    get_snippet_fragments,
    get_turn_entries,
    normalize_quote,
)
from trace_mcp.schema import Actor, TraceEvent
from trace_mcp.schema.events import (
    AnnotationData,
    ContributionData,
    DecisionData,
    EventContext,
    ToolCallData,
)

T0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)
T3 = T0 + timedelta(minutes=3)
SECOND = timedelta(seconds=1)


def _decision(
    disposition: str,
    *,
    resolver: str | None = None,
    ts: datetime = T1,
    resolved_at: datetime | None = None,
    snippet: str | None = None,
    resolution_snippet: str | None = None,
) -> TraceEvent:
    """Build a decision event proposed by an AI at ``ts``."""
    resolved_by = Actor(type=resolver, id=f"{resolver}-1") if resolver else None  # type: ignore[arg-type]
    note = "changed" if disposition in ("revised", "rejected") else None
    return TraceEvent(
        id="evt_001",
        timestamp=ts,
        session_id="trace_test",
        type="decision",
        actor=Actor(type="ai", id="ai-1"),
        decision=DecisionData(
            description="use three retries",
            proposed_by=Actor(type="ai", id="ai-1"),
            disposition=disposition,  # type: ignore[arg-type]
            resolved_by=resolved_by,
            resolved_at=resolved_at,
            resolution_snippet=resolution_snippet,
            revision_note=note,
        ),
        context=EventContext(conversation_snippet=snippet),
    )


def _human_decision(resolved_at: datetime | None, resolution_snippet: str | None, snippet: str | None = None):
    return _decision(
        "accepted", resolver="human", resolved_at=resolved_at, resolution_snippet=resolution_snippet, snippet=snippet
    )


def _annotation(actor_type: str, *, snippet: str | None = None, ts: datetime = T1) -> TraceEvent:
    return TraceEvent(
        id="evt_002",
        timestamp=ts,
        session_id="trace_test",
        type="annotation",
        actor=Actor(type=actor_type, id=f"{actor_type}-1"),  # type: ignore[arg-type]
        annotation=AnnotationData(category="learning", content="a note"),
        context=EventContext(conversation_snippet=snippet),
    )


def _contribution() -> TraceEvent:
    return TraceEvent(
        session_id="trace_test",
        type="contribution",
        actor=Actor(type="ai", id="ai-1"),
        contribution=ContributionData(description="wrote it", direction="human", execution="ai"),
    )


def _tool_call() -> TraceEvent:
    return TraceEvent(
        session_id="trace_test",
        type="tool_call",
        actor=Actor(type="ai", id="ai-1"),
        tool_call=ToolCallData(server="s", name="n", input={}),
    )


def _words(*items: tuple) -> list[WordsEntry]:
    """Words entries from ``(ts, text)`` or ``(ts, text, kind)`` tuples."""
    out = []
    for item in items:
        ts, text = item[0], item[1]
        kind = item[2] if len(item) > 2 else "prompt"
        out.append(WordsEntry(ts=ts, host_session_id="host-1", text=text, kind=kind))
    return out


# --------------------------------------------------------------------------- constants


def test_constants():
    assert MIN_FRAGMENT_CHARS == 8
    assert SOURCE_UNKNOWN_TAG == "[source-unknown]"
    assert MARK_LEGEND == (
        "Marks: [accepted] a person accepted it; [revised] a person changed it, so its text may not be what "
        "they approved; [proposed] an AI proposal no person resolved "
        "(a suggestion, not a rule); [rejected] a person rejected it; [ai-resolved] resolved by an AI, "
        "not a person; [ai-note] an AI's own note; [ai-record] an AI's or tool's record of work; no person "
        "resolved it; [person-note] a note recorded in a person's name; [source-unknown] the source event "
        "could not be found."
    )


def test_models_are_frozen_and_ignore_unknown_fields():
    entry = WordsEntry.model_validate(
        {"ts": T0, "host_session_id": "h", "text": "x", "kind": "prompt", "something_new": 1}
    )
    assert not hasattr(entry, "something_new")
    with pytest.raises(ValidationError):
        entry.text = "y"  # type: ignore[misc]
    marks = EventMarks(base=None, quote="absent", resolution="not_applicable")
    with pytest.raises(ValidationError):
        marks.base = "accepted"  # type: ignore[misc]


# --------------------------------------------------------------------------- base marks


@pytest.mark.parametrize(
    ("disposition", "resolver", "expected"),
    [
        ("proposed", None, "proposed"),
        ("accepted", "human", "accepted"),
        ("revised", "human", "revised"),
        ("rejected", "human", "rejected"),
        ("accepted", "ai", "ai-resolved"),
        ("rejected", "system", "ai-resolved"),
    ],
)
def test_base_mark_for_decisions(disposition, resolver, expected):
    assert get_base_mark(_decision(disposition, resolver=resolver)) == (expected, True)


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (_annotation("ai"), "ai-note"),
        (_annotation("human"), None),
        (_contribution(), None),
        (_tool_call(), None),
    ],
)
def test_base_mark_for_other_events(event, expected):
    assert get_base_mark(event) == (expected, True)


@pytest.mark.parametrize(
    ("resolved_at", "as_of", "expected"),
    [
        (T2, T1 + SECOND, ("proposed", True)),
        (T2, T2, ("accepted", True)),
        (T2, T3, ("accepted", True)),
        (None, T1 + SECOND, ("accepted", False)),
    ],
)
def test_base_mark_as_of(resolved_at, as_of, expected):
    event = _decision("accepted", resolver="human", resolved_at=resolved_at)
    assert get_base_mark(event, as_of=as_of) == expected


# --------------------------------------------------------------------------- text


def test_normalize_quote():
    assert normalize_quote("  Yes,\n LET'S  go ") == "yes, let's go"
    assert normalize_quote("é") == normalize_quote("é")


def test_snippet_fragments():
    assert get_snippet_fragments("keep the retry limit ... at three please … ok") == [
        "keep the retry limit",
        "at three please",
    ]


# --------------------------------------------------------------------------- quote status


@pytest.mark.parametrize(
    ("words", "snippet", "expected"),
    [
        (_words((T0, "Keep  the RETRY limit at three")), "keep the retry limit", "verified"),
        (
            _words((T0, "keep the retry limit"), (T1, "at three please")),
            "keep the retry limit ... at three please",
            "verified",
        ),
        (_words((T0, "something else entirely")), "keep the retry limit", "not_found"),
        (_words((T0, "unrelated message"), (T3, "keep the retry limit")), "keep the retry limit", "not_found"),
        (
            _words((T0, "Use the local cache (Recommended)", "dialog_answer")),
            "use the local cache",
            "verified",
        ),
        (_words((T0, "keep the retry limit")), None, "absent"),
        (_words((T0, "keep the retry limit")), "", "absent"),
        (_words((T0, "keep the retry limit")), "<autonomous-stretch>", "absence_marker"),
        (_words((T0, "keep the retry limit")), " <no recent user message> ", "absence_marker"),
        ([], "keep the retry limit", "unchecked"),
        (_words((T2, "keep the retry limit")), "keep the retry limit", "unchecked"),
        (_words((T0, "hello there friend")), "ok ... yes", "too_short"),
        (_words((T0, "Yes!")), "yes", "verified"),
        (_words((T0, "yes please rename it too")), "yes", "too_short"),
        (_words((T0, "yes"), (T1, "now rename the module please")), "yes", "too_short"),
        (_words((T0, "which one do you want"), (T1, "Yes", "dialog_answer")), "yes", "verified"),
        (_words((T0, "...")), "!?", "too_short"),
    ],
)
def test_quote_status(words, snippet, expected):
    assert get_quote_status(snippet, words, not_after=T2, session_created=T1) == expected


# --------------------------------------------------------------------------- turn entries

_P0, _D1, _P2, _D3 = _words((T0, "p0"), (T1, "d1", "dialog_answer"), (T2, "p2"), (T3, "d3", "dialog_answer"))


@pytest.mark.parametrize(
    ("words", "not_after", "expected"),
    [
        ([_P0, _D1, _P2, _D3], T3, [_P2, _D3]),
        ([_P0, _D1, _P2, _D3], T1, [_P0, _D1]),
        (_words((T0, "d0", "dialog_answer"), (T1, "d1", "dialog_answer")), T2, None),
        ([], T2, []),
    ],
)
def test_turn_entries(words, not_after, expected):
    if expected is None:
        expected = words
    assert get_turn_entries(words, not_after=not_after) == expected


def test_turn_entries_ignores_input_order():
    assert get_turn_entries([_D3, _P2, _D1, _P0], not_after=T3) == [_P2, _D3]


# --------------------------------------------------------------------------- quote position


@pytest.mark.parametrize(
    ("words", "snippet", "expected"),
    [
        (_words((T0, "hello there"), (T1, "keep the retry limit at three")), "keep the retry limit", "turn"),
        (_words((T0, "keep the retry limit at three"), (T1, "and ship it today")), "keep the retry limit", "older"),
        (
            _words((T0, "keep the retry limit"), (T1, "at three please")),
            "keep the retry limit ... at three please",
            "older",
        ),
        (
            _words((T0, "which cache"), (T1, "Use the local cache (Recommended)", "dialog_answer")),
            "use the local cache",
            "turn",
        ),
        (_words((T0, "Yes!")), "yes", "turn"),
    ],
)
def test_quote_position(words, snippet, expected):
    assert get_quote_status(snippet, words, not_after=T2, session_created=T1) == "verified"
    assert get_quote_position(snippet, words, not_after=T2) == expected


# --------------------------------------------------------------------------- word share


@pytest.mark.parametrize(
    ("words", "snippet", "expected"),
    [
        (_words((T0, "please keep the retry limit at three")), "keep the retry limit at four", pytest.approx(0.8)),
        (_words((T0, "keep the retry limit")), "keep the retry limit ... delete every old log", pytest.approx(0.0)),
        (_words((T3, "keep the retry limit at four")), "keep the retry limit at four", 0.0),
    ],
)
def test_quote_word_share(words, snippet, expected):
    assert get_quote_word_share(snippet, words, not_after=T2) == expected


# --------------------------------------------------------------------------- resolution quote

_EARLIER = (T0, "an earlier message")


@pytest.mark.parametrize(
    ("resolved_at", "resolution_snippet", "extra", "expected"),
    [
        (T3, "yes, go with three retries", [(T2, "Yes, go with three retries please")], "verified"),
        (T2, "yes, go with three retries", [(T3, "yes, go with three retries")], "not_found"),
        (T3, None, [], "absent"),
        (T3, "<autonomous-stretch>", [], "absence_marker"),
        (T3, "yes", [(T2, "yes")], "verified"),
        (T3, "yes", [(T2, "yes, and also rename it")], "too_short"),
        (T3, "yes", [(T1, "yes"), (T2, "also update the docs")], "too_short"),
        (None, "yes, go with three retries", [], "unknown"),
    ],
)
def test_resolution_quote(resolved_at, resolution_snippet, extra, expected):
    event = _human_decision(resolved_at, resolution_snippet)
    words = _words(_EARLIER, *extra)
    assert get_resolution_quote(event, words, session_created=T1) == expected


def test_resolution_quote_unchecked_without_words():
    event = _human_decision(T3, "yes, go with three retries")
    assert get_resolution_quote(event, [], session_created=T1) == "unchecked"


@pytest.mark.parametrize(
    "event",
    [_decision("proposed"), _decision("accepted", resolver="ai", resolved_at=T2), _annotation("human")],
)
def test_resolution_quote_not_applicable(event):
    assert get_resolution_quote(event, _words(_EARLIER), session_created=T1) == "not_applicable"


def test_event_marks_as_of_before_resolution():
    event = _human_decision(T2, "yes, go with three retries")
    words = _words(_EARLIER, (T2 - SECOND, "yes, go with three retries"))
    assert get_resolution_quote(event, words, session_created=T1) == "verified"
    marks = get_event_marks(event, words, session_created=T1, as_of=T1 + SECOND)
    assert marks.base == "proposed"
    assert marks.resolution == "not_applicable"


# --------------------------------------------------------------------------- resolution timing


@pytest.mark.parametrize(
    ("resolved_at", "words", "expected"),
    [
        (T3, _words((T2, "go ahead")), "person_spoke_between"),
        (T3, _words((T0, "go ahead")), "same_turn"),
        (T2, _words((T0, "x"), (T3, "go ahead")), "same_turn"),
        (None, _words((T2, "go ahead")), "unknown"),
        (T2, [], "unknown"),
    ],
)
def test_resolution_timing(resolved_at, words, expected):
    assert get_resolution_timing(_human_decision(resolved_at, None), words) == expected


@pytest.mark.parametrize("event", [_decision("proposed"), _decision("accepted", resolver="ai", resolved_at=T2)])
def test_resolution_timing_not_applicable(event):
    assert get_resolution_timing(event, _words((T2, "go ahead"))) == "not_applicable"


# --------------------------------------------------------------------------- tags


@pytest.mark.parametrize(
    ("marks", "expected"),
    [
        (
            EventMarks(base="accepted", quote="verified", resolution="verified"),
            ["[accepted]", "[quote-verified]", "[resolution-verified]"],
        ),
        (
            EventMarks(base="accepted", quote="not_found", resolution="absent"),
            ["[accepted]", "[quote-not-found]", "[resolution-unquoted]"],
        ),
        (EventMarks(base="rejected", quote="absent", resolution="not_found"), ["[rejected]", "[resolution-not-found]"]),
        (EventMarks(base="accepted", quote="absent", resolution="unknown"), ["[accepted]"]),
        (EventMarks(base="accepted", quote="absent", resolution="too_short"), ["[accepted]"]),
        (EventMarks(base="accepted", quote="absent", resolution="absence_marker"), ["[accepted]"]),
        (EventMarks(base="accepted", quote="absent", resolution="unchecked"), ["[accepted]"]),
        (EventMarks(base=None, quote="verified", resolution="not_applicable"), ["[quote-verified]"]),
        (EventMarks(base="proposed", quote="absent", resolution="not_applicable"), ["[proposed]"]),
    ],
)
def test_tags(marks, expected):
    assert marks.tags() == expected


def test_human_annotation_with_verified_quote_tags():
    event = _annotation("human", snippet="keep the retry limit", ts=T2)
    marks = get_event_marks(event, _words((T0, "keep the retry limit at three")), session_created=T1)
    assert marks.tags() == ["[quote-verified]"]


def test_timing_never_reaches_a_tag():
    quote = "yes, go with three retries"
    event = _human_decision(T3, quote, snippet="keep the retry limit")
    # Same quote results both ways: the snippet and the resolution quote are found in both word sets.
    with_between = _words((T0, "keep the retry limit, and yes, go with three retries"), (T2, "an aside"))
    without_between = _words((T0, "keep the retry limit, and yes, go with three retries"))
    assert get_resolution_timing(event, with_between) == "person_spoke_between"
    assert get_resolution_timing(event, without_between) == "same_turn"
    a = get_event_marks(event, with_between, session_created=T1)
    b = get_event_marks(event, without_between, session_created=T1)
    assert a.quote == b.quote and a.resolution == b.resolution
    assert a.tags() == b.tags()


# --------------------------------------------------------------------------- timestamps with no timezone


def _naive(ts: datetime) -> datetime:
    return ts.replace(tzinfo=None)


def test_a_timestamp_with_no_timezone_is_read_as_utc():
    """A record written without an offset must not crash a comparison with words recorded in UTC."""
    event = _decision(
        "accepted",
        resolver="human",
        ts=_naive(T1),
        resolved_at=_naive(T2),
        snippet="keep the retry limit at three",
        resolution_snippet="yes, go with three retries",
    )
    words = _words((T0, "please keep the retry limit at three"), (T2 - SECOND, "yes, go with three retries"))
    marks = get_event_marks(event, words, session_created=_naive(T0 + SECOND))
    assert marks.tags() == ["[accepted]", "[quote-verified]", "[resolution-verified]"]
    assert get_resolution_timing(event, words) == "person_spoke_between"
    assert get_base_mark(event, as_of=T1 + SECOND) == ("proposed", True)
    assert get_event_marks(event, words, session_created=T1, as_of=_naive(T1 + SECOND)).base == "proposed"


def test_words_recorded_with_no_timezone_are_read_as_utc():
    words = [WordsEntry(ts=_naive(T0), host_session_id="host-1", text="please keep the retry limit at three")]
    event = _decision("proposed", snippet="keep the retry limit at three")
    assert get_quote_status("keep the retry limit at three", words, not_after=T1, session_created=T1) == "verified"
    assert get_quote_position("keep the retry limit at three", words, not_after=T1) == "turn"
    assert get_quote_word_share("keep the retry limit at four", words, not_after=T1) > 0.5
    assert get_event_marks(event, words, session_created=T1).quote == "verified"


def test_is_human_resolution_is_public_and_shared():
    """One definition of "resolved by a person" serves the marks and the report."""
    from trace_mcp import provenance_marks
    from trace_mcp.extensions.learn.shadow import report

    assert provenance_marks.is_human_resolution(_human_decision(T2, None)) is True
    assert provenance_marks.is_human_resolution(_decision("accepted", resolver="ai")) is False
    assert provenance_marks.is_human_resolution(_decision("proposed")) is False
    assert provenance_marks.is_human_resolution(_annotation("human")) is False
    assert not hasattr(report, "_is_human_resolution"), "the report must use the shared helper, not a copy"
    assert report.is_human_resolution is provenance_marks.is_human_resolution


# --------------------------------------------------------------------------- boundary guard


def test_module_imports_nothing_from_extensions():
    source = Path(__file__).resolve().parents[1] / "src" / "trace_mcp" / "provenance_marks.py"
    tree = ast.parse(source.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert names, "expected the module to import something"
    assert not [n for n in names if "extensions" in n]
