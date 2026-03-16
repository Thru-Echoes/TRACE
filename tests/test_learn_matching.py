"""Standalone tests for trace-learn matching backends (matching.py).

Tests three backends independently:
- JaccardBackend (legacy)
- BM25Backend (fallback)
- LLMBackend (primary, mocked)

Plus: backend auto-selection, recall_learnings integration,
threshold/limit behavior, tag boosting, edge cases.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trace_mcp.extensions.learn.config import LearnConfig
from trace_mcp.extensions.learn.matching import (
    BM25Backend,
    JaccardBackend,
    _BM25Index,
    _normalize_bm25,
    _stem,
    _tag_overlap,
    _tokenize,
    get_default_backend,
    jaccard_similarity,
    recall_learnings,
    score_learning,
)
from trace_mcp.extensions.learn.models import Learning

# ── Shared test data ──────────────────────────────────────────────────────

CONDA_LEARNING = Learning(
    id="lrn_001",
    content="Always use the ml-dev conda environment, not base",
    tags=["conda", "env", "critical"],
)
LOGGING_LEARNING = Learning(
    id="lrn_002",
    content="Log decisions in real-time before implementing, not after",
    tags=["trace", "logging", "discipline"],
)
COOKING_LEARNING = Learning(
    id="lrn_003",
    content="The best pasta recipe uses fresh tomatoes and basil",
    tags=["food", "recipe"],
)
ATTRIBUTION_LEARNING = Learning(
    id="lrn_004",
    content="Version 1 over-attributed changes to AI even when human-directed",
    tags=["attribution", "bias", "schema"],
)

ALL_LEARNINGS = [CONDA_LEARNING, LOGGING_LEARNING, COOKING_LEARNING, ATTRIBUTION_LEARNING]


# ── Tokenization tests ───────────────────────────────────────────────────


class TestTokenization:
    def test_basic_tokenization(self):
        tokens = _tokenize("Hello World")
        assert tokens == ["hello", "world"]

    def test_preserves_duplicates(self):
        tokens = _tokenize("the cat and the dog")
        assert tokens.count("the") == 2

    def test_strips_punctuation(self):
        tokens = _tokenize("hello, world! how's it going?")
        assert "hello" in tokens
        assert "world" in tokens

    def test_empty_string(self):
        assert _tokenize("") == []


class TestTagOverlap:
    def test_perfect_overlap(self):
        assert _tag_overlap(["conda", "env"], ["conda", "env"]) == 1.0

    def test_no_overlap(self):
        assert _tag_overlap(["conda"], ["food"]) == 0.0

    def test_partial_overlap(self):
        score = _tag_overlap(["conda", "env", "critical"], ["conda", "env"])
        assert 0.0 < score < 1.0

    def test_empty_tags(self):
        assert _tag_overlap([], ["conda"]) == 0.0
        assert _tag_overlap(["conda"], []) == 0.0
        assert _tag_overlap(["conda"], None) == 0.0

    def test_case_insensitive(self):
        assert _tag_overlap(["CONDA"], ["conda"]) == 1.0


# ── Jaccard backend tests ────────────────────────────────────────────────


class TestJaccardBackend:
    def test_identical_texts(self):
        assert jaccard_similarity("hello world", "hello world") == 1.0

    def test_disjoint_texts(self):
        assert jaccard_similarity("hello world", "foo bar") == 0.0

    def test_partial_overlap(self):
        score = jaccard_similarity("hello world", "hello there")
        assert 0.0 < score < 1.0

    def test_empty_text(self):
        assert jaccard_similarity("", "hello") == 0.0
        assert jaccard_similarity("hello", "") == 0.0

    def test_case_insensitive(self):
        assert jaccard_similarity("Hello World", "hello world") == 1.0

    def test_score_learning_with_tags(self):
        score_no_tags = score_learning(CONDA_LEARNING, "conda environment setup")
        score_with_tags = score_learning(
            CONDA_LEARNING, "conda environment setup", context_tags=["conda"]
        )
        assert score_with_tags > score_no_tags

    async def test_backend_interface(self):
        """JaccardBackend implements the MatchingBackend protocol."""
        backend = JaccardBackend()
        results = await backend.score_batch(ALL_LEARNINGS, "conda environment")
        assert len(results) == len(ALL_LEARNINGS)
        assert all(isinstance(idx, int) and isinstance(score, float) for idx, score in results)

    async def test_conda_ranked_above_cooking(self):
        backend = JaccardBackend()
        results = await backend.score_batch(ALL_LEARNINGS, "conda environment activation")
        scores_by_id = {ALL_LEARNINGS[idx].id: score for idx, score in results}
        assert scores_by_id["lrn_001"] > scores_by_id["lrn_003"]


# ── BM25 backend tests ───────────────────────────────────────────────────


class TestBM25Index:
    def test_single_document_scoring(self):
        docs = [["hello", "world"]]
        index = _BM25Index(docs)
        score = index.score(["hello"], 0)
        assert score > 0

    def test_irrelevant_query_scores_zero(self):
        docs = [["hello", "world"]]
        index = _BM25Index(docs)
        score = index.score(["completely", "unrelated"], 0)
        assert score == 0.0

    def test_more_relevant_scores_higher(self):
        docs = [
            ["conda", "env", "ml", "dev", "activate"],
            ["cooking", "recipe", "pasta", "tomato"],
        ]
        index = _BM25Index(docs)
        query = ["conda", "env", "activate"]
        score_relevant = index.score(query, 0)
        score_irrelevant = index.score(query, 1)
        assert score_relevant > score_irrelevant

    def test_idf_rare_term_worth_more(self):
        docs = [
            ["the", "cat", "conda"],
            ["the", "dog", "food"],
            ["the", "bird", "seed"],
        ]
        index = _BM25Index(docs)
        idf_the = index.idf("the")  # appears in all docs
        idf_conda = index.idf("conda")  # appears in 1 doc
        assert idf_conda > idf_the

    def test_empty_corpus(self):
        index = _BM25Index([])
        assert index.n == 0


class TestBM25Normalization:
    def test_zero_stays_zero(self):
        assert _normalize_bm25(0.0) == 0.0

    def test_negative_stays_zero(self):
        assert _normalize_bm25(-1.0) == 0.0

    def test_positive_is_bounded(self):
        """Normalized score is always < 1.0."""
        for raw in [0.1, 1.0, 5.0, 10.0, 100.0]:
            normed = _normalize_bm25(raw)
            assert 0.0 < normed < 1.0

    def test_monotonically_increasing(self):
        scores = [_normalize_bm25(x) for x in [0.5, 1.0, 2.0, 5.0, 10.0]]
        for i in range(len(scores) - 1):
            assert scores[i] < scores[i + 1]


class TestBM25Backend:
    async def test_basic_scoring(self):
        backend = BM25Backend()
        results = await backend.score_batch(ALL_LEARNINGS, "conda environment")
        assert len(results) == len(ALL_LEARNINGS)

    async def test_conda_ranked_first(self):
        """BM25 should rank the conda learning above cooking."""
        backend = BM25Backend()
        results = await backend.score_batch(ALL_LEARNINGS, "conda environment activation")
        sorted_results = sorted(results, key=lambda x: x[1], reverse=True)
        top_idx = sorted_results[0][0]
        assert ALL_LEARNINGS[top_idx].id == "lrn_001"

    async def test_logging_query_finds_logging_learning(self):
        backend = BM25Backend()
        results = await backend.score_batch(ALL_LEARNINGS, "logging decisions real-time")
        sorted_results = sorted(results, key=lambda x: x[1], reverse=True)
        top_idx = sorted_results[0][0]
        assert ALL_LEARNINGS[top_idx].id == "lrn_002"

    async def test_tag_boosting(self):
        backend = BM25Backend(tag_weight=0.3)
        results_no_tags = await backend.score_batch(ALL_LEARNINGS, "environment")
        results_with_tags = await backend.score_batch(
            ALL_LEARNINGS, "environment", context_tags=["conda"]
        )
        # Conda learning should score higher with matching tags
        conda_idx = 0  # lrn_001
        score_no_tags = dict(results_no_tags)[conda_idx]
        score_with_tags = dict(results_with_tags)[conda_idx]
        assert score_with_tags > score_no_tags

    async def test_empty_learnings(self):
        backend = BM25Backend()
        results = await backend.score_batch([], "anything")
        assert results == []

    async def test_bm25_beats_jaccard_on_term_frequency(self):
        """BM25 should rank a doc with repeated relevant terms higher than Jaccard would."""
        learnings = [
            Learning(id="lrn_a", content="conda conda conda environment setup"),
            Learning(id="lrn_b", content="conda environment setup other stuff here"),
        ]
        bm25 = BM25Backend(tag_weight=0.0)
        results = await bm25.score_batch(learnings, "conda environment")
        scores = dict(results)
        # BM25 considers term frequency — first doc has more "conda" mentions
        assert scores[0] > scores[1]


# ── LLM backend tests (mocked) ───────────────────────────────────────────


class TestLLMBackend:
    """Tests for LLMBackend with mocked OpenAI client."""

    def _make_config(self) -> LearnConfig:
        return LearnConfig(
            openai_api_key="test-key-123",
            llm_model="gpt-5-nano",
            llm_enabled=True,
        )

    async def test_llm_scoring_mocked(self):
        """LLM backend returns scores from the mocked response."""
        config = self._make_config()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "0": 0.95,
            "1": 0.3,
            "2": 0.05,
            "3": 0.7,
        })

        with patch("trace_mcp.extensions.learn.matching._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.matching.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                MockClient.return_value = mock_client

                from trace_mcp.extensions.learn.matching import LLMBackend

                backend = LLMBackend(config)
                backend._client = mock_client

                results = await backend.score_batch(ALL_LEARNINGS, "conda environment")

        scores = dict(results)
        assert scores[0] == pytest.approx(0.95)
        assert scores[2] == pytest.approx(0.05)

    async def test_llm_fallback_on_error(self):
        """When LLM fails, falls back to BM25."""
        config = self._make_config()

        with patch("trace_mcp.extensions.learn.matching._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.matching.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(
                    side_effect=Exception("API error")
                )
                MockClient.return_value = mock_client

                from trace_mcp.extensions.learn.matching import LLMBackend

                backend = LLMBackend(config)
                backend._client = mock_client

                results = await backend.score_batch(ALL_LEARNINGS, "conda environment")

        # Should still return results (from BM25 fallback)
        assert len(results) == len(ALL_LEARNINGS)
        scores = dict(results)
        # BM25 should give conda learning a non-zero score
        assert scores[0] > 0

    async def test_llm_prefilters_large_stores(self):
        """When store has >50 learnings, BM25 pre-filters before LLM."""
        config = self._make_config()
        many_learnings = [
            Learning(id=f"lrn_{i:03d}", content=f"learning number {i}")
            for i in range(1, 62)  # 61 learnings > MAX_DIRECT_CANDIDATES
        ]

        mock_response = MagicMock()
        # Return scores for 50 candidates (pre-filtered)
        scores_dict = {str(i): 0.5 for i in range(50)}
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(scores_dict)

        with patch("trace_mcp.extensions.learn.matching._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.matching.AsyncOpenAI") as MockClient:
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                MockClient.return_value = mock_client

                from trace_mcp.extensions.learn.matching import LLMBackend

                backend = LLMBackend(config)
                backend._client = mock_client

                results = await backend.score_batch(many_learnings, "some context")

        # Should have results (50 scored by LLM after BM25 pre-filter)
        assert len(results) == 50


# ── Backend auto-selection tests ──────────────────────────────────────────


class TestBackendSelection:
    def test_bm25_when_no_api_key(self):
        config = LearnConfig(openai_api_key=None, llm_enabled=False)
        backend = get_default_backend(config)
        assert isinstance(backend, BM25Backend)

    def test_bm25_when_llm_disabled(self):
        config = LearnConfig(openai_api_key="sk-test", llm_enabled=False)
        backend = get_default_backend(config)
        assert isinstance(backend, BM25Backend)

    def test_bm25_when_no_openai_package(self):
        config = LearnConfig(openai_api_key="sk-test", llm_enabled=True)
        with patch("trace_mcp.extensions.learn.matching._HAS_OPENAI", False):
            backend = get_default_backend(config)
        assert isinstance(backend, BM25Backend)

    def test_llm_when_configured(self):
        config = LearnConfig(openai_api_key="sk-test", llm_enabled=True)
        with patch("trace_mcp.extensions.learn.matching._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.matching.AsyncOpenAI"):
                backend = get_default_backend(config)
        # Can't check isinstance(LLMBackend) easily due to conditional import
        assert not isinstance(backend, BM25Backend)
        assert not isinstance(backend, JaccardBackend)


# ── recall_learnings integration tests ────────────────────────────────────


class TestRecallLearnings:
    async def test_threshold_filters(self):
        """Results below threshold are excluded."""
        results = await recall_learnings(
            ALL_LEARNINGS,
            "conda environment",
            threshold=0.5,
            backend=JaccardBackend(),
        )
        for r in results:
            assert r["score"] >= 0.5

    async def test_limit_caps_results(self):
        results = await recall_learnings(
            ALL_LEARNINGS,
            "conda environment",
            threshold=0.0,  # Include everything
            limit=2,
            backend=JaccardBackend(),
        )
        assert len(results) <= 2

    async def test_sorted_by_score_descending(self):
        results = await recall_learnings(
            ALL_LEARNINGS,
            "conda environment",
            threshold=0.0,
            backend=JaccardBackend(),
        )
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    async def test_empty_learnings(self):
        results = await recall_learnings([], "anything", backend=JaccardBackend())
        assert results == []

    async def test_result_structure(self):
        results = await recall_learnings(
            [CONDA_LEARNING],
            "conda",
            threshold=0.0,
            backend=JaccardBackend(),
        )
        assert len(results) == 1
        assert "learning" in results[0]
        assert "score" in results[0]
        assert results[0]["learning"]["id"] == "lrn_001"

    async def test_bm25_backend_via_recall(self):
        """BM25 backend works through the recall_learnings interface."""
        results = await recall_learnings(
            ALL_LEARNINGS,
            "conda environment activation",
            threshold=0.05,
            backend=BM25Backend(),
        )
        assert len(results) > 0
        # Conda learning should be in results
        ids = [r["learning"]["id"] for r in results]
        assert "lrn_001" in ids

    async def test_cooking_not_returned_for_conda_query(self):
        """Unrelated learnings should be filtered out."""
        results = await recall_learnings(
            ALL_LEARNINGS,
            "conda environment ml-dev activate",
            threshold=0.1,
            backend=BM25Backend(),
        )
        ids = [r["learning"]["id"] for r in results]
        assert "lrn_003" not in ids  # cooking learning


# ── BM25 vs Jaccard comparison tests ─────────────────────────────────────


class TestBM25VsJaccard:
    """Tests demonstrating BM25 improvements over Jaccard."""

    async def test_bm25_handles_document_length_better(self):
        """BM25 normalizes for doc length; Jaccard penalizes long docs."""
        short = Learning(id="short", content="conda env")
        long_doc = Learning(
            id="long",
            content="use the conda env ml-dev for all machine learning tasks in this project",
        )
        query = "conda env"

        jaccard = JaccardBackend(tag_weight=0.0)
        bm25 = BM25Backend(tag_weight=0.0)

        j_results = dict(await jaccard.score_batch([short, long_doc], query))
        b_results = dict(await bm25.score_batch([short, long_doc], query))

        # Jaccard heavily penalizes the long doc (low intersection/union ratio)
        assert j_results[0] > j_results[1]
        # BM25 is more balanced — both mention conda env equally
        # The ratio between scores should be closer for BM25
        j_ratio = j_results[1] / j_results[0] if j_results[0] > 0 else 0
        b_ratio = b_results[1] / b_results[0] if b_results[0] > 0 else 0
        assert b_ratio > j_ratio  # BM25 is less punitive on long docs


# ── Stemmer unit tests ──────────────────────────────────────────────────


class TestStemmer:
    """Tests for the lightweight suffix-stripping stemmer."""

    def test_plural_s(self):
        assert _stem("decisions") == "decision"
        assert _stem("errors") == "error"
        assert _stem("thresholds") == "threshold"

    def test_plural_ies(self):
        assert _stem("entries") == "entry"
        assert _stem("queries") == "query"
        assert _stem("boundaries") == "boundary"

    def test_plural_sses(self):
        assert _stem("processes") == "process"
        assert _stem("addresses") == "address"

    def test_plural_ches_shes_xes_zes(self):
        assert _stem("watches") == "watch"
        assert _stem("pushes") == "push"
        assert _stem("fixes") == "fix"

    def test_gerund_ing(self):
        assert _stem("implementing") == "implement"
        assert _stem("working") == "work"
        assert _stem("testing") == "test"

    def test_gerund_doubled_consonant(self):
        assert _stem("logging") == "log"
        assert _stem("running") == "run"
        assert _stem("sitting") == "sit"
        assert _stem("stopping") == "stop"

    def test_past_tense_ed(self):
        assert _stem("implemented") == "implement"
        assert _stem("worked") == "work"
        assert _stem("tested") == "test"

    def test_past_tense_doubled_consonant(self):
        assert _stem("logged") == "log"
        assert _stem("stopped") == "stop"

    def test_multi_step_stemming(self):
        """Plural + gerund stripping in sequence."""
        assert _stem("learnings") == "learn"
        assert _stem("learning") == "learn"
        assert _stem("learn") == "learn"

    def test_short_words_unchanged(self):
        assert _stem("log") == "log"
        assert _stem("the") == "the"
        assert _stem("is") == "is"

    def test_protected_endings(self):
        """Words ending in -ss, -us, -is should not lose their trailing 's'."""
        assert _stem("process") == "process"
        assert _stem("status") == "status"
        assert _stem("analysis") == "analysis"

    def test_no_vowel_in_stem_prevents_stripping(self):
        """'string' ending in 'ing' should not be stemmed (stem 'str' has no vowel)."""
        assert _stem("string") == "string"

    def test_tokenize_with_stemming(self):
        tokens = _tokenize("logging decisions and implementing errors", stem=True)
        assert "log" in tokens
        assert "decision" in tokens
        assert "implement" in tokens
        assert "error" in tokens

    def test_tokenize_without_stemming(self):
        """Default tokenization does not stem."""
        tokens = _tokenize("logging decisions")
        assert "logging" in tokens
        assert "decisions" in tokens


# ── BM25 stemming tests ─────────────────────────────────────────────────


class TestBM25Stemming:
    """Tests that BM25 stemming fixes morphological mismatches."""

    async def test_decisions_matches_decision(self):
        """'decisions' in query should match 'decision' in learning."""
        learnings = [
            Learning(id="lrn_a", content="Log every decision in TRACE before acting"),
            Learning(id="lrn_b", content="Best pasta recipe uses fresh tomatoes"),
        ]
        backend = BM25Backend(tag_weight=0.0)
        results = await backend.score_batch(learnings, "How should I handle decisions?")
        scores = dict(results)
        assert scores[0] > scores[1]

    async def test_logging_matches_log(self):
        """'logging' in learning should match 'log' in query."""
        learnings = [
            Learning(id="lrn_a", content="Logging discipline is critical for TRACE"),
            Learning(id="lrn_b", content="Best pasta recipe uses fresh tomatoes"),
        ]
        backend = BM25Backend(tag_weight=0.0)
        results = await backend.score_batch(learnings, "How do I log to TRACE?")
        scores = dict(results)
        assert scores[0] > scores[1]

    async def test_errors_matches_error(self):
        """'errors' in learning should match 'error' in query."""
        learnings = [
            Learning(id="lrn_a", content="Check API errors carefully before retrying"),
            Learning(id="lrn_b", content="Best pasta recipe uses fresh tomatoes"),
        ]
        backend = BM25Backend(tag_weight=0.0)
        results = await backend.score_batch(learnings, "Got an error from the API")
        scores = dict(results)
        assert scores[0] > scores[1]

    async def test_stemming_improves_morphological_recall(self):
        """Query with morphological variants recalls the correct learning."""
        learnings = [
            Learning(id="lrn_001", content="Always use ml-dev conda environment, not base"),
            Learning(id="lrn_002", content="Log decisions before implementing — TRACE discipline"),
        ]
        backend = BM25Backend(tag_weight=0.0)
        results = await backend.score_batch(
            learnings, "implementing decisions in the workflow"
        )
        scores = dict(results)
        assert scores[1] > scores[0]  # lrn_002 > lrn_001


# ── Per-backend threshold tests ──────────────────────────────────────────


class TestBackendThresholds:
    """Tests for per-backend default threshold configuration."""

    def test_bm25_default_threshold(self):
        assert BM25Backend().default_threshold == 0.15

    def test_jaccard_default_threshold(self):
        assert JaccardBackend().default_threshold == 0.1

    def test_llm_default_threshold(self):
        config = LearnConfig(openai_api_key="test", llm_enabled=True)
        with patch("trace_mcp.extensions.learn.matching._HAS_OPENAI", True):
            with patch("trace_mcp.extensions.learn.matching.AsyncOpenAI"):
                from trace_mcp.extensions.learn.matching import LLMBackend

                backend = LLMBackend(config)
        assert backend.default_threshold == 0.2

    async def test_recall_uses_backend_threshold_when_none(self):
        """When threshold=None, recall uses the backend's default."""
        backend = BM25Backend()
        results = await recall_learnings(
            ALL_LEARNINGS, "conda environment", threshold=None, backend=backend,
        )
        for r in results:
            assert r["score"] >= backend.default_threshold

    async def test_explicit_threshold_overrides_default(self):
        """Explicit threshold takes precedence over backend default."""
        backend = BM25Backend()
        results_low = await recall_learnings(
            ALL_LEARNINGS, "conda environment", threshold=0.01, backend=backend,
        )
        results_default = await recall_learnings(
            ALL_LEARNINGS, "conda environment", threshold=None, backend=backend,
        )
        assert len(results_low) >= len(results_default)


