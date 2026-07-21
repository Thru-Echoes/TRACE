"""Tests for the TRACE_PROJECT process pin and its fail-closed boundaries (ADR-006 S3).

Exercises the pin-guarded enforcement in ``server.py`` — none of which the
pre-existing suite covers, because no other test sets ``TRACE_PROJECT``:
pin-aware ``trace_start_session``, cross-project read hard-deny (+ escape hatch),
pointer-capture denial in the logging path, the reserved-key usage ban, the
auto-quarantine extraction gate, and ``TRACE_REQUIRE_PIN``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import trace_mcp.project_identity as pident
from trace_mcp import server
from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.storage.json_file import JsonFileStorage


@pytest.fixture(autouse=True)
def _isolated_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh per-test storage + registry, clean pin env, reset server globals."""
    monkeypatch.setattr(server, "storage", JsonFileStorage(directory=str(tmp_path / "sessions")))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    for var in ("TRACE_PROJECT", "TRACE_DEFAULT_PROJECT", "TRACE_REQUIRE_PIN", "TRACE_ALLOW_CROSS_PROJECT_READS"):
        monkeypatch.delenv(var, raising=False)
    server._current_session_id = None
    server.active_sessions.clear()
    server._unpinned_warned = False
    pident._reset_registry_cache()


def _enroll(*keys: str) -> None:
    with pident.locked_registry() as reg:
        for key in keys:
            reg.projects[key] = pident.ProjectEntry(key=key, display_label=key)
    pident._reset_registry_cache()


async def _make_foreign_session(sid: str, project: str) -> None:
    await server.storage.create_session(Session(id=sid, metadata=SessionMetadata(project=project)))


# ── pin-aware start ────────────────────────────────────────────────────────


async def test_pinned_start_defaults_to_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    _enroll("waggle")
    monkeypatch.setenv("TRACE_PROJECT", "waggle")
    result = await server.trace_start_session()
    assert "Error" not in result
    assert server._current_session_id is not None
    session = await server.storage.get_session(server._current_session_id)
    assert session.metadata.project == "waggle"


async def test_pinned_start_foreign_label_errors_naming_both(monkeypatch: pytest.MonkeyPatch) -> None:
    _enroll("waggle")
    monkeypatch.setenv("TRACE_PROJECT", "waggle")
    result = await server.trace_start_session(project="chemmasters")
    assert "Error" in result and "waggle" in result and "chemmasters" in result


async def test_unpinned_start_requires_project() -> None:
    result = await server.trace_start_session()
    assert "Error" in result and "not pinned" in result


async def test_unpinned_start_rejects_reserved_key() -> None:
    result = await server.trace_start_session(project="auto")
    assert "Error" in result and "reserved" in result


async def test_require_pin_fails_closed_when_unpinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_REQUIRE_PIN", "1")
    result = await server.trace_start_session(project="anything")
    assert "Error" in result and "TRACE_REQUIRE_PIN" in result


# ── cross-project read scope ───────────────────────────────────────────────


async def test_cross_project_read_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    _enroll("waggle", "chemmasters")
    await _make_foreign_session("trace_20260720_r1", "chemmasters")
    monkeypatch.setenv("TRACE_PROJECT", "waggle")
    result = await server.trace_get_session("trace_20260720_r1")
    assert "Error" in result and "denied" in result and "waggle" in result


async def test_cross_project_read_escape_hatch_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    _enroll("waggle", "chemmasters")
    await _make_foreign_session("trace_20260720_r2", "chemmasters")
    monkeypatch.setenv("TRACE_PROJECT", "waggle")
    monkeypatch.setenv("TRACE_ALLOW_CROSS_PROJECT_READS", "1")
    result = await server.trace_get_session("trace_20260720_r2")
    assert "Error" not in result


async def test_same_project_read_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _enroll("waggle")
    await _make_foreign_session("trace_20260720_r3", "Waggle")  # case variant of the pin
    monkeypatch.setenv("TRACE_PROJECT", "waggle")
    result = await server.trace_get_session("trace_20260720_r3")
    assert "Error" not in result


# ── pointer capture (logging path) ─────────────────────────────────────────


async def test_pointer_capture_denied_in_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    _enroll("waggle", "chemmasters")
    await _make_foreign_session("trace_20260720_p1", "chemmasters")
    monkeypatch.setenv("TRACE_PROJECT", "waggle")
    result = await server.trace_log_annotation(category="observation", content="x", session_id="trace_20260720_p1")
    assert "Error" in result and "another project" in result
    # The foreign session must NOT have become the current pointer.
    assert server._current_session_id is None


# ── auto-quarantine extraction gate ────────────────────────────────────────


async def test_end_auto_session_skips_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[tuple[str, str]] = []

    async def _spy_extract(project: str, session_id: str):  # noqa: ANN202 - test stub
        called.append((project, session_id))

        class _R:
            error = None
            new_ids: list[str] = []

        return _R()

    monkeypatch.setattr(server.hooks, "extract_if_available", _spy_extract)
    # An unpinned auto session (project == "auto").
    await server.storage.create_session(Session(id="trace_20260720_a1", metadata=SessionMetadata(project="auto")))
    result = await server.trace_end_session("trace_20260720_a1", summary="done", write_scratchpad=False)
    assert "Error" not in result
    assert called == []  # extraction gated off for the reserved quarantine pool


async def test_end_real_session_still_extracts(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[tuple[str, str]] = []

    async def _spy_extract(project: str, session_id: str):  # noqa: ANN202 - test stub
        called.append((project, session_id))

        class _R:
            error = None
            new_ids: list[str] = []

        return _R()

    monkeypatch.setattr(server.hooks, "extract_if_available", _spy_extract)
    await server.storage.create_session(Session(id="trace_20260720_a2", metadata=SessionMetadata(project="waggle")))
    result = await server.trace_end_session("trace_20260720_a2", summary="done", write_scratchpad=False)
    assert "Error" not in result
    assert called == [("waggle", "trace_20260720_a2")]
