"""
Tests for TRACE V&V Snapshot System
"""

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "mcp_server"))

from vv.snapshots import SnapshotManager


@pytest.fixture
def temp_project():
    """Create a temporary project directory with test files."""
    temp_dir = Path(tempfile.mkdtemp())

    # Create project structure
    trace_dir = temp_dir / ".trace"
    trace_dir.mkdir()

    # Create some test files
    src_dir = temp_dir / "src"
    src_dir.mkdir()

    test_file = src_dir / "main.py"
    test_file.write_text("def hello():\n    print('Hello, world!')\n\nhello()\n")

    docs_dir = temp_dir / "docs"
    docs_dir.mkdir()

    doc_file = docs_dir / "readme.md"
    doc_file.write_text("# Project\n\nThis is a test project.\n\n## Usage\n\nRun the main script.\n")

    yield temp_dir

    # Cleanup
    shutil.rmtree(temp_dir)


@pytest.fixture
def snapshot_manager(temp_project):
    """Create a SnapshotManager instance."""
    return SnapshotManager(temp_project / ".trace")


class TestSnapshotCreation:
    """Tests for snapshot creation."""

    def test_create_single_file_snapshot(self, snapshot_manager, temp_project):
        """Test creating a snapshot of a single file."""
        test_file = temp_project / "src" / "main.py"

        result = snapshot_manager.create_snapshot(files=[str(test_file)], trigger="manual")

        assert "snapshot_id" in result
        assert result["snapshot_id"].startswith("SNAP-")
        assert len(result["files"]) == 1
        assert result["trigger"] == "manual"
        assert result["files"][0]["exists"] is True
        assert result["files"][0]["hash"].startswith("sha256:")

    def test_create_multi_file_snapshot(self, snapshot_manager, temp_project):
        """Test creating a snapshot of multiple files."""
        files = [str(temp_project / "src" / "main.py"), str(temp_project / "docs" / "readme.md")]

        result = snapshot_manager.create_snapshot(files=files, trigger="session_start")

        assert len(result["files"]) == 2
        assert all(f["exists"] for f in result["files"])

    def test_snapshot_with_session_id(self, snapshot_manager, temp_project):
        """Test creating a snapshot with session ID."""
        test_file = temp_project / "src" / "main.py"

        result = snapshot_manager.create_snapshot(files=[str(test_file)], trigger="pre_contribution", session_id="S001")

        assert result["session_id"] == "S001"

    def test_snapshot_nonexistent_file(self, snapshot_manager, temp_project):
        """Test snapshotting a file that doesn't exist."""
        nonexistent = temp_project / "missing.py"

        result = snapshot_manager.create_snapshot(files=[str(nonexistent)], trigger="manual")

        assert len(result["files"]) == 1
        assert result["files"][0]["exists"] is False
        assert "FILE_NOT_FOUND" in result["files"][0]["hash"]

    def test_snapshot_contains_git_state(self, snapshot_manager, temp_project):
        """Test that snapshot includes git state."""
        test_file = temp_project / "src" / "main.py"

        result = snapshot_manager.create_snapshot(files=[str(test_file)], trigger="manual")

        assert "git_state" in result
        assert "timestamp" in result["git_state"]


class TestSnapshotRetrieval:
    """Tests for snapshot retrieval."""

    def test_get_snapshot(self, snapshot_manager, temp_project):
        """Test retrieving a snapshot by ID."""
        test_file = temp_project / "src" / "main.py"

        created = snapshot_manager.create_snapshot(files=[str(test_file)], trigger="manual")

        retrieved = snapshot_manager.get_snapshot(created["snapshot_id"])

        assert retrieved is not None
        assert retrieved["snapshot_id"] == created["snapshot_id"]

    def test_get_nonexistent_snapshot(self, snapshot_manager):
        """Test retrieving a snapshot that doesn't exist."""
        result = snapshot_manager.get_snapshot("SNAP-999")
        assert result is None

    def test_get_file_content(self, snapshot_manager, temp_project):
        """Test retrieving file content from a snapshot."""
        test_file = temp_project / "src" / "main.py"
        original_content = test_file.read_bytes()

        created = snapshot_manager.create_snapshot(files=[str(test_file)], trigger="manual")

        # Modify the file
        test_file.write_text("# Modified content\n")

        # Retrieve original content from snapshot
        retrieved_content = snapshot_manager.get_snapshot_file_content(created["snapshot_id"], str(test_file))

        assert retrieved_content == original_content


class TestSnapshotListing:
    """Tests for listing snapshots."""

    def test_list_snapshots(self, snapshot_manager, temp_project):
        """Test listing all snapshots."""
        test_file = temp_project / "src" / "main.py"

        # Create multiple snapshots
        for trigger in ["manual", "session_start", "pre_contribution"]:
            snapshot_manager.create_snapshot(files=[str(test_file)], trigger=trigger)

        snapshots = snapshot_manager.list_snapshots()

        assert len(snapshots) == 3

    def test_list_snapshots_by_trigger(self, snapshot_manager, temp_project):
        """Test filtering snapshots by trigger."""
        test_file = temp_project / "src" / "main.py"

        # Create snapshots with different triggers
        snapshot_manager.create_snapshot(files=[str(test_file)], trigger="manual")
        snapshot_manager.create_snapshot(files=[str(test_file)], trigger="manual")
        snapshot_manager.create_snapshot(files=[str(test_file)], trigger="session_start")

        manual_snapshots = snapshot_manager.list_snapshots(trigger="manual")

        assert len(manual_snapshots) == 2

    def test_list_snapshots_by_session(self, snapshot_manager, temp_project):
        """Test filtering snapshots by session ID."""
        test_file = temp_project / "src" / "main.py"

        snapshot_manager.create_snapshot(files=[str(test_file)], trigger="manual", session_id="S001")
        snapshot_manager.create_snapshot(files=[str(test_file)], trigger="manual", session_id="S002")

        s001_snapshots = snapshot_manager.list_snapshots(session_id="S001")

        assert len(s001_snapshots) == 1

    def test_list_snapshots_limit(self, snapshot_manager, temp_project):
        """Test limiting snapshot list results."""
        test_file = temp_project / "src" / "main.py"

        # Create many snapshots
        for _ in range(10):
            snapshot_manager.create_snapshot(files=[str(test_file)], trigger="manual")

        limited = snapshot_manager.list_snapshots(limit=5)

        assert len(limited) == 5


class TestSnapshotCleanup:
    """Tests for snapshot cleanup."""

    def test_cleanup_old_snapshots(self, snapshot_manager, temp_project):
        """Test cleanup of old snapshots."""
        test_file = temp_project / "src" / "main.py"

        # Create several snapshots
        for _ in range(5):
            snapshot_manager.create_snapshot(files=[str(test_file)], trigger="manual")

        result = snapshot_manager.cleanup_old_snapshots(max_count=3)

        assert result["deleted_count"] == 2
        assert result["kept_count"] == 3

        # Verify only 3 remain
        remaining = snapshot_manager.list_snapshots()
        assert len(remaining) == 3


class TestSnapshotCompression:
    """Tests for snapshot compression."""

    def test_compression_ratio(self, snapshot_manager, temp_project):
        """Test that files are compressed."""
        # Create a file with repetitive content (compresses well)
        test_file = temp_project / "repetitive.txt"
        test_file.write_text("hello " * 1000)

        result = snapshot_manager.create_snapshot(files=[str(test_file)], trigger="manual")

        file_info = result["files"][0]
        assert file_info["compressed_size"] < file_info["size_bytes"]
        assert file_info["compression_ratio"] < 1.0
