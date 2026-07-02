"""Egress ledger (egress-as-provenance) — behavior tests.

Pins the two load-bearing properties of the ledger (INV-5, docs/INVARIANTS.md):

1. **Pre-call attestation**: every cloud call site appends its ledger line
   BEFORE the OpenAI-SDK request is made.
2. **Fail closed**: when the ledger cannot be written, the cloud call does not
   happen at all — and under permissive (non-strict) config the caller falls
   back to the local path, so nothing leaves the machine and nothing breaks.

The AST-level site enumeration lives in tests/test_invariants.py (INV-5).
"""

from __future__ import annotations

import json
import os
import stat
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trace_mcp.extensions.learn.config import LearnConfig
from trace_mcp.extensions.learn.egress import EgressAttestationError, attest_egress, egress_log_path
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.schema import Session
from trace_mcp.schema.events import AnnotationData, TraceEvent
from trace_mcp.schema.session import Actor, SessionMetadata

# ── Helpers ───────────────────────────────────────────────────────────────


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Point the egress ledger at a per-test file and return its path."""
    path = tmp_path / "egress.jsonl"
    monkeypatch.setenv("TRACE_EGRESS_LOG", str(path))
    return path


@pytest.fixture
def blocked_ledger(tmp_path, monkeypatch):
    """Point the ledger at an unwritable location (parent 'dir' is a file)."""
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    path = blocker / "nested" / "egress.jsonl"
    monkeypatch.setenv("TRACE_EGRESS_LOG", str(path))
    return path


def _read_lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _session(events: list[TraceEvent] | None = None) -> Session:
    return Session(
        id="egress_test_session",
        metadata=SessionMetadata(
            project="egress-test-project",
            participants=[Actor(type="ai", id="ai-assistant")],
        ),
        events=events or [],
    )


def _annotation(event_id: str, category: Literal["learning", "correction"], content: str) -> TraceEvent:
    return TraceEvent(
        id=event_id,
        session_id="egress_test_session",
        type="annotation",
        actor=Actor(type="ai", id="ai-assistant"),
        annotation=AnnotationData(category=category, content=content),
    )


def _chat_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    return response


# ── The writer itself ─────────────────────────────────────────────────────


class TestAttestEgress:
    def test_appends_one_json_line_per_call(self, ledger):
        attest_egress(
            provider="openai",
            endpoint="chat.completions",
            model="gpt-5.4-mini",
            purpose="extraction",
            content_class="session-events",
            item_count=7,
            project="proj-a",
            session_id="sess-1",
        )
        attest_egress(
            provider="openai",
            endpoint="embeddings",
            model="text-embedding-3-small",
            purpose="embedding",
            content_class="learning-or-query-text",
            item_count=3,
        )
        entries = _read_lines(ledger)
        assert len(entries) == 2
        first, second = entries
        assert first["endpoint"] == "chat.completions"
        assert first["project"] == "proj-a"
        assert first["session_id"] == "sess-1"
        assert first["item_count"] == 7
        assert "ts" in first
        assert second["endpoint"] == "embeddings"
        assert second["project"] is None
        # The FACT of egress is recorded — never the content.
        for entry in entries:
            assert "content" not in entry

    def test_ledger_created_with_owner_only_mode(self, ledger):
        attest_egress(
            provider="openai",
            endpoint="embeddings",
            model="m",
            purpose="embedding",
            content_class="learning-or-query-text",
            item_count=1,
        )
        mode = stat.S_IMODE(os.stat(ledger).st_mode)
        assert mode == 0o600

    def test_creates_parent_directories(self, tmp_path, monkeypatch):
        path = tmp_path / "deep" / "nested" / "egress.jsonl"
        monkeypatch.setenv("TRACE_EGRESS_LOG", str(path))
        attest_egress(
            provider="openai",
            endpoint="embeddings",
            model="m",
            purpose="embedding",
            content_class="learning-or-query-text",
            item_count=1,
        )
        assert path.is_file()

    def test_env_override_resolves_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRACE_EGRESS_LOG", str(tmp_path / "custom.jsonl"))
        assert egress_log_path() == tmp_path / "custom.jsonl"

    def test_default_path_is_trace_home(self, monkeypatch):
        monkeypatch.delenv("TRACE_EGRESS_LOG", raising=False)
        assert egress_log_path().name == "egress.jsonl"
        assert egress_log_path().parent.name == ".trace"

    def test_unwritable_ledger_fails_closed(self, blocked_ledger):
        with pytest.raises(EgressAttestationError, match="TRACE_EGRESS_LOG"):
            attest_egress(
                provider="openai",
                endpoint="embeddings",
                model="m",
                purpose="embedding",
                content_class="learning-or-query-text",
                item_count=1,
            )


# ── Embeddings call site ──────────────────────────────────────────────────

_EMB = "trace_mcp.extensions.learn.embeddings"


class TestOpenAIEmbeddingEgress:
    def _provider_with_mock_client(self):
        from trace_mcp.extensions.learn.embeddings import OpenAIEmbeddingProvider

        with patch(f"{_EMB}._HAS_OPENAI", True):
            with patch("openai.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                MockClient.return_value = mock_client
                provider = OpenAIEmbeddingProvider(api_key="sk-test", model="text-embedding-3-small")
        return provider, mock_client

    async def test_attests_before_the_request(self, ledger):
        provider, mock_client = self._provider_with_mock_client()

        def _assert_ledger_written(**kwargs):
            entries = _read_lines(ledger)
            assert entries and entries[-1]["endpoint"] == "embeddings", (
                "embeddings.create ran before the egress attestation was written"
            )
            response = MagicMock()
            response.data = [MagicMock(embedding=[0.1, 0.2])]
            return response

        mock_client.embeddings.create = AsyncMock(side_effect=_assert_ledger_written)

        vecs = await provider.embed_texts(["hello"])
        assert vecs == [[0.1, 0.2]]
        entry = _read_lines(ledger)[-1]
        assert entry["purpose"] == "embedding"
        assert entry["item_count"] == 1
        assert entry["model"] == "text-embedding-3-small"

    async def test_no_egress_when_ledger_unwritable(self, blocked_ledger):
        """THE property: if the fact of egress cannot be recorded, no content
        may leave the machine."""
        provider, mock_client = self._provider_with_mock_client()
        mock_client.embeddings.create = AsyncMock()

        with pytest.raises(EgressAttestationError):
            await provider.embed_texts(["secret content"])
        mock_client.embeddings.create.assert_not_called()


# ── Extraction call site ──────────────────────────────────────────────────


class TestExtractionEgress:
    async def test_llm_extraction_attests_with_project_and_session(self, ledger):
        from trace_mcp.extensions.learn.extraction import extract_from_session_llm

        config = LearnConfig(openai_api_key="sk-test", llm_enabled=True, strict_llm=False)
        session = _session([_annotation("evt_001", "learning", "something worth keeping")])
        ks = KnowledgeStore(project="egress-test-project")

        with patch("trace_mcp.extensions.learn.extraction._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.extraction.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=_chat_response({"learnings": []}))
                MockClient.return_value = mock_client
                await extract_from_session_llm(ks, session, config)

        entry = _read_lines(ledger)[-1]
        assert entry["purpose"] == "extraction"
        assert entry["endpoint"] == "chat.completions"
        assert entry["project"] == "egress-test-project"
        assert entry["session_id"] == "egress_test_session"
        assert entry["item_count"] == 1

    async def test_blocked_ledger_falls_back_to_rule_based(self, blocked_ledger):
        """Permissive mode + unwritable ledger: no cloud call, rule-based
        extraction still delivers learnings."""
        from trace_mcp.extensions.learn.extraction import extract_from_session_llm

        config = LearnConfig(openai_api_key="sk-test", llm_enabled=True, strict_llm=False)
        session = _session([_annotation("evt_001", "correction", "the model was wrong about X")])
        ks = KnowledgeStore(project="egress-test-project")

        with patch("trace_mcp.extensions.learn.extraction._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.extraction.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock()
                MockClient.return_value = mock_client
                new_ids = await extract_from_session_llm(ks, session, config)

        mock_client.chat.completions.create.assert_not_called()
        assert new_ids, "rule-based fallback should still extract the correction"

    async def test_blocked_ledger_raises_in_strict_mode(self, blocked_ledger):
        from trace_mcp.extensions.learn.config import LLMFallbackError
        from trace_mcp.extensions.learn.extraction import extract_from_session_llm

        config = LearnConfig(openai_api_key="sk-test", llm_enabled=True, strict_llm=True)
        session = _session([_annotation("evt_001", "learning", "content")])
        ks = KnowledgeStore(project="egress-test-project")

        with patch("trace_mcp.extensions.learn.extraction._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.extraction.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock()
                MockClient.return_value = mock_client
                with pytest.raises(LLMFallbackError):
                    await extract_from_session_llm(ks, session, config)
        mock_client.chat.completions.create.assert_not_called()


# ── Matching call site ────────────────────────────────────────────────────


class TestMatchingEgress:
    def _learnings(self) -> list[Learning]:
        return [
            Learning(id="lrn_001", content="use ml-dev conda env", tags=["conda"]),
            Learning(id="lrn_002", content="pin numpy below 2.0", tags=["numpy"]),
        ]

    async def test_llm_matching_attests(self, ledger):
        from trace_mcp.extensions.learn import matching

        config = LearnConfig(openai_api_key="sk-test", llm_enabled=True, strict_llm=False)
        with patch.object(matching, "_HAS_OPENAI", True):
            with patch.object(matching, "AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=_chat_response({"0": 0.9, "1": 0.1}))
                MockClient.return_value = mock_client
                backend = matching.LLMBackend(config)
                scores = await backend.score_batch(self._learnings(), "conda environment setup")

        assert scores
        entry = _read_lines(ledger)[-1]
        assert entry["purpose"] == "matching"
        assert entry["endpoint"] == "chat.completions"
        assert entry["item_count"] == 2

    async def test_blocked_ledger_falls_back_to_bm25(self, blocked_ledger):
        """Permissive mode + unwritable ledger: no cloud call, BM25 still scores."""
        from trace_mcp.extensions.learn import matching

        config = LearnConfig(openai_api_key="sk-test", llm_enabled=True, strict_llm=False)
        with patch.object(matching, "_HAS_OPENAI", True):
            with patch.object(matching, "AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock()
                MockClient.return_value = mock_client
                backend = matching.LLMBackend(config)
                scores = await backend.score_batch(self._learnings(), "conda environment setup")

        mock_client.chat.completions.create.assert_not_called()
        assert scores, "BM25 fallback should still return scores"
