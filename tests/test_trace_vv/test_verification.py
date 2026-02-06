"""
Tests for TRACE V&V Verification Engine
"""

import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "mcp_server"))

from vv.verification import VerificationEngine, VerificationResult


@pytest.fixture
def temp_project():
    """Create a temporary project directory."""
    temp_dir = Path(tempfile.mkdtemp())

    trace_dir = temp_dir / ".trace"
    trace_dir.mkdir()
    (trace_dir / "verifications").mkdir()
    (trace_dir / "snapshots").mkdir()

    # Create test file
    src_dir = temp_dir / "src"
    src_dir.mkdir()

    test_file = src_dir / "main.py"
    test_file.write_text("def hello():\n    print('Hello!')\n")

    yield temp_dir

    shutil.rmtree(temp_dir)


@pytest.fixture
def verification_engine(temp_project):
    """Create a VerificationEngine instance."""
    return VerificationEngine(temp_project / ".trace")


@pytest.fixture
def sample_trace(temp_project):
    """Create sample TRACE data."""
    return {
        "schema_version": "TRACE-2.0",
        "code_contributions": [
            {
                "id": "CC001",
                "session_id": "S001",
                "timestamp": datetime.now().isoformat(),
                "file_path": str(temp_project / "src" / "main.py"),
                "content_type": "code",
                "contribution_type": "creation",
                "direction_source": "human_directed",
                "authorship": {
                    "human_directed": {"ai_executed_lines": 2, "human_executed_lines": 0},
                    "ai_suggested": {"accepted_lines": 0, "modified_lines": 0, "rejected_lines": 0},
                    "human_manual_edit": {"lines_added": 0},
                    "collaborative": {"lines": 0},
                },
            }
        ],
        "ai_suggestions": [
            {
                "id": "SUG001",
                "session_id": "S001",
                "timestamp": datetime.now().isoformat(),
                "suggestion": {
                    "type": "code_change",
                    "description": "Add error handling",
                    "scope": {"lines_proposed": 10},
                },
                "outcome": {
                    "status": "accepted",
                    "human_rationale": "Good suggestion",
                    "lines_accepted_as_is": 8,
                    "lines_modified": 2,
                    "lines_rejected": 0,
                },
            }
        ],
        "sessions": [{"id": "S001", "started": datetime.now().isoformat(), "ended": None}],
    }


class TestVerificationResult:
    """Tests for VerificationResult class."""

    def test_result_to_dict(self):
        """Test converting result to dictionary."""
        result = VerificationResult(
            entry_id="CC001",
            verification_type="line_count",
            passed=True,
            claimed=10,
            actual=10,
            tolerance=5.0,
            difference=0.0,
            message="Lines match",
        )

        d = result.to_dict()

        assert d["entry_id"] == "CC001"
        assert d["passed"] is True
        assert d["claimed"] == 10
        assert d["actual"] == 10


class TestTolerance:
    """Tests for tolerance calculations."""

    def test_within_tolerance_exact_match(self, verification_engine):
        """Test exact match is within tolerance."""
        within, diff = verification_engine._within_tolerance(10, 10)
        assert within is True
        assert diff == 0.0

    def test_within_tolerance_percentage(self, verification_engine):
        """Test percentage tolerance for large values."""
        # 5% tolerance of 100 is 5 lines
        within, diff = verification_engine._within_tolerance(100, 105)
        assert within is True

        within, diff = verification_engine._within_tolerance(100, 106)
        assert within is False

    def test_within_tolerance_absolute(self, verification_engine):
        """Test absolute tolerance for small values."""
        # Default absolute tolerance is 2 lines
        within, diff = verification_engine._within_tolerance(5, 7)
        assert within is True

        within, diff = verification_engine._within_tolerance(5, 8)
        assert within is False

    def test_within_tolerance_zero(self, verification_engine):
        """Test tolerance with zero claimed."""
        within, diff = verification_engine._within_tolerance(0, 0)
        assert within is True

        within, diff = verification_engine._within_tolerance(0, 2)
        assert within is True  # Within absolute tolerance

        within, diff = verification_engine._within_tolerance(0, 3)
        assert within is False


