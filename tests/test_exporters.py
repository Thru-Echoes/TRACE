"""Tests for TRACE export formatters."""

from __future__ import annotations

import json

import pytest

from trace_mcp.exporters import export_markdown, export_prov_jsonld
from trace_mcp.schema import (
    Actor,
    AnnotationData,
    ContributionData,
    DecisionData,
    Environment,
    Session,
    SessionMetadata,
    StateChangeData,
    ToolCallData,
    TraceEvent,
)


@pytest.fixture
def sample_session() -> Session:
    """Create a session with all four event types for export testing."""
    session = Session(
        id="trace_20260205_abc123",
        metadata=SessionMetadata(
            project="Climate discourse analysis",
            experiment_id="exp-017",
            participants=[
                Actor(type="human", id="researcher-jane", role="lead"),
                Actor(type="ai", id="claude-sonnet-4", role="assistant"),
            ],
            environment=Environment(
                mcp_servers=["corpus-search-mcp"],
                client="claude-code",
                os="Darwin",
                trace_version="0.2.0",
            ),
            tags=["ipcc", "adaptation"],
        ),
        summary="Analyzed adaptation language shifts",
        events=[
            # Tool call
            TraceEvent(
                id="evt_001",
                session_id="trace_20260205_abc123",
                type="tool_call",
                actor=Actor(type="ai", id="claude-sonnet-4"),
                tool_call=ToolCallData(
                    server="corpus-search-mcp",
                    name="search_passages",
                    input={"query": "adaptation"},
                    output={"passages_found": 47},
                    duration_ms=3200,
                    status="success",
                ),
            ),
            # Decision: accepted
            TraceEvent(
                id="evt_002",
                session_id="trace_20260205_abc123",
                type="decision",
                actor=Actor(type="ai", id="claude-sonnet-4"),
                decision=DecisionData(
                    description="Use cosine similarity threshold of 0.85",
                    rationale="F1=0.78 on validation set",
                    proposed_by=Actor(type="ai", id="claude-sonnet-4"),
                    disposition="accepted",
                    resolved_by=Actor(type="human", id="researcher-jane"),
                ),
            ),
            # Decision: revised (with link to evt_002 for chain test)
            TraceEvent(
                id="evt_003",
                session_id="trace_20260205_abc123",
                type="decision",
                actor=Actor(type="ai", id="claude-sonnet-4"),
                decision=DecisionData(
                    description="Lower threshold to 0.80",
                    rationale="Higher recall for exploratory analysis",
                    proposed_by=Actor(type="human", id="researcher-jane"),
                    disposition="revised",
                    resolved_by=Actor(type="human", id="researcher-jane"),
                    revision_note="Want higher recall, will manually review extras",
                    revises_event_id="evt_002",
                ),
            ),
            # Annotation: gotcha
            TraceEvent(
                id="evt_004",
                session_id="trace_20260205_abc123",
                type="annotation",
                actor=Actor(type="ai", id="claude-sonnet-4"),
                annotation=AnnotationData(
                    category="gotcha",
                    content="Unicode encoding issues in IPCC PDFs",
                    related_event_ids=["evt_001"],
                    tags=["preprocessing"],
                ),
            ),
            # Contribution
            TraceEvent(
                id="evt_005",
                session_id="trace_20260205_abc123",
                type="contribution",
                actor=Actor(type="ai", id="claude-sonnet-4"),
                contribution=ContributionData(
                    description="Implemented cosine similarity function",
                    artifact="src/similarity.py",
                    direction="human",
                    execution="ai",
                    related_decision_ids=["evt_002"],
                    tags=["implementation"],
                ),
            ),
            # State change
            TraceEvent(
                id="evt_006",
                session_id="trace_20260205_abc123",
                type="state_change",
                actor=Actor(type="human", id="researcher-jane"),
                state_change=StateChangeData(
                    description="Switched embedding model",
                    field="environment.embedding_model",
                    old_value="MiniLM",
                    new_value="BGE-large",
                    reason="Domain sense conflation",
                ),
            ),
        ],
    )
    return session


# ── Markdown Export ──────────────────────────────────────────────────────────


