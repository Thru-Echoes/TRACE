"""The session-start notice for a server older than the source it builds from.

A merge is not a deploy: an MCP server keeps the build it started with for the
life of the host session. Every session recorded between 2026-09-03 and
2026-09-06 carried a wire version three days stale for exactly this reason, and
nothing said so — the version stamp simply read low.

What makes it worth a check rather than a docs note is the silence. MCP argument
models discard unknown fields, so calling a tool with an argument only a newer
build knows drops it without an error: the event is written without it and reads
exactly like one where the caller chose not to supply it.

These tests pin both halves: that a real drift is reported with both numbers and
a remedy, and — the half that decides whether anyone keeps the check — that every
configuration it cannot evaluate stays silent instead of crying wolf.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trace_mcp import __version__
from trace_mcp.conformance.staleness import get_stale_build_notice
from trace_mcp.schema import SCHEMA_VERSION


def _write_source(root: Path, *, package: str, schema: str) -> Path:
    """A minimal source tree carrying the two version declarations."""
    source = root / "source"
    (source / "src" / "trace_mcp" / "schema").mkdir(parents=True, exist_ok=True)
    (source / "pyproject.toml").write_text(f'[project]\nname = "trace-mcp"\nversion = "{package}"\n', encoding="utf-8")
    (source / "src" / "trace_mcp" / "schema" / "session.py").write_text(
        f'SCHEMA_VERSION = "{schema}"\n', encoding="utf-8"
    )
    return source


def _write_config(project: Path, args: list[str], *, server_key: str = "trace") -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {server_key: {"command": "uvx", "args": args}}}), encoding="utf-8"
    )


def _launch_args(source: Path) -> list[str]:
    return ["--from", str(source), "--refresh-package", "trace-mcp", "trace-mcp"]


class TestDriftIsReported:
    def test_a_stale_wire_version_is_named(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, package=__version__, schema="9.9.9")
        project = tmp_path / "project"
        _write_config(project, _launch_args(source))

        notice = get_stale_build_notice(project)

        assert notice is not None
        assert SCHEMA_VERSION in notice and "9.9.9" in notice
        assert "Restart" in notice

    def test_a_stale_package_version_is_named(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, package="9.9.9", schema=SCHEMA_VERSION)
        project = tmp_path / "project"
        _write_config(project, _launch_args(source))

        notice = get_stale_build_notice(project)

        assert notice is not None
        assert __version__ in notice and "9.9.9" in notice

    def test_both_drifts_are_reported_together(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, package="9.9.9", schema="8.8.8")
        project = tmp_path / "project"
        _write_config(project, _launch_args(source))

        notice = get_stale_build_notice(project)

        assert notice is not None
        assert "9.9.9" in notice and "8.8.8" in notice

    def test_the_notice_explains_the_silent_failure(self, tmp_path: Path) -> None:
        """A reader must learn *why* it matters, not only that it happened."""
        source = _write_source(tmp_path, package=__version__, schema="9.9.9")
        project = tmp_path / "project"
        _write_config(project, _launch_args(source))

        notice = get_stale_build_notice(project)

        assert notice is not None
        assert "dropped silently" in notice


class TestSilenceWhenItCannotJudge:
    """A check that cries wolf gets ignored, so every unevaluable case is quiet."""

    def test_matching_versions_produce_no_notice(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, package=__version__, schema=SCHEMA_VERSION)
        project = tmp_path / "project"
        _write_config(project, _launch_args(source))

        assert get_stale_build_notice(project) is None

    def test_no_config_at_all(self, tmp_path: Path) -> None:
        assert get_stale_build_notice(tmp_path) is None

    def test_config_without_a_trace_server(self, tmp_path: Path) -> None:
        source = _write_source(tmp_path, package="9.9.9", schema="8.8.8")
        project = tmp_path / "project"
        _write_config(project, _launch_args(source), server_key="something-else")

        assert get_stale_build_notice(project) is None

    def test_unparseable_config(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / ".mcp.json").write_text("{not json", encoding="utf-8")

        assert get_stale_build_notice(project) is None

    def test_a_git_ref_source_is_not_compared(self, tmp_path: Path) -> None:
        """Installing from a git ref is legitimate and has no local tree to read."""
        project = tmp_path / "project"
        _write_config(project, ["--from", "git+https://github.com/Thru-Echoes/TRACE@v0.5.1", "trace-mcp"])

        assert get_stale_build_notice(project) is None

    def test_a_source_path_that_does_not_exist(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        _write_config(project, _launch_args(tmp_path / "moved-away"))

        assert get_stale_build_notice(project) is None

    def test_no_from_argument(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        _write_config(project, ["trace-mcp"])

        assert get_stale_build_notice(project) is None

    def test_a_from_argument_with_nothing_after_it(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        _write_config(project, ["trace-mcp", "--from"])

        assert get_stale_build_notice(project) is None

    def test_a_source_tree_missing_its_version_files(self, tmp_path: Path) -> None:
        source = tmp_path / "empty-source"
        source.mkdir()
        project = tmp_path / "project"
        _write_config(project, _launch_args(source))

        assert get_stale_build_notice(project) is None

    @pytest.mark.parametrize("shape", ['{"mcpServers": []}', '{"mcpServers": {"trace": "nope"}}', "[]"])
    def test_malformed_config_shapes(self, tmp_path: Path, shape: str) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / ".mcp.json").write_text(shape, encoding="utf-8")

        assert get_stale_build_notice(project) is None


class TestItNeverRaises:
    def test_an_unreadable_project_directory_is_survivable(self, tmp_path: Path) -> None:
        """A session must never fail to start because an advisory check could not run."""
        assert get_stale_build_notice(tmp_path / "does-not-exist") is None

    def test_the_real_checkout_reports_nothing(self) -> None:
        """Positive control against this repository: no false alarm in-tree."""
        assert get_stale_build_notice(Path(__file__).resolve().parent.parent) is None


class TestItReachesTheBanner:
    async def test_the_notice_appears_in_the_session_start_banner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trace_mcp.schema import Session
        from trace_mcp.storage.json_file import JsonFileStorage
        from trace_mcp.tools import session_tools

        source = _write_source(tmp_path, package=__version__, schema="9.9.9")
        project = tmp_path / "project"
        _write_config(project, _launch_args(source))
        monkeypatch.chdir(project)

        storage = JsonFileStorage(directory=str(tmp_path / "store"))
        active: dict[str, Session] = {}
        banner = await session_tools.start_session(
            storage, active, project="staleness-test", description="banner check"
        )

        assert "older build than its source" in banner
        assert "TRACE audit logging is now active." in banner
