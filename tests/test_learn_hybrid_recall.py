"""Hybrid recall ranking: fusion of embedding and BM25 ranks, relative floor, weak candidates."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from trace_mcp.extensions.learn import matching
from trace_mcp.extensions.learn.matching import (
    RRF_K,
    SMALL_STORE,
    UNMATCHED_CAP,
    BM25Backend,
    DecayParams,
    EmbeddingBackend,
    RecallOutcome,
    _competition_ranks,
    _rrf,
    _zscores,
    recall_learnings,
    recall_ranked,
)
from trace_mcp.extensions.learn.models import Learning


def _provider(query_vec: list[float]):
    prov = AsyncMock()
    prov.model_name = "test-model"
    prov.dimensions = len(query_vec)
    prov.embed_texts = AsyncMock(return_value=[query_vec])
    prov.embed_query = AsyncMock(return_value=query_vec)
    return prov


def _lrn(i: int, content: str, vec: list[float] | None, days_old: int = 0, tags: list[str] | None = None) -> Learning:
    return Learning(
        id=f"lrn_{i:03d}",
        content=content,
        tags=tags or [],
        embedding=vec,
        embedding_model="test-model" if vec is not None else None,
        created=datetime.now(UTC) - timedelta(days=days_old),
    )


def _big_store(n: int = 12) -> list[Learning]:
    """n learnings; lrn_000 is lexically on-topic, lrn_001 is semantically on-topic, rest are filler."""
    out = [
        _lrn(0, "Set TRACE_EMBEDDING_MODEL to pick the embedding model for recall", [0.6, 0.8, 0.0, 0.0]),
        _lrn(1, "Pick the vector model used when searching past notes", [0.95, 0.3, 0.0, 0.0]),
    ]
    for i in range(2, n):
        out.append(_lrn(i, f"Unrelated cooking note number {i} about pasta and basil", [0.0, 0.0, 1.0, 0.0]))
    return out


class TestFusion:
    async def test_lexical_hit_can_outrank_slightly_higher_cosine(self):
        # query vector is closest to lrn_001 (cosine ~0.95) but lrn_000 shares three query words
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(_big_store(), "embedding model for recall", backend=backend, limit=5)
        assert isinstance(out, RecallOutcome)
        top_two = {r["learning"]["id"] for r in out.results[:2]}
        assert top_two == {"lrn_000", "lrn_001"}
        assert out.results[0]["signals"]["bm25"] is not None
        assert out.results[0]["signals"]["fused"] >= out.results[1]["signals"]["fused"]

    async def test_filler_is_not_matched_on_large_store(self):
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(_big_store(), "embedding model for recall", backend=backend, limit=10)
        by_id = {r["learning"]["id"]: r for r in out.results}
        assert by_id["lrn_000"]["matched"] and by_id["lrn_001"]["matched"]
        assert not any(r["matched"] for r in out.results if r["learning"]["id"] not in {"lrn_000", "lrn_001"})

    async def test_no_absolute_cutoff_low_cosines_still_return(self):
        """Scores well under the old 0.3 line still come back when they stand out from the store."""
        store = [_lrn(i, f"filler {i}", [0.0, 0.0, 1.0, 0.0]) for i in range(11)]
        store.append(_lrn(11, "the one about locks", [0.25, 0.0, 0.97, 0.0]))
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "zzz", backend=backend, limit=3)
        assert out.results[0]["learning"]["id"] == "lrn_011"
        assert out.results[0]["signals"]["similarity"] < 0.3
        assert out.results[0]["matched"] is True  # z >= 1 relative to the store


class TestFloorAndWeakCandidates:
    async def test_nothing_matched_returns_up_to_three_weak_candidates(self):
        store = [_lrn(i, f"filler {i}", [0.0, 0.0, 1.0, 0.0]) for i in range(12)]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "zzz", backend=backend, limit=10)
        assert out.summary.matched == 0
        assert len(out.results) == UNMATCHED_CAP
        assert all(r["matched"] is False for r in out.results)
        assert "z>=" in out.summary.floor

    async def test_zero_variance_marks_nothing_matched(self):
        store = [_lrn(i, f"same {i}", [0.0, 0.0, 1.0, 0.0]) for i in range(12)]
        backend = EmbeddingBackend(provider=_provider([0.0, 0.0, 1.0, 0.0]))
        out = await recall_ranked(store, "zzz", backend=backend, limit=5)
        assert out.summary.matched == 0  # identical cosines: no learning stands out, no lexical hit
        assert all(r["signals"]["z"] == 0.0 for r in out.results)

    async def test_weak_candidates_do_not_count_as_recalled(self):
        store = [_lrn(i, f"filler {i}", [0.0, 0.0, 1.0, 0.0]) for i in range(12)]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        await recall_ranked(store, "zzz", backend=backend, limit=5)
        assert all(lrn.recall_count == 0 and lrn.last_surfaced is None for lrn in store)

    async def test_matched_results_increment_recall_count(self):
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        store = _big_store()
        await recall_ranked(store, "embedding model for recall", backend=backend, limit=2)
        assert store[0].recall_count == 1 and store[1].recall_count == 1
        assert store[5].recall_count == 0


class TestSmallStore:
    async def test_small_store_returns_everything_newest_first_flagged_weak(self):
        """No signal at all: every note still comes back (newest first) so the session can judge."""
        store = [
            _lrn(0, "oldest note about scope 2 units", [0.0, 0.0, 1.0, 0.0], days_old=3),
            _lrn(1, "middle note about page digests", [0.0, 0.0, 1.0, 0.0], days_old=2),
            _lrn(2, "newest note about assurance", [0.0, 0.0, 1.0, 0.0], days_old=1),
        ]
        assert len(store) < SMALL_STORE
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "RUN_TOKEN=abc task A", backend=backend, limit=5)
        assert [r["learning"]["id"] for r in out.results] == ["lrn_002", "lrn_001", "lrn_000"]
        assert all(r["matched"] is False for r in out.results)
        assert out.summary.small_store is True and out.summary.returned == 3 and out.summary.matched == 0
        assert "small store" in out.summary.floor

    async def test_small_store_ranks_a_lexical_hit_first_and_marks_it_matched(self):
        store = [
            _lrn(0, "newest unrelated note", [0.0, 0.0, 1.0, 0.0], days_old=1),
            _lrn(1, "the ZANZIBAR-9931 form gate", [0.0, 0.0, 1.0, 0.0], days_old=5),
            _lrn(2, "another unrelated note", [0.0, 0.0, 1.0, 0.0], days_old=3),
        ]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "ZANZIBAR-9931", backend=backend, limit=5)
        assert out.results[0]["learning"]["id"] == "lrn_001" and out.results[0]["matched"] is True
        assert [r["matched"] for r in out.results] == [True, False, False]
        assert out.summary.matched == 1 and out.summary.returned == 3

    async def test_small_store_respects_limit(self):
        store = [_lrn(i, f"note {i}", [0.0, 0.0, 1.0, 0.0], days_old=10 - i) for i in range(5)]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "q", backend=backend, limit=2)
        assert [r["learning"]["id"] for r in out.results] == ["lrn_004", "lrn_003"]


class TestCompatibility:
    async def test_explicit_threshold_keeps_absolute_semantics(self):
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        store = _big_store()
        everything = await recall_learnings(store, "q", backend=backend, threshold=0.0, limit=100)
        assert len(everything) == len(store)
        assert all(r["matched"] for r in everything)
        assert [r["score"] for r in everything] == sorted((r["score"] for r in everything), reverse=True)
        # Legacy semantics: the combined score is 0.7 * cosine with no tags, so 0.6 admits only lrn_001 (0.67).
        strict = await recall_learnings(store, "q", backend=backend, threshold=0.6, limit=100)
        assert {r["learning"]["id"] for r in strict} == {"lrn_001"}
        assert strict[0]["score"] == pytest.approx(0.7 * 0.9536, abs=0.01)

    async def test_recall_learnings_returns_list_of_dicts_with_score(self):
        backend = BM25Backend()
        store = _big_store()
        results = await recall_learnings(store, "embedding model for recall", backend=backend, limit=3)
        assert isinstance(results, list) and results and "score" in results[0] and "matched" in results[0]

    async def test_bm25_only_backend_uses_its_own_floor(self):
        out = await recall_ranked(_big_store(), "embedding model for recall", backend=BM25Backend(), limit=5)
        assert out.summary.floor.startswith("score>=0.15")
        assert out.results[0]["learning"]["id"] == "lrn_000" and out.results[0]["matched"]
        assert out.results[0]["signals"]["bm25"] is None  # single-signal backend: no second list

    async def test_decay_lowers_score_and_ordering(self):
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        fresh = _lrn(0, "a", [1.0, 0.0, 0.0, 0.0])
        old = _lrn(1, "a", [1.0, 0.0, 0.0, 0.0], days_old=400)
        decay = DecayParams(enabled=True, half_life_days=30)
        store = [old, fresh] + [_lrn(i, "f", [0.0, 0.0, 1.0, 0.0]) for i in range(2, 12)]
        out = await recall_ranked(store, "a", backend=backend, decay_config=decay, limit=2)
        assert out.results[0]["learning"]["id"] == "lrn_000"
        assert out.results[0]["score"] > out.results[1]["score"]

    async def test_unembedded_learnings_rank_by_bm25_standin(self):
        store = _big_store()
        for lrn in store:
            lrn.embedding = None
            lrn.embedding_model = None
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "embedding model for recall", backend=backend, limit=3)
        assert out.results[0]["learning"]["id"] == "lrn_000"

    async def test_tags_boost_only_when_given(self):
        backend = EmbeddingBackend(provider=_provider([0.0, 0.0, 1.0, 0.0]))
        store = _big_store()
        store[7].tags = ["locks", "storage"]
        without = await recall_ranked(store, "zzz", backend=backend, limit=12)
        with_tags = await recall_ranked(store, "zzz", context_tags=["locks"], backend=backend, limit=12)
        assert with_tags.results[0]["learning"]["id"] == "lrn_007"
        assert with_tags.results[0]["matched"] is True
        assert without.results[0]["signals"]["tag_overlap"] == 0.0

    async def test_summary_fields(self):
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(_big_store(), "embedding model for recall", backend=backend, limit=5)
        s = out.summary
        assert s.store_size == 12 and s.scored == 12 and s.returned == len(out.results)
        assert s.backend.startswith("hybrid:embedding:test-model")
        assert s.best_similarity == pytest.approx(0.95, abs=0.02)
        assert s.query == "embedding model for recall"


# ── Exact arithmetic ─────────────────────────────────────────────────────


class TestArithmetic:
    def test_competition_ranks_share_the_best_rank_on_ties(self):
        assert _competition_ranks([0.9, 0.5, 0.5, 0.1]) == [1, 2, 2, 4]
        assert _competition_ranks([0.0, 0.0, 0.0]) == [1, 1, 1]

    def test_rrf_exact_values(self):
        fused = _rrf([[0.9, 0.5, 0.5, 0.1], [0.0, 0.4, 0.0, 0.0]])
        k = RRF_K
        assert k == 60
        assert fused == pytest.approx(
            [1 / (k + 1) + 1 / (k + 2), 1 / (k + 2) + 1 / (k + 1), 2 / (k + 2), 1 / (k + 4) + 1 / (k + 2)]
        )

    def test_zscores_use_population_sd_and_guard_zero_variance(self):
        assert _zscores([1.0, 1.0, 0.0, 0.0]) == pytest.approx([1.0, 1.0, -1.0, -1.0])
        assert _zscores([0.5] * 5 + [0.5 + 1e-12]) == [0.0] * 6
        assert _zscores([]) == []

    async def test_z_floor_is_inclusive_at_exactly_one(self):
        """Four learnings at cosine 1 and four at 0: mean 0.5, sd 0.5, z exactly +1 for the first four."""
        store = [_lrn(i, f"n{i}", [1.0, 0.0, 0.0, 0.0]) for i in range(4)]
        store += [_lrn(i, f"n{i}", [0.0, 0.0, 1.0, 0.0]) for i in range(4, 8)]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "zzz", backend=backend, limit=8)
        flags = {r["learning"]["id"]: (r["matched"], r["signals"]["z"]) for r in out.results}
        assert all(flags[f"lrn_{i:03d}"] == (True, 1.0) for i in range(4))
        assert out.summary.matched == 4 and out.summary.best_z == 1.0

    async def test_lexical_fusion_changes_the_winner(self):
        """A has the higher cosine; B has a slightly lower cosine and the query's words. Fusion picks B."""
        store = [_lrn(i, f"filler {i} pasta", [0.0, 0.0, 1.0, 0.0]) for i in range(10)]
        store[0].content = "filler recall pasta"  # one filler shares a word, so A's lexical rank is 3, not 2
        store.append(_lrn(10, "alpha beta gamma", [0.95, 0.31, 0.0, 0.0]))  # A: cosine ~0.95, no query words
        store.append(_lrn(11, "embedding model for recall", [0.90, 0.44, 0.0, 0.0]))  # B: cosine ~0.90
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "embedding model for recall", backend=backend, limit=2)
        assert [r["learning"]["id"] for r in out.results] == ["lrn_011", "lrn_010"]
        k = RRF_K
        # B: similarity rank 2, lexical rank 1. A: similarity rank 1, lexical rank 3 (behind B and the filler).
        # signals round fused to 5 decimals
        assert out.results[0]["signals"]["fused"] == pytest.approx(1 / (k + 2) + 1 / (k + 1), abs=1e-5)
        assert out.results[1]["signals"]["fused"] == pytest.approx(1 / (k + 1) + 1 / (k + 3), abs=1e-5)
        assert out.results[0]["signals"]["similarity"] < out.results[1]["signals"]["similarity"]


