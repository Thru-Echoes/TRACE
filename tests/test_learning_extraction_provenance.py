"""Learning creation-path provenance (extraction_method / generated_by).

Every Learning records HOW it was created: "llm" (cloud extraction — content
is model output, so generated_by names the model), "rule-based" (local
extractor — content is quoted from session events), or "manual"
(trace_learn_add). Old stores predating the fields load as None.
"""

from __future__ import annotations

import json
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

from trace_mcp.extensions.learn.config import LearnConfig
from trace_mcp.extensions.learn.models import KnowledgeStore
from trace_mcp.extensions.learn.store import add_learning, learning_to_dict
from trace_mcp.schema import Session
from trace_mcp.schema.events import AnnotationData, TraceEvent
from trace_mcp.schema.session import Actor, SessionMetadata


def _session(events: list[TraceEvent]) -> Session:
    return Session(
        id="prov_test_session",
        metadata=SessionMetadata(
            project="prov-test-project",
            participants=[Actor(type="ai", id="ai-assistant")],
        ),
        events=events,
    )


def _annotation(event_id: str, category: Literal["learning", "correction"], content: str) -> TraceEvent:
    return TraceEvent(
        id=event_id,
        session_id="prov_test_session",
        type="annotation",
        actor=Actor(type="ai", id="ai-assistant"),
        annotation=AnnotationData(category=category, content=content),
    )


class TestExtractionMethodProvenance:
    def test_rule_based_extraction_marks_rule_based(self):
        from trace_mcp.extensions.learn.extraction import extract_from_session

        ks = KnowledgeStore(project="prov-test-project")
        session = _session([_annotation("evt_001", "learning", "quoted insight from events")])
        new_ids = extract_from_session(ks, session)
        assert new_ids
        lrn = ks.learnings[0]
        assert lrn.extraction_method == "rule-based"
        assert lrn.generated_by is None

    async def test_llm_extraction_marks_llm_and_model(self):
        from trace_mcp.extensions.learn.extraction import extract_from_session_llm

        config = LearnConfig(
            openai_api_key="sk-test",
            llm_extraction_model="gpt-5.4-mini",
            llm_enabled=True,
            strict_llm=False,
        )
        ks = KnowledgeStore(project="prov-test-project")
        session = _session([_annotation("evt_001", "learning", "raw event content")])

        llm_payload = {
            "learnings": [
                {
                    "content": "Synthesized insight (model output, not a quote)",
                    "category": "learning",
                    "tags": ["synthesized"],
                    "source_event": "evt_001",
                    "corrects_event_ids": [],
                }
            ]
        }
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(llm_payload)

        with patch("trace_mcp.extensions.learn.extraction._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.extraction.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                MockClient.return_value = mock_client
                new_ids = await extract_from_session_llm(ks, session, config)

        assert new_ids
        lrn = ks.learnings[0]
        assert lrn.extraction_method == "llm"
        assert lrn.generated_by == "gpt-5.4-mini"

    def test_manual_add_marks_manual(self):
        ks = KnowledgeStore(project="prov-test-project")
        lrn = add_learning(ks, content="hand-entered insight", extraction_method="manual")
        assert lrn.extraction_method == "manual"
        assert lrn.generated_by is None

    def test_old_store_records_load_as_none(self):
        """Stores written before the fields existed must load unchanged."""
        raw = {
            "project": "prov-test-project",
            "learnings": [
                {
                    "id": "lrn_001",
                    "content": "pre-existing learning",
                    "category": "learning",
                }
            ],
        }
        ks = KnowledgeStore.model_validate(raw)
        lrn = ks.learnings[0]
        assert lrn.extraction_method is None
        assert lrn.generated_by is None

    def test_tool_response_exposes_provenance_fields(self):
        """learning_to_dict (tool/API serialization) must carry the fields so
        clients can distinguish model-generated from quoted content."""
        ks = KnowledgeStore(project="prov-test-project")
        lrn = add_learning(ks, content="x", extraction_method="llm", generated_by="gpt-5.4-mini")
        d = learning_to_dict(lrn)
        assert d["extraction_method"] == "llm"
        assert d["generated_by"] == "gpt-5.4-mini"