# ── Recall tracking tests ────────────────────────────────────────────────


class TestRecallTracking:
    """Tests that recall_learnings increments recall_count and sets last_surfaced."""

    async def test_recall_count_incremented(self):
        """Matched learnings should have recall_count incremented."""
        lrn = Learning(id="lrn_001", content="use ml-dev conda environment")
        assert lrn.recall_count == 0
        results = await recall_learnings(
            [lrn], "conda environment", threshold=0.0, backend=BM25Backend(),
        )
        assert len(results) > 0
        assert lrn.recall_count == 1

    async def test_last_surfaced_set(self):
        """Matched learnings should have last_surfaced set."""
        lrn = Learning(id="lrn_001", content="use ml-dev conda environment")
        assert lrn.last_surfaced is None
        await recall_learnings(
            [lrn], "conda environment", threshold=0.0, backend=BM25Backend(),
        )
        assert lrn.last_surfaced is not None

    async def test_below_threshold_not_tracked(self):
        """Learnings that don't match (below threshold) should not be tracked."""
        lrn = Learning(id="lrn_001", content="best pasta recipe with tomatoes")
        await recall_learnings(
            [lrn], "conda environment ml-dev", threshold=0.5, backend=BM25Backend(),
        )
        assert lrn.recall_count == 0
        assert lrn.last_surfaced is None

    async def test_multiple_recalls_accumulate(self):
        """Multiple recalls should accumulate recall_count."""
        lrn = Learning(id="lrn_001", content="use ml-dev conda environment")
        for _ in range(3):
            await recall_learnings(
                [lrn], "conda environment", threshold=0.0, backend=BM25Backend(),
            )
        assert lrn.recall_count == 3