# ── Policy edges ─────────────────────────────────────────────────────────


class TestPolicyEdges:
    async def test_small_store_returns_all_seven_not_three(self):
        store = [_lrn(i, f"filler {i}", [0.0, 0.0, 1.0, 0.0], days_old=i) for i in range(7)]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "zzz", backend=backend, limit=10)
        assert [r["learning"]["id"] for r in out.results] == [f"lrn_{i:03d}" for i in range(7)]
        assert all(r["matched"] is False for r in out.results)

    async def test_store_of_eight_is_not_small_and_caps_weak_results_at_three(self):
        store = [_lrn(i, f"filler {i}", [0.0, 0.0, 1.0, 0.0], days_old=i) for i in range(8)]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "zzz", backend=backend, limit=10)
        ids = [r["learning"]["id"] for r in out.results]
        assert ids == ["lrn_000", "lrn_001", "lrn_002"] and len(set(ids)) == 3
        assert out.summary.small_store is False

    async def test_small_store_suppresses_z_only_matches(self):
        store = [_lrn(0, "standout", [1.0, 0.0, 0.0, 0.0])] + [
            _lrn(i, f"filler {i}", [0.0, 0.0, 1.0, 0.0]) for i in range(1, 5)
        ]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "zzz", backend=backend, limit=5)
        top = out.results[0]
        assert top["learning"]["id"] == "lrn_000" and top["signals"]["z"] > 1.0 and top["matched"] is False

    async def test_small_store_lexical_floor_is_the_bm25_default(self):
        """Matched exactly when the BM25 stand-in is >= 0.15; a weaker lexical overlap is not enough."""
        store = [
            _lrn(0, "the ZANZIBAR-9931 form gate", [0.0, 0.0, 1.0, 0.0]),
            _lrn(1, "a note about something else entirely", [0.0, 0.0, 1.0, 0.0]),
            _lrn(2, "yet another unrelated note", [0.0, 0.0, 1.0, 0.0]),
        ]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "ZANZIBAR-9931", backend=backend, limit=5)
        by_id = {r["learning"]["id"]: r for r in out.results}
        assert by_id["lrn_000"]["signals"]["bm25"] >= 0.15 and by_id["lrn_000"]["matched"]
        assert by_id["lrn_001"]["signals"]["bm25"] == 0.0 and not by_id["lrn_001"]["matched"]

    async def test_unused_query_tags_do_not_change_selection(self):
        """An all-zero tag list is left out of the fusion, so it cannot interact with decay."""
        now = datetime(2026, 10, 12, tzinfo=UTC)

        def make_store() -> list[Learning]:  # recall marks its winner surfaced, so each call gets a fresh store
            a = _lrn(0, "a", [0.9, math.sqrt(1 - 0.81), 0.0, 0.0])
            a.created = now - timedelta(days=4)
            b = _lrn(1, "b", [0.8, 0.6, 0.0, 0.0])
            b.created = now
            return [a, b] + [_lrn(i, "f", [0.0, 0.0, 1.0, 0.0]) for i in range(2, 12)]

        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        decay = DecayParams(enabled=True, half_life_days=365)
        plain = await recall_ranked(make_store(), "zzz", backend=backend, decay_config=decay, limit=1, now=now)
        tagged = await recall_ranked(
            make_store(), "zzz", context_tags=["never-used"], backend=backend, decay_config=decay, limit=1, now=now
        )
        assert plain.results[0]["learning"]["id"] == "lrn_000"
        assert tagged.results[0]["learning"]["id"] == "lrn_000"
        assert tagged.results[0]["signals"]["fused"] == plain.results[0]["signals"]["fused"]

    async def test_tag_fusion_reorders_two_matched_candidates(self):
        """A leads on similarity, B on lexical; they tie on fusion until the tag list breaks it for B."""

        def make_store() -> list[Learning]:
            store = [_lrn(i, f"filler {i}", [0.0, 0.0, 1.0, 0.0]) for i in range(10)]
            store.append(_lrn(10, "embedding model for recall", [0.95, 0.31, 0.0, 0.0]))  # A: no tags
            store.append(_lrn(11, "embedding model embedding model for recall", [0.90, 0.44, 0.0, 0.0], tags=["locks"]))
            return store

        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        k = RRF_K
        without = await recall_ranked(make_store(), "embedding model for recall", backend=backend, limit=2)
        # A: similarity rank 1, lexical rank 2. B: similarity rank 2, lexical rank 1. Equal fusion; A by similarity.
        assert [r["learning"]["id"] for r in without.results] == ["lrn_010", "lrn_011"]
        assert without.results[0]["signals"]["fused"] == pytest.approx(1 / (k + 1) + 1 / (k + 2), abs=1e-5)
        with_tags = await recall_ranked(
            make_store(), "embedding model for recall", context_tags=["locks"], backend=backend, limit=2
        )
        # The tag list adds rank 1 for B and rank 2 for everyone else: B now leads.
        assert [r["learning"]["id"] for r in with_tags.results] == ["lrn_011", "lrn_010"]
        assert with_tags.results[0]["signals"]["fused"] == pytest.approx(2 / (k + 1) + 1 / (k + 2), abs=1e-5)
        assert with_tags.results[1]["signals"]["fused"] == pytest.approx(1 / (k + 1) + 2 / (k + 2), abs=1e-5)
        assert with_tags.results[0]["signals"]["tag_overlap"] == 1.0
        assert all(r["matched"] for r in with_tags.results)

    async def test_lexical_floor_boundary_hybrid_and_bm25_only(self):
        """Matched exactly at bm25 >= 0.15 for both the hybrid floor and the BM25-only floor."""
        store = [_lrn(i, f"n{i}", [0.0, 0.0, 1.0, 0.0]) for i in range(12)]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        lexical = [0.0] * 12
        lexical[0], lexical[1], lexical[2] = 0.149, 0.15, 0.151
        backend.bm25.text_scores = AsyncMock(return_value=lexical)  # type: ignore[method-assign]
        out = await recall_ranked(store, "q", backend=backend, limit=12)
        flags = {r["learning"]["id"]: r["matched"] for r in out.results}
        assert (flags["lrn_000"], flags["lrn_001"], flags["lrn_002"]) == (False, True, True)
        assert out.summary.matched == 2
        bm25_only = BM25Backend()
        bm25_only.text_scores = AsyncMock(return_value=lexical)  # type: ignore[method-assign]
        out2 = await recall_ranked(store, "q", backend=bm25_only, limit=12)
        flags2 = {r["learning"]["id"]: r["matched"] for r in out2.results}
        assert (flags2["lrn_000"], flags2["lrn_001"], flags2["lrn_002"]) == (False, True, True)
        assert out2.summary.matched == 2 and out2.summary.floor == "score>=0.15"

    async def test_decay_never_outranks_evidence_but_breaks_ties_and_scales_score(self):
        now = datetime(2026, 10, 12, tzinfo=UTC)
        params = DecayParams(enabled=True, half_life_days=30)

        def make_store() -> list[Learning]:
            old_strong = _lrn(0, "a", [1.0, 0.0, 0.0, 0.0])  # cosine 1.0, 400 days old
            old_strong.created = now - timedelta(days=400)
            fresh_weaker = _lrn(1, "a", [0.9, math.sqrt(1 - 0.81), 0.0, 0.0])  # cosine 0.9, today
            fresh_weaker.created = now
            return [old_strong, fresh_weaker] + [_lrn(i, "f", [0.0, 0.0, 1.0, 0.0]) for i in range(2, 12)]

        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(make_store(), "zzz", backend=backend, decay_config=params, limit=2, now=now)
        # Different evidence: the stronger learning stays first however old it is ...
        assert [r["learning"]["id"] for r in out.results] == ["lrn_000", "lrn_001"]
        old_row, fresh_row = out.results
        assert old_row["signals"]["fused"] == pytest.approx(1 / (RRF_K + 1), abs=1e-5)
        assert fresh_row["signals"]["fused"] == pytest.approx(1 / (RRF_K + 2), abs=1e-5)
        # ... but its displayed score carries the decay.
        expected = 2 ** (-400 / 30)
        assert old_row["signals"]["decay"] == pytest.approx(expected, abs=1e-3)
        assert old_row["score"] == pytest.approx(1.0 * expected, abs=1e-3)
        assert fresh_row["score"] == pytest.approx(0.9, abs=1e-3)

    async def test_decay_breaks_ties_with_the_evergreen_floor_beating_mere_recency(self):
        """Equal evidence: an evergreen learning (floor 0.8) outranks a fresher one whose decay is 0.707."""
        now = datetime(2026, 10, 12, tzinfo=UTC)
        params = DecayParams(enabled=True, half_life_days=30, evergreen_recall_threshold=3, evergreen_floor=0.8)

        def make_store() -> list[Learning]:
            evergreen = _lrn(0, "a", [1.0, 0.0, 0.0, 0.0])
            evergreen.created = now - timedelta(days=400)
            evergreen.recall_count = 5
            newer = _lrn(1, "a", [1.0, 0.0, 0.0, 0.0])
            newer.created = now - timedelta(days=15)
            return [newer, evergreen] + [_lrn(i, "f", [0.0, 0.0, 1.0, 0.0]) for i in range(2, 12)]

        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        with_decay = await recall_ranked(make_store(), "zzz", backend=backend, decay_config=params, limit=2, now=now)
        assert [r["learning"]["id"] for r in with_decay.results] == ["lrn_000", "lrn_001"]
        assert with_decay.results[0]["signals"]["decay"] == pytest.approx(0.8)
        assert with_decay.results[1]["signals"]["decay"] == pytest.approx(2 ** (-15 / 30), abs=1e-3)
        no_decay = await recall_ranked(
            make_store(), "zzz", backend=backend, decay_config=DecayParams(enabled=False), limit=2, now=now
        )
        assert [r["learning"]["id"] for r in no_decay.results] == ["lrn_001", "lrn_000"]  # newest created wins the tie

    async def test_only_surfaced_matches_get_bookkeeping_and_weak_state_is_preserved(self):
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        store = [_lrn(i, f"filler {i}", [0.0, 0.0, 1.0, 0.0]) for i in range(8)]
        for j in range(8, 12):  # four lexical hits, all matched
            store.append(_lrn(j, "embedding model for recall", [0.0, 0.0, 1.0, 0.0]))
        for lrn in store:
            lrn.recall_count = 7
            lrn.last_surfaced = t0
        backend = EmbeddingBackend(provider=_provider([0.0, 0.0, 1.0, 0.0]))
        now = datetime(2026, 10, 12, tzinfo=UTC)
        out = await recall_ranked(store, "embedding model for recall", backend=backend, limit=2, now=now)
        surfaced = {r["learning"]["id"] for r in out.results}
        assert len(surfaced) == 2 and surfaced <= {f"lrn_{j:03d}" for j in range(8, 12)}
        for lrn in store:
            if lrn.id in surfaced:
                assert lrn.recall_count == 8 and lrn.last_surfaced == now
            else:
                assert lrn.recall_count == 7 and lrn.last_surfaced == t0

    async def test_weak_results_are_distinct_learnings(self):
        store = [_lrn(i, f"same {i}", [0.0, 0.0, 1.0, 0.0]) for i in range(12)]
        backend = EmbeddingBackend(provider=_provider([0.0, 0.0, 1.0, 0.0]))
        out = await recall_ranked(store, "zzz", backend=backend, limit=5)
        ids = [r["learning"]["id"] for r in out.results]
        assert len(ids) == 3 and len(set(ids)) == 3 and all(r["matched"] is False for r in out.results)

    async def test_summary_is_exact(self):
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(_big_store(), "embedding model for recall", backend=backend, limit=5)
        s = out.summary
        assert (s.backend, s.store_size, s.scored, s.matched, s.returned, s.small_store) == (
            "hybrid:embedding:test-model+bm25",
            12,
            12,
            2,
            5,
            False,
        )
        assert s.floor == "z>=1 or bm25>=0.15"
        sims = [0.6, 0.95 / math.sqrt(0.95**2 + 0.3**2)] + [0.0] * 10  # the store's cosines against [1,0,0,0]
        mean = sum(sims) / 12
        sd = math.sqrt(sum((v - mean) ** 2 for v in sims) / 12)
        assert s.best_similarity == pytest.approx(max(sims), abs=1e-3)
        assert s.best_z == pytest.approx((max(sims) - mean) / sd, abs=0.01)
        assert s.query == "embedding model for recall"

    async def test_empty_store_returns_before_any_backend_is_built(self):
        with patch.object(matching, "get_default_backend", side_effect=RuntimeError("must not be called")):
            assert await recall_learnings([], "x") == []
            out = await recall_ranked([], "x")
        assert out.summary.backend == "none" and out.summary.scored == 0


