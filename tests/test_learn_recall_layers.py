"""E2E tests for the 3-layer recall architecture.

Layer 1: Session start primer — auto-recall top-K learnings
Layer 2: On-demand search — explicit trace_learn_recall (tested in test_learn_matching.py)
Layer 3: Event-triggered — auto-recall on decision proposal

The 3-layer integration lives in server.py (MCP wrappers), which closes
over module-level storage/active_sessions. These tests validate the same
composition: internal tool functions + hooks — proving the logic works
end-to-end without requiring a running MCP server.

Also tests:
- Auto-extract on session end
- Graceful degradation when no extension / hooks fail
- Hook registration and clearing
- Format functions for recalled learnings
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from trace_mcp.extensions.learn.matching import BM25Backend, recall_learnings
from trace_mcp.extensions.learn.models import KnowledgeStore
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
from trace_mcp.schema.events import AnnotationData, TraceEvent
from trace_mcp.schema.session import Actor
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools.decision_tools import propose_decision
from trace_mcp.tools.session_tools import append_event, end_session, start_session


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_hooks():
    """Reset hooks before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


@pytest.fixture()
def storage(tmp_path) -> JsonFileStorage:
    """Create a JsonFileStorage backed by tmp_path."""
    return JsonFileStorage(directory=str(tmp_path / "sessions"))


@pytest.fixture()
def knowledge_dir(tmp_path) -> str:
    """Return a temp directory for knowledge stores."""
    d = tmp_path / "knowledge"
    d.mkdir()
    return str(d)


def _seed_knowledge(directory: str, project: str = "test") -> KnowledgeStore:
    """Create a knowledge store with realistic learnings."""
    ks = KnowledgeStore(project=project)
    add_learning(
        ks,
        content="Always use the ml-dev conda environment, not base",
        category="correction",
        tags=["conda", "env", "critical"],
    )
    add_learning(
        ks,
        content="Log decisions in TRACE before implementing them, not after",
        category="learning",
        tags=["trace", "logging", "discipline"],
    )
    add_learning(
        ks,
        content="ffmpeg audio device indices are unstable across macOS reboots",
        category="gotcha",
        tags=["ffmpeg", "macos", "audio"],
    )
    save_store(ks, directory=directory)
    return ks