class TestMarkdownExport:
    def test_contains_header(self, sample_session: Session) -> None:
        md = export_markdown(sample_session)
        assert "# TRACE Session: trace_20260205_abc123" in md

    def test_contains_project(self, sample_session: Session) -> None:
        md = export_markdown(sample_session)
        assert "Climate discourse analysis" in md

    def test_contains_decision_log(self, sample_session: Session) -> None:
        md = export_markdown(sample_session)
        assert "## Decision Log" in md

    def test_contains_tool_calls(self, sample_session: Session) -> None:
        md = export_markdown(sample_session)
        assert "## Tool Calls" in md
        assert "search_passages" in md

    def test_contains_annotations(self, sample_session: Session) -> None:
        md = export_markdown(sample_session)
        assert "## Annotations" in md
        assert "Unicode encoding" in md

    def test_contains_statistics(self, sample_session: Session) -> None:
        md = export_markdown(sample_session)
        assert "## Statistics" in md
        assert "Total events" in md

    def test_accepted_emoji(self, sample_session: Session) -> None:
        md = export_markdown(sample_session)
        assert "\u2705" in md  # check mark for accepted

    def test_revised_emoji(self, sample_session: Session) -> None:
        md = export_markdown(sample_session)
        assert "\u270f\ufe0f" in md  # pencil for revised

    def test_gotcha_emoji(self, sample_session: Session) -> None:
        md = export_markdown(sample_session)
        assert "\U0001f525" in md  # fire for gotcha

    def test_contains_contributions(self, sample_session: Session) -> None:
        md = export_markdown(sample_session)
        assert "## Contributions" in md
        assert "cosine similarity" in md
        assert "src/similarity.py" in md
        assert "human" in md  # direction

    def test_summary_section(self, sample_session: Session) -> None:
        md = export_markdown(sample_session)
        assert "## Summary" in md
        assert "Analyzed adaptation language shifts" in md


# ── PROV JSON-LD Export ──────────────────────────────────────────────────────


class TestProvJsonLdExport:
    def test_has_context(self, sample_session: Session) -> None:
        raw = export_prov_jsonld(sample_session)
        doc = json.loads(raw)
        ctx = doc["@context"]
        assert "prov" in ctx
        assert "trace" in ctx

    def test_has_agents(self, sample_session: Session) -> None:
        raw = export_prov_jsonld(sample_session)
        doc = json.loads(raw)
        bundle_key = list(doc["bundle"].keys())[0]
        bundle = doc["bundle"][bundle_key]
        assert "agent" in bundle
        assert any("researcher-jane" in k for k in bundle["agent"])

    def test_has_activities(self, sample_session: Session) -> None:
        raw = export_prov_jsonld(sample_session)
        doc = json.loads(raw)
        bundle_key = list(doc["bundle"].keys())[0]
        bundle = doc["bundle"][bundle_key]
        assert "activity" in bundle

    def test_decision_revision_link(self, sample_session: Session) -> None:
        raw = export_prov_jsonld(sample_session)
        doc = json.loads(raw)
        bundle_key = list(doc["bundle"].keys())[0]
        bundle = doc["bundle"][bundle_key]
        # evt_003 revises evt_002 so wasRevisionOf should exist
        assert "wasRevisionOf" in bundle
        revisions = bundle["wasRevisionOf"]
        assert len(revisions) > 0

    def test_contribution_activity(self, sample_session: Session) -> None:
        raw = export_prov_jsonld(sample_session)
        doc = json.loads(raw)
        bundle_key = list(doc["bundle"].keys())[0]
        bundle = doc["bundle"][bundle_key]
        # evt_005 is a contribution
        assert "trace:evt_005" in bundle["activity"]
        activity = bundle["activity"]["trace:evt_005"]
        assert activity["prov:type"] == "trace:Contribution"
        assert activity["trace:direction"] == "human"
        assert activity["trace:execution"] == "ai"

    def test_valid_json(self, sample_session: Session) -> None:
        raw = export_prov_jsonld(sample_session)
        doc = json.loads(raw)
        assert isinstance(doc, dict)