# ── Explicit threshold: the legacy path, verbatim ────────────────────────


class _SparseBackend:
    """A pre-filtering backend: scores only some learnings (like the LLM backend)."""

    default_threshold = 0.2

    def __init__(self, pairs: list[tuple[int, float]]) -> None:
        self.pairs = pairs

    async def text_scores(self, learnings, context):  # pragma: no cover - not used on the legacy path
        raise AssertionError("legacy path must not call text_scores on a backend without mix_scores")

    async def score_batch(self, learnings, context, context_tags=None):
        return list(self.pairs)


class TestExplicitThresholdLegacyPath:
    async def test_sparse_backend_results_stay_sparse(self):
        store = _big_store(4)
        backend = _SparseBackend([(2, 0.9)])
        out = await recall_ranked(store, "q", threshold=0.0, backend=backend, limit=10)
        assert [r["learning"]["id"] for r in out.results] == ["lrn_002"]
        assert out.summary.scored == 1 and out.summary.matched == 1
        assert [lrn.recall_count for lrn in store] == [0, 0, 1, 0]
        assert out.results[0]["signals"]["similarity"] == 0.9  # the backend's own score is its similarity

    async def test_threshold_is_inclusive(self):
        store = _big_store(4)
        out = await recall_ranked(store, "q", threshold=0.9, backend=_SparseBackend([(2, 0.9), (1, 0.89)]), limit=10)
        assert [r["learning"]["id"] for r in out.results] == ["lrn_002"]

    async def test_equal_scores_keep_the_backend_order_even_with_limit_one(self):
        store = _big_store(4)
        out = await recall_ranked(store, "q", threshold=0.0, backend=_SparseBackend([(3, 0.5), (0, 0.5)]), limit=1)
        assert [r["learning"]["id"] for r in out.results] == ["lrn_003"]
        assert store[3].recall_count == 1 and store[0].recall_count == 0

    async def test_embedding_legacy_order_puts_embedded_first_on_ties(self):
        """Equal combined scores: the embedded learning precedes the BM25 stand-in, as score_batch always ordered them."""
        unemb = _lrn(0, "u", None)
        emb = _lrn(1, "e", [1.0, 0.0, 0.0, 0.0])
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]), tag_weight=0.0)
        backend.text_scores = AsyncMock(return_value=[0.5, 0.5])  # type: ignore[method-assign]
        assert backend.mix_scores([0.5, 0.5], [unemb, emb], None) == [(1, 0.5), (0, 0.5)]
        out = await recall_ranked([unemb, emb], "q", threshold=0.0, backend=backend, limit=1)
        assert out.results[0]["learning"]["id"] == "lrn_001"
        assert emb.recall_count == 1 and unemb.recall_count == 0

    async def test_an_overridden_score_batch_is_authoritative(self):
        """An injected backend that overrides score_batch decides eligibility; text_scores only feeds diagnostics."""

        class Opinionated(EmbeddingBackend):
            async def score_batch(self, learnings, context, context_tags=None):
                return [(1, 0.9), (0, 0.1)]  # sparse, distinct, deliberately not the mixed cosine

        store = _big_store(4)
        backend = Opinionated(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        out = await recall_ranked(store, "q", threshold=0.5, backend=backend, limit=10)
        assert [r["learning"]["id"] for r in out.results] == ["lrn_001"]
        assert out.results[0]["score"] == 0.9 and out.summary.scored == 2
        assert out.results[0]["signals"]["similarity"] == pytest.approx(0.9536, abs=1e-3)  # raw cosine, for diagnostics

    async def test_diagnostics_report_raw_similarity_not_the_mixed_score(self):
        doc = _lrn(0, "d", [0.0, 1.0, 0.0, 0.0], tags=["t"])
        store = [doc] + [_lrn(i, "f", [0.0, 0.0, 1.0, 0.0]) for i in range(1, 3)]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]), tag_weight=0.3)
        out = await recall_ranked(store, "q", context_tags=["t"], threshold=0.0, backend=backend, limit=3)
        top = out.results[0]
        assert top["learning"]["id"] == "lrn_000"
        assert top["score"] == pytest.approx(0.3)  # 0.7 * cosine 0 + 0.3 * tag overlap 1
        assert top["signals"]["similarity"] == 0.0


