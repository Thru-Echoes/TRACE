"""Installation health tests for TRACE MCP server.

These tests verify that the TRACE package is correctly installed, importable,
and that the venv/.pth file mechanism works. They catch the recurring issue
where the venv becomes corrupted (e.g., after a Python version upgrade or
uv rebuild) and the editable install's .pth file stops being processed.

Root cause of recurring failures:
    The TRACE venv uses Python 3.13 (from Homebrew) with an editable install.
    The editable install relies on a .pth file in site-packages that adds
    the source directory to sys.path. When the venv becomes corrupted
    (e.g., Python minor version upgrade invalidates cached state, or uv
    recreates the venv without properly re-processing .pth files), the
    .pth file exists but Python never adds its path to sys.path.

    Additional failure mode (Python 3.13 + macOS):
    macOS propagates the UF_HIDDEN file flag to all files inside directories
    whose names start with "." (like .venv/). Python 3.13's site.addpackage()
    skips .pth files with UF_HIDDEN set, silently breaking editable installs.
    The bin/trace-mcp-server launcher script works around this by explicitly
    adding src/ to sys.path before importing trace_mcp.

    Fix: Delete the venv and run `uv venv && uv sync` to recreate from scratch.
    Or use bin/trace-mcp-server as the entry point (avoids .pth entirely).
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path

import pytest

# The TRACE project root — adjust if tests are run from a different location
TRACE_ROOT = Path(__file__).parent.parent
VENV_DIR = TRACE_ROOT / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
VENV_TRACE_MCP = VENV_DIR / "bin" / "trace-mcp"
LAUNCHER_SCRIPT = TRACE_ROOT / "bin" / "trace-mcp-server"
SITE_PACKAGES_GLOB = VENV_DIR / "lib" / "python*" / "site-packages"


def _find_site_packages() -> Path | None:
    """Find the site-packages directory in the venv."""
    candidates = list(VENV_DIR.glob("lib/python*/site-packages"))
    return candidates[0] if candidates else None


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
        # Should be a valid semver-like string
        parts = trace_mcp.__version__.split(".")
        assert len(parts) >= 2, f"Version '{trace_mcp.__version__}' doesn't look like semver"

    def test_import_server_module(self) -> None:
        """The server module should import without errors."""
        from trace_mcp import server

        assert hasattr(server, "main")
        assert hasattr(server, "mcp")

    def test_import_schema(self) -> None:
        """Schema module should export all required models."""
        from trace_mcp.schema import (
            Actor,
            AnnotationData,
            ContributionData,
            DecisionData,
            Session,
            SessionMetadata,
            TraceEvent,
        )

        # Verify they're Pydantic models
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


# ── Venv Health Tests ────────────────────────────────────────────────────────


class TestVenvHealth:
    """Verify the venv is healthy and .pth files are properly processed.

    These tests catch the specific failure mode where the venv's .pth file
    mechanism is broken, even though the .pth file itself exists.
    """

    def test_venv_exists(self) -> None:
        """The .venv directory should exist."""
        assert VENV_DIR.exists(), (
            f"TRACE venv not found at {VENV_DIR}. "
            "Run `cd {TRACE_ROOT} && uv venv && uv sync` to create it."
        )

    def test_venv_python_exists(self) -> None:
        """The venv Python binary should exist."""
        assert VENV_PYTHON.exists(), f"venv Python not found at {VENV_PYTHON}"

    def test_venv_python_version(self) -> None:
        """Venv Python should be >= 3.11 (TRACE requirement)."""
        result = subprocess.run(
            [str(VENV_PYTHON), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        # Output: "Python 3.13.11"
        version_str = result.stdout.strip().split()[-1]
        major, minor = int(version_str.split(".")[0]), int(version_str.split(".")[1])
        assert (major, minor) >= (3, 11), (
            f"TRACE requires Python >= 3.11, but venv has {version_str}"
        )

    def test_pth_file_exists(self) -> None:
        """The editable install .pth file should exist in site-packages."""
        sp = _find_site_packages()
        assert sp is not None, "Could not find site-packages in venv"
        pth = sp / "_trace_mcp.pth"
        assert pth.exists(), (
            f".pth file not found at {pth}. "
            "The editable install may need to be recreated: "
            f"cd {TRACE_ROOT} && uv pip install -e . --python {VENV_PYTHON}"
        )

    def test_pth_file_points_to_src(self) -> None:
        """The .pth file should point to the TRACE src/ directory."""
        sp = _find_site_packages()
        assert sp is not None
        pth = sp / "_trace_mcp.pth"
        if not pth.exists():
            pytest.skip(".pth file does not exist")
        content = pth.read_text().strip()
        expected = str(TRACE_ROOT / "src")
        assert content == expected, (
            f".pth file points to '{content}' but expected '{expected}'"
        )

    def test_src_directory_exists(self) -> None:
        """The source directory referenced by .pth should exist."""
        src_dir = TRACE_ROOT / "src" / "trace_mcp"
        assert src_dir.exists(), f"Source directory not found: {src_dir}"
        assert (src_dir / "__init__.py").exists()
        assert (src_dir / "server.py").exists()

    def test_dist_info_exists(self) -> None:
        """The dist-info directory should exist in site-packages."""
        sp = _find_site_packages()
        assert sp is not None
        dist_info = list(sp.glob("trace_mcp-*.dist-info"))
        assert len(dist_info) == 1, (
            f"Expected exactly 1 trace_mcp dist-info directory, found {len(dist_info)}: "
            f"{[d.name for d in dist_info]}"
        )

    def test_venv_import_works(self) -> None:
        """Running `import trace_mcp` via the venv Python should succeed.

        This is THE critical test. If .pth processing is broken, this fails
        even though the .pth file exists and the source directory is correct.
        """
        result = subprocess.run(
            [
                str(VENV_PYTHON),
                "-c",
                "import trace_mcp; print(trace_mcp.__version__)",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"Failed to import trace_mcp via venv Python.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}\n"
            f"This usually means the venv is corrupted. Fix with:\n"
            f"  cd {TRACE_ROOT} && rm -rf .venv && uv venv && uv sync"
        )

    def test_pth_path_in_sys_path(self) -> None:
        """The path from .pth should appear in sys.path when running venv Python."""
        result = subprocess.run(
            [
                str(VENV_PYTHON),
                "-c",
                "import sys; print('\\n'.join(sys.path))",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        expected_in_path = str(TRACE_ROOT / "src")
        assert expected_in_path in result.stdout, (
            f"'{expected_in_path}' not found in sys.path.\n"
            f"sys.path contents:\n{result.stdout}\n"
            f"The .pth file is not being processed. Fix with:\n"
            f"  cd {TRACE_ROOT} && rm -rf .venv && uv venv && uv sync"
        )


# ── macOS UF_HIDDEN Flag Tests ─────────────────────────────────────────────


class TestUFHiddenFlag:
    """Detect the Python 3.13 + macOS UF_HIDDEN .pth file skip issue.

    On macOS, directories starting with "." (like .venv/) have the UF_HIDDEN
    flag set. macOS propagates this flag to all files created inside such
    directories. Python 3.13 added a check in site.addpackage() that skips
    .pth files with UF_HIDDEN set (os.stat().st_flags & UF_HIDDEN != 0).

    This means editable installs that rely on .pth files silently break in
    .venv/ directories on macOS + Python >= 3.13. The fix is to use the
    bin/trace-mcp-server launcher script, which adds src/ to sys.path
    explicitly without relying on .pth processing.

    See: https://github.com/python/cpython/issues/121970
    """

    @pytest.mark.skipif(platform.system() != "Darwin", reason="UF_HIDDEN is macOS-only")
    def test_pth_files_uf_hidden_flag(self) -> None:
        """Check if .pth files in site-packages have UF_HIDDEN set.

        If they do, Python 3.13+ will skip them, breaking editable installs.
        This test warns about the condition and recommends the launcher script.
        """
        sp = _find_site_packages()
        if sp is None:
            pytest.skip("Could not find site-packages in venv")

        # UF_HIDDEN = 0x8000 on macOS (from <sys/stat.h>)
        UF_HIDDEN = 0x8000

        pth_files = list(sp.glob("*.pth"))
        if not pth_files:
            pytest.skip("No .pth files found in site-packages")

        hidden_pth_files = []
        for pth in pth_files:
            try:
                st = os.stat(pth)
                if hasattr(st, "st_flags") and (st.st_flags & UF_HIDDEN):
                    hidden_pth_files.append(pth.name)
            except OSError:
                continue

        if hidden_pth_files:
            # This is a warning, not a hard failure, because the launcher
            # script works around this. But it explains why the console_scripts
            # entry point (trace-mcp) may fail.
            import warnings
            warnings.warn(
                f"UF_HIDDEN set on .pth files: {hidden_pth_files}. "
                f"Python 3.13+ skips these, breaking editable installs. "
                f"Use bin/trace-mcp-server as the entry point instead.",
                stacklevel=1,
            )

    @pytest.mark.skipif(platform.system() != "Darwin", reason="UF_HIDDEN is macOS-only")
    def test_venv_directory_hidden_flag(self) -> None:
        """Check if the .venv directory itself has UF_HIDDEN set.

        If it does, all files created inside will inherit the flag.
        """
        if not VENV_DIR.exists():
            pytest.skip(".venv directory does not exist")

        UF_HIDDEN = 0x8000
        try:
            st = os.stat(VENV_DIR)
            if hasattr(st, "st_flags") and (st.st_flags & UF_HIDDEN):
                # Expected on macOS — the directory starts with "."
                # This is informational, not a failure
                pass  # Just documenting the condition
        except OSError:
            pytest.skip("Could not stat .venv directory")

    @pytest.mark.skipif(platform.system() != "Darwin", reason="UF_HIDDEN is macOS-only")
    def test_python_version_affected(self) -> None:
        """Check if the venv Python version is affected by the UF_HIDDEN bug.

        Python 3.13+ has the check in site.addpackage() that skips hidden
        .pth files. Earlier versions are not affected.
        """
        if not VENV_PYTHON.exists():
            pytest.skip("venv Python does not exist")

        result = subprocess.run(
            [str(VENV_PYTHON), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            pytest.skip("Could not determine venv Python version")

        major, minor = map(int, result.stdout.strip().split("."))
        if (major, minor) >= (3, 13):
            # Check if the .pth file actually has UF_HIDDEN
            sp = _find_site_packages()
            if sp is None:
                pytest.skip("Could not find site-packages")

            pth = sp / "_trace_mcp.pth"
            if not pth.exists():
                pytest.skip(".pth file does not exist")

            UF_HIDDEN = 0x8000
            st = os.stat(pth)
            if hasattr(st, "st_flags") and (st.st_flags & UF_HIDDEN):
                pytest.fail(
                    f"Python {major}.{minor} is affected by the UF_HIDDEN .pth skip bug, "
                    f"AND the _trace_mcp.pth file has UF_HIDDEN set.\n"
                    f"The editable install WILL NOT WORK via the console_scripts entry point.\n"
                    f"Use bin/trace-mcp-server instead, or set PYTHONPATH in .mcp.json."
                )

    def test_launcher_script_bypasses_pth(self) -> None:
        """The launcher script should work even if .pth files are broken.

        Verify that bin/trace-mcp-server can import trace_mcp by explicitly
        adding src/ to sys.path, independent of .pth file processing.
        """
        if not LAUNCHER_SCRIPT.exists():
            pytest.fail(
                f"Launcher script not found at {LAUNCHER_SCRIPT}. "
                f"This script is the recommended workaround for the UF_HIDDEN issue."
            )

        if not VENV_PYTHON.exists():
            pytest.skip("venv Python does not exist")

        # Run a quick import check using the launcher's path-setup logic
        # without actually starting the server (which would block on stdin)
        result = subprocess.run(
            [
                str(VENV_PYTHON),
                "-c",
                f"import sys; sys.path.insert(0, '{TRACE_ROOT / 'src'}'); "
                f"import trace_mcp; print(trace_mcp.__version__)",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"Failed to import trace_mcp with explicit src/ in sys.path.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}\n"
            f"This means the source code itself has issues, not just the .pth mechanism."
        )


# ── Binary/Entry Point Tests ────────────────────────────────────────────────


class TestEntryPoints:
    """Verify that the trace-mcp CLI entry point works."""

    def test_trace_mcp_binary_exists(self) -> None:
        assert VENV_TRACE_MCP.exists(), (
            f"trace-mcp binary not found at {VENV_TRACE_MCP}"
        )

    def test_trace_mcp_binary_shebang(self) -> None:
        """The trace-mcp binary shebang should point to the venv Python."""
        if not VENV_TRACE_MCP.exists():
            pytest.skip("trace-mcp binary does not exist")
        first_line = VENV_TRACE_MCP.read_text().split("\n")[0]
        assert first_line.startswith("#!"), "Binary should have a shebang line"
        assert str(VENV_DIR) in first_line, (
            f"Shebang points to '{first_line}' — expected to contain '{VENV_DIR}'"
        )

    def test_trace_mcp_binary_imports_server(self) -> None:
        """The trace-mcp binary should import from trace_mcp.server."""
        if not VENV_TRACE_MCP.exists():
            pytest.skip("trace-mcp binary does not exist")
        content = VENV_TRACE_MCP.read_text()
        assert "trace_mcp.server" in content

    def test_trace_mcp_init_binary_exists(self) -> None:
        """The trace-mcp-init binary should also exist."""
        init_binary = VENV_DIR / "bin" / "trace-mcp-init"
        assert init_binary.exists(), (
            f"trace-mcp-init binary not found at {init_binary}"
        )

    def test_launcher_script_exists(self) -> None:
        """The bin/trace-mcp-server launcher script should exist."""
        assert LAUNCHER_SCRIPT.exists(), (
            f"Launcher script not found at {LAUNCHER_SCRIPT}. "
            f"This is the recommended entry point for .mcp.json configs."
        )

    def test_launcher_script_executable(self) -> None:
        """The launcher script should have executable permission."""
        if not LAUNCHER_SCRIPT.exists():
            pytest.skip("Launcher script does not exist")
        assert os.access(LAUNCHER_SCRIPT, os.X_OK), (
            f"Launcher script {LAUNCHER_SCRIPT} is not executable. "
            f"Run: chmod +x {LAUNCHER_SCRIPT}"
        )

    def test_launcher_script_shebang(self) -> None:
        """The launcher script should have a proper Python shebang."""
        if not LAUNCHER_SCRIPT.exists():
            pytest.skip("Launcher script does not exist")
        first_line = LAUNCHER_SCRIPT.read_text().split("\n")[0]
        assert first_line.startswith("#!/usr/bin/env python3"), (
            f"Launcher shebang is '{first_line}', expected '#!/usr/bin/env python3'"
        )

    def test_launcher_script_adds_src_to_path(self) -> None:
        """The launcher script should add src/ to sys.path."""
        if not LAUNCHER_SCRIPT.exists():
            pytest.skip("Launcher script does not exist")
        content = LAUNCHER_SCRIPT.read_text()
        assert "sys.path" in content, "Launcher should manipulate sys.path"
        assert '"src"' in content or "'src'" in content or "/ \"src\"" in content, (
            "Launcher should reference the src/ directory"
        )

    def test_launcher_script_imports_main(self) -> None:
        """The launcher script should import and call main from trace_mcp.server."""
        if not LAUNCHER_SCRIPT.exists():
            pytest.skip("Launcher script does not exist")
        content = LAUNCHER_SCRIPT.read_text()
        assert "from trace_mcp.server import main" in content


# ── MCP Configuration Tests ─────────────────────────────────────────────────


class TestMCPConfiguration:
    """Verify .mcp.json files are correctly configured."""

    def test_trace_mcp_json_exists(self) -> None:
        """The TRACE project should have a .mcp.json."""
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

    def test_mcp_json_command_resolves(self) -> None:
        """The command in .mcp.json should be executable."""
        mcp_json = TRACE_ROOT / ".mcp.json"
        if not mcp_json.exists():
            pytest.skip(".mcp.json does not exist")
        data = json.loads(mcp_json.read_text())
        trace_config = data["mcpServers"]["trace"]
        command = trace_config["command"]

        # Check if the command is available
        result = subprocess.run(
            ["which", command],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, (
            f"Command '{command}' from .mcp.json is not found in PATH. "
            f"Ensure it is installed."
        )


# ── Dependency Tests ─────────────────────────────────────────────────────────


class TestDependencies:
    """Verify that required dependencies are available."""

    def test_mcp_importable(self) -> None:
        """The mcp package should be importable."""
        import mcp

        # mcp >= 1.26 doesn't expose __version__; just verify import works
        assert hasattr(mcp, "server")

    def test_pydantic_version(self) -> None:
        """Pydantic should be >= 2.0."""
        import pydantic

        major = int(pydantic.__version__.split(".")[0])
        assert major >= 2, f"TRACE requires pydantic >= 2.0, found {pydantic.__version__}"

    def test_fastmcp_importable(self) -> None:
        """FastMCP should be importable from the mcp package."""
        from mcp.server.fastmcp import FastMCP

        assert FastMCP is not None

    def test_venv_has_mcp(self) -> None:
        """The venv should have the mcp package installed."""
        result = subprocess.run(
            [str(VENV_PYTHON), "-c", "import mcp; print('mcp OK')"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"mcp package not importable in venv.\nstderr: {result.stderr}"
        )

    def test_venv_has_pydantic_v2(self) -> None:
        """The venv should have pydantic >= 2.0."""
        result = subprocess.run(
            [
                str(VENV_PYTHON),
                "-c",
                "import pydantic; v = int(pydantic.__version__.split('.')[0]); "
                "assert v >= 2, f'need pydantic >= 2, got {pydantic.__version__}'; "
                "print('OK:', pydantic.__version__)",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"pydantic v2 not available in venv.\nstderr: {result.stderr}"
        )


# ── pyproject.toml Consistency Tests ─────────────────────────────────────────


class TestPyprojectConsistency:
    """Verify pyproject.toml is consistent with the installed package."""

    def test_pyproject_exists(self) -> None:
        assert (TRACE_ROOT / "pyproject.toml").exists()

    def test_version_matches_pyproject(self) -> None:
        """The installed version should match pyproject.toml."""
        import trace_mcp

        pyproject = (TRACE_ROOT / "pyproject.toml").read_text()
        # Parse version from pyproject.toml
        for line in pyproject.split("\n"):
            if line.strip().startswith("version"):
                # version = "0.3.0"
                pyproject_version = line.split('"')[1]
                break
        else:
            pytest.fail("Could not find version in pyproject.toml")

        assert trace_mcp.__version__ == pyproject_version, (
            f"Installed version {trace_mcp.__version__} != "
            f"pyproject.toml version {pyproject_version}"
        )

    def test_entry_points_in_pyproject(self) -> None:
        """pyproject.toml should declare trace-mcp entry point."""
        pyproject = (TRACE_ROOT / "pyproject.toml").read_text()
        assert "trace-mcp" in pyproject
        assert "trace_mcp.server:main" in pyproject


# ── Consumer Project Tests ───────────────────────────────────────────────────


class TestConsumerProjects:
    """Verify that projects referencing TRACE can use it correctly.

    These tests check the .mcp.json configurations in projects that
    consume the TRACE MCP server (like green-narrative).
    """

    # Known consumer projects
    CONSUMER_PROJECTS = [
        Path("/Users/echoes/Documents/Berkeley/Research/green-narrative"),
    ]

    @pytest.mark.parametrize(
        "project_dir",
        CONSUMER_PROJECTS,
        ids=[p.name for p in CONSUMER_PROJECTS],
    )
    def test_consumer_mcp_json_valid(self, project_dir: Path) -> None:
        """Consumer project .mcp.json should reference TRACE correctly."""
        mcp_json = project_dir / ".mcp.json"
        if not mcp_json.exists():
            pytest.skip(f"No .mcp.json in {project_dir}")

        data = json.loads(mcp_json.read_text())
        assert "mcpServers" in data
        assert "trace" in data["mcpServers"], (
            f"{project_dir.name}/.mcp.json does not configure a 'trace' server"
        )

        trace_config = data["mcpServers"]["trace"]
        # Verify the command references the TRACE directory
        args_str = " ".join(trace_config.get("args", []))
        full_cmd = f"{trace_config.get('command', '')} {args_str}"
        assert "TRACE" in full_cmd or "trace-mcp" in full_cmd.lower(), (
            f"Consumer {project_dir.name} trace config doesn't reference TRACE: {full_cmd}"
        )

    @pytest.mark.parametrize(
        "project_dir",
        CONSUMER_PROJECTS,
        ids=[p.name for p in CONSUMER_PROJECTS],
    )
    def test_consumer_trace_directory_valid(self, project_dir: Path) -> None:
        """The TRACE directory referenced by consumer .mcp.json should exist."""
        mcp_json = project_dir / ".mcp.json"
        if not mcp_json.exists():
            pytest.skip(f"No .mcp.json in {project_dir}")

        data = json.loads(mcp_json.read_text())
        trace_config = data["mcpServers"].get("trace", {})
        args = trace_config.get("args", [])

        # Look for --directory flag in args
        for i, arg in enumerate(args):
            if arg == "--directory" and i + 1 < len(args):
                trace_dir = Path(args[i + 1])
                assert trace_dir.exists(), (
                    f"TRACE directory '{trace_dir}' referenced in "
                    f"{project_dir.name}/.mcp.json does not exist"
                )
                assert (trace_dir / "pyproject.toml").exists(), (
                    f"'{trace_dir}' exists but has no pyproject.toml — "
                    "is it really the TRACE project?"
                )
