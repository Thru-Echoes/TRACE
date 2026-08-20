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
import shutil
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

        for model in (Actor, AnnotationData, ContributionData, DecisionData, SessionMetadata):
            assert model is not None  # the module exports each required model
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
        # trace-mcp is a LOCAL-only package (not published to PyPI): the
        # source after --from must be the local path '.', never a PyPI
        # package spec. This is the load-bearing local-only guarantee.
        assert args[args.index("--from") + 1] == ".", (
            f"uvx --from must point at the local path '.', not a PyPI package. Got: {args[args.index('--from') + 1]!r}"
        )
        # A refresh flag must be present so local source changes are picked
        # up on server restart. Either form is valid: `--refresh-package
        # trace-mcp` (targeted) or `--refresh` (whole-cache, used when extra
        # `--with` embedding deps are pinned).
        assert "--refresh-package" in args or "--refresh" in args, (
            "uvx args should include a refresh flag (--refresh-package or "
            f"--refresh) so local edits are rebuilt. Got: {args}"
        )

    @pytest.mark.skipif(
        shutil.which("uvx") is None,
        reason="uvx not installed — the .mcp.json launch path can't be exercised on this machine",
    )
    def test_mcp_json_command_resolves(self) -> None:
        """When uvx is installed, it must resolve on PATH (skip otherwise:
        contributors without uv shouldn't fail the suite over the maintainer's
        launch mechanism)."""
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
            f"Legacy launcher {legacy_bin} still exists. Remove it — all projects now use uvx."
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
            f"Installed version {trace_mcp.__version__} != pyproject.toml version {pyproject_version}"
        )

    def test_entry_points_in_pyproject(self) -> None:
        pyproject = (TRACE_ROOT / "pyproject.toml").read_text()
        assert "trace-mcp" in pyproject
        assert "trace_mcp.server:main" in pyproject


# ── Version-Declaration Enumeration Guard ────────────────────────────────────
#
# The package version is restated in several files that no test used to read,
# so a release bump could land in pyproject.toml while `server.json` kept
# advertising the previous version to the MCP registry — a stale version there
# is what an installer resolves, not a cosmetic typo. These tests enumerate
# every site that restates a version and pin each to its single source of
# truth: the package version to pyproject.toml, the spec/wire version to
# ``SCHEMA_VERSION``.
#
# The two versions are deliberately independent — a hardening release may bump
# the package while the wire format stands still — so nothing here asserts that
# they are equal.
#
# To add a site: append it to the relevant helper below. Do not "fix" a
# failure by loosening the assertion.


def _package_version() -> str:
    """Read the canonical package version out of pyproject.toml.

    Pure. Fails the calling test if the version cannot be located.
    """
    for line in (TRACE_ROOT / "pyproject.toml").read_text().split("\n"):
        if line.strip().startswith("version"):
            return line.split('"')[1]
    pytest.fail("Could not find version in pyproject.toml")


def _spec_version() -> str:
    """Read the canonical spec/wire version out of ``schema/session.py``.

    Pure. Fails the calling test if ``SCHEMA_VERSION`` cannot be located.
    """
    from trace_mcp.schema.session import SCHEMA_VERSION

    return SCHEMA_VERSION


