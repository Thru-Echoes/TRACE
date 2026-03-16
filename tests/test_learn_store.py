"""Standalone tests for trace-learn store (store.py).

Tests: CRUD operations, persistence, atomic writes, error handling,
timestamp updates, strict mode.
"""

from __future__ import annotations

import json
import os

import pytest

from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.extensions.learn.store import (
    StoreLoadError,
    add_learning,
    list_learnings,
    load_store,
    remove_learning,
    save_store,
)


class TestLoadStore:
    """Tests for load_store()."""

    def test_nonexistent_returns_empty(self, tmp_path):
        ks = load_store("nonexistent", directory=str(tmp_path))
        assert ks.project == "nonexistent"
        assert ks.learnings == []
        assert ks.version == "0.2"

    def test_roundtrip(self, tmp_path):
        ks = KnowledgeStore(project="roundtrip")
        add_learning(ks, content="persisted insight", tags=["test"])
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("roundtrip", directory=str(tmp_path))
        assert len(loaded.learnings) == 1
        assert loaded.learnings[0].content == "persisted insight"
        assert loaded.learnings[0].tags == ["test"]

    def test_corrupt_json_returns_empty(self, tmp_path):
        """Corrupt file → fresh store (default lenient mode)."""
        path = tmp_path / "corrupt.json"
        path.write_text("{invalid json!!!", encoding="utf-8")
        ks = load_store("corrupt", directory=str(tmp_path))
        assert ks.project == "corrupt"
        assert ks.learnings == []

    def test_corrupt_json_strict_raises(self, tmp_path):
        """Corrupt file → StoreLoadError in strict mode."""
        path = tmp_path / "corrupt.json"
        path.write_text("{invalid json!!!", encoding="utf-8")
        with pytest.raises(StoreLoadError, match="Corrupt JSON"):
            load_store("corrupt", directory=str(tmp_path), strict=True)

    def test_invalid_schema_returns_empty(self, tmp_path):
        """Valid JSON but wrong schema → fresh store."""
        path = tmp_path / "bad_schema.json"
        path.write_text('{"not_a_valid_field": 42}', encoding="utf-8")
        ks = load_store("bad_schema", directory=str(tmp_path))
        assert ks.learnings == []

    def test_invalid_schema_strict_raises(self, tmp_path):
        path = tmp_path / "bad_schema.json"
        path.write_text('{"not_a_valid_field": 42}', encoding="utf-8")
        with pytest.raises(StoreLoadError, match="Failed to validate"):
            load_store("bad_schema", directory=str(tmp_path), strict=True)


class TestSaveStore:
    """Tests for save_store()."""

    def test_creates_directory(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        ks = KnowledgeStore(project="test")
        save_store(ks, directory=str(nested))
        assert (nested / "test.json").exists()

    def test_updates_timestamp(self, tmp_path):
        ks = KnowledgeStore(project="test")
        old_updated = ks.updated
        save_store(ks, directory=str(tmp_path))
        assert ks.updated >= old_updated

    def test_atomic_write_leaves_no_temp_files(self, tmp_path):
        """After a successful save, no .tmp files remain."""
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="test")
        save_store(ks, directory=str(tmp_path))
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_file_is_valid_json(self, tmp_path):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="check json validity")
        path = save_store(ks, directory=str(tmp_path))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["project"] == "test"
        assert len(data["learnings"]) == 1

    def test_pretty_printed(self, tmp_path):
        """Output is indented for human readability."""
        ks = KnowledgeStore(project="test")
        path = save_store(ks, directory=str(tmp_path))
        content = path.read_text(encoding="utf-8")
        assert "\n" in content
        assert "  " in content  # indent=2

    def test_overwrite_preserves_data(self, tmp_path):
        """Saving twice overwrites cleanly."""
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="first")
        save_store(ks, directory=str(tmp_path))

        add_learning(ks, content="second")
        save_store(ks, directory=str(tmp_path))

        loaded = load_store("test", directory=str(tmp_path))
        assert len(loaded.learnings) == 2


class TestAddLearning:
    """Tests for add_learning()."""

    def test_basic_add(self):
        ks = KnowledgeStore(project="test")
        lrn = add_learning(ks, content="new insight", category="gotcha", tags=["data"])
        assert lrn.id == "lrn_001"
        assert lrn.content == "new insight"
        assert lrn.category == "gotcha"
        assert len(ks.learnings) == 1

    def test_sequential_ids(self):
        ks = KnowledgeStore(project="test")
        lrn1 = add_learning(ks, content="first")
        lrn2 = add_learning(ks, content="second")
        assert lrn1.id == "lrn_001"
        assert lrn2.id == "lrn_002"

    def test_with_corrects_event_ids(self):
        ks = KnowledgeStore(project="test")
        lrn = add_learning(
            ks,
            content="correction",
            category="correction",
            corrects_event_ids=["evt_001", "evt_002"],
        )
        assert lrn.corrects_event_ids == ["evt_001", "evt_002"]

    def test_with_source_info(self):
        ks = KnowledgeStore(project="test")
        lrn = add_learning(
            ks,
            content="from session",
            source_session="sess_001",
            source_event="evt_001",
        )
        assert lrn.source_session == "sess_001"
        assert lrn.source_event == "evt_001"


class TestRemoveLearning:
    """Tests for remove_learning()."""

    def test_remove_existing(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="to remove")
        assert remove_learning(ks, "lrn_001") is True
        assert len(ks.learnings) == 0

    def test_remove_nonexistent(self):
        ks = KnowledgeStore(project="test")
        assert remove_learning(ks, "lrn_999") is False

    def test_remove_preserves_others(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="keep")
        add_learning(ks, content="remove")
        add_learning(ks, content="keep too")
        remove_learning(ks, "lrn_002")
        assert len(ks.learnings) == 2
        ids = [lrn.id for lrn in ks.learnings]
        assert "lrn_001" in ids
        assert "lrn_003" in ids


class TestListLearnings:
    """Tests for list_learnings()."""

    def test_list_all(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="a", category="learning")
        add_learning(ks, content="b", category="correction")
        add_learning(ks, content="c", category="gotcha")
        results = list_learnings(ks)
        assert len(results) == 3

    def test_filter_by_category(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="a", category="learning")
        add_learning(ks, content="b", category="correction")
        add_learning(ks, content="c", category="correction")
        results = list_learnings(ks, category="correction")
        assert len(results) == 2
        assert all(r["category"] == "correction" for r in results)

    def test_returns_dicts(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="test")
        results = list_learnings(ks)
        assert isinstance(results[0], dict)
        assert "content" in results[0]
        assert "id" in results[0]

    def test_empty_store(self):
        ks = KnowledgeStore(project="test")
        assert list_learnings(ks) == []

    def test_nonexistent_category_returns_empty(self):
        ks = KnowledgeStore(project="test")
        add_learning(ks, content="a", category="learning")
        assert list_learnings(ks, category="correction") == []


class TestEnvironmentVariable:
    """Tests for TRACE_KNOWLEDGE_DIR env var override."""

    def test_env_var_override(self, tmp_path, monkeypatch):
        custom_dir = tmp_path / "custom"
        monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(custom_dir))
        ks = KnowledgeStore(project="env_test")
        add_learning(ks, content="env override")
        # Pass directory=None so it reads from env var
        save_store(ks, directory=None)
        assert (custom_dir / "env_test.json").exists()
        loaded = load_store("env_test", directory=None)
        assert len(loaded.learnings) == 1
