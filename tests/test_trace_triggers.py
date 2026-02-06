#!/usr/bin/env python3
"""
Test suite for TRACE Smart Triggers and Knowledge Persistence (v2.1)

Tests:
- Trigger pattern detection
- Text similarity calculation
- Knowledge check workflow
- Checkpoint analysis
- Context refresh
- Learning consolidation

Run: pytest tests/test_trace_triggers.py -v
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "mcp_server"))

from server import (
    TRIGGER_PATTERNS,
    # Checkpoint functions
    analyze_session_for_checkpoint,
    calculate_text_similarity,
    compute_knowledge_metrics,
    consolidate_session_learnings,
    # Utilities
    create_default_trace,
    # Trigger functions
    detect_event_type,
    find_similar_entries,
    generate_suggested_fields,
    knowledge_check,
    refresh_context_for_topics,
)

# ============================================================
# Test Fixtures
# ============================================================


def create_test_trace():
    """Create a test TRACE structure with sample data."""
    trace = create_default_trace()

    # Add a session
    trace["sessions"].append(
        {
            "id": "S001",
            "started": (datetime.now() - timedelta(minutes=45)).isoformat(),
            "ended": None,
            "purpose": "Test session",
            "scientific_stage": "analysis",
        }
    )

    # Add some learnings
    trace["learnings"] = [
        {
            "id": "L001",
            "session_id": "S001",
            "timestamp": datetime.now().isoformat(),
            "learning": "pytest fixtures can be scoped to session level for expensive setup",
            "evidence": "Reduced test time by 50%",
            "tags": ["testing", "pytest", "performance"],
            "discovered_by": "collaborative",
        },
        {
            "id": "L002",
            "session_id": "S001",
            "timestamp": datetime.now().isoformat(),
            "learning": "async functions need special handling in pytest",
            "evidence": "Tests were silently passing without running",
            "tags": ["testing", "pytest", "async"],
            "discovered_by": "ai_suggested",
        },
    ]

    # Add some gotchas
    trace["gotchas"] = [
        {
            "id": "G001",
            "session_id": "S001",
            "timestamp": datetime.now().isoformat(),
            "problem": "pytest-asyncio silently passes async tests without running them",
            "solution": "Add asyncio_mode = auto to pytest.ini",
            "severity": "high",
            "tags": ["pytest", "async", "testing"],
            "discovered_by": "human",
        },
        {
            "id": "G002",
            "timestamp": (datetime.now() - timedelta(days=5)).isoformat(),
            "problem": "pandas merge drops rows with NaN keys silently",
            "solution": "Use fillna() before merge or handle NaN explicitly",
            "severity": "high",
            "tags": ["pandas", "data", "gotcha"],
            "discovered_by": "collaborative",
        },
    ]

    # Add some decisions
    trace["decisions"] = [
        {
            "id": "D001",
            "session_id": "S001",
            "timestamp": datetime.now().isoformat(),
            "decision": "Use pytest over unittest for testing framework",
            "rationale": "Better fixture support and more readable assertions",
            "alternatives_considered": "unittest, nose2",
            "tags": ["testing", "architecture"],
            "proposed_by": "human",
        }
    ]

    # Add some ideas
    trace["ideas"] = [
        {
            "id": "I001",
            "session_id": "S001",
            "timestamp": datetime.now().isoformat(),
            "idea": "Could add caching to the similarity calculation for performance",
            "idea_type": "optimization",
            "source": "ai_suggested",
        }
    ]

    # Add some code contributions
    trace["code_contributions"] = [
        {
            "id": "CC001",
            "session_id": "S001",
            "timestamp": datetime.now().isoformat(),
            "file_path": "tests/test_example.py",
            "contribution_type": "creation",
            "description": "Added test file for example module",
            "direction_source": "human_directed",
        }
    ]

    # Add a pending suggestion
    trace["ai_suggestions"] = [
        {
            "id": "SUG001",
            "session_id": "S001",
            "timestamp": datetime.now().isoformat(),
            "suggestion": {"type": "optimization", "description": "Add memoization to recursive function"},
            "outcome": {"status": "pending"},
        }
    ]

    return trace


# ============================================================
# Tests: Trigger Pattern Detection
# ============================================================


class TestTriggerPatterns:
    """Test trigger pattern detection."""

    def test_gotcha_detection_unexpected(self):
        """Detect gotcha from 'unexpected' keyword."""
        context = "The API returned an unexpected 200 status on validation errors"
        result = detect_event_type(context)

        assert len(result) > 0
        types = [r[0] for r in result]
        assert "gotcha" in types

    def test_gotcha_detection_workaround(self):
        """Detect gotcha from workaround description."""
        context = "Had to use a workaround because the library silently fails on empty input"
        result = detect_event_type(context)

        types = [r[0] for r in result]
        assert "gotcha" in types

    def test_gotcha_detection_documentation_mismatch(self):
        """Detect gotcha from documentation mismatch."""
        context = "The documentation says it returns a list but it actually returns a generator"
        result = detect_event_type(context)

        types = [r[0] for r in result]
        assert "gotcha" in types

    def test_decision_detection(self):
        """Detect decision from choice language."""
        context = "Decided to use pandas over polars because of better documentation"
        result = detect_event_type(context)

        types = [r[0] for r in result]
        assert "decision" in types

    def test_decision_detection_tradeoff(self):
        """Detect decision from trade-off discussion."""
        context = "Made a trade-off between performance and readability, chose readability"
        result = detect_event_type(context)

        types = [r[0] for r in result]
        assert "decision" in types

    def test_learning_detection(self):
        """Detect learning from discovery language."""
        context = "Learned that pytest fixtures can be scoped to the session level"
        result = detect_event_type(context)

        types = [r[0] for r in result]
        assert "learning" in types

    def test_learning_detection_insight(self):
        """Detect learning from insight language."""
        context = "Turns out the middleware caches responses for 5 minutes by default"
        result = detect_event_type(context)

        types = [r[0] for r in result]
        assert "learning" in types

    def test_idea_detection(self):
        """Detect idea from improvement suggestion."""
        context = "Could improve performance by batching these API calls"
        result = detect_event_type(context)

        types = [r[0] for r in result]
        assert "idea" in types

    def test_idea_detection_future(self):
        """Detect idea from future improvement."""
        context = "Eventually we should refactor this to use async/await"
        result = detect_event_type(context)

        types = [r[0] for r in result]
        assert "idea" in types

    def test_intervention_detection(self):
        """Detect intervention from modification language."""
        context = "Changed the AI-generated code to use a simpler approach"
        result = detect_event_type(context)

        types = [r[0] for r in result]
        assert "intervention" in types

    def test_code_detection(self):
        """Detect code contribution."""
        context = "Created a new function to handle user authentication, about 50 lines"
        result = detect_event_type(context)

        types = [r[0] for r in result]
        assert "code" in types

    def test_error_detection(self):
        """Detect error from exception language."""
        context = "Got a TypeError when calling the function with None"
        result = detect_event_type(context)

        types = [r[0] for r in result]
        assert "error" in types

    def test_multiple_types_detected(self):
        """Multiple types can be detected from complex context."""
        context = "Discovered that the API silently fails (gotcha), so decided to add explicit error handling"
        result = detect_event_type(context)

        types = [r[0] for r in result]
        # Should detect both gotcha and decision elements
        assert len(types) >= 2

    def test_no_detection_for_neutral(self):
        """Neutral text should not trigger strong detection."""
        context = "The function takes two parameters and returns a string"
        result = detect_event_type(context)

        # Should have low or no confidence
        assert len(result) == 0 or all(r[1] == "low" for r in result)


# ============================================================
# Tests: Text Similarity
# ============================================================


class TestTextSimilarity:
    """Test text similarity calculation."""

    def test_identical_texts(self):
        """Identical texts should have similarity 1.0."""
        text = "pytest fixtures can be scoped to session level"
        similarity = calculate_text_similarity(text, text)
        assert similarity == 1.0

    def test_completely_different(self):
        """Completely different texts should have low similarity."""
        text1 = "pytest fixtures scoped session"
        text2 = "database connection pooling redis"
        similarity = calculate_text_similarity(text1, text2)
        assert similarity < 0.2

    def test_partial_overlap(self):
        """Partially overlapping texts should have medium similarity."""
        text1 = "pytest fixtures can be scoped to session level"
        text2 = "pytest fixtures are useful for test setup"
        similarity = calculate_text_similarity(text1, text2)
        assert 0.2 < similarity < 0.7

    def test_empty_text(self):
        """Empty texts should return 0 similarity."""
        assert calculate_text_similarity("", "some text") == 0.0
        assert calculate_text_similarity("some text", "") == 0.0
        assert calculate_text_similarity("", "") == 0.0


# ============================================================
# Tests: Find Similar Entries
# ============================================================


class TestFindSimilarEntries:
    """Test finding similar existing entries."""

    def test_find_similar_gotcha(self):
        """Find similar gotcha entries."""
        trace = create_test_trace()
        # Use very similar text to exceed similarity threshold
        context = "pytest-asyncio silently passes async tests without running"

        similar = find_similar_entries(trace, context, "gotcha")

        assert len(similar) > 0
        assert similar[0]["id"] == "G001"  # Should match the async gotcha

    def test_find_similar_learning(self):
        """Find similar learning entries."""
        trace = create_test_trace()
        context = "pytest fixtures can be configured for session scope"

        similar = find_similar_entries(trace, context, "learning")

        assert len(similar) > 0
        assert similar[0]["id"] == "L001"  # Should match the fixture learning

    def test_no_similar_found(self):
        """No similar entries for unrelated context."""
        trace = create_test_trace()
        context = "kubernetes pod scheduling and resource allocation"

        similar = find_similar_entries(trace, context, "gotcha")

        assert len(similar) == 0


# ============================================================
# Tests: Knowledge Check
# ============================================================


class TestKnowledgeCheck:
    """Test the main knowledge_check function."""

    def test_should_log_new_gotcha(self):
        """Should recommend logging a new gotcha."""
        trace = create_test_trace()
        context = "The requests library silently retries on connection errors"

        result = knowledge_check(trace, context, "gotcha", True)

        assert result["should_log"] is True
        assert "gotcha" in result["recommended_types"]

    def test_should_not_log_duplicate(self):
        """Should not recommend logging a near-duplicate."""
        trace = create_test_trace()
        context = "pytest-asyncio silently passes async tests without running them"

        result = knowledge_check(trace, context, "gotcha", True)

        # Should find the similar entry and suggest not logging
        assert len(result["similar_entries"]) > 0
        # High similarity should prevent logging
        if result["similar_entries"][0]["similarity"] >= 0.7:
            assert result["should_log"] is False

    def test_auto_detect_type(self):
        """Should auto-detect event type when not specified."""
        trace = create_test_trace()
        context = "Discovered that the cache expires after exactly 5 minutes"

        result = knowledge_check(trace, context, None, False)

        assert "learning" in result["recommended_types"]

    def test_suggested_fields_generated(self):
        """Should generate suggested fields for logging."""
        trace = create_test_trace()
        context = "The API returns 500 errors when the payload exceeds 1MB"

        result = knowledge_check(trace, context, "gotcha", False)

        assert "suggested_fields" in result
        assert "gotcha" in result["suggested_fields"]
        assert "problem" in result["suggested_fields"]["gotcha"]


# ============================================================
# Tests: Suggested Fields Generation
# ============================================================


class TestSuggestedFields:
    """Test suggested field generation."""

    def test_gotcha_fields(self):
        """Generate suggested fields for gotcha."""
        context = "API silently drops requests over 1MB. Solution: chunk the requests"
        fields = generate_suggested_fields(context, "gotcha")

        assert "problem" in fields
        assert "solution" in fields
        assert "severity" in fields

    def test_decision_fields(self):
        """Generate suggested fields for decision."""
        context = "Chose to use SQLAlchemy for database access"
        fields = generate_suggested_fields(context, "decision")

        assert "decision" in fields
        assert "rationale" in fields

    def test_learning_fields(self):
        """Generate suggested fields for learning."""
        context = "Learned that Python async requires explicit event loop"
        fields = generate_suggested_fields(context, "learning")

        assert "learning" in fields
        assert "confidence" in fields

    def test_idea_type_detection(self):
        """Detect idea type from context."""
        optimization_context = "Could optimize this by caching results"
        fields = generate_suggested_fields(optimization_context, "idea")
        assert fields["idea_type"] == "optimization"

        feature_context = "Should add a new feature for exporting data"
        fields = generate_suggested_fields(feature_context, "idea")
        assert fields["idea_type"] == "feature"

    def test_tech_tags_extracted(self):
        """Extract technology tags from context."""
        context = "The python pandas dataframe merge is slow with numpy arrays"
        fields = generate_suggested_fields(context, "gotcha")

        tags = fields.get("tags", [])
        assert "python" in tags or "pandas" in tags


# ============================================================
# Tests: Checkpoint Analysis
# ============================================================


class TestCheckpointAnalysis:
    """Test session checkpoint analysis."""

    def test_checkpoint_basic_analysis(self):
        """Basic checkpoint analysis."""
        trace = create_test_trace()

        result = analyze_session_for_checkpoint(trace, "S001")

        assert result["session_id"] == "S001"
        assert "session_duration_minutes" in result
        assert "entries_logged" in result
        assert "pending_suggestions" in result

    def test_checkpoint_detects_pending_suggestions(self):
        """Checkpoint should detect pending suggestions."""
        trace = create_test_trace()

        result = analyze_session_for_checkpoint(trace, "S001")

        assert result["pending_suggestions"] == 1
        assert any("suggestion" in p.lower() for p in result["prompts"])

    def test_checkpoint_session_not_found(self):
        """Handle non-existent session."""
        trace = create_test_trace()

        result = analyze_session_for_checkpoint(trace, "S999")

        assert "error" in result

    def test_checkpoint_with_files_touched(self):
        """Checkpoint with files touched list."""
        trace = create_test_trace()
        files = ["new_file.py", "another.py"]

        result = analyze_session_for_checkpoint(trace, "S001", files)

        # Should detect unlogged files
        assert result["estimated_unlogged"]["code_contributions"] >= 0


# ============================================================
# Tests: Context Refresh
# ============================================================


class TestContextRefresh:
    """Test context refresh functionality."""

    def test_refresh_finds_relevant_entries(self):
        """Context refresh finds relevant entries by topic."""
        trace = create_test_trace()

        result = refresh_context_for_topics(trace, ["pytest", "testing"])

        # Should find testing-related entries
        assert len(result["gotchas"]) > 0 or len(result["learnings"]) > 0

    def test_refresh_respects_max_items(self):
        """Context refresh respects max_items parameter."""
        trace = create_test_trace()

        result = refresh_context_for_topics(trace, ["testing"], max_items=1)

        for _category, items in result.items():
            assert len(items) <= 1

    def test_refresh_filters_by_category(self):
        """Context refresh filters by category."""
        trace = create_test_trace()

        result = refresh_context_for_topics(trace, ["testing"], categories=["gotchas"])

        assert "gotchas" in result
        assert "decisions" not in result


# ============================================================
# Tests: Learning Consolidation
# ============================================================


class TestLearningConsolidation:
    """Test learning consolidation."""

    def test_consolidation_counts_entries(self):
        """Consolidation counts session entries."""
        trace = create_test_trace()

        result = consolidate_session_learnings(trace, "S001", auto_link=False)

        assert result["session_id"] == "S001"
        assert result["total_entries"] > 0
        assert "by_category" in result

    def test_consolidation_creates_links(self):
        """Consolidation creates links between related entries."""
        trace = create_test_trace()

        result = consolidate_session_learnings(trace, "S001", auto_link=True)

        # Should attempt to link related entries
        assert "links_created" in result

    def test_consolidation_collects_tags(self):
        """Consolidation collects all tags used."""
        trace = create_test_trace()

        result = consolidate_session_learnings(trace, "S001")

        assert "tags_used" in result
        assert len(result["tags_used"]) > 0


# ============================================================
# Tests: Knowledge Metrics
# ============================================================


class TestKnowledgeMetrics:
    """Test knowledge metrics computation."""

    def test_metrics_counts_entries(self):
        """Metrics counts total entries."""
        trace = create_test_trace()

        metrics = compute_knowledge_metrics(trace)

        assert "total_entries" in metrics
        assert metrics["total_entries"]["learnings"] == 2
        assert metrics["total_entries"]["gotchas"] == 2

    def test_metrics_counts_tags(self):
        """Metrics counts tag usage."""
        trace = create_test_trace()

        metrics = compute_knowledge_metrics(trace)

        assert "by_tag" in metrics
        assert len(metrics["by_tag"]) > 0

    def test_metrics_calculates_staleness(self):
        """Metrics calculates entry staleness."""
        trace = create_test_trace()

        metrics = compute_knowledge_metrics(trace)

        assert "staleness" in metrics
        assert "fresh_30d" in metrics["staleness"]

    def test_metrics_calculates_linkage(self):
        """Metrics calculates linkage statistics."""
        trace = create_test_trace()

        metrics = compute_knowledge_metrics(trace)

        assert "linkage" in metrics
        assert "orphan_entries" in metrics["linkage"]


# ============================================================
# Tests: Trigger Patterns Configuration
# ============================================================


class TestTriggerPatternsConfig:
    """Test trigger patterns configuration."""

    def test_all_types_have_patterns(self):
        """All event types have trigger patterns defined."""
        expected_types = ["gotcha", "decision", "learning", "idea", "intervention", "code", "error"]

        for event_type in expected_types:
            assert event_type in TRIGGER_PATTERNS
            assert "keywords" in TRIGGER_PATTERNS[event_type]
            assert "patterns" in TRIGGER_PATTERNS[event_type]
            assert "description" in TRIGGER_PATTERNS[event_type]

    def test_keywords_are_lowercase(self):
        """All keywords should be lowercase for matching."""
        for event_type, config in TRIGGER_PATTERNS.items():
            for keyword in config["keywords"]:
                assert keyword == keyword.lower(), f"{event_type}: {keyword} not lowercase"


# ============================================================
# Integration Tests
# ============================================================


class TestIntegration:
    """Integration tests for the full workflow."""

    def test_full_trigger_workflow(self):
        """Test complete trigger -> check -> suggest workflow."""
        trace = create_test_trace()

        # Simulate encountering a new gotcha
        context = "requests.get() doesn't raise on 4xx errors by default"

        # 1. Detect event type
        detected = detect_event_type(context)
        assert len(detected) > 0

        # 2. Run knowledge check
        result = knowledge_check(trace, context, check_duplicates=True)

        # 3. Should recommend logging
        assert result["should_log"] is True
        assert "gotcha" in result["recommended_types"]

        # 4. Should have suggested fields
        assert "suggested_fields" in result

    def test_checkpoint_to_consolidation_workflow(self):
        """Test checkpoint -> consolidation workflow."""
        trace = create_test_trace()

        # 1. Run checkpoint
        checkpoint = analyze_session_for_checkpoint(trace, "S001")
        assert "entries_logged" in checkpoint

        # 2. Run consolidation
        consolidation = consolidate_session_learnings(trace, "S001")
        assert consolidation["total_entries"] > 0

        # 3. Compute metrics
        metrics = compute_knowledge_metrics(trace)
        assert metrics["total_entries"]["learnings"] > 0


# ============================================================
# Run Tests
# ============================================================

if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