# ── Final-review additions ───────────────────────────────────────────────


class TestFinalReview:
    async def test_lexical_floor_is_inclusive_at_exactly_0_15_and_exclusive_just_below(self):
        store = [_lrn(i, f"n{i}", [0.0, 0.0, 1.0, 0.0]) for i in range(12)]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        lexical = [0.0] * 12
        lexical[0], lexical[1] = math.nextafter(0.15, 0.0), 0.15
        backend.bm25.text_scores = AsyncMock(return_value=lexical)  # type: ignore[method-assign]
        out = await recall_ranked(store, "q", backend=backend, limit=12)
        flags = {r["learning"]["id"]: r["matched"] for r in out.results}
        assert (flags["lrn_000"], flags["lrn_001"]) == (False, True)

    async def test_evergreen_floor_applies_at_exactly_the_configured_count(self):
        now = datetime(2026, 10, 12, tzinfo=UTC)
        params = DecayParams(enabled=True, half_life_days=30, evergreen_recall_threshold=3, evergreen_floor=0.8)
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        for count, expect_first in ((2, "lrn_001"), (3, "lrn_000"), (4, "lrn_000")):
            old = _lrn(0, "a", [1.0, 0.0, 0.0, 0.0])
            old.created = now - timedelta(days=400)
            old.recall_count = count
            newer = _lrn(1, "a", [1.0, 0.0, 0.0, 0.0])
            newer.created = now - timedelta(days=15)
            store = [newer, old] + [_lrn(i, "f", [0.0, 0.0, 1.0, 0.0]) for i in range(2, 12)]
            out = await recall_ranked(store, "zzz", backend=backend, decay_config=params, limit=1, now=now)
            assert out.results[0]["learning"]["id"] == expect_first, count

    async def test_bm25_only_keeps_the_older_stronger_lexical_hit_first_under_decay(self):
        now = datetime(2026, 10, 12, tzinfo=UTC)
        old = _lrn(0, "embedding model for recall embedding model", None)
        old.created = now - timedelta(days=700)
        fresh = _lrn(1, "an embedding note", None)
        fresh.created = now
        store = [fresh, old] + [_lrn(i, f"pasta {i}", None) for i in range(2, 12)]
        params = DecayParams(enabled=True, half_life_days=30)
        out = await recall_ranked(
            store, "embedding model for recall", backend=BM25Backend(), decay_config=params, limit=2, now=now
        )
        assert [r["learning"]["id"] for r in out.results] == ["lrn_000", "lrn_001"]
        assert out.results[0]["score"] < out.results[1]["score"]  # the displayed score still carries decay

    async def test_legacy_diagnostics_failure_does_not_discard_a_successful_scoring(self):
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        store = _big_store(4)
        calls = {"n": 0}
        real_text_scores = backend.text_scores

        async def flaky(learnings, context):
            calls["n"] += 1
            if calls["n"] == 2:  # the diagnostic read after a successful score_batch
                raise RuntimeError("rate limited")
            return await real_text_scores(learnings, context)

        backend.text_scores = flaky  # type: ignore[method-assign]
        out = await recall_ranked(store, "q", threshold=0.0, backend=backend, limit=10)
        assert len(out.results) == 4 and out.summary.scored == 4
        # the raw similarity is reported as unavailable, never substituted
        assert all(r["signals"]["similarity"] is None and r["signals"]["z"] is None for r in out.results)
        assert out.summary.best_similarity is None and out.summary.best_z is None
        assert out.results[0]["score"] > 0.0

    async def test_legacy_path_skips_diagnostics_when_nothing_was_scored(self):
        backend = _SparseBackend([])
        store = _big_store(4)
        out = await recall_ranked(store, "q", threshold=0.0, backend=backend, limit=10)
        assert out.results == [] and out.summary.scored == 0

    async def test_authoritative_scores_above_0_9_pass_through(self):
        out = await recall_ranked(
            _big_store(4), "q", threshold=0.95, backend=_SparseBackend([(1, 1.0), (2, 0.96), (0, 0.94)]), limit=10
        )
        assert [(r["learning"]["id"], r["score"]) for r in out.results] == [("lrn_001", 1.0), ("lrn_002", 0.96)]

    async def test_bm25_only_lexical_floor_is_exact_too(self):
        store = [_lrn(i, f"n{i}", None) for i in range(12)]
        bm25_only = BM25Backend()
        lexical = [0.0] * 12
        lexical[0], lexical[1] = math.nextafter(0.15, 0.0), 0.15
        bm25_only.text_scores = AsyncMock(return_value=lexical)  # type: ignore[method-assign]
        out = await recall_ranked(store, "q", backend=bm25_only, limit=12)
        flags = {r["learning"]["id"]: r["matched"] for r in out.results}
        assert (flags["lrn_000"], flags["lrn_001"]) == (False, True)

    async def test_distinct_tag_overlaps_rank_distinctly(self):
        store = [_lrn(i, f"filler {i}", [0.0, 0.0, 1.0, 0.0]) for i in range(10)]
        store.append(_lrn(10, "a", [0.0, 0.0, 1.0, 0.0], tags=["locks", "storage"]))  # overlap 0.5 with ["locks"]
        store.append(_lrn(11, "b", [0.0, 0.0, 1.0, 0.0], tags=["locks"]))  # overlap 1.0
        backend = EmbeddingBackend(provider=_provider([0.0, 0.0, 1.0, 0.0]))
        out = await recall_ranked(store, "zzz", context_tags=["locks"], backend=backend, limit=2)
        assert [r["learning"]["id"] for r in out.results] == ["lrn_011", "lrn_010"]
        assert [r["signals"]["tag_overlap"] for r in out.results] == [1.0, 0.5]
        k = RRF_K
        assert out.results[0]["signals"]["fused"] == pytest.approx(1 / (k + 1) + 1 / (k + 1), abs=1e-5)
        assert out.results[1]["signals"]["fused"] == pytest.approx(1 / (k + 1) + 1 / (k + 2), abs=1e-5)

    async def test_similarity_precedes_decay_when_fusion_ties(self):
        """A leads on similarity, B on lexical (fusion tie); A is 400 days old, B fresh: A still wins."""
        now = datetime(2026, 10, 12, tzinfo=UTC)
        store = [_lrn(i, f"filler {i}", [0.0, 0.0, 1.0, 0.0]) for i in range(10)]
        a = _lrn(10, "alpha beta gamma", [0.95, 0.31, 0.0, 0.0])
        a.created = now - timedelta(days=400)
        b = _lrn(11, "embedding model for recall", [0.90, 0.44, 0.0, 0.0])
        b.created = now
        store += [a, b]
        backend = EmbeddingBackend(provider=_provider([1.0, 0.0, 0.0, 0.0]))
        params = DecayParams(enabled=True, half_life_days=30)
        out = await recall_ranked(
            store, "embedding model for recall", backend=backend, decay_config=params, limit=2, now=now
        )
        assert out.results[0]["signals"]["fused"] == pytest.approx(out.results[1]["signals"]["fused"], abs=1e-9)
        assert [r["learning"]["id"] for r in out.results] == ["lrn_010", "lrn_011"]
