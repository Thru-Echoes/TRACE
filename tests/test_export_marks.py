"""An export keeps the whole record and shows each event's marks beside it (ADR 008 D1).

The JSON export is the record itself: every event with its whole payload, and
no mark written into it. The Markdown and PROV exports are renderings; they
show the marks computed from the record, and the quote checks when the person's
recorded words are supplied.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from trace_mcp import extension_hooks
from trace_mcp.exporters.markdown_export import export_markdown
from trace_mcp.exporters.prov_jsonld import export_prov_jsonld
from trace_mcp.provenance_marks import WordsEntry, get_mark_legend
from trace_mcp.schema import Actor, Session, SessionMetadata, TraceEvent
from trace_mcp.schema.events import (
    AnnotationData,
    ContributionData,
    DecisionData,
    EventContext,
    StateChangeData,
    ToolCallData,
)
from trace_mcp.tools.export_tools import export_session

T = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
SID = "trace_20261101_aaaaaa"
AI = Actor(type="ai", id="ai-assistant")
ALICE = Actor(type="human", id="alice")


def _event(n: int, kind: str, **payload: object) -> TraceEvent:
    return TraceEvent(
        id=f"evt_{n:03d}",
        timestamp=T + timedelta(minutes=n),
        session_id=SID,
        type=kind,  # type: ignore[arg-type]
        actor=AI,
        **payload,  # type: ignore[arg-type]
    )


def _session() -> Session:
    accepted = {"disposition": "accepted", "resolved_by": ALICE, "proposed_by": AI}
    events = [
        _event(1, "annotation", annotation=AnnotationData(category="gotcha", content="The upload retries forever.")),
        _event(2, "decision", decision=DecisionData(description="Use exponential backoff.", proposed_by=AI)),
        _event(
            3,
            "decision",
            decision=DecisionData(
                description="Cap upload retries at three.",
                resolved_at=T + timedelta(minutes=4),
                resolution_snippet="yes, go with three retries",
                **accepted,  # type: ignore[arg-type]
            ),
            context=EventContext(conversation_snippet="keep the retry limit at three"),
        ),
        _event(
            5,
            "decision",
            decision=DecisionData(
                description="Log every retry.",
                resolved_at=T + timedelta(minutes=6),
                **accepted,  # type: ignore[arg-type]
            ),
        ),
        _event(
            7,
            "contribution",
            contribution=ContributionData(description="Capped the retries.", direction="human", execution="ai"),
            context=EventContext(conversation_snippet="keep the retry limit at three"),
        ),
        _event(8, "state_change", state_change=StateChangeData(description="Switched to the staging bucket.")),
        _event(9, "tool_call", tool_call=ToolCallData(server="s", name="upload", input={"file": "a.csv"})),
    ]
    return Session(id=SID, created=T, metadata=SessionMetadata(project="collab-a"), events=events)


def _words() -> list[WordsEntry]:
    return [
        WordsEntry(ts=T - timedelta(minutes=1), host_session_id="host-1", text="please keep the retry limit at three"),
        WordsEntry(
            ts=T + timedelta(minutes=3, seconds=30), host_session_id="host-1", text="yes, go with three retries"
        ),
    ]


def test_the_json_export_is_the_whole_record_with_no_mark_written_into_it() -> None:
    session = _session()
    out = export_session(session, format="json")
    assert Session.model_validate(json.loads(out)) == session
    assert [e["id"] for e in json.loads(out)["events"]] == [e.id for e in session.events]
    assert "[accepted]" not in out and "[proposed]" not in out and "provenanceMark" not in out


def test_markdown_shows_the_marks_the_record_alone_supports() -> None:
    text = export_markdown(_session())
    assert "- **Marks**: [proposed]\n" in text
    assert "- **Marks**: [accepted]\n" in text
    assert "- **Marks**: [accepted] [resolution-unquoted]\n" in text
    assert "*Marks*: [ai-note]\n" in text
    assert "quote-verified" not in text
    legend = get_mark_legend(["[ai-note]", "[proposed]", "[accepted]", "[resolution-unquoted]"])
    assert f"Marks are computed from this record. {legend}" in text


def test_markdown_checks_the_quotes_when_given_the_persons_words() -> None:
    text = export_markdown(_session(), words=_words())
    assert "- **Marks**: [accepted] [quote-verified] [resolution-verified]\n" in text
    assert "[quote-verified] the quoted words were found in the person's recorded words" in text


def test_markdown_lists_every_event_once_in_time_order() -> None:
    """The decision, tool and contribution sections are views; the event list at the end leaves nothing out."""
    session = _session()
    text = export_markdown(session)
    listing = text.split("## All events\n", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in listing.splitlines() if line.startswith("| evt_")]
    assert [row.split(" | ")[0].lstrip("| ") for row in rows] == [e.id for e in session.events]
    assert "| evt_008 | 10:08 | state_change | ai-assistant (ai) |  |" in listing
    assert "| evt_002 | 10:02 | decision | ai-assistant (ai) | [proposed] |" in listing


def test_prov_carries_the_marks_as_a_literal_beside_the_event() -> None:
    graph = {n["@id"]: n for n in json.loads(export_prov_jsonld(_session(), words=_words()))["@graph"]}
    assert graph["trace:evt_002"]["trace:provenanceMark"] == ["[proposed]"]
    assert graph["trace:evt_003"]["trace:provenanceMark"] == ["[accepted]", "[quote-verified]", "[resolution-verified]"]
    assert graph["trace:evt_005"]["trace:provenanceMark"] == ["[accepted]", "[resolution-unquoted]"]
    assert graph["trace:evt_001_annotation"]["trace:provenanceMark"] == ["[ai-note]"]
    assert graph["trace:evt_007"]["trace:provenanceMark"] == ["[quote-verified]"]
    assert "trace:provenanceMark" not in graph["trace:evt_008"]
    assert graph["trace:evt_003"]["trace:resolutionSnippet"] == "yes, go with three retries"


def test_prov_without_words_carries_only_what_the_record_supports() -> None:
    graph = {n["@id"]: n for n in json.loads(export_prov_jsonld(_session()))["@graph"]}
    assert graph["trace:evt_003"]["trace:provenanceMark"] == ["[accepted]"]
    assert "trace:provenanceMark" not in graph["trace:evt_007"]


def test_the_export_tool_uses_the_words_an_extension_supplies() -> None:
    session = _session()
    seen: list[str] = []

    def words_for(project: str) -> list[WordsEntry]:
        seen.append(project)
        return _words()

    extension_hooks.register_words_hook(words_for)
    try:
        text = export_session(session, format="markdown")
        prov = export_session(session, format="prov-jsonld")
    finally:
        extension_hooks.clear_hooks()
    assert seen == ["collab-a", "collab-a"]
    assert "[resolution-verified]" in text and "[resolution-verified]" in prov
    assert "[resolution-verified]" not in export_session(session, format="markdown")


def test_a_failing_words_hook_never_fails_an_export() -> None:
    def boom(_project: str) -> list[WordsEntry]:
        raise OSError("words unreadable")

    extension_hooks.register_words_hook(boom)
    try:
        text = export_session(_session(), format="markdown")
    finally:
        extension_hooks.clear_hooks()
    assert "- **Marks**: [accepted]\n" in text
