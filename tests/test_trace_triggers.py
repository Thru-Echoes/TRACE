"""Tests for TRACE v0.2 trigger behavior: stemming recall, per-backend
thresholds, hook-driven triggers, and real LLM integration.

Replaces the legacy test_trace_triggers.py which tested the retired v3
monolith (mcp_server/server.py).  All imports now come from the v0.2
codebase in src/trace_mcp/.

Run all:       uv run pytest tests/test_trace_triggers.py -v
Run LLM only:  uv run pytest tests/test_trace_triggers.py -k llm -v
"""

from __future__ import annotations

from typing import Any

import pytest

from trace_mcp.extensions.learn.config import load_config
from trace_mcp.extensions.learn.extraction import (
    extract_from_session,
    extract_from_session_llm,
)
from trace_mcp.extensions.learn.matching import (
    BM25Backend,
    _tokenize,
    get_default_backend,
    recall_learnings,
)
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.extensions.learn.store import add_learning, load_store, save_store
from trace_mcp.hooks import (
    clear_hooks,
    extract_if_available,
    format_decision_warnings,
    format_recalled_learnings,
    recall_if_available,
    register_extract_hook,
    register_recall_hook,
)
from trace_mcp.schema import Session
from trace_mcp.schema.events import AnnotationData, DecisionData, TraceEvent
from trace_mcp.schema.session import Actor, SessionMetadata

# ── Shared test data ─────────────────────────────────────────────────────

LEARNINGS = [
    Learning(
        id="lrn_001",
        content="Always use ml-dev conda environment, not base",
        tags=["conda", "env", "critical"],
    ),
    Learning(
        id="lrn_002",
        content="Log decisions before implementing — TRACE discipline",
        tags=["trace", "logging", "discipline"],
    ),
    Learning(
        id="lrn_003",
        content="Best pasta recipe uses fresh tomatoes and basil",
        tags=["food", "recipe"],
    ),
    Learning(
        id="lrn_004",
        content="Check error codes from external APIs carefully",
        tags=["api", "errors"],
    ),
]


def _has_openai_key() -> bool:
    try:
        config = load_config()
        return bool(config.openai_api_key)
    except Exception:
        return False