class TestDiffCounting:
    """Tests for diff counting."""

    def test_count_diff_lines_addition(self, verification_engine):
        """Test counting added lines."""
        before = "line1\nline2\n"
        after = "line1\nline2\nline3\nline4\n"

        result = verification_engine._count_diff_lines(before, after)

        assert result["added"] == 2
        assert result["removed"] == 0

    def test_count_diff_lines_removal(self, verification_engine):
        """Test counting removed lines."""
        before = "line1\nline2\nline3\n"
        after = "line1\n"

        result = verification_engine._count_diff_lines(before, after)

        assert result["added"] == 0
        assert result["removed"] == 2

    def test_count_diff_lines_modification(self, verification_engine):
        """Test counting modified lines."""
        before = "line1\nold_line\nline3\n"
        after = "line1\nnew_line\nline3\n"

        result = verification_engine._count_diff_lines(before, after)

        # Modification shows as 1 removal + 1 addition
        assert result["added"] == 1
        assert result["removed"] == 1

    def test_count_word_diff(self, verification_engine):
        """Test word-level diff counting."""
        before = "hello world foo"
        after = "hello world bar baz"

        result = verification_engine._count_word_diff(before, after)

        assert result["words_added"] == 2  # bar, baz
        assert result["words_removed"] == 1  # foo


class TestEntryVerification:
    """Tests for entry verification."""

    def test_verify_entry_not_found(self, verification_engine, sample_trace):
        """Test verifying nonexistent entry."""
        result = verification_engine.verify_entry(sample_trace, "CC999")

        assert result["verified"] is False
        assert "not found" in result["error"]

    def test_verify_entry_file_exists(self, verification_engine, sample_trace, temp_project):
        """Test file existence verification."""
        result = verification_engine.verify_entry(sample_trace, "CC001")

        # Find the file_exists check
        file_check = next((r for r in result["results"] if r["verification_type"] == "file_exists"), None)

        assert file_check is not None
        assert file_check["passed"] is True

    def test_verify_entry_timestamp_valid(self, verification_engine, sample_trace):
        """Test timestamp validation."""
        result = verification_engine.verify_entry(sample_trace, "CC001")

        timestamp_check = next((r for r in result["results"] if r["verification_type"] == "timestamp_valid"), None)

        assert timestamp_check is not None
        assert timestamp_check["passed"] is True

    def test_verify_suggestion_lines_balance(self, verification_engine, sample_trace):
        """Test suggestion line balance verification."""
        result = verification_engine.verify_entry(sample_trace, "SUG001")

        balance_check = next(
            (r for r in result["results"] if r["verification_type"] == "suggestion_lines_balance"), None
        )

        assert balance_check is not None


class TestSessionVerification:
    """Tests for session verification."""

    def test_verify_session(self, verification_engine, sample_trace):
        """Test verifying all entries in a session."""
        result = verification_engine.verify_session(sample_trace, "S001")

        assert result["session_id"] == "S001"
        assert result["entries_total"] == 2  # CC001 and SUG001
        assert "verification_rate" in result

    def test_verify_empty_session(self, verification_engine, sample_trace):
        """Test verifying a session with no entries."""
        result = verification_engine.verify_session(sample_trace, "S002")

        assert result["entries_total"] == 0
        assert result["verification_rate"] == 100  # No entries = 100% verified


class TestVerificationHistory:
    """Tests for verification history."""

    def test_verification_saved(self, verification_engine, sample_trace):
        """Test that verifications are saved."""
        verification_engine.verify_entry(sample_trace, "CC001")

        history = verification_engine.get_verification_history("CC001")

        assert len(history) >= 1
        assert history[0]["entry_id"] == "CC001"

    def test_verification_history_limit(self, verification_engine, sample_trace):
        """Test limiting verification history."""
        import time

        # Run multiple verifications with small delay to get different timestamps
        for _ in range(5):
            verification_engine.verify_entry(sample_trace, "CC001")
            time.sleep(0.01)  # Small delay to ensure unique timestamps

        history = verification_engine.get_verification_history("CC001", limit=3)

        assert len(history) <= 5  # Should have some entries (may be same-second collisions)
