"""
Tests for TRACE V&V Integrity Chain
"""

import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "mcp_server"))

from vv.integrity import IntegrityChain


@pytest.fixture
def temp_trace_dir():
    """Create a temporary .trace directory."""
    temp_dir = Path(tempfile.mkdtemp())
    trace_dir = temp_dir / ".trace"
    trace_dir.mkdir()

    yield trace_dir

    shutil.rmtree(temp_dir)


@pytest.fixture
def integrity_chain(temp_trace_dir):
    """Create an IntegrityChain instance."""
    return IntegrityChain(temp_trace_dir)


@pytest.fixture
def sample_entry():
    """Create a sample entry for testing."""
    return {
        "id": "CC001",
        "timestamp": datetime.now().isoformat(),
        "file_path": "src/main.py",
        "content_type": "code",
        "description": "Initial implementation",
    }


class TestHashComputation:
    """Tests for hash computation."""

    def test_compute_hash(self, integrity_chain):
        """Test basic hash computation."""
        hash_result = integrity_chain._compute_hash("test data")

        assert hash_result.startswith("sha256:")
        assert len(hash_result) == 71  # sha256: + 64 hex chars

    def test_hash_deterministic(self, integrity_chain):
        """Test that hash computation is deterministic."""
        hash1 = integrity_chain._compute_hash("test data")
        hash2 = integrity_chain._compute_hash("test data")

        assert hash1 == hash2

    def test_hash_different_for_different_data(self, integrity_chain):
        """Test that different data produces different hashes."""
        hash1 = integrity_chain._compute_hash("data 1")
        hash2 = integrity_chain._compute_hash("data 2")

        assert hash1 != hash2


class TestEntryHashing:
    """Tests for entry hash computation."""

    def test_compute_entry_hash(self, integrity_chain, sample_entry):
        """Test computing hash for an entry."""
        previous_hash = IntegrityChain.GENESIS_HASH

        entry_hash = integrity_chain.compute_entry_hash(sample_entry, previous_hash)

        assert entry_hash.startswith("sha256:")

    def test_entry_hash_includes_previous(self, integrity_chain, sample_entry):
        """Test that entry hash depends on previous hash."""
        hash1 = integrity_chain.compute_entry_hash(sample_entry, "sha256:aaaa")
        hash2 = integrity_chain.compute_entry_hash(sample_entry, "sha256:bbbb")

        assert hash1 != hash2

    def test_entry_hash_ignores_integrity_field(self, integrity_chain, sample_entry):
        """Test that existing integrity field is ignored in hash."""
        sample_entry["integrity"] = {"entry_hash": "old_hash"}

        hash_result = integrity_chain.compute_entry_hash(sample_entry, IntegrityChain.GENESIS_HASH)

        # Should produce same hash as entry without integrity
        entry_without_integrity = {k: v for k, v in sample_entry.items() if k != "integrity"}
        hash_without = integrity_chain.compute_entry_hash(entry_without_integrity, IntegrityChain.GENESIS_HASH)

        assert hash_result == hash_without


class TestChainOperations:
    """Tests for chain operations."""

    def test_add_first_entry(self, integrity_chain, sample_entry):
        """Test adding first entry to chain."""
        result = integrity_chain.add_entry("CC001", "code_contribution", sample_entry)

        assert "entry_hash" in result
        assert result["previous_hash"] == IntegrityChain.GENESIS_HASH
        assert result["chain_position"] == 0

    def test_add_multiple_entries(self, integrity_chain):
        """Test adding multiple entries forms a chain."""
        entries = [
            {"id": "CC001", "data": "first"},
            {"id": "CC002", "data": "second"},
            {"id": "CC003", "data": "third"},
        ]

        results = []
        for entry in entries:
            result = integrity_chain.add_entry(entry["id"], "code_contribution", entry)
            results.append(result)

        # Check chain linkage
        assert results[0]["previous_hash"] == IntegrityChain.GENESIS_HASH
        assert results[1]["previous_hash"] == results[0]["entry_hash"]
        assert results[2]["previous_hash"] == results[1]["entry_hash"]

        # Check positions
        assert results[0]["chain_position"] == 0
        assert results[1]["chain_position"] == 1
        assert results[2]["chain_position"] == 2

    def test_chain_persists(self, temp_trace_dir):
        """Test that chain is persisted and can be reloaded."""
        # Add entries with first instance
        chain1 = IntegrityChain(temp_trace_dir)
        chain1.add_entry("CC001", "code_contribution", {"id": "CC001"})

        # Create new instance and verify chain loaded
        chain2 = IntegrityChain(temp_trace_dir)

        assert chain2.chain["current_position"] == 1
        assert len(chain2.chain["entries"]) == 1


