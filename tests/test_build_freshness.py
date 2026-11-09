"""A launch from a local clone runs that clone's code, and says so when it does not.

``uvx`` reuses a cached environment for a local source unless the package declares
what its build depends on, and the version string cannot tell two builds apart.
"""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest

import trace_mcp
from trace_mcp.conformance import staleness
from trace_mcp.conformance.staleness import get_running_build_digest, get_stale_build_notice, get_tree_digest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = Path(trace_mcp.__file__).parent


def _clone_of_the_running_build(tmp_path: Path) -> tuple[Path, Path]:
    """A source tree holding the running package, and a project launched from it."""
    source = tmp_path / "source"
    shutil.copytree(PACKAGE, source / "src" / "trace_mcp", ignore=shutil.ignore_patterns("__pycache__"))
    (source / "pyproject.toml").write_text(f'[project]\nname = "trace-mcp"\nversion = "{trace_mcp.__version__}"\n')
    project = tmp_path / "project"
    project.mkdir()
    args = ["--from", str(source), "--refresh-package", "trace-mcp", "trace-mcp"]
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"trace": {"command": "uvx", "args": args}}}))
    return source, project


def test_the_build_declares_everything_it_depends_on() -> None:
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["cache-keys"]
    files = {entry["file"] for entry in declared if "file" in entry}
    assert {"pyproject.toml", "src/**/*"} <= files


def test_the_running_digest_is_fixed_when_first_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The files of an editable install can change under a running process."""
    get_running_build_digest.cache_clear()
    first = get_running_build_digest()
    monkeypatch.setattr(staleness, "get_tree_digest", lambda _root: "changed-on-disk")
    assert get_running_build_digest() == first
    get_running_build_digest.cache_clear()


def test_an_unreadable_package_gives_no_notice_and_no_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _source, project = _clone_of_the_running_build(tmp_path)
    real = staleness.get_tree_digest

    def fail_on_the_running_package(root: Path) -> str:
        if root == PACKAGE.resolve():
            raise FileNotFoundError(root)
        return real(root)

    get_running_build_digest.cache_clear()
    monkeypatch.setattr(staleness, "get_tree_digest", fail_on_the_running_package)
    assert get_running_build_digest() == "unknown"
    assert get_stale_build_notice(project) is None
    get_running_build_digest.cache_clear()


def test_the_digest_follows_code_and_ignores_bytecode(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    first = get_tree_digest(tmp_path)
    assert get_tree_digest(tmp_path) == first
    (tmp_path / "cache.pyc").write_bytes(b"\x00")
    assert get_tree_digest(tmp_path) == first
    (tmp_path / "a.py").write_text("x = 2\n")
    assert get_tree_digest(tmp_path) != first


def test_the_digest_follows_a_renamed_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    first = get_tree_digest(tmp_path)
    (tmp_path / "a.py").rename(tmp_path / "b.py")
    assert get_tree_digest(tmp_path) != first


def test_a_build_matching_its_source_gets_no_notice(tmp_path: Path) -> None:
    _source, project = _clone_of_the_running_build(tmp_path)
    assert get_stale_build_notice(project) is None


def test_same_version_with_different_code_is_reported(tmp_path: Path) -> None:
    source, project = _clone_of_the_running_build(tmp_path)
    with (source / "src" / "trace_mcp" / "server.py").open("a") as handle:
        handle.write("\n# a source-only change\n")
    notice = get_stale_build_notice(project)
    assert notice is not None
    assert get_running_build_digest() in notice
    assert "uv" in notice and "0.11" in notice