class TestVersionDeclarationSites:
    """Every file restating a version agrees with that version's source of truth."""

    def test_server_json_advertises_the_package_version(self) -> None:
        """`server.json` feeds the MCP registry; a stale version there is what
        an installer resolves. Both the top-level and per-package versions count.
        """
        manifest = json.loads((TRACE_ROOT / "server.json").read_text())
        expected = _package_version()

        declared = {("server.json:version", manifest.get("version"))}
        declared |= {
            (f"server.json:packages[{i}].version", pkg.get("version"))
            for i, pkg in enumerate(manifest.get("packages", []))
        }

        assert declared, "server.json positive control failed: no version fields found at all."
        stale = {site: got for site, got in declared if got != expected}
        assert not stale, f"server.json versions disagree with pyproject.toml ({expected}): {stale}"

    def test_citation_cff_declares_the_package_version(self) -> None:
        """CITATION.cff is what a DOI archive and citation tooling read."""
        expected = _package_version()
        lines = [ln for ln in (TRACE_ROOT / "CITATION.cff").read_text().split("\n") if ln.startswith("version:")]

        assert len(lines) == 1, f"expected exactly one top-level `version:` line in CITATION.cff, found {len(lines)}"
        assert lines[0].split(":", 1)[1].strip().strip("\"'") == expected, (
            f"CITATION.cff version != pyproject.toml version ({expected}): {lines[0]!r}"
        )

    @pytest.mark.parametrize(
        ("doc", "prefix"),
        [
            ("README.md", "**Version:**"),
            ("CLAUDE.md", "> **Version**:"),
        ],
    )
    def test_docs_state_the_package_version(self, doc: str, prefix: str) -> None:
        """The version banners in the two front-door docs are the ones a reader
        trusts without checking pyproject.toml.
        """
        expected = _package_version()
        matches = [ln for ln in (TRACE_ROOT / doc).read_text().split("\n") if ln.startswith(prefix)]

        assert len(matches) == 1, f"expected exactly one line starting {prefix!r} in {doc}, found {len(matches)}"
        assert expected in matches[0], f"{doc} version banner does not state {expected}: {matches[0]!r}"

    def test_specification_states_the_schema_version(self) -> None:
        """The spec heading is the human-readable form of ``SCHEMA_VERSION``."""
        expected = _spec_version()
        heading = f"## Specification v{expected}"
        text = (TRACE_ROOT / "docs" / "specification.md").read_text()

        assert heading in text, f"docs/specification.md does not carry the heading {heading!r} for SCHEMA_VERSION"

    def test_schema_file_tracks_the_schema_version(self) -> None:
        """The published schema filename and its ``$id`` both encode the
        major.minor of the wire version, and the copy shipped inside the package
        must not lag the top-level one.
        """
        major, minor, *_ = _spec_version().split(".")
        name = f"trace-v{major}.{minor}.json"

        published = TRACE_ROOT / "schemas" / name
        packaged = TRACE_ROOT / "src" / "trace_mcp" / "schemas" / name
        assert published.exists(), f"schemas/{name} missing for SCHEMA_VERSION {_spec_version()}"
        assert packaged.exists(), f"src/trace_mcp/schemas/{name} missing for SCHEMA_VERSION {_spec_version()}"

        schema_id = json.loads(published.read_text()).get("$id", "")
        assert schema_id.endswith(name), f"schemas/{name} has a mismatched $id: {schema_id!r}"


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
                failures.append(f"{project_dir}/.mcp.json: command is '{trace_config.get('command')}', expected 'uvx'")
                continue

            args = trace_config.get("args", [])
            if "--from" not in args:
                failures.append(f"{project_dir}/.mcp.json: missing '--from' in uvx args")
            if "trace-mcp" not in args:
                failures.append(f"{project_dir}/.mcp.json: missing 'trace-mcp' in uvx args")

        if failures:
            pytest.fail("Consumer project configuration check failed:\n  - " + "\n  - ".join(failures))


class TestNoSyncConflictArtifacts:
    """Cloud-sync/Finder conflict copies ("pyproject 2.toml", "uv 2.lock") must
    never be tracked. One pair was committed alongside an unrelated fix and
    shipped a stale pre-fix pyproject snapshot in the public repo; .gitignore
    patterns alone cannot retro-protect against an accidental `git add`, so
    this guard fails the suite the moment such a filename is tracked."""

    def test_no_numbered_duplicate_filenames_tracked(self) -> None:
        """A conflict copy is a numbered file whose UN-numbered sibling is also
        tracked ("pyproject 2.toml" beside "pyproject.toml") — filename shape
        alone is not evidence, so a legitimately numbered standalone file is
        never flagged."""
        import re

        try:
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=TRACE_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            pytest.skip("git not installed")
        if result.returncode != 0:
            pytest.skip("not a git checkout (sdist/wheel test run)")
        tracked = set(result.stdout.splitlines())
        conflict_shape = re.compile(r"^(?P<stem>.+) \d+(?P<ext>\.[^./]+)$")
        dupes = sorted(
            f for f in tracked if (m := conflict_shape.match(f)) and (m.group("stem") + m.group("ext")) in tracked
        )
        assert not dupes, (
            f"sync-conflict duplicate filenames are tracked: {dupes}. "
            "Remove them (git rm) — they are stale copies minted by a sync tool, not sources."
        )
