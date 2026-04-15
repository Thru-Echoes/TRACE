"""Installation health tests for TRACE MCP server.

These tests verify that the TRACE package is correctly installed, importable,
and that the uvx-based launch mechanism works. They catch configuration drift
across consumer projects.

Launch mechanism (uvx):
    All .mcp.json files use `uvx --from <TRACE_ROOT> --refresh-package trace-mcp trace-mcp`.
    This builds a wheel from source into an isolated environment managed by uvx,
    avoiding the recurring .venv/.pth breakage from Homebrew Python upgrades.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

# The TRACE project root — adjust if tests are run from a different location
TRACE_ROOT = Path(__file__).parent.parent


# ── Package Import Tests ─────────────────────────────────────────────────────


class TestPackageImport:
    """Verify trace_mcp can be imported and has expected attributes."""

    def test_import_trace_mcp(self) -> None:
        """The trace_mcp package should be importable."""
        import trace_mcp

        assert hasattr(trace_mcp, "__version__")

    def test_version_is_string(self) -> None:
        import trace_mcp

        assert isinstance(trace_mcp.__version__, str)
        parts = trace_mcp.__version__.split(".")
        assert len(parts) >= 2, f"Version '{trace_mcp.__version__}' doesn't look like semver"

    def test_import_server_module(self) -> None:
        """The server module should import without errors."""
        from trace_mcp import server

        assert hasattr(server, "main")
        assert hasattr(server, "mcp")

    def test_import_schema(self) -> None:
        """Schema module should export all required models."""
        from trace_mcp.schema import (  # noqa: F811
            Actor,  # noqa: F401
            AnnotationData,  # noqa: F401
            ContributionData,  # noqa: F401
            DecisionData,  # noqa: F401
            Session,
            SessionMetadata,  # noqa: F401
            TraceEvent,
        )

        assert hasattr(Session, "model_validate")
        assert hasattr(TraceEvent, "model_validate")

    def test_import_storage(self) -> None:
        from trace_mcp.storage.json_file import JsonFileStorage

        assert hasattr(JsonFileStorage, "create_session")
        assert hasattr(JsonFileStorage, "get_session")
        assert hasattr(JsonFileStorage, "update_session")
        assert hasattr(JsonFileStorage, "list_sessions")
        assert hasattr(JsonFileStorage, "delete_session")

    def test_import_tools(self) -> None:
        from trace_mcp.tools import (
            decision_tools,
            export_tools,
            logging_tools,
            query_tools,
            session_tools,
        )

        assert hasattr(session_tools, "start_session")
        assert hasattr(session_tools, "end_session")
        assert hasattr(decision_tools, "propose_decision")
        assert hasattr(decision_tools, "resolve_decision")
        assert hasattr(logging_tools, "log_tool_call")
        assert hasattr(logging_tools, "log_annotation")
        assert hasattr(logging_tools, "log_contribution")
        assert hasattr(logging_tools, "log_state_change")
        assert hasattr(query_tools, "get_decisions")
        assert hasattr(query_tools, "search_events")
        assert hasattr(export_tools, "export_session")

    def test_import_extensions(self) -> None:
        """Extension packages should be importable."""
        import trace_mcp.extensions.learn

        assert hasattr(trace_mcp.extensions.learn, "register")

    def test_import_exporters(self) -> None:
        """Exporter modules should be importable."""
        from trace_mcp.exporters import markdown_export

        assert hasattr(markdown_export, "export_markdown")


# ── MCP Configuration Tests ─────────────────────────────────────────────────


class TestMCPConfiguration:
    """Verify .mcp.json files are correctly configured for uvx."""

    def test_trace_mcp_json_exists(self) -> None:
        mcp_json = TRACE_ROOT / ".mcp.json"
        assert mcp_json.exists(), f".mcp.json not found at {mcp_json}"

    def test_trace_mcp_json_valid(self) -> None:
        """The .mcp.json should be valid JSON with expected structure."""
        mcp_json = TRACE_ROOT / ".mcp.json"
        if not mcp_json.exists():
            pytest.skip(".mcp.json does not exist")
        data = json.loads(mcp_json.read_text())
        assert "mcpServers" in data, ".mcp.json missing 'mcpServers' key"
        assert "trace" in data["mcpServers"], ".mcp.json missing 'trace' server"
        trace_config = data["mcpServers"]["trace"]
        assert "command" in trace_config, "trace server config missing 'command'"
        assert "args" in trace_config, "trace server config missing 'args'"

    def test_mcp_json_uses_uvx(self) -> None:
        """The .mcp.json should use uvx (not uv run)."""
        mcp_json = TRACE_ROOT / ".mcp.json"
        if not mcp_json.exists():
            pytest.skip(".mcp.json does not exist")
        data = json.loads(mcp_json.read_text())
        trace_config = data["mcpServers"]["trace"]
        assert trace_config["command"] == "uvx", (
            f"Expected command 'uvx', got '{trace_config['command']}'. "
            "All .mcp.json files should use uvx for reliability."
        )
        args = trace_config["args"]
        assert "--from" in args, "uvx args should include --from"
        assert "--refresh-package" in args, "uvx args should include --refresh-package"

    def test_mcp_json_command_resolves(self) -> None:
        """The uvx command should be available on PATH."""
        result = subprocess.run(
            ["which", "uvx"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, "uvx not found in PATH. Install uv: https://docs.astral.sh/uv/"

    def test_no_legacy_bin_directory(self) -> None:
        """The legacy bin/ launcher should not exist (replaced by uvx)."""
        legacy_bin = TRACE_ROOT / "bin" / "trace-mcp-server"
        assert not legacy_bin.exists(), (
            f"Legacy launcher {legacy_bin} still exists. "
            "Remove it — all projects now use uvx."
        )


# ── Dependency Tests ─────────────────────────────────────────────────────────


class TestDependencies:
    """Verify that required dependencies are available."""

    def test_mcp_importable(self) -> None:
        import mcp

        assert hasattr(mcp, "server")

    def test_pydantic_version(self) -> None:
        import pydantic

        major = int(pydantic.__version__.split(".")[0])
        assert major >= 2, f"TRACE requires pydantic >= 2.0, found {pydantic.__version__}"

    def test_fastmcp_importable(self) -> None:
        from mcp.server.fastmcp import FastMCP

        assert FastMCP is not None


# ── pyproject.toml Consistency Tests ─────────────────────────────────────────


class TestPyprojectConsistency:
    """Verify pyproject.toml is consistent with the installed package."""

    def test_pyproject_exists(self) -> None:
        assert (TRACE_ROOT / "pyproject.toml").exists()

    def test_version_matches_pyproject(self) -> None:
        import trace_mcp

        pyproject = (TRACE_ROOT / "pyproject.toml").read_text()
        for line in pyproject.split("\n"):
            if line.strip().startswith("version"):
                pyproject_version = line.split('"')[1]
                break
        else:
            pytest.fail("Could not find version in pyproject.toml")

        assert trace_mcp.__version__ == pyproject_version, (
            f"Installed version {trace_mcp.__version__} != "
            f"pyproject.toml version {pyproject_version}"
        )

    def test_entry_points_in_pyproject(self) -> None:
        pyproject = (TRACE_ROOT / "pyproject.toml").read_text()
        assert "trace-mcp" in pyproject
        assert "trace_mcp.server:main" in pyproject


# ── Consumer Project Tests ───────────────────────────────────────────────────


class TestConsumerProjects:
    """Verify that consumer projects referencing TRACE use the current uvx config.

    These tests check `.mcp.json` files in user-specified consumer projects to
    verify they point at TRACE with the expected `uvx` command pattern. They
    are skipped by default and run only when the `TRACE_CONSUMER_PROJECTS`
    environment variable is set to a colon-separated list of project paths,
    e.g.:

        TRACE_CONSUMER_PROJECTS=/path/to/proj-a:/path/to/proj-b pytest ...

    This keeps the test suite agnostic to any individual user's local project
    layout while still allowing project owners to verify their consumer
    configurations against the current TRACE recommendation.
    """

    @staticmethod
    def _consumer_projects() -> list[Path]:
        env_value = os.environ.get("TRACE_CONSUMER_PROJECTS", "").strip()
        if not env_value:
            return []
        return [Path(p) for p in env_value.split(os.pathsep) if p.strip()]

    def test_consumer_mcp_json_uses_uvx(self) -> None:
        """Each consumer .mcp.json should use uvx, not legacy uv run or bin/."""
        projects = self._consumer_projects()
        if not projects:
            pytest.skip(
                "TRACE_CONSUMER_PROJECTS env var not set; "
                "set it to a colon-separated list of project paths to run this check."
            )

        failures: list[str] = []
        for project_dir in projects:
            mcp_json = project_dir / ".mcp.json"
            if not mcp_json.exists():
                failures.append(f"{project_dir}: no .mcp.json present")
                continue

            try:
                data = json.loads(mcp_json.read_text())
            except json.JSONDecodeError as exc:
                failures.append(f"{project_dir}/.mcp.json: invalid JSON: {exc}")
                continue

            servers = data.get("mcpServers", {})
            if "trace" not in servers:
                failures.append(f"{project_dir}/.mcp.json: no 'trace' server configured")
                continue

            trace_config = servers["trace"]
            if trace_config.get("command") != "uvx":
                failures.append(
                    f"{project_dir}/.mcp.json: command is "
                    f"'{trace_config.get('command')}', expected 'uvx'"
                )
                continue

            args = trace_config.get("args", [])
            if "--from" not in args:
                failures.append(f"{project_dir}/.mcp.json: missing '--from' in uvx args")
            if "trace-mcp" not in args:
                failures.append(f"{project_dir}/.mcp.json: missing 'trace-mcp' in uvx args")

        if failures:
            pytest.fail(
                "Consumer project configuration check failed:\n  - "
                + "\n  - ".join(failures)
            )