requires_llm = pytest.mark.skipif(
    not _has_openai_key(), reason="No OPENAI_API_KEY in ~/.trace/.env"
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_hooks():
    """Reset hooks before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


@pytest.fixture()
def knowledge_dir(tmp_path) -> str:
    d = tmp_path / "knowledge"
    d.mkdir()
    return str(d)


def _seed_store(directory: str, project: str = "test") -> KnowledgeStore:
    """Create a knowledge store with the shared test learnings."""
    ks = KnowledgeStore(project=project)
    for lrn in LEARNINGS:
        add_learning(ks, content=lrn.content, category=lrn.category, tags=list(lrn.tags))
    save_store(ks, directory=directory)
    return ks


def _register_bm25_recall(directory: str) -> BM25Backend:
    """Register a BM25 recall hook backed by a temp knowledge store."""
    backend = BM25Backend()

    async def _recall(
        project: str, context: str, tags: list[str] | None, limit: int,
    ) -> list[dict[str, Any]]:
        ks = load_store(project, directory=directory)
        if not ks.learnings:
            return []
        return await recall_learnings(
            ks.learnings,
            context=context,
            context_tags=tags,
            threshold=None,  # use backend default
            limit=limit,
            backend=backend,
        )

    register_recall_hook(_recall)
    return backend


def _make_session(
    session_id: str = "test_session",
    project: str = "test",
    events: list[TraceEvent] | None = None,
) -> Session:
    """Build a minimal Session for testing."""
    return Session(
        id=session_id,
        metadata=SessionMetadata(project=project),
        events=events or [],
    )


# ── Stemmer: BM25 morphological recall ──────────────────────────────────


class TestBM25MorphologicalRecall:
    """Stemming closes the morphological gap discovered in
    test_multi_session_extraction_and_recall."""

    async def test_decisions_recalled_from_decision(self, knowledge_dir):
        """Query 'decisions' recalls learning containing 'decision'."""
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Log every decision in TRACE before acting", tags=["trace"])
        save_store(ks, directory=knowledge_dir)

        loaded = load_store("test", directory=knowledge_dir)
        results = await recall_learnings(
            loaded.learnings,
            "How should I handle decisions?",
            threshold=0.05,
            backend=BM25Backend(tag_weight=0.0),
        )
        assert len(results) > 0

    async def test_log_recalled_from_logging(self, knowledge_dir):
        """Query 'log' recalls learning containing 'logging'."""
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Logging discipline is critical for TRACE", tags=["trace"])
        save_store(ks, directory=knowledge_dir)

        loaded = load_store("test", directory=knowledge_dir)
        results = await recall_learnings(
            loaded.learnings,
            "How do I log to TRACE?",
            threshold=0.05,
            backend=BM25Backend(tag_weight=0.0),
        )
        assert len(results) > 0

    async def test_error_recalled_from_errors(self, knowledge_dir):
        """Query 'error' recalls learning containing 'errors'."""
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="Check API errors carefully before retrying", tags=["api"])
        save_store(ks, directory=knowledge_dir)

        loaded = load_store("test", directory=knowledge_dir)
        results = await recall_learnings(
            loaded.learnings,
            "Got an error from the API",
            threshold=0.05,
            backend=BM25Backend(tag_weight=0.0),
        )
        assert len(results) > 0

    async def test_stemming_tokens_match(self):
        """Verify stemmed tokens from document and query overlap."""
        doc_tokens = set(_tokenize("Log decisions before implementing", stem=True))
        query_tokens = set(_tokenize("implementing decisions in the workflow", stem=True))
        # "decision" and "implement" should appear in both after stemming
        assert "decision" in doc_tokens
        assert "decision" in query_tokens
        assert "implement" in doc_tokens
        assert "implement" in query_tokens


# ── Per-backend threshold behavior ──────────────────────────────────────


class TestThresholdBehavior:
    """Tests that per-backend thresholds reduce false positives."""

    async def test_bm25_default_filters_noise(self):
        """BM25's 0.15 threshold filters out low-relevance matches."""
        backend = BM25Backend()
        results = await recall_learnings(
            LEARNINGS,
            "fresh pasta tomato recipe",
            threshold=None,  # uses 0.15
            backend=backend,
        )
        # Only the cooking learning should survive at threshold 0.15
        for r in results:
            assert r["score"] >= 0.15

    async def test_permissive_threshold_admits_more(self):
        """A lower explicit threshold lets more results through."""
        backend = BM25Backend()
        results_strict = await recall_learnings(
            LEARNINGS, "environment", threshold=None, backend=backend,
        )
        results_permissive = await recall_learnings(
            LEARNINGS, "environment", threshold=0.01, backend=backend,
        )
        assert len(results_permissive) >= len(results_strict)

    async def test_irrelevant_query_returns_empty(self):
        """Completely unrelated query returns nothing at default threshold."""
        backend = BM25Backend()
        results = await recall_learnings(
            LEARNINGS,
            "quantum entanglement photon detector calibration",
            threshold=None,
            backend=backend,
        )
        assert len(results) == 0


# ── Hook-driven trigger: recall on session start ────────────────────────


class TestRecallTrigger:
    """Tests that the recall hook fires and returns relevant learnings."""

    async def test_recall_surfaces_relevant_learnings(self, knowledge_dir):
        _seed_store(knowledge_dir)
        _register_bm25_recall(knowledge_dir)

        results = await recall_if_available("test", "conda environment setup", ["conda"], 5)
        assert len(results) > 0
        assert any("conda" in r["learning"]["content"].lower() for r in results)

    async def test_recall_empty_without_hook(self):
        results = await recall_if_available("test", "anything", None, 5)
        assert results == []

    async def test_recall_formats_for_session_start(self, knowledge_dir):
        _seed_store(knowledge_dir)
        _register_bm25_recall(knowledge_dir)

        results = await recall_if_available("test", "conda environment", ["conda"], 5)
        output = format_recalled_learnings(results)
        assert "Relevant learnings from past sessions" in output

    async def test_recall_formats_for_decision_warning(self, knowledge_dir):
        _seed_store(knowledge_dir)
        _register_bm25_recall(knowledge_dir)

        results = await recall_if_available("test", "conda base environment", ["conda"], 3)
        output = format_decision_warnings(results)
        if results:
            assert "Related learnings" in output


# ── Hook-driven trigger: extract on session end ─────────────────────────


class TestExtractTrigger:
    """Tests that the extract hook fires and persists learnings."""

    async def test_extract_hook_called(self):
        calls: list[tuple[str, str]] = []

        async def _hook(project: str, session_id: str) -> list[str]:
            calls.append((project, session_id))
            return ["lrn_new"]

        register_extract_hook(_hook)
        result = await extract_if_available("test", "sess_001")
        assert result == ["lrn_new"]
        assert calls == [("test", "sess_001")]

    async def test_extract_empty_without_hook(self):
        result = await extract_if_available("test", "sess_001")
        assert result == []

    async def test_extract_fail_open(self):
        async def _bad_hook(project: str, session_id: str) -> list[str]:
            raise RuntimeError("Boom")

        register_extract_hook(_bad_hook)
        result = await extract_if_available("test", "sess_001")
        assert result == []  # fail-open, no exception


# ── Multi-session E2E: extract → persist → recall with stemming ────────


class TestMultiSessionE2E:
    """End-to-end: Session A creates learnings, Session B recalls them."""

    async def test_learning_persists_and_recalls_with_stemming(self, knowledge_dir):
        """
        Session A stores: 'Log decisions before implementing'
        Session B queries: 'logging and decisions workflow'
        Stemming ensures 'logging'→'log' and 'decisions'→'decision' match.
        """
        # Session A: extract a learning
        session_a = _make_session(
            session_id="sess_a",
            events=[
                TraceEvent(
                    id="evt_001",
                    session_id="sess_a",
                    type="annotation",
                    actor=Actor(type="human", id="researcher"),
                    annotation=AnnotationData(
                        category="learning",
                        content="Log decisions before implementing — TRACE discipline",
                        tags=["trace", "logging"],
                    ),
                ),
            ],
        )
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, session_a)
        assert len(new_ids) == 1
        save_store(ks, directory=knowledge_dir)

        # Session B: recall with morphological variants
        loaded = load_store("test", directory=knowledge_dir)
        backend = BM25Backend()
        results = await recall_learnings(
            loaded.learnings,
            "How should I handle logging and decisions in my workflow?",
            context_tags=["trace"],
            threshold=None,
            backend=backend,
        )
        assert len(results) > 0
        content = results[0]["learning"]["content"].lower()
        assert "decision" in content or "log" in content

    async def test_correction_extracted_and_recalled(self, knowledge_dir):
        """Correction annotation → extracted → recalled by variant query."""
        session = _make_session(
            session_id="sess_correction",
            events=[
                TraceEvent(
                    id="evt_001",
                    session_id="sess_correction",
                    type="annotation",
                    actor=Actor(type="human", id="researcher"),
                    annotation=AnnotationData(
                        category="correction",
                        content="Wrong conda env — always use ml-dev, not base",
                        corrects_event_ids=["evt_000"],
                        tags=["conda", "environment"],
                    ),
                ),
            ],
        )
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 1
        assert ks.learnings[0].corrects_event_ids == ["evt_000"]
        save_store(ks, directory=knowledge_dir)

        # Recall with "environments" (plural) — stemming should match "environment"
        loaded = load_store("test", directory=knowledge_dir)
        results = await recall_learnings(
            loaded.learnings,
            "setting up conda environments",
            context_tags=["conda"],
            threshold=0.05,
            backend=BM25Backend(),
        )
        assert len(results) > 0

    async def test_rejected_decision_extracted_and_recalled(self, knowledge_dir):
        """Rejected decision → extracted as learning → recallable."""
        session = _make_session(
            session_id="sess_decision",
            events=[
                TraceEvent(
                    id="evt_001",
                    session_id="sess_decision",
                    type="decision",
                    actor=Actor(type="ai", id="assistant"),
                    decision=DecisionData(
                        description="Use base conda environment for analysis",
                        rationale="It's the default",
                        proposed_by=Actor(type="ai", id="assistant"),
                        disposition="rejected",
                        resolved_by=Actor(type="human", id="researcher"),
                        revision_note="Must use ml-dev, base lacks required packages",
                        suggestion_type="proactive",
                        tags=["conda"],
                    ),
                ),
            ],
        )
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, session)
        assert len(new_ids) == 1
        assert "ml-dev" in ks.learnings[0].content
        save_store(ks, directory=knowledge_dir)

        loaded = load_store("test", directory=knowledge_dir)
        results = await recall_learnings(
            loaded.learnings,
            "which conda environment to use?",
            threshold=0.05,
            backend=BM25Backend(),
        )
        assert len(results) > 0