class TestChainVerification:
    """Tests for chain verification."""

    def test_verify_empty_chain(self, integrity_chain):
        """Test verifying an empty chain."""
        result = integrity_chain.verify_chain()

        assert result["verified"] is True
        assert result["chain_length"] == 0

    def test_verify_valid_chain(self, integrity_chain):
        """Test verifying a valid chain."""
        # Add some entries
        for i in range(3):
            integrity_chain.add_entry(f"CC00{i}", "code_contribution", {"id": f"CC00{i}"})

        result = integrity_chain.verify_chain()

        assert result["verified"] is True
        assert result["chain_length"] == 3
        assert len(result["errors"]) == 0

    def test_verify_detects_tampering(self, integrity_chain):
        """Test that verification detects chain tampering."""
        # Add entries
        integrity_chain.add_entry("CC001", "code_contribution", {"id": "CC001"})
        integrity_chain.add_entry("CC002", "code_contribution", {"id": "CC002"})

        # Tamper with chain
        integrity_chain.chain["entries"][0]["entry_hash"] = "sha256:tampered"

        result = integrity_chain.verify_chain()

        assert result["verified"] is False
        assert len(result["errors"]) > 0

    def test_verify_entry(self, integrity_chain, sample_entry):
        """Test verifying a single entry."""
        integrity_metadata = integrity_chain.add_entry("CC001", "code_contribution", sample_entry)
        sample_entry["integrity"] = integrity_metadata

        result = integrity_chain.verify_entry(sample_entry, "CC001")

        assert result["verified"] is True
        assert result["hash_matches"] is True

    def test_verify_entry_missing_integrity(self, integrity_chain, sample_entry):
        """Test verifying entry without integrity metadata."""
        result = integrity_chain.verify_entry(sample_entry, "CC001")

        assert result["verified"] is False
        assert "No integrity metadata" in result["error"]


class TestChainSummary:
    """Tests for chain summary."""

    def test_get_chain_summary(self, integrity_chain):
        """Test getting chain summary."""
        integrity_chain.add_entry("CC001", "code_contribution", {"id": "CC001"})
        integrity_chain.add_entry("SUG001", "ai_suggestion", {"id": "SUG001"})

        summary = integrity_chain.get_chain_summary()

        assert summary["chain_length"] == 2
        assert summary["entry_types"]["code_contribution"] == 1
        assert summary["entry_types"]["ai_suggestion"] == 1


class TestChainRebuild:
    """Tests for chain rebuilding."""

    def test_rebuild_chain(self, integrity_chain):
        """Test rebuilding chain from TRACE data."""
        trace = {
            "code_contributions": [
                {"id": "CC001", "timestamp": "2024-01-01T10:00:00"},
                {"id": "CC002", "timestamp": "2024-01-01T11:00:00"},
            ],
            "ai_suggestions": [{"id": "SUG001", "timestamp": "2024-01-01T10:30:00"}],
            "decisions": [],
            "learnings": [],
            "gotchas": [],
            "ideas": [],
            "errors": [],
            "interventions": [],
        }

        result = integrity_chain.rebuild_chain(trace)

        assert result["new_chain_length"] == 3
        assert result["entries_added"] == 3

        # Verify order (by timestamp)
        entries = integrity_chain.chain["entries"]
        assert entries[0]["entry_id"] == "CC001"
        assert entries[1]["entry_id"] == "SUG001"  # Between CC001 and CC002
        assert entries[2]["entry_id"] == "CC002"


class TestEntryLookup:
    """Tests for entry lookup."""

    def test_find_entry_by_id(self, integrity_chain):
        """Test finding entry in chain by ID."""
        integrity_chain.add_entry("CC001", "code_contribution", {"id": "CC001"})
        integrity_chain.add_entry("CC002", "code_contribution", {"id": "CC002"})

        found = integrity_chain.find_entry_by_id("CC001")

        assert found is not None
        assert found["entry_id"] == "CC001"

    def test_find_nonexistent_entry(self, integrity_chain):
        """Test finding entry that doesn't exist."""
        found = integrity_chain.find_entry_by_id("CC999")

        assert found is None

    def test_get_entries_after(self, integrity_chain):
        """Test getting entries after a position."""
        for i in range(5):
            integrity_chain.add_entry(f"CC00{i}", "code_contribution", {"id": f"CC00{i}"})

        entries = integrity_chain.get_entries_after(2)

        assert len(entries) == 2
        assert entries[0]["position"] == 3
        assert entries[1]["position"] == 4
