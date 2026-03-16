"""Standalone tests for trace-learn extraction (extraction.py).

Tests: rule-based extraction (annotations, decisions, contributions),
LLM-enhanced extraction (mocked), idempotency, metadata preservation.
"""

from __future__ import annotations

import json
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trace_mcp.extensions.learn.config import LearnConfig
from trace_mcp.extensions.learn.extraction import (
    _EXTRACTABLE_CATEGORIES,
    extract_from_session,
    extract_from_session_auto,
    extract_from_session_llm,
)
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.schema import Session
from trace_mcp.schema.events import (
    AnnotationData,
    ContributionData,
    DecisionData,
    TraceEvent,
)
from trace_mcp.schema.session import Actor, SessionMetadata

# ── Helpers ───────────────────────────────────────────────────────────────


def _session(events: list[TraceEvent] | None = None, session_id: str = "test_session_001") -> Session:
    return Session(
        id=session_id,
        metadata=SessionMetadata(
            project="test-project",
            participants=[Actor(type="ai", id="ai-assistant")],
        ),
        events=events or [],
    )


def _annotation(
    event_id: str,
    category: Literal["learning", "gotcha", "observation", "correction", "todo", "question", "other"],
    content: str,
    tags: list[str] | None = None,
    corrects_event_ids: list[str] | None = None,
) -> TraceEvent:
    return TraceEvent(
        id=event_id,
        session_id="test_session_001",
        type="annotation",
        actor=Actor(type="ai", id="ai-assistant"),
        annotation=AnnotationData(
            category=category,
            content=content,
            tags=tags or [],
            corrects_event_ids=corrects_event_ids or [],
        ),
    )


def _decision(
    event_id: str,
    description: str,
    disposition: Literal["proposed", "accepted", "revised", "rejected"] = "proposed",
    rationale: str | None = None,
    revision_note: str | None = None,
    suggestion_type: Literal["proactive", "requested", "collaborative"] | None = None,
    tags: list[str] | None = None,
) -> TraceEvent:
    resolved_by = Actor(type="human", id="researcher") if disposition != "proposed" else None
    return TraceEvent(
        id=event_id,
        session_id="test_session_001",
        type="decision",
        actor=Actor(type="ai", id="ai-assistant"),
        decision=DecisionData(
            description=description,
            proposed_by=Actor(type="ai", id="ai-assistant"),
            disposition=disposition,
            resolved_by=resolved_by,
            rationale=rationale,
            revision_note=revision_note,
            suggestion_type=suggestion_type,
            tags=tags or [],
        ),
    )


def _contribution(
    event_id: str,
    description: str,
    direction: Literal["human", "ai", "collaborative"] = "collaborative",
    execution: Literal["human", "ai", "collaborative"] = "ai",
    artifact: str | None = None,
    tags: list[str] | None = None,
) -> TraceEvent:
    return TraceEvent(
        id=event_id,
        session_id="test_session_001",
        type="contribution",
        actor=Actor(type="ai", id="ai-assistant"),
        contribution=ContributionData(
            description=description,
            direction=direction,
            execution=execution,
            artifact=artifact,
            tags=tags or [],
        ),
    )


# ── Rule-based extraction: annotations ────────────────────────────────────


class TestAnnotationExtraction:
    """Tests for extracting learnings from annotation events."""

    def test_extract_learning(self):
        session = _session([_annotation("evt_001", "learning", "Always check data types")])
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 1
        assert ks.learnings[0].content == "Always check data types"
        assert ks.learnings[0].category == "learning"
        assert ks.learnings[0].source_session == "test_session_001"
        assert ks.learnings[0].source_event == "evt_001"

    def test_extract_correction(self):
        session = _session([
            _annotation("evt_001", "correction", "Wrong env — use ml-dev", corrects_event_ids=["evt_000"])
        ])
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 1
        assert ks.learnings[0].category == "correction"

    def test_extract_correction_preserves_corrects_event_ids(self):
        """corrects_event_ids from annotation are preserved in the learning."""
        session = _session([
            _annotation(
                "evt_001",
                "correction",
                "Fixed wrong env",
                corrects_event_ids=["evt_010", "evt_011"],
            )
        ])
        ks = KnowledgeStore(project="test")
        extract_from_session(ks, session)
        assert ks.learnings[0].corrects_event_ids == ["evt_010", "evt_011"]

    def test_extract_gotcha(self):
        session = _session([_annotation("evt_001", "gotcha", "ffmpeg device indices change")])
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 1
        assert ks.learnings[0].category == "gotcha"

    def test_extract_preserves_tags(self):
        session = _session([
            _annotation("evt_001", "learning", "test", tags=["conda", "env"])
        ])
        ks = KnowledgeStore(project="test")
        extract_from_session(ks, session)
        assert ks.learnings[0].tags == ["conda", "env"]

    def test_skip_observation(self):
        session = _session([_annotation("evt_001", "observation", "Just noting")])
        ks = KnowledgeStore(project="test")
        assert extract_from_session(ks, session) == []

    def test_skip_todo(self):
        session = _session([_annotation("evt_001", "todo", "Fix later")])
        ks = KnowledgeStore(project="test")
        assert extract_from_session(ks, session) == []

    def test_skip_question(self):
        session = _session([_annotation("evt_001", "question", "Why does this happen?")])
        ks = KnowledgeStore(project="test")
        assert extract_from_session(ks, session) == []

    def test_extractable_categories_match(self):
        """Verify the extractable set includes the right categories."""
        assert _EXTRACTABLE_CATEGORIES == {"learning", "correction", "gotcha"}


