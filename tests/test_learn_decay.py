"""Tests for trace-learn decay and staleness scoring.

Tests: compute_decay math, evergreen floor, last_surfaced vs created,
decay in recall_learnings integration, decay disabled behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trace_mcp.extensions.learn.matching import (
    BM25Backend,
    DecayParams,
    compute_decay,
    recall_learnings,
)
from trace_mcp.extensions.learn.models import Learning


# ── compute_decay unit tests ─────────────────────────────────────────────


class TestComputeDecay:
    def test_fresh_learning_no_decay(self):
        """A learning created just now should have multiplier ~1.0."""
        now = datetime.now(UTC)
        lrn = Learning(content="fresh", created=now)
        m = compute_decay(lrn, now=now)
        assert m == pytest.approx(1.0)

    def test_half_life_produces_half(self):
        """A learning aged exactly one half-life should have multiplier ~0.5."""
        now = datetime.now(UTC)
        lrn = Learning(content="old", created=now - timedelta(days=365))
        m = compute_decay(lrn, half_life_days=365.0, now=now)
        assert m == pytest.approx(0.5, abs=0.01)

    def test_two_half_lives(self):
        """A learning aged two half-lives should have multiplier ~0.25."""
        now = datetime.now(UTC)
        lrn = Learning(content="very old", created=now - timedelta(days=730))
        m = compute_decay(lrn, half_life_days=365.0, now=now)
        assert m == pytest.approx(0.25, abs=0.01)

    def test_never_surfaced_uses_created(self):
        """A learning with no last_surfaced should decay from created date."""
        now = datetime.now(UTC)
        lrn = Learning(content="never surfaced", created=now - timedelta(days=365))
        assert lrn.last_surfaced is None
        m = compute_decay(lrn, half_life_days=365.0, now=now)
        assert m == pytest.approx(0.5, abs=0.01)

    def test_recently_surfaced_resets_age(self):
        """A learning surfaced recently should have high multiplier even if old."""
        now = datetime.now(UTC)
        lrn = Learning(
            content="old but active",
            created=now - timedelta(days=730),  # 2 years old
            last_surfaced=now - timedelta(days=1),  # surfaced yesterday
            recall_count=1,
        )
        m = compute_decay(lrn, half_life_days=365.0, now=now)
        assert m > 0.99  # almost no decay

    def test_evergreen_floor_applies(self):
        """A learning surfaced 3+ times should never decay below floor."""
        now = datetime.now(UTC)
        lrn = Learning(
            content="evergreen",
            created=now - timedelta(days=3650),  # 10 years old
            recall_count=3,
        )
        m = compute_decay(
            lrn,
            half_life_days=365.0,
            evergreen_recall_threshold=3,
            evergreen_floor=0.8,
            now=now,
        )
        assert m >= 0.8

    def test_below_evergreen_threshold_no_floor(self):
        """A learning surfaced fewer than threshold times gets no floor."""
        now = datetime.now(UTC)
        lrn = Learning(
            content="not evergreen",
            created=now - timedelta(days=3650),  # 10 years old
            recall_count=2,  # below threshold of 3
        )
        m = compute_decay(
            lrn,
            half_life_days=365.0,
            evergreen_recall_threshold=3,
            evergreen_floor=0.8,
            now=now,
        )
        assert m < 0.8

    def test_zero_half_life_returns_one(self):
        """Edge case: zero half_life should return 1.0 (no decay)."""
        now = datetime.now(UTC)
        lrn = Learning(content="test", created=now - timedelta(days=365))
        m = compute_decay(lrn, half_life_days=0.0, now=now)
        assert m == 1.0

    def test_evergreen_with_recent_surface(self):
        """Evergreen + recently surfaced should be near 1.0."""
        now = datetime.now(UTC)
        lrn = Learning(
            content="best practice",
            created=now - timedelta(days=365),
            last_surfaced=now - timedelta(hours=1),
            recall_count=10,
        )
        m = compute_decay(lrn, now=now)
        assert m > 0.99


# ── Decay in recall_learnings integration ────────────────────────────────


class TestDecayInRecall:
    async def test_old_learning_scores_lower_than_fresh(self):
        """With decay enabled, an old learning should score lower."""
        now = datetime.now(UTC)
        old = Learning(
            id="lrn_old",
            content="use conda ml-dev environment for ML tasks",
            created=now - timedelta(days=730),
        )
        fresh = Learning(
            id="lrn_fresh",
            content="use conda ml-dev environment for ML tasks",
            created=now,
        )
        decay = DecayParams(enabled=True, half_life_days=365.0)
        results = await recall_learnings(
            [old, fresh],
            "conda ml-dev environment",
            threshold=0.0,
            backend=BM25Backend(tag_weight=0.0),
            decay_config=decay,
        )
        scores = {r["learning"]["id"]: r["score"] for r in results}
        assert scores["lrn_fresh"] > scores["lrn_old"]

    async def test_decay_disabled_no_effect(self):
        """With decay disabled, old and fresh learnings with same content score equally."""
        now = datetime.now(UTC)
        old = Learning(
            id="lrn_old",
            content="use conda ml-dev environment",
            created=now - timedelta(days=730),
        )
        fresh = Learning(
            id="lrn_fresh",
            content="use conda ml-dev environment",
            created=now,
        )
        decay = DecayParams(enabled=False)
        results = await recall_learnings(
            [old, fresh],
            "conda ml-dev environment",
            threshold=0.0,
            backend=BM25Backend(tag_weight=0.0),
            decay_config=decay,
        )
        scores = {r["learning"]["id"]: r["score"] for r in results}
        assert scores["lrn_fresh"] == pytest.approx(scores["lrn_old"])

    async def test_no_decay_config_no_effect(self):
        """When decay_config is None, no decay is applied."""
        now = datetime.now(UTC)
        old = Learning(
            id="lrn_old",
            content="use conda ml-dev environment",
            created=now - timedelta(days=730),
        )
        fresh = Learning(
            id="lrn_fresh",
            content="use conda ml-dev environment",
            created=now,
        )
        results = await recall_learnings(
            [old, fresh],
            "conda ml-dev environment",
            threshold=0.0,
            backend=BM25Backend(tag_weight=0.0),
            decay_config=None,
        )
        scores = {r["learning"]["id"]: r["score"] for r in results}
        assert scores["lrn_fresh"] == pytest.approx(scores["lrn_old"])


# ── Additional decay tests (Phase 5b) ───────────────────────────────────


class TestComputeDecayExtended:
    """Additional edge case tests for compute_decay."""

    def test_fresh_learning_is_near_one(self):
        """Learning created this instant → decay ~1.0."""
        now = datetime.now(UTC)
        lrn = Learning(content="brand new", created=now)
        m = compute_decay(lrn, now=now)
        assert m == pytest.approx(1.0)

    def test_90_days_old_significant_decay(self):
        """90 days old with 365-day half-life → noticeable decay."""
        now = datetime.now(UTC)
        lrn = Learning(content="3 months old", created=now - timedelta(days=90))
        m = compute_decay(lrn, half_life_days=365.0, now=now)
        # 2^(-90/365) ≈ 0.834
        assert 0.80 < m < 0.90

    def test_evergreen_floor_at_threshold(self):
        """Exactly at evergreen threshold → floor applies."""
        now = datetime.now(UTC)
        lrn = Learning(
            content="evergreen at threshold",
            created=now - timedelta(days=3650),
            recall_count=3,
        )
        m = compute_decay(lrn, evergreen_recall_threshold=3, evergreen_floor=0.8, now=now)
        assert m >= 0.8

    def test_negative_age_clamped(self):
        """If last_surfaced is in the future (clock skew), age clamped to 0."""
        now = datetime.now(UTC)
        lrn = Learning(
            content="future surfaced",
            created=now - timedelta(days=10),
            last_surfaced=now + timedelta(days=1),  # future!
            recall_count=1,
        )
        m = compute_decay(lrn, now=now)
        assert m == pytest.approx(1.0)
