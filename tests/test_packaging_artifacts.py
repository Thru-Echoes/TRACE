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

import json
import os
import shutil
import subprocess
import sys
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
    assets = sorted(str(p.relative_to(TRACE_ROOT / "src")) for p in ASSETS_DIR.rglob("*") if p.is_file())
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
        assert not missing, f"Adapter assets missing from the wheel (trace-mcp-init is dead on arrival): {missing}"

    def test_wheel_contains_packaged_schema(self, built_dist: dict[str, Path]) -> None:
        """`trace-mcp validate` loads the schema as package data; a wheel
        without it makes the subcommand crash on installed packages."""
        members = get_wheel_members(built_dist["wheel"])
        assert "trace_mcp/schemas/trace-v0.5.json" in members, (
            "trace_mcp/schemas/trace-v0.5.json missing from the wheel — "
            "trace-mcp validate would crash on any installed package"
        )

    def test_wheel_matches_package_tree(self, built_dist: dict[str, Path]) -> None:
        """Tree parity: every TRACKED non-Python file under src/trace_mcp must
        reach the wheel. Direct-from-tree builds (uvx --from <path>) include
        the whole package dir, but uv build goes through the sdist allowlist —
        any file missing there silently produces two DIFFERENT wheels from the
        same commit depending on the build path. This guard self-extends to
        files added in the future.

        The universe is `git ls-files` (committed files), not a raw tree scan:
        the build legitimately excludes gitignored/untracked local junk
        (.DS_Store, macOS ' 2' duplicates, *.local.json), and a raw rglob would
        false-positive on those during local dev."""
        ls = subprocess.run(
            ["git", "ls-files", "--", "src/trace_mcp"],
            cwd=TRACE_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if ls.returncode != 0:
            pytest.skip("git unavailable or not a git checkout — tree-parity universe needs git ls-files")

        members = get_wheel_members(built_dist["wheel"])
        tree_files = sorted(
            line.removeprefix("src/") for line in ls.stdout.splitlines() if line and not line.endswith(".py")
        )
        assert tree_files, "Expected tracked non-Python package files under src/trace_mcp"
        missing = [f for f in tree_files if f not in members]
        assert not missing, (
            f"Tracked package files missing from the wheel "
            f"(sdist allowlist gap — tree-built and sdist-built wheels now differ): {missing}"
        )


class TestWheelInstallE2E:
    """Install the built wheel into a clean venv and exercise the console
    script — the original C2 reproduction path ('crashes on any installed
    package'). Network + ~seconds of venv setup; uv's cache keeps it fast."""

    def test_trace_mcp_validate_works_from_wheel_install(self, built_dist: dict[str, Path], tmp_path: Path) -> None:
        venv_dir = tmp_path / "venv"
        subprocess.run(["uv", "venv", str(venv_dir)], check=True, capture_output=True, timeout=120)
        python = venv_dir / ("Scripts" if sys.platform == "win32" else "bin") / "python"
        # Install through the [validate] extra (PEP 508 direct reference) so the
        # extra itself is exercised — installing jsonschema manually would let a
        # broken/renamed extra pass every test.
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                f"trace-mcp[validate] @ {built_dist['wheel'].as_uri()}",
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
        trace_mcp_bin = python.parent / "trace-mcp"

        fixture = TRACE_ROOT / "tests" / "fixtures" / "waggle_session_2026-05-13.json"
        good = subprocess.run(
            [str(trace_mcp_bin), "validate", str(fixture)], capture_output=True, text=True, timeout=60
        )
        assert good.returncode == 0, (
            f"trace-mcp validate failed from a wheel install (the C2 crash path):\n"
            f"stdout: {good.stdout}\nstderr: {good.stderr}"
        )
        assert "PASS" in good.stdout

        # Negative control: a non-session document must yield exit 1 (proves
        # the CLI actually validates rather than rubber-stamping).
        bad_file = tmp_path / "bad.json"
        bad_file.write_text('{"not": "a session"}')
        bad = subprocess.run(
            [str(trace_mcp_bin), "validate", str(bad_file)], capture_output=True, text=True, timeout=60
        )
        assert bad.returncode == 1, f"Expected exit 1 for invalid doc, got {bad.returncode}: {bad.stdout}"
        assert "FAIL" in bad.stdout

    def test_trace_mcp_doctor_works_from_wheel_install(self, built_dist: dict[str, Path], tmp_path: Path) -> None:
        """INV-11 against the SHIPPED artifact, not the source tree.

        `trace-mcp doctor` derives its expectations from packaged data (the
        adapter's hook assets), exactly like `trace-mcp-init` copies them — so
        a sdist-allowlist gap would leave the doctor dead on arrival from a
        wheel install while every source-tree test stayed green. This installs
        the wheel, initializes a project with the installed console script, and
        checks it with the installed doctor: a fresh install must be clean.
        """
        venv_dir = tmp_path / "venv"
        subprocess.run(["uv", "venv", str(venv_dir)], check=True, capture_output=True, timeout=120)
        python = venv_dir / ("Scripts" if sys.platform == "win32" else "bin") / "python"
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), f"trace-mcp @ {built_dist['wheel'].as_uri()}"],
            check=True,
            capture_output=True,
            timeout=300,
        )

        project = tmp_path / "consumer"
        project.mkdir()
        source = tmp_path / "TRACE-checkout"
        source.mkdir()
        env = {
            **os.environ,
            # An installed wheel has no safe `--from` to infer (the PyPI name
            # belongs to another project), so init fails closed without this.
            "TRACE_SOURCE_PATH": str(source),
            "TRACE_REGISTRY_PATH": str(tmp_path / "projects.json"),
            "TRACE_SESSIONS_DIR": str(tmp_path / "sessions"),
            "TRACE_KNOWLEDGE_DIR": str(tmp_path / "knowledge"),
        }
        init = subprocess.run(
            [str(python.parent / "trace-mcp-init"), str(project), "--client", "claude-code"],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        assert init.returncode == 0, f"trace-mcp-init failed from a wheel install:\n{init.stdout}\n{init.stderr}"

        doctor = subprocess.run(
            [str(python.parent / "trace-mcp"), "doctor", str(project)],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        assert doctor.returncode == 0, (
            f"a freshly initialized project is not doctor-clean when both commands come from the shipped "
            f"wheel (INV-11):\n{doctor.stdout}\n{doctor.stderr}"
        )
        assert "FAIL" not in doctor.stdout

        # Negative control: an unrelated directory must fail, or the check above
        # would pass just as happily against a doctor that rubber-stamps.
        empty = subprocess.run(
            [str(python.parent / "trace-mcp"), "doctor", str(tmp_path / "TRACE-checkout")],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        assert empty.returncode == 1, f"Expected findings for an uninitialized directory: {empty.stdout}"
        assert "FAIL" in empty.stdout


class TestShippedLaunchPath:
    """Resolve every dependency from scratch — the resolution consumers get —
    and prove the server still imports and registers its tools.

    ``uv run pytest`` resolves through ``uv.lock``, which pins a known-good
    ``mcp``. Consumers launch with ``uvx --refresh``, which ignores the lockfile
    and re-resolves the declared spec on each server start. The paths agree
    until an upstream major lands: ``mcp`` 2.0.0 removed ``mcp.server.fastmcp``,
    so an unbounded ``mcp>=1.0.0`` resolved to a release whose import raises
    ``ModuleNotFoundError`` before a single tool is registered.

    ``TestUvxIntegration::test_uvx_entry_point_resolves`` already covers the
    launch path and does fail on that break — but only where the resolution is
    genuinely cold. It passes ``--refresh-package trace-mcp``, which refreshes
    the package and *not* its transitive deps, so a developer whose uv cache
    already holds an env built against ``mcp`` 1.x sees green while consumers
    with a cold cache get a dead server. This guard is not subject to that: it
    installs the built wheel into a brand-new venv with no lockfile, which
    re-resolves the whole dependency set.

    It also asserts further along. The existing check proves the module
    imports; this one lists the registered tools, so a resolution that imports
    but registers nothing still fails.

    The assertion is behavioural, so a future port to the ``mcp`` 2.x API
    satisfies this guard without editing it.
    """

    # 17 core tools; the 5 trace-learn tools need heavy optional deps and load
    # in main(), not at import, so they are deliberately out of scope here.
    CORE_TOOL_FLOOR = 17

    PROBE = (
        "import asyncio, json\n"
        "from trace_mcp.server import mcp\n"
        "print(json.dumps(sorted(t.name for t in asyncio.run(mcp.list_tools()))))\n"
    )

    def test_server_registers_tools_under_fresh_resolution(self, built_dist: dict[str, Path], tmp_path: Path) -> None:
        venv_dir = tmp_path / "venv"
        subprocess.run(["uv", "venv", str(venv_dir)], check=True, capture_output=True, timeout=120)
        python = venv_dir / ("Scripts" if sys.platform == "win32" else "bin") / "python"

        # No --frozen, no lockfile: dependency specs resolve from the index
        # exactly as `uvx --refresh` resolves them for a consumer.
        install = subprocess.run(
            ["uv", "pip", "install", "--python", str(python), f"trace-mcp @ {built_dist['wheel'].as_uri()}"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert install.returncode == 0, (
            f"Fresh resolution of the shipped wheel failed — consumers would get this on server start:\n"
            f"stdout: {install.stdout}\nstderr: {install.stderr}"
        )

        # Point storage at a temp dir so the probe cannot touch the real store.
        probe = subprocess.run(
            [str(python), "-c", self.PROBE],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "TRACE_SESSIONS_DIR": str(tmp_path / "sessions")},
        )
        assert probe.returncode == 0, (
            f"The server failed to import under fresh dependency resolution — every tool would be "
            f"missing for every consumer, while the locked dev environment stays green:\n"
            f"stdout: {probe.stdout}\nstderr: {probe.stderr}"
        )

        tool_names = json.loads(probe.stdout.strip().splitlines()[-1])
        assert len(tool_names) >= self.CORE_TOOL_FLOOR, (
            f"Expected at least {self.CORE_TOOL_FLOOR} core tools from a freshly resolved server, "
            f"got {len(tool_names)}: {tool_names}"
        )
        for required in ("trace_start_session", "trace_health_check"):
            assert required in tool_names, f"{required} missing from a freshly resolved server: {tool_names}"


class TestNoPersonalDataInSdist:
    """Content-level leak guard: no file shipped in the sdist may contain a
    personal home-directory path. The release.yml leak-guard only checks file
    NAMES in the tarball; this catches personal paths inside file contents
    (e.g. real transcript paths in session fixtures)."""

    def test_no_personal_home_paths_in_sdist_contents(self, built_dist: dict[str, Path]) -> None:
        # Built by concatenation so this test file (which ships in the sdist)
        # can never contain the contiguous needle and self-trigger.
        needle = "/Users/" + "echoes"
        offenders: list[str] = []
        with tarfile.open(built_dist["sdist"], "r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                fh = tf.extractfile(member)
                if fh is None:
                    continue
                content = fh.read()
                if needle.encode("utf-8") in content:
                    offenders.append(member.name)
        assert not offenders, (
            f"Personal home-directory paths found inside sdist file contents (sanitize before shipping): {offenders}"
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

    def test_sdist_contains_anchored_top_level_metadata(self, built_dist: dict[str, Path]) -> None:
        """The top-level metadata entries are anchored with a leading slash
        (gitignore-style patterns otherwise match at any depth); make sure the
        anchored syntax still ships them at the sdist root."""
        members = get_sdist_members(built_dist["sdist"])
        expected = [
            "README.md",
            "LICENSE",
            "NOTICE",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CHANGELOG.md",
            "server.json",
            "pyproject.toml",
        ]
        missing = [f for f in expected if f not in members]
        assert not missing, f"Anchored top-level metadata missing from the sdist: {missing}"