# ── Real LLM integration ────────────────────────────────────────────────


class TestLLMIntegration:
    """Real OpenAI API calls — require OPENAI_API_KEY in ~/.trace/.env.

    Run: uv run pytest tests/test_trace_triggers.py -k llm -v
    """

    @requires_llm
    async def test_llm_scoring_real(self):
        """LLM scores conda learning higher than cooking for a conda query."""
        config = load_config()

        from trace_mcp.extensions.learn.matching import LLMBackend

        backend = LLMBackend(config)
        results = await backend.score_batch(LEARNINGS, "conda environment activation")
        scores = dict(results)

        # Conda learning should rank highest; cooking should be near zero
        assert scores[0] > scores[2], "Conda learning should outscore cooking"
        assert scores[2] < 0.3, "Cooking learning should be < 0.3 for conda query"

    @requires_llm
    async def test_llm_extraction_real(self):
        """LLM extracts a learning from a correction annotation."""
        config = load_config()

        session = _make_session(
            session_id="test_llm_extract",
            events=[
                TraceEvent(
                    id="evt_001",
                    session_id="test_llm_extract",
                    type="annotation",
                    actor=Actor(type="human", id="researcher"),
                    annotation=AnnotationData(
                        category="correction",
                        content="Wrong conda env — always use ml-dev, not base",
                        tags=["conda", "environment"],
                    ),
                ),
            ],
        )

        store = KnowledgeStore(project="test")
        new_ids = await extract_from_session_llm(store, session, config)

        assert len(new_ids) > 0
        all_content = " ".join(lrn.content.lower() for lrn in store.learnings)
        assert "conda" in all_content or "ml-dev" in all_content

    @requires_llm
    async def test_llm_recall_pipeline(self):
        """Full recall pipeline with real LLM backend."""
        config = load_config()
        backend = get_default_backend(config)

        results = await recall_learnings(
            LEARNINGS,
            "What conda environment should I use?",
            context_tags=["conda"],
            threshold=0.1,
            limit=5,
            backend=backend,
        )
        assert len(results) > 0
        assert "conda" in results[0]["learning"]["content"].lower()

    @requires_llm
    async def test_llm_backend_is_selected_when_configured(self):
        """With API key present, get_default_backend returns LLMBackend."""
        config = load_config()
        backend = get_default_backend(config)
        # Should NOT be BM25 when LLM is configured
        assert not isinstance(backend, BM25Backend)
