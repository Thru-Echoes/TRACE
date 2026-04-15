"""End-to-end integration tests for the trace-learn extension.

Tests the full workflow across ALL modules:
  session → extract → persist → recall → verify

Tests both backend paths:
  - BM25 (pure Python, always available)
  - LLM (mocked OpenAI)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trace_mcp.extensions.learn.config import LearnConfig
from trace_mcp.extensions.learn.extraction import (
    extract_from_session,
    extract_from_session_auto,
)
from trace_mcp.extensions.learn.matching import (
    BM25Backend,
    recall_learnings,
)
from trace_mcp.extensions.learn.models import KnowledgeStore
from trace_mcp.extensions.learn.store import (
    add_learning,
    load_store,
    remove_learning,
    save_store,
)
from trace_mcp.schema import Session
from trace_mcp.schema.events import (
    AnnotationData,
    ContributionData,
    DecisionData,
    TraceEvent,
)
from trace_mcp.schema.session import Actor, SessionMetadata


# ── Helpers ───────────────────────────────────────────────────────────────


def _session(
    events: list[TraceEvent],
    session_id: str = "test_session_001",
    project: str = "test-project",
) -> Session:
    return Session(
        id=session_id,
        metadata=SessionMetadata(
            project=project,
            participants=[Actor(type="ai", id="ai-assistant")],
        ),
        events=events,
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


# ── E2E: Full workflow with BM25 backend ─────────────────────────────────


class TestE2EWithBM25:
    """Complete workflow: extract → persist → recall using BM25."""

    async def test_full_workflow(self, tmp_path):
        """Session → extract → save → load → recall → verify results."""
        # 1. Create a realistic session
        events = [
            _annotation(
                "evt_001",
                "correction",
                "Use ml-dev conda environment, not base — base lacks scikit-learn",
                tags=["conda", "env", "critical"],
                corrects_event_ids=["evt_000"],
            ),
            _annotation(
                "evt_002",
                "learning",
                "Always activate conda environment before pip install to avoid path conflicts",
                tags=["conda", "pip", "workflow"],
            ),
            _annotation(
                "evt_003",
                "observation",
                "Pipeline completed in 45 minutes",
            ),
            _decision(
                "evt_004",
                "Use GPU instance for training",
                disposition="revised",
                rationale="GPU seemed faster for large models",
                revision_note="Use CPU instead — GPU quota exhausted this month",
                tags=["compute", "resource"],
            ),
            _contribution(
                "evt_005",
                "Developed collaborative data preprocessing pipeline",
                direction="collaborative",
                artifact="preprocessing.py",
                tags=["pipeline", "data"],
            ),
        ]
        session = _session(events)

        # 2. Extract learnings
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 4  # correction + learning + decision + contribution

        # 3. Persist to disk
        save_store(ks, directory=str(tmp_path))

        # 4. Load from disk (simulating a new session)
        loaded = load_store("test", directory=str(tmp_path))
        assert len(loaded.learnings) == 4

        # 5. Recall with conda query — should find conda-related learnings
        results = await recall_learnings(
            loaded.learnings,
            context="which conda environment should I use for ML tasks",
            context_tags=["conda"],
            threshold=0.05,
            backend=BM25Backend(),
        )
        assert len(results) > 0
        top_content = results[0]["learning"]["content"].lower()
        assert "conda" in top_content or "env" in top_content

        # 6. Recall with GPU query — should find the compute decision
        results = await recall_learnings(
            loaded.learnings,
            context="should I use GPU or CPU for training",
            context_tags=["compute"],
            threshold=0.05,
            backend=BM25Backend(),
        )
        assert len(results) > 0
        all_content = " ".join(r["learning"]["content"].lower() for r in results)
        assert "gpu" in all_content or "cpu" in all_content

        # 7. Cooking query should return nothing relevant
        results = await recall_learnings(
            loaded.learnings,
            context="best pasta recipe with fresh tomatoes",
            threshold=0.2,
            backend=BM25Backend(),
        )
        assert len(results) == 0

    async def test_idempotent_extraction_across_persist(self, tmp_path):
        """Extract → save → load → extract again → no duplicates."""
        session = _session([
            _annotation("evt_001", "learning", "Important insight"),
            _annotation("evt_002", "gotcha", "Surprising behavior"),
        ])

        # First extraction
        ks = KnowledgeStore(project="test")
        ids1 = extract_from_session(ks, session)
        save_store(ks, directory=str(tmp_path))

        # Load and extract again
        loaded = load_store("test", directory=str(tmp_path))
        ids2 = extract_from_session(loaded, session)
        assert len(ids1) == 2
        assert len(ids2) == 0
        assert len(loaded.learnings) == 2

    async def test_multi_session_extraction_and_recall(self, tmp_path):
        """Extract from multiple sessions → recall finds learnings from all."""
        session_a = _session(
            [_annotation("evt_001", "correction", "Always use ml-dev conda env", tags=["conda"])],
            session_id="sess_A",
        )
        session_b = _session(
            [_annotation("evt_001", "gotcha", "ffmpeg audio device indices are unstable on macOS", tags=["ffmpeg", "macos"])],
            session_id="sess_B",
        )
        session_c = _session(
            [_annotation("evt_001", "learning", "Log decisions before implementing, not after", tags=["trace", "logging"])],
            session_id="sess_C",
        )

        ks = KnowledgeStore(project="test")
        for sess in [session_a, session_b, session_c]:
            extract_from_session(ks, sess)
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("test", directory=str(tmp_path))
        assert len(loaded.learnings) == 3

        # Each query should find the right learning
        conda_results = await recall_learnings(
            loaded.learnings, "conda environment", threshold=0.05, backend=BM25Backend()
        )
        assert any("conda" in r["learning"]["content"].lower() for r in conda_results)

        ffmpeg_results = await recall_learnings(
            loaded.learnings, "audio device ffmpeg", threshold=0.05, backend=BM25Backend()
        )
        assert any("ffmpeg" in r["learning"]["content"].lower() for r in ffmpeg_results)

        logging_results = await recall_learnings(
            loaded.learnings, "log decisions before implementing", threshold=0.05, backend=BM25Backend()
        )
        assert any("log" in r["learning"]["content"].lower() for r in logging_results)

    async def test_add_then_recall(self, tmp_path):
        """Manually added learnings are recallable."""
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Use pyright for type checking", category="learning", tags=["pyright", "typing"])
        add_learning(ks, content="Run ruff before committing", category="learning", tags=["ruff", "lint"])
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("test", directory=str(tmp_path))
        results = await recall_learnings(
            loaded.learnings,
            "type checking python",
            context_tags=["typing"],
            threshold=0.05,
            backend=BM25Backend(),
        )
        assert len(results) > 0
        assert any("pyright" in r["learning"]["content"].lower() for r in results)

    async def test_remove_then_recall(self, tmp_path):
        """Removed learnings no longer appear in recall."""
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Old incorrect info", tags=["outdated"])
        add_learning(ks, content="Current correct info", tags=["current"])
        remove_learning(ks, "lrn_001")
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("test", directory=str(tmp_path))
        assert len(loaded.learnings) == 1
        results = await recall_learnings(
            loaded.learnings, "info", threshold=0.0, backend=BM25Backend()
        )
        ids = [r["learning"]["id"] for r in results]
        assert "lrn_001" not in ids
        assert "lrn_002" in ids


# ── E2E: Full workflow with LLM backend (mocked) ─────────────────────────


class TestE2EWithLLM:
    """Complete workflow using mocked LLM for both extraction and recall."""

    def _make_config(self) -> LearnConfig:
        return LearnConfig(
            openai_api_key="test-key",
            llm_model="gpt-5.4-mini",
            llm_extraction_model="gpt-5.4-mini",
            llm_enabled=True,
        )

    async def test_llm_extract_and_recall(self, tmp_path):
        """LLM extraction → persist → LLM recall → verify."""
        config = self._make_config()

        events = [
            _annotation("evt_001", "correction", "Wrong conda env — use ml-dev"),
            _annotation("evt_002", "learning", "Log decisions before implementing"),
        ]
        session = _session(events)
        ks = KnowledgeStore(project="test")

        # Mock LLM extraction
        extraction_response = {
            "learnings": [
                {
                    "content": "Always use the ml-dev conda environment instead of base for ML work",
                    "category": "correction",
                    "tags": ["conda", "environment", "ml-dev"],
                    "source_event": "evt_001",
                },
                {
                    "content": "Decisions must be logged in TRACE before implementing them",
                    "category": "learning",
                    "tags": ["trace", "logging", "workflow"],
                    "source_event": "evt_002",
                },
            ]
        }
        mock_extract_response = MagicMock()
        mock_extract_response.choices = [MagicMock()]
        mock_extract_response.choices[0].message.content = json.dumps(extraction_response)

        with patch("trace_mcp.extensions.learn.extraction._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.extraction.AsyncOpenAI") as MockExtrClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_extract_response)
                MockExtrClient.return_value = mock_client

                new_ids = await extract_from_session_auto(ks, session, config)

        assert len(new_ids) == 2
        save_store(ks, directory=str(tmp_path))

        # Mock LLM recall
        recall_response = {"0": 0.92, "1": 0.15}
        mock_recall_response = MagicMock()
        mock_recall_response.choices = [MagicMock()]
        mock_recall_response.choices[0].message.content = json.dumps(recall_response)

        loaded = load_store("test", directory=str(tmp_path))

        with patch("trace_mcp.extensions.learn.matching._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.matching.AsyncOpenAI") as MockRecallClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_recall_response)
                MockRecallClient.return_value = mock_client

                from trace_mcp.extensions.learn.matching import LLMBackend

                backend = LLMBackend(config)
                backend._client = mock_client

                results = await recall_learnings(
                    loaded.learnings,
                    "which conda environment should I use",
                    threshold=0.1,
                    backend=backend,
                )

        assert len(results) >= 1
        assert results[0]["score"] >= 0.9  # LLM gave 0.92 to conda learning


# ── E2E: Backend fallback behavior ───────────────────────────────────────


class TestE2EBackendFallback:
    """Tests that demonstrate graceful fallback from LLM to BM25."""

    async def test_llm_fails_bm25_succeeds(self, tmp_path):
        """Permissive mode: when LLM API fails, recall still works via BM25 fallback."""
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Use ml-dev conda env for ML", tags=["conda"])
        add_learning(ks, content="Pasta recipe with basil", tags=["food"])
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("test", directory=str(tmp_path))
        # Explicitly permissive: strict_llm=False opts into silent BM25 fallback
        config = LearnConfig(openai_api_key="test-key", llm_enabled=True, strict_llm=False)

        with patch("trace_mcp.extensions.learn.matching._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.matching.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(
                    side_effect=Exception("API down")
                )
                MockClient.return_value = mock_client

                from trace_mcp.extensions.learn.matching import LLMBackend

                backend = LLMBackend(config)
                backend._client = mock_client

                # Should fall back to BM25, NOT raise
                results = await recall_learnings(
                    loaded.learnings,
                    "conda environment",
                    threshold=0.05,
                    backend=backend,
                )

        assert len(results) > 0
        # BM25 fallback should still find the conda learning
        assert any("conda" in r["learning"]["content"].lower() for r in results)


# ── E2E: Config loading ──────────────────────────────────────────────────


class TestE2EConfig:
    """Tests for .env config loading and its effect on backend selection."""

    def test_config_from_trace_env_file(self, tmp_path):
        """~/.trace/.env file is read for API key."""
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=sk-from-dotenv\n")

        from trace_mcp.extensions.learn.config import _parse_dotenv

        parsed = _parse_dotenv(env_file)
        assert parsed["OPENAI_API_KEY"] == "sk-from-dotenv"

    def test_parse_dotenv_strips_inline_comments(self, tmp_path):
        """Inline comments (KEY=value # comment) must not contaminate the value.

        Regression test: a user's .env file had
            TRACE_LLM_ENABLED=true      # set false to force BM25-only
        which was being parsed as "true      # set false to force BM25-only"
        and failing the `.lower() in ("true","1","yes")` check, silently
        disabling LLM features.
        """
        env_file = tmp_path / ".env"
        env_file.write_text(
            "TRACE_LLM_ENABLED=true      # set false to force BM25-only\n"
            'QUOTED_VAL="has # hash inside"   # trailing comment\n'
            "PLAIN=value\n"
        )

        from trace_mcp.extensions.learn.config import _parse_dotenv

        parsed = _parse_dotenv(env_file)
        assert parsed["TRACE_LLM_ENABLED"] == "true"
        # Inside a quoted value, the # is preserved (not treated as comment)
        assert parsed["QUOTED_VAL"] == "has # hash inside"
        assert parsed["PLAIN"] == "value"

    def test_config_env_var_overrides(self, monkeypatch):
        """Environment variable takes precedence over .env file."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        monkeypatch.setenv("TRACE_LLM_ENABLED", "true")

        from trace_mcp.extensions.learn.config import load_config

        config = load_config()
        assert config.openai_api_key == "sk-from-env"

    def test_config_no_key_disables_llm(self, monkeypatch):
        """When no API key is found, LLM is automatically disabled."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        from trace_mcp.extensions.learn import config as _cfg
        from trace_mcp.extensions.learn.config import load_config

        # Prevent load_config from reading the real ~/.trace/.env
        monkeypatch.setattr(_cfg, "_TRACE_ENV_PATH", Path("/nonexistent/.env"))

        config = load_config()
        assert config.llm_enabled is False

    def test_config_explicit_disable(self, monkeypatch):
        """TRACE_LLM_ENABLED=false disables LLM even with API key."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("TRACE_LLM_ENABLED", "false")

        from trace_mcp.extensions.learn.config import load_config

        config = load_config()
        assert config.llm_enabled is False


# ── E2E: Correction chain tracking ───────────────────────────────────────


class TestE2ECorrectionChains:
    """Tests that correction chains (corrects_event_ids) survive the full pipeline."""

    async def test_correction_chain_persists(self, tmp_path):
        """corrects_event_ids from annotations survive extract → persist → load."""
        session = _session([
            _annotation(
                "evt_005",
                "correction",
                "Human caught: AI used wrong conda env 3 times",
                tags=["conda", "correction"],
                corrects_event_ids=["evt_001", "evt_002", "evt_003"],
            ),
        ])

        ks = KnowledgeStore(project="test")
        extract_from_session(ks, session)
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("test", directory=str(tmp_path))
        assert len(loaded.learnings) == 1
        assert loaded.learnings[0].corrects_event_ids == ["evt_001", "evt_002", "evt_003"]

        # And it's recallable
        results = await recall_learnings(
            loaded.learnings,
            "conda environment error",
            context_tags=["conda"],
            threshold=0.05,
            backend=BM25Backend(),
        )
        assert len(results) == 1
        assert results[0]["learning"]["corrects_event_ids"] == ["evt_001", "evt_002", "evt_003"]