# ── Rule-based extraction: decisions ──────────────────────────────────────


class TestDecisionExtraction:
    """Tests for extracting learnings from decision events."""

    def test_extract_rejected_decision(self):
        session = _session([
            _decision(
                "evt_001",
                "Use base conda env",
                disposition="rejected",
                revision_note="Always use ml-dev",
            )
        ])
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 1
        assert "ml-dev" in ks.learnings[0].content
        assert ks.learnings[0].category == "decision"

    def test_extract_revised_decision(self):
        session = _session([
            _decision(
                "evt_001",
                "Use GPU for training",
                disposition="revised",
                revision_note="Use CPU — GPU quota exhausted",
            )
        ])
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 1
        assert "CPU" in ks.learnings[0].content

    def test_skip_accepted_decision(self):
        session = _session([
            _decision("evt_001", "Use pandas", disposition="accepted")
        ])
        ks = KnowledgeStore(project="test")
        assert extract_from_session(ks, session) == []

    def test_skip_proposed_decision(self):
        session = _session([_decision("evt_001", "Maybe use spark")])
        ks = KnowledgeStore(project="test")
        assert extract_from_session(ks, session) == []

    def test_preserves_rationale(self):
        """Decision rationale is included in the extracted learning content."""
        session = _session([
            _decision(
                "evt_001",
                "Switch to BM25",
                disposition="rejected",
                rationale="Jaccard is too simple for semantic matching",
                revision_note="Use LLM instead",
            )
        ])
        ks = KnowledgeStore(project="test")
        extract_from_session(ks, session)
        content = ks.learnings[0].content
        assert "Rationale:" in content
        assert "Jaccard" in content

    def test_preserves_suggestion_type_as_tag(self):
        """suggestion_type is added as a tag on the extracted learning."""
        session = _session([
            _decision(
                "evt_001",
                "Use BM25",
                disposition="rejected",
                suggestion_type="proactive",
                revision_note="Rejected",
                tags=["matching"],
            )
        ])
        ks = KnowledgeStore(project="test")
        extract_from_session(ks, session)
        assert "proactive" in ks.learnings[0].tags
        assert "matching" in ks.learnings[0].tags


# ── Rule-based extraction: contributions ──────────────────────────────────


class TestContributionExtraction:
    """Tests for extracting learnings from contribution events."""

    def test_extract_collaborative_contribution(self):
        session = _session([
            _contribution(
                "evt_001",
                "Developed hybrid matching approach",
                direction="collaborative",
                artifact="matching.py",
                tags=["matching"],
            )
        ])
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 1
        assert "hybrid matching" in ks.learnings[0].content
        assert "matching.py" in ks.learnings[0].content
        assert ks.learnings[0].category == "observation"

    def test_skip_human_directed_contribution(self):
        session = _session([
            _contribution("evt_001", "Wrote the code", direction="human")
        ])
        ks = KnowledgeStore(project="test")
        assert extract_from_session(ks, session) == []

    def test_skip_ai_directed_contribution(self):
        session = _session([
            _contribution("evt_001", "AI suggested this", direction="ai")
        ])
        ks = KnowledgeStore(project="test")
        assert extract_from_session(ks, session) == []


# ── Idempotency ──────────────────────────────────────────────────────────


class TestIdempotency:
    def test_double_extraction_no_duplicates(self):
        session = _session([
            _annotation("evt_001", "learning", "insight A"),
            _annotation("evt_002", "gotcha", "gotcha B"),
        ])
        ks = KnowledgeStore(project="test")

        ids1 = extract_from_session(ks, session)
        ids2 = extract_from_session(ks, session)
        assert len(ids1) == 2
        assert len(ids2) == 0
        assert len(ks.learnings) == 2

    def test_idempotent_across_sessions(self):
        """Extraction from different sessions doesn't conflict."""
        session_a = _session(
            [_annotation("evt_001", "learning", "insight A")],
            session_id="sess_A",
        )
        session_b = _session(
            [_annotation("evt_001", "learning", "insight B")],
            session_id="sess_B",
        )
        ks = KnowledgeStore(project="test")

        ids_a = extract_from_session(ks, session_a)
        ids_b = extract_from_session(ks, session_b)
        assert len(ids_a) == 1
        assert len(ids_b) == 1
        assert len(ks.learnings) == 2


# ── Multi-event extraction ────────────────────────────────────────────────


