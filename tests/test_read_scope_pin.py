"""A pinned server scopes its project-keyed read tools (INV-16).

These run the real tool functions against a temporary store holding sessions from
two projects.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import trace_mcp
from trace_mcp import project_identity as pident
from trace_mcp import server
from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.storage.json_file import JsonFileStorage

OURS = "trace_20260101_aaa111"
THEIRS = "trace_20260101_bbb222"


@pytest.fixture(autouse=True)
async def _two_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.delenv("TRACE_PROJECT", raising=False)
    monkeypatch.delenv("TRACE_ALLOW_CROSS_PROJECT_READS", raising=False)
    pident._reset_registry_cache()
    storage = JsonFileStorage(str(tmp_path / "sessions"))
    monkeypatch.setattr(server, "storage", storage)
    await storage.create_session(Session(id=OURS, metadata=SessionMetadata(project="collab-a")))
    await storage.create_session(Session(id=THEIRS, metadata=SessionMetadata(project="collab-b")))


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_PROJECT", "collab-a")


def _listed_ids(output: str) -> set[str]:
    return {s["id"] for s in json.loads(output)}


@pytest.mark.usefixtures("pinned")
class TestPinned:
    async def test_listing_with_no_project_shows_only_the_pinned_project(self) -> None:
        assert _listed_ids(await server.trace_list_sessions()) == {OURS}

    async def test_listing_the_pinned_project_works(self) -> None:
        assert _listed_ids(await server.trace_list_sessions(project="collab-a")) == {OURS}

    async def test_listing_another_project_is_refused(self) -> None:
        output = await server.trace_list_sessions(project="collab-b")
        assert "Error" in output and "pinned" in output and THEIRS not in output

    async def test_summary_of_another_project_is_refused(self) -> None:
        output = await server.trace_project_summary(project="collab-b")
        assert "Error" in output and "pinned" in output

    async def test_summary_of_the_pinned_project_works(self) -> None:
        assert "Error" not in await server.trace_project_summary(project="collab-a")

    async def test_health_of_another_project_is_refused(self) -> None:
        output = await server.trace_health_check(project="collab-b")
        assert "Error" in output and "pinned" in output

    async def test_health_with_no_project_counts_only_the_pinned_project(self) -> None:
        assert json.loads(await server.trace_health_check())["session_count"] == 1

    async def test_health_of_another_projects_session_is_refused(self) -> None:
        output = await server.trace_health_check(session_id=THEIRS)
        assert "Error" in output and "pinned" in output

    async def test_health_of_the_pinned_projects_session_works(self) -> None:
        assert json.loads(await server.trace_health_check(session_id=OURS))["session_count"] == 1

    async def test_health_of_a_missing_session_is_not_an_error(self) -> None:
        assert json.loads(await server.trace_health_check(session_id="trace_20260101_gone00"))["session_count"] == 0

    async def test_an_empty_project_means_the_pin(self) -> None:
        assert _listed_ids(await server.trace_list_sessions(project="")) == {OURS}

    async def test_a_corrupt_registry_still_gets_a_health_report(self, tmp_path: Path) -> None:
        (tmp_path / "projects.json").write_text("{not json")
        pident._reset_registry_cache()
        report = json.loads(await server.trace_health_check())
        assert report["identity"]["registry"] != "ok"
        assert report["session_count"] == 0

    async def test_the_override_restores_the_cross_project_view(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACE_ALLOW_CROSS_PROJECT_READS", "1")
        assert _listed_ids(await server.trace_list_sessions()) == {OURS, THEIRS}
        assert _listed_ids(await server.trace_list_sessions(project="collab-b")) == {THEIRS}
        assert json.loads(await server.trace_health_check(session_id=THEIRS))["session_count"] == 1


class TestUnpinned:
    async def test_listing_is_unchanged(self) -> None:
        assert _listed_ids(await server.trace_list_sessions()) == {OURS, THEIRS}

    async def test_health_is_unchanged(self) -> None:
        assert json.loads(await server.trace_health_check())["session_count"] == 2


async def test_health_reports_which_build_is_running() -> None:
    from trace_mcp.conformance.staleness import get_running_build_digest

    report = json.loads(await server.trace_health_check())
    assert report["build_digest"] == get_running_build_digest()
    assert report["version"] == trace_mcp.__version__
