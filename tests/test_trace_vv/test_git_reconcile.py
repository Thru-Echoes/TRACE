"""
Tests for TRACE V&V Git Reconciliation
"""

import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "mcp_server"))

from vv.git_reconcile import GitReconciler


@pytest.fixture
def git_project():
    """Create a temporary git repository with some commits."""
    temp_dir = Path(tempfile.mkdtemp())

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=temp_dir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=temp_dir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=temp_dir, capture_output=True)

    # Create initial commit
    readme = temp_dir / "README.md"
    readme.write_text("# Test Project\n")
    subprocess.run(["git", "add", "README.md"], cwd=temp_dir, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=temp_dir, capture_output=True)

    # Create a file and commit
    main_py = temp_dir / "main.py"
    main_py.write_text("print('hello')\n")
    subprocess.run(["git", "add", "main.py"], cwd=temp_dir, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add main.py"], cwd=temp_dir, capture_output=True)

    # Create a human-edit commit
    main_py.write_text("print('hello world')\n")
    subprocess.run(["git", "add", "main.py"], cwd=temp_dir, capture_output=True)
    subprocess.run(["git", "commit", "-m", "[HUMAN-EDIT] Fixed greeting"], cwd=temp_dir, capture_output=True)

    yield temp_dir

    shutil.rmtree(temp_dir)


@pytest.fixture
def non_git_project():
    """Create a temporary directory that is not a git repo."""
    temp_dir = Path(tempfile.mkdtemp())

    # Create a file but don't init git
    readme = temp_dir / "README.md"
    readme.write_text("# Test\n")

    yield temp_dir

    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_trace():
    """Create sample TRACE data."""
    return {
        "schema_version": "TRACE-2.0",
        "code_contributions": [],
        "ai_suggestions": [],
        "human_manual_edits": [],
        "sessions": [{"id": "S001", "started": datetime.now().isoformat()}],
    }


class TestGitDetection:
    """Tests for git repository detection."""

    def test_detect_git_repo(self, git_project):
        """Test detecting a valid git repository."""
        reconciler = GitReconciler(git_project)
        assert reconciler._is_git_repo() is True

    def test_detect_non_git(self, non_git_project):
        """Test detecting a non-git directory."""
        reconciler = GitReconciler(non_git_project)
        assert reconciler._is_git_repo() is False


class TestGitLogParsing:
    """Tests for git log parsing."""

    def test_parse_git_log(self, git_project):
        """Test parsing git log output."""
        reconciler = GitReconciler(git_project)
        commits = reconciler._parse_git_log("1 month ago")

        assert len(commits) >= 2  # At least initial and main.py commits
        assert all("hash" in c for c in commits)
        assert all("message" in c for c in commits)

    def test_detect_human_edit_tag(self, git_project):
        """Test detecting [HUMAN-EDIT] tagged commits."""
        reconciler = GitReconciler(git_project)
        commits = reconciler._parse_git_log("1 month ago")

        human_edit_commits = [c for c in commits if c["has_human_edit_tag"]]

        assert len(human_edit_commits) >= 1
        assert "[HUMAN-EDIT]" in human_edit_commits[0]["message"]


class TestReconciliation:
    """Tests for TRACE-git reconciliation."""

    def test_reconcile_non_git_repo(self, non_git_project, sample_trace):
        """Test reconciliation on non-git directory."""
        reconciler = GitReconciler(non_git_project)
        result = reconciler.reconcile(sample_trace)

        assert result.get("error") is not None
        assert result["is_git_repo"] is False

    def test_reconcile_empty_trace(self, git_project, sample_trace):
        """Test reconciliation with empty TRACE data."""
        reconciler = GitReconciler(git_project)
        result = reconciler.reconcile(sample_trace, since="1 month ago")

        assert result["is_git_repo"] is True
        assert "summary" in result
        assert result["summary"]["total_commits"] >= 2

    def test_reconcile_finds_human_edits(self, git_project, sample_trace):
        """Test that reconciliation finds human edit commits."""
        reconciler = GitReconciler(git_project)
        result = reconciler.reconcile(sample_trace, since="1 month ago")

        assert result["summary"]["human_edit_commits"] >= 1
        assert len(result["human_edit_commits"]) >= 1

    def test_reconcile_coverage_calculation(self, git_project, sample_trace):
        """Test coverage percentage calculation."""
        reconciler = GitReconciler(git_project)
        result = reconciler.reconcile(sample_trace, since="1 month ago")

        # With empty TRACE, coverage should be low (only README might be excluded)
        assert "coverage_percent" in result["summary"]

    def test_reconcile_with_tracked_commit(self, git_project, sample_trace):
        """Test reconciliation with a tracked commit."""
        reconciler = GitReconciler(git_project)

        # First, get a commit hash
        commits = reconciler._parse_git_log("1 month ago")
        if commits:
            sample_trace["code_contributions"].append(
                {"id": "CC001", "git_commit": commits[0]["hash"], "file_path": "main.py"}
            )

        result = reconciler.reconcile(sample_trace, since="1 month ago")

        assert result["summary"]["tracked_commits"] >= 1


class TestFileHistory:
    """Tests for file history retrieval."""

    def test_get_file_history(self, git_project):
        """Test getting history for a specific file."""
        reconciler = GitReconciler(git_project)
        history = reconciler.get_file_history("main.py", since="1 month ago")

        assert len(history) >= 2  # Creation and human-edit
        assert all("hash" in h for h in history)


class TestUntaggedHumanEdits:
    """Tests for detecting untagged human edits."""

    def test_detect_potential_human_edits(self, git_project, sample_trace):
        """Test detection of potential untagged human edits."""
        # Add a small commit without HUMAN-EDIT tag
        small_file = git_project / "fix.py"
        small_file.write_text("x = 1\n")
        subprocess.run(["git", "add", "fix.py"], cwd=git_project, capture_output=True)
        subprocess.run(["git", "commit", "-m", "typo fix"], cwd=git_project, capture_output=True)

        reconciler = GitReconciler(git_project)
        potential = reconciler.detect_untagged_human_edits(sample_trace, since="1 month ago")

        # Should detect the small "typo fix" commit as potential human edit
        typo_commits = [p for p in potential if "typo" in p.get("message", "").lower()]
        assert len(typo_commits) >= 1 or len(potential) >= 1


class TestAutoLogSuggestions:
    """Tests for auto-logging suggestions."""

    def test_reconcile_with_auto_log(self, git_project, sample_trace):
        """Test reconciliation with auto_log_missing enabled."""
        reconciler = GitReconciler(git_project)
        result = reconciler.reconcile(sample_trace, since="1 month ago", auto_log_missing=True)

        # Should generate suggestions for unlogged commits
        if result["summary"]["unlogged_commits"] > 0:
            assert len(result["suggestions"]) > 0
            assert "type" in result["suggestions"][0]

    def test_suggestion_format(self, git_project, sample_trace):
        """Test format of auto-log suggestions."""
        reconciler = GitReconciler(git_project)
        result = reconciler.reconcile(sample_trace, since="1 month ago", auto_log_missing=True)

        for suggestion in result.get("suggestions", []):
            assert "commit_hash" in suggestion
            assert "files" in suggestion
            assert "type" in suggestion