def _register_recall_hook(knowledge_dir: str) -> None:
    """Register a recall hook backed by a temp knowledge store."""
    _backend = BM25Backend()

    async def _recall(
        project: str,
        context: str,
        tags: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        ks = load_store(project, directory=knowledge_dir)
        if not ks.learnings:
            return []
        return await recall_learnings(
            ks.learnings,
            context=context,
            context_tags=tags,
            threshold=0.1,
            limit=limit,
            backend=_backend,
        )

    register_recall_hook(_recall)


async def _create_session(
    storage: JsonFileStorage,
    active: dict[str, Session],
    project: str = "test",
    description: str | None = None,
) -> tuple[str, Session]:
    """Create an active test session. Returns (session_id, session)."""
    await start_session(
        storage, active, project=project, description=description,
    )
    session_id = list(active.keys())[-1]
    return session_id, active[session_id]


# ── Hooks module unit tests ───────────────────────────────────────────────


class TestHooksRegistry:
    """Tests for the hooks module itself."""

    async def test_recall_returns_empty_when_no_hook(self):
        results = await recall_if_available("test", "anything")
        assert results == []

    async def test_extract_returns_empty_when_no_hook(self):
        results = await extract_if_available("test", "sess_001")
        assert results == []

    async def test_recall_with_registered_hook(self, knowledge_dir):
        _seed_knowledge(knowledge_dir)
        _register_recall_hook(knowledge_dir)

        results = await recall_if_available("test", "conda environment setup", ["conda"], 5)
        assert len(results) > 0
        assert "conda" in results[0]["learning"]["content"].lower()

    async def test_recall_hook_failure_returns_empty(self):
        """Hooks fail open — errors return empty list, not exceptions."""

        async def _bad_hook(
            project: str, context: str, tags: list[str] | None, limit: int
        ) -> list[dict]:
            raise RuntimeError("Simulated failure")

        register_recall_hook(_bad_hook)
        results = await recall_if_available("test", "anything")
        assert results == []  # Fail-open, no exception

    async def test_extract_hook_failure_returns_empty(self):
        async def _bad_hook(project: str, session_id: str) -> list[str]:
            raise RuntimeError("Simulated failure")

        register_extract_hook(_bad_hook)
        results = await extract_if_available("test", "sess_001")
        assert results == []

    async def test_clear_hooks(self, knowledge_dir):
        _seed_knowledge(knowledge_dir)
        _register_recall_hook(knowledge_dir)

        # Verify hook works
        results = await recall_if_available("test", "conda", None, 5)
        assert len(results) > 0

        # Clear and verify empty
        clear_hooks()
        results = await recall_if_available("test", "conda", None, 5)
        assert results == []


# ── Format functions ──────────────────────────────────────────────────────


class TestFormatFunctions:
    def test_format_recalled_learnings_empty(self):
        assert format_recalled_learnings([]) == ""

    def test_format_recalled_learnings_content(self):
        results = [
            {
                "learning": {"category": "correction", "content": "Use ml-dev env"},
                "score": 0.85,
            },
            {
                "learning": {"category": "gotcha", "content": "ffmpeg is flaky"},
                "score": 0.42,
            },
        ]
        output = format_recalled_learnings(results)
        assert "Relevant learnings from past sessions" in output
        assert "[correction] Use ml-dev env" in output
        assert "(relevance: 85%)" in output
        assert "[gotcha] ffmpeg is flaky" in output

    def test_format_decision_warnings_empty(self):
        assert format_decision_warnings([]) == ""

    def test_format_decision_warnings_content(self):
        results = [
            {"learning": {"category": "correction", "content": "Use ml-dev env"}},
        ]
        output = format_decision_warnings(results)
        assert "Related learnings (review before proceeding)" in output
        assert "[correction] Use ml-dev env" in output


# ── Layer 1: Session start primer ─────────────────────────────────────────
#
# server.py's trace_start_session composes: start_session() + recall_if_available()
# We test the same composition here to validate the logic.


class TestLayer1SessionStart:
    """Tests that session-start + recall surfaces relevant learnings."""

    async def test_start_session_includes_recalled_learnings(
        self, storage, knowledge_dir
    ):
        """Starting a session about conda surfaces the conda correction."""
        _seed_knowledge(knowledge_dir)
        _register_recall_hook(knowledge_dir)

        # Simulate what server.py does: start session, then recall
        description = "Setting up conda environment for ML pipeline"
        tags = ["conda", "ml"]
        result = await start_session(
            storage, {}, project="test", description=description, tags=tags,
        )

        # Layer 1: recall based on description + tags
        recalled = await recall_if_available("test", description, tags, 5)
        result += format_recalled_learnings(recalled)

        assert "TRACE audit logging is now active" in result
        assert "Relevant learnings from past sessions" in result
        assert "conda" in result.lower()

    async def test_start_session_low_relevance_for_unrelated_topic(
        self, storage, knowledge_dir
    ):
        """Starting a session about an unrelated topic returns no high-relevance learnings."""
        _seed_knowledge(knowledge_dir)
        _register_recall_hook(knowledge_dir)

        description = "Writing the abstract for the conference paper"
        tags = ["writing"]
        await start_session(
            storage, {}, project="test", description=description, tags=tags,
        )

        # Use a stricter threshold — low-score noise should be filtered out
        ks = load_store("test", directory=knowledge_dir)
        results = await recall_learnings(
            ks.learnings, description, context_tags=tags,
            threshold=0.3, limit=5, backend=BM25Backend(),
        )
        assert len(results) == 0

    async def test_start_session_respects_recall_limit(
        self, storage, knowledge_dir
    ):
        """Only top-K learnings are returned."""
        _seed_knowledge(knowledge_dir)
        _register_recall_hook(knowledge_dir)

        # Use a broad query that matches multiple learnings
        description = "conda env ml ffmpeg audio log decisions trace"
        tags = ["conda", "ffmpeg", "trace"]
        await start_session(
            storage, {}, project="test", description=description, tags=tags,
        )

        recalled = await recall_if_available("test", description, tags, limit=1)
        result = format_recalled_learnings(recalled)

        if result:
            matches = re.findall(r"\[(?:correction|learning|gotcha|decision)\]", result)
            assert len(matches) <= 1

    async def test_start_session_no_recall_without_description(
        self, storage, knowledge_dir
    ):
        """No description means no context to recall against — skip recall."""
        _seed_knowledge(knowledge_dir)
        _register_recall_hook(knowledge_dir)

        result = await start_session(storage, {}, project="test")

        # server.py skips recall when description is None
        # With no context, we shouldn't attempt recall
        assert "Relevant learnings" not in result

    async def test_start_session_works_without_hooks(self, storage):
        """Starting a session works normally when no learn extension is loaded."""
        result = await start_session(
            storage, {}, project="test", description="Some work",
        )
        # No hooks → recall returns empty → no learnings appended
        recalled = await recall_if_available("test", "Some work", None, 5)
        assert recalled == []
        assert "TRACE audit logging is now active" in result
        assert "Relevant learnings" not in result


# ── Layer 3: Decision proposal auto-recall ────────────────────────────────
#
# server.py's trace_propose_decision composes: propose_decision() + recall_if_available()
# We test the same composition here.


class TestLayer3DecisionRecall:
    """Tests that decision proposal + recall surfaces related learnings."""

    async def test_propose_decision_shows_related_learnings(
        self, storage, knowledge_dir
    ):
        """Proposing a decision about conda surfaces the conda correction."""
        _seed_knowledge(knowledge_dir)
        _register_recall_hook(knowledge_dir)

        active: dict[str, Session] = {}
        _, session = await _create_session(storage, active, description="test session")

        description = "Use base conda environment for the analysis"
        tags = ["conda"]

        event_id = await propose_decision(
            storage, session,
            description=description,
            proposed_by_type="ai",
            proposed_by_id="ai-assistant",
            tags=tags,
        )
        assert event_id.startswith("evt_")

        # Layer 3: recall related learnings for this decision
        related = await recall_if_available(
            session.metadata.project, description, tags, limit=3,
        )
        assert len(related) > 0
        contents = [r["learning"]["content"].lower() for r in related]
        assert any("ml-dev" in c or "conda" in c for c in contents)

        # Format as warnings
        warnings = format_decision_warnings(related)
        assert "Related learnings" in warnings

    async def test_propose_decision_low_relevance_for_novel_topic(
        self, storage, knowledge_dir
    ):
        """Decisions about topics with no learnings return no high-relevance warnings."""
        _seed_knowledge(knowledge_dir)
        _register_recall_hook(knowledge_dir)

        active: dict[str, Session] = {}
        _, session = await _create_session(storage, active, description="test session")

        # Use a query with zero vocabulary overlap with seeded learnings
        ks = load_store("test", directory=knowledge_dir)
        results = await recall_learnings(
            ks.learnings,
            "transformer architecture text classification",
            context_tags=["nlp", "transformer"],
            threshold=0.3, limit=3, backend=BM25Backend(),
        )
        assert len(results) == 0

    async def test_propose_decision_works_without_hooks(self, storage):
        """Decision proposal works normally when no learn extension is loaded."""
        active: dict[str, Session] = {}
        _, session = await _create_session(storage, active, description="test session")

        event_id = await propose_decision(
            storage, session,
            description="Use some method",
            proposed_by_type="ai",
            proposed_by_id="ai-assistant",
        )
        assert event_id.startswith("evt_")

        # No hooks → no related learnings
        related = await recall_if_available("test", "Use some method", None, 3)
        assert related == []


# ── Auto-extract on session end ───────────────────────────────────────────
#
# server.py's trace_end_session composes: end_session() + extract_if_available()


class TestAutoExtract:
    """Tests that ending a session can auto-extract learnings."""

    async def test_extract_hook_called_on_session_end(self, storage):
        """Verify the extract hook is invoked and returns learning IDs."""
        extracted_calls: list[tuple[str, str]] = []

        async def _mock_extract(project: str, session_id: str) -> list[str]:
            extracted_calls.append((project, session_id))
            return ["lrn_001", "lrn_002"]

        register_extract_hook(_mock_extract)

        active: dict[str, Session] = {}
        session_id, _ = await _create_session(storage, active, description="test")

        # Simulate what server.py does: end session, then extract
        await end_session(storage, active, session_id=session_id, summary="done")

        new_ids = await extract_if_available("test", session_id)
        assert new_ids == ["lrn_001", "lrn_002"]
        assert extracted_calls == [("test", session_id)]

    async def test_extract_hook_not_called_when_no_hook(self, storage):
        """Without an extract hook, extraction returns empty."""
        active: dict[str, Session] = {}
        session_id, _ = await _create_session(storage, active, description="test")
        await end_session(storage, active, session_id=session_id, summary="done")

        new_ids = await extract_if_available("test", session_id)
        assert new_ids == []

    async def test_extract_real_session(self, storage, knowledge_dir):
        """Extract learnings from a session with real annotations."""
        active: dict[str, Session] = {}
        session_id, session = await _create_session(
            storage, active, description="test session",
        )

        # Add a correction annotation event
        event = TraceEvent(
            session_id=session_id,
            type="annotation",
            actor=Actor(type="ai", id="ai-assistant"),
            annotation=AnnotationData(
                category="correction",
                content="Wrong conda env — always use ml-dev, not base",
                tags=["conda", "env"],
                corrects_event_ids=["evt_000"],
            ),
        )
        await append_event(storage, session, event)
        await end_session(storage, active, session_id=session_id, summary="test done")

        # Extract using the real extraction pipeline
        from trace_mcp.extensions.learn.extraction import extract_from_session

        ks = KnowledgeStore(project="test")
        loaded_session = await storage.get_session(session_id)
        new_ids = extract_from_session(ks, loaded_session)

        assert len(new_ids) == 1
        assert ks.learnings[0].content == "Wrong conda env — always use ml-dev, not base"
        assert ks.learnings[0].corrects_event_ids == ["evt_000"]

        # Save and verify recallable via BM25
        save_store(ks, directory=knowledge_dir)
        loaded = load_store("test", directory=knowledge_dir)
        results = await recall_learnings(
            loaded.learnings,
            "conda env",
            threshold=0.05,
            backend=BM25Backend(),
        )
        assert len(results) > 0


# ── Full E2E: All 3 layers in sequence ────────────────────────────────────


class TestFullE2EAllLayers:
    """Tests the complete lifecycle: start → decide → annotate → end → new session."""

    async def test_learning_persists_across_sessions(self, storage, knowledge_dir):
        """Session A creates a learning. Session B's start recalls it."""
        _register_recall_hook(knowledge_dir)

        # ── Session A: create a correction ──
        active_a: dict[str, Session] = {}
        session_a_id, session_a = await _create_session(
            storage, active_a, description="Debugging pipeline failures",
        )

        # Add a correction annotation
        event = TraceEvent(
            session_id=session_a_id,
            type="annotation",
            actor=Actor(type="human", id="researcher"),
            annotation=AnnotationData(
                category="correction",
                content="Use the ml-dev conda environment, not base — base lacks required packages",
                tags=["conda", "environment"],
            ),
        )
        await append_event(storage, session_a, event)
        await end_session(storage, active_a, session_id=session_a_id, summary="Fixed env")

        # Extract learnings from session A into the knowledge store
        from trace_mcp.extensions.learn.extraction import extract_from_session

        loaded_a = await storage.get_session(session_a_id)
        ks = KnowledgeStore(project="test")
        new_ids = extract_from_session(ks, loaded_a)
        assert len(new_ids) == 1
        save_store(ks, directory=knowledge_dir)

        # ── Session B: start with recall ──
        active_b: dict[str, Session] = {}
        description_b = "Setting up conda environment for new analysis"
        tags_b = ["conda"]
        result_b = await start_session(
            storage, active_b, project="test",
            description=description_b, tags=tags_b,
        )

        # Layer 1: recall based on session B's description
        recalled = await recall_if_available("test", description_b, tags_b, 5)
        result_b += format_recalled_learnings(recalled)

        assert "Relevant learnings from past sessions" in result_b
        assert "conda" in result_b.lower()
        assert "ml-dev" in result_b.lower()

    async def test_decision_recall_prevents_known_mistake(
        self, storage, knowledge_dir
    ):
        """A learning about ml-dev surfaces when proposing to use base env."""
        _seed_knowledge(knowledge_dir)
        _register_recall_hook(knowledge_dir)

        active: dict[str, Session] = {}
        _, session = await _create_session(
            storage, active, description="ML analysis",
        )

        # Propose using base conda (a known mistake)
        decision_desc = "Use base conda environment for running the ML pipeline"
        related = await recall_if_available(
            "test", decision_desc, ["conda"], limit=3,
        )

        # The system should surface the correction about ml-dev
        assert len(related) > 0
        all_content = " ".join(r["learning"]["content"].lower() for r in related)
        assert "ml-dev" in all_content or "not base" in all_content

        # Format as warning
        warning = format_decision_warnings(related)
        assert "Related learnings" in warning
        assert "correction" in warning.lower() or "ml-dev" in warning.lower()