class TestMultiEventExtraction:
    def test_mixed_events(self):
        """Extract from a session with multiple event types."""
        session = _session([
            _annotation("evt_001", "correction", "Wrong env", corrects_event_ids=["evt_000"]),
            _annotation("evt_002", "learning", "Always activate first"),
            _annotation("evt_003", "observation", "Pipeline took 45 min"),
            _decision(
                "evt_004",
                "Use GPU",
                disposition="revised",
                revision_note="Use CPU instead",
                tags=["compute"],
            ),
            _contribution(
                "evt_005",
                "Collaborative analysis approach",
                direction="collaborative",
                tags=["analysis"],
            ),
        ])
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, session)
        # correction + learning + revised decision + collaborative contribution = 4
        # observation is skipped
        assert len(new_ids) == 4
        categories = [lrn.category for lrn in ks.learnings]
        assert "correction" in categories
        assert "learning" in categories
        assert "decision" in categories
        assert "observation" in categories  # From contribution

    def test_empty_session(self):
        session = _session([])
        ks = KnowledgeStore(project="test")
        assert extract_from_session(ks, session) == []


# ── LLM extraction (mocked) ──────────────────────────────────────────────


class TestLLMExtraction:
    """Tests for LLM-enhanced extraction with mocked OpenAI."""

    def _make_config(self) -> LearnConfig:
        return LearnConfig(
            openai_api_key="test-key",
            llm_extraction_model="gpt-5-mini",
            llm_enabled=True,
        )

    async def test_llm_extraction_basic(self):
        config = self._make_config()
        session = _session([
            _annotation("evt_001", "correction", "Wrong conda env"),
            _annotation("evt_002", "learning", "Always use ml-dev"),
        ])
        ks = KnowledgeStore(project="test")

        llm_response = {
            "learnings": [
                {
                    "content": "Always use ml-dev conda environment, not base",
                    "category": "correction",
                    "tags": ["conda", "env"],
                    "source_event": "evt_001",
                    "corrects_event_ids": [],
                },
                {
                    "content": "Activate ml-dev before any pip install",
                    "category": "learning",
                    "tags": ["conda", "pip"],
                    "source_event": "evt_002",
                    "corrects_event_ids": [],
                },
            ]
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(llm_response)

        with patch("trace_mcp.extensions.learn.extraction._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.extraction.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                MockClient.return_value = mock_client

                new_ids = await extract_from_session_llm(ks, session, config)

        assert len(new_ids) == 2
        assert ks.learnings[0].content == "Always use ml-dev conda environment, not base"

    async def test_llm_extraction_fallback_on_error(self):
        """When LLM fails, falls back to rule-based extraction."""
        config = self._make_config()
        session = _session([
            _annotation("evt_001", "learning", "Rule-based fallback content"),
        ])
        ks = KnowledgeStore(project="test")

        with patch("trace_mcp.extensions.learn.extraction._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.extraction.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(
                    side_effect=Exception("API error")
                )
                MockClient.return_value = mock_client

                new_ids = await extract_from_session_llm(ks, session, config)

        # Should have extracted via rule-based fallback
        assert len(new_ids) == 1
        assert ks.learnings[0].content == "Rule-based fallback content"

    async def test_llm_extraction_idempotent(self):
        """LLM extraction skips already-extracted events."""
        config = self._make_config()
        session = _session([
            _annotation("evt_001", "learning", "Already extracted"),
        ])
        ks = KnowledgeStore(project="test")
        # Pre-add a learning from this event
        ks.learnings.append(
            Learning(id="lrn_001", content="Already extracted", source_session="test_session_001", source_event="evt_001")
        )

        llm_response = {
            "learnings": [
                {
                    "content": "Already extracted",
                    "category": "learning",
                    "source_event": "evt_001",
                    "tags": [],
                },
            ]
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(llm_response)

        with patch("trace_mcp.extensions.learn.extraction._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.extraction.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                MockClient.return_value = mock_client

                new_ids = await extract_from_session_llm(ks, session, config)

        assert len(new_ids) == 0
        assert len(ks.learnings) == 1  # No duplicates


# ── Auto-selection tests ──────────────────────────────────────────────────


class TestAutoExtraction:
    async def test_auto_uses_rule_based_when_no_llm(self):
        config = LearnConfig(openai_api_key=None, llm_enabled=False)
        session = _session([_annotation("evt_001", "learning", "test")])
        ks = KnowledgeStore(project="test")

        new_ids = await extract_from_session_auto(ks, session, config)
        assert len(new_ids) == 1

    async def test_auto_uses_llm_when_configured(self):
        config = LearnConfig(openai_api_key="test-key", llm_enabled=True)
        session = _session([_annotation("evt_001", "learning", "test")])
        ks = KnowledgeStore(project="test")

        llm_response = {
            "learnings": [
                {
                    "content": "LLM-enhanced learning",
                    "category": "learning",
                    "source_event": "evt_001",
                    "tags": ["enhanced"],
                },
            ]
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(llm_response)

        with patch("trace_mcp.extensions.learn.extraction._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.extraction.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                MockClient.return_value = mock_client

                new_ids = await extract_from_session_auto(ks, session, config)

        assert len(new_ids) == 1
        assert ks.learnings[0].content == "LLM-enhanced learning"
