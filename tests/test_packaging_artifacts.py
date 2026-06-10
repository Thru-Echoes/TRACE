"""Positive packaging guards: the built artifacts must CONTAIN required files.

The release leak-guard (release.yml) only asserts that private/cruft files are
*absent* from the artifacts; nothing asserted that required runtime files are
*present*. That gap shipped a wheel missing ``py.typed`` and all of
``adapters/claude_code/assets/`` — a clean-venv install of that wheel installs
zero hooks silently, then ``trace-mcp-init`` crashes with FileNotFoundError in
``_merge_settings``.

Root cause: ``uv build`` constructs the wheel FROM the sdist, and the sdist
allowlist's only src pattern was ``src/trace_mcp/**/*.py`` — so every
non-Python package file was dropped from both artifacts. Local
``uvx --from <path>`` launches build direct-from-tree and *do* include the
files, which is why nothing surfaced before the release path was exercised.

These tests build the artifacts exactly the way release.yml does (``uv build``)
and assert the required files are present in both the sdist and the wheel.
Expected assets are derived from the live source tree so newly added assets
are guarded automatically.

Side effects: runs ``uv build`` in a temp directory (writes dist artifacts
there only; the repo's own ``dist/`` is untouched).
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

TRACE_ROOT = Path(__file__).parent.parent
ASSETS_DIR = TRACE_ROOT / "src" / "trace_mcp" / "adapters" / "claude_code" / "assets"

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None,
    reason="uv not on PATH — packaging guard builds artifacts with `uv build` (same as release.yml)",
)


def get_expected_asset_relpaths() -> list[str]:
    """Return adapter asset paths relative to the package root, from the live tree.

    Derived dynamically so that adding a new asset file automatically extends
    the guard. A floor of 6 known assets (settings template, CLAUDE block,
    4 hook scripts) protects against the tree itself going missing.
    """
    assets = sorted(
        str(p.relative_to(TRACE_ROOT / "src")) for p in ASSETS_DIR.rglob("*") if p.is_file()
    )
    assert len(assets) >= 6, (
        f"Expected at least the 6 known adapter assets under {ASSETS_DIR}, found {len(assets)}: {assets}"
    )
    return assets


@pytest.fixture(scope="module")
def built_dist(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Build sdist + wheel with `uv build` (wheel is built FROM the sdist).

    Returns {"sdist": <path to .tar.gz>, "wheel": <path to .whl>}.
    Module-scoped: one build serves every assertion in this file.
    """
    out_dir = tmp_path_factory.mktemp("dist")
    result = subprocess.run(
        ["uv", "build", "--out-dir", str(out_dir)],
        cwd=TRACE_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"uv build failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"

    sdists = list(out_dir.glob("*.tar.gz"))
    wheels = list(out_dir.glob("*.whl"))
    assert len(sdists) == 1, f"Expected exactly one sdist in {out_dir}, found: {sdists}"
    assert len(wheels) == 1, f"Expected exactly one wheel in {out_dir}, found: {wheels}"
    return {"sdist": sdists[0], "wheel": wheels[0]}


def get_wheel_members(wheel_path: Path) -> set[str]:
    """Return all member paths in the wheel zip."""
    with zipfile.ZipFile(wheel_path) as zf:
        return set(zf.namelist())


def get_sdist_members(sdist_path: Path) -> set[str]:
    """Return all member paths in the sdist tarball, with the top-level
    ``<name>-<version>/`` prefix stripped so paths are repo-relative."""
    with tarfile.open(sdist_path, "r:gz") as tf:
        names = tf.getnames()
    return {name.split("/", 1)[1] for name in names if "/" in name}


class TestWheelContainsRequiredFiles:
    """The wheel a user installs must contain every required runtime file."""

    def test_wheel_contains_py_typed(self, built_dist: dict[str, Path]) -> None:
        """PEP 561: the `Typing :: Typed` classifier is false advertising
        unless py.typed ships in the wheel."""
        members = get_wheel_members(built_dist["wheel"])
        assert "trace_mcp/py.typed" in members, (
            "trace_mcp/py.typed missing from the wheel — sdist allowlist must include it "
            "(uv build constructs the wheel from the sdist)"
        )

    def test_wheel_contains_claude_code_adapter_assets(self, built_dist: dict[str, Path]) -> None:
        """trace-mcp-init copies these at runtime; a wheel without them
        installs zero hooks silently, then crashes in _merge_settings."""
        members = get_wheel_members(built_dist["wheel"])
        missing = [a for a in get_expected_asset_relpaths() if a not in members]
        assert not missing, (
            f"Adapter assets missing from the wheel (trace-mcp-init is dead on arrival): {missing}"
        )


class TestSdistContainsRequiredFiles:
    """uv build constructs the wheel from the sdist, so the sdist is the
    root-cause surface: anything missing here is missing from the wheel."""

    def test_sdist_contains_py_typed(self, built_dist: dict[str, Path]) -> None:
        members = get_sdist_members(built_dist["sdist"])
        assert "src/trace_mcp/py.typed" in members, "src/trace_mcp/py.typed missing from the sdist allowlist"

    def test_sdist_contains_claude_code_adapter_assets(self, built_dist: dict[str, Path]) -> None:
        members = get_sdist_members(built_dist["sdist"])
        expected = ["src/" + a for a in get_expected_asset_relpaths()]
        missing = [a for a in expected if a not in members]
        assert not missing, f"Adapter assets missing from the sdist allowlist: {missing}"
