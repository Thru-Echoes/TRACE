"""Tests for TRACE JSON file storage backend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trace_mcp.schema import Actor, AnnotationData, Session, SessionMetadata, TraceEvent
from trace_mcp.storage.json_file import JsonFileStorage, sanitize_name


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(directory=str(tmp_path))


@pytest.fixture
def sample_session() -> Session:
    return Session(
        id="trace_20260205_abc123",
        metadata=SessionMetadata(project="test-project"),
    )


# ── Create ───────────────────────────────────────────────────────────────────


class TestCreateSession:
    async def test_creates_file(self, storage: JsonFileStorage, sample_session: Session, tmp_path: Path) -> None:
        await storage.create_session(sample_session)
        path = tmp_path / "trace_20260205_abc123.json"
        assert path.exists()

    async def test_file_is_valid_json(self, storage: JsonFileStorage, sample_session: Session, tmp_path: Path) -> None:
        await storage.create_session(sample_session)
        path = tmp_path / "trace_20260205_abc123.json"
        data = json.loads(path.read_text())
        assert data["id"] == "trace_20260205_abc123"
        assert data["metadata"]["project"] == "test-project"

    async def test_returns_session_id(self, storage: JsonFileStorage, sample_session: Session) -> None:
        sid = await storage.create_session(sample_session)
        assert sid == "trace_20260205_abc123"


# ── Update ───────────────────────────────────────────────────────────────────


class TestUpdateSession:
    async def test_update_adds_events(self, storage: JsonFileStorage, sample_session: Session, tmp_path: Path) -> None:
        await storage.create_session(sample_session)
        sample_session.events.append(
            TraceEvent(
                id="evt_001",
                session_id=sample_session.id,
                type="annotation",
                actor=Actor(type="ai", id="claude"),
                annotation=AnnotationData(category="observation", content="test note"),
            )
        )
        await storage.update_session(sample_session)
        path = tmp_path / "trace_20260205_abc123.json"
        data = json.loads(path.read_text())
        assert len(data["events"]) == 1
        assert data["events"][0]["id"] == "evt_001"

    async def test_update_nonexistent_raises(self, storage: JsonFileStorage, sample_session: Session) -> None:
        with pytest.raises(FileNotFoundError):
            await storage.update_session(sample_session)


# ── Get ──────────────────────────────────────────────────────────────────────


class TestGetSession:
    async def test_roundtrip(self, storage: JsonFileStorage, sample_session: Session) -> None:
        await storage.create_session(sample_session)
        loaded = await storage.get_session("trace_20260205_abc123")
        assert loaded.id == sample_session.id
        assert loaded.metadata.project == "test-project"

    async def test_get_nonexistent_raises(self, storage: JsonFileStorage) -> None:
        with pytest.raises(FileNotFoundError):
            await storage.get_session("nonexistent")

    async def test_events_in_order(self, storage: JsonFileStorage, sample_session: Session) -> None:
        for i in range(5):
            sample_session.events.append(
                TraceEvent(
                    id=f"evt_{i + 1:03d}",
                    session_id=sample_session.id,
                    type="annotation",
                    actor=Actor(type="ai", id="claude"),
                    annotation=AnnotationData(category="observation", content=f"note {i + 1}"),
                )
            )
        await storage.create_session(sample_session)
        loaded = await storage.get_session(sample_session.id)
        ids = [e.id for e in loaded.events]
        assert ids == ["evt_001", "evt_002", "evt_003", "evt_004", "evt_005"]


# ── List ─────────────────────────────────────────────────────────────────────


class TestListSessions:
    async def test_list_empty(self, storage: JsonFileStorage) -> None:
        result = await storage.list_sessions()
        assert result == []

    async def test_list_returns_summaries(self, storage: JsonFileStorage) -> None:
        for i in range(3):
            s = Session(
                id=f"trace_20260205_{i:06x}",
                metadata=SessionMetadata(project=f"project-{i}"),
            )
            await storage.create_session(s)
        result = await storage.list_sessions()
        assert len(result) == 3
        assert all("id" in r for r in result)
        assert all("project" in r for r in result)

    async def test_list_filter_by_project(self, storage: JsonFileStorage) -> None:
        await storage.create_session(
            Session(
                id="trace_20260205_aaa000",
                metadata=SessionMetadata(project="climate-analysis"),
            )
        )
        await storage.create_session(
            Session(
                id="trace_20260205_bbb000",
                metadata=SessionMetadata(project="materials-science"),
            )
        )
        # INV-4: project filtering is EXACT (case-sensitive), matching the adapter
        # hooks — a substring like "climate" must NOT merge in "climate-analysis".
        assert await storage.list_sessions(project="climate") == []
        exact = await storage.list_sessions(project="climate-analysis")
        assert len(exact) == 1
        assert exact[0]["project"] == "climate-analysis"

    async def test_list_limit(self, storage: JsonFileStorage) -> None:
        for i in range(5):
            await storage.create_session(
                Session(
                    id=f"trace_20260205_{i:06x}",
                    metadata=SessionMetadata(project="test"),
                )
            )
        result = await storage.list_sessions(limit=2)
        assert len(result) == 2


# ── Delete ───────────────────────────────────────────────────────────────────


class TestDeleteSession:
    async def test_delete_removes_file(self, storage: JsonFileStorage, sample_session: Session, tmp_path: Path) -> None:
        await storage.create_session(sample_session)
        path = tmp_path / "trace_20260205_abc123.json"
        assert path.exists()
        await storage.delete_session("trace_20260205_abc123")
        assert not path.exists()

    async def test_delete_nonexistent_raises(self, storage: JsonFileStorage) -> None:
        with pytest.raises(FileNotFoundError):
            await storage.delete_session("nonexistent")


# ── Path Sanitization (Phase 1b) ────────────────────────────────────────


class TestSanitizeName:
    def test_safe_passthrough(self) -> None:
        assert sanitize_name("trace_20260205_abc123") == "trace_20260205_abc123"

    def test_strips_path_separators(self) -> None:
        result = sanitize_name("../../etc/passwd")
        assert "/" not in result
        assert ".." not in result

    def test_strips_leading_dots(self) -> None:
        assert sanitize_name(".hidden") == "hidden"

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty string"):
            sanitize_name("////")

    def test_preserves_hyphens_and_dots(self) -> None:
        assert sanitize_name("my-project.v2") == "my-project.v2"

    def test_replaces_spaces(self) -> None:
        assert sanitize_name("my project") == "my_project"


class TestSessionPathSanitized:
    async def test_session_path_doesnt_escape(self, storage: JsonFileStorage, tmp_path: Path) -> None:
        """Session path with traversal attempt stays in directory."""
        path = storage._session_path("../../etc/passwd")
        assert str(tmp_path) in str(path)
        assert ".." not in path.name


class TestAtomicWrite:
    async def test_atomic_write_creates_file(self, storage: JsonFileStorage, tmp_path: Path) -> None:
        """_write_file creates a file with correct content without fcntl."""
        test_path = tmp_path / "test_atomic.json"
        storage._write_file(test_path, '{"test": true}')
        assert test_path.exists()
        assert test_path.read_text() == '{"test": true}'


class TestCorruptSessionJson:
    async def test_corrupt_json_raises(self, storage: JsonFileStorage, tmp_path: Path) -> None:
        """Corrupt JSON in session file raises on get_session."""
        path = tmp_path / "trace_20260205_abc123.json"
        path.write_text("{invalid json!!!", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            await storage.get_session("trace_20260205_abc123")

    async def test_truncated_json_raises(self, storage: JsonFileStorage, tmp_path: Path) -> None:
        """Truncated JSON in session file raises on get_session."""
        path = tmp_path / "trace_20260205_abc123.json"
        path.write_text('{"id": "trace_20260205_abc', encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            await storage.get_session("trace_20260205_abc123")
