"""Tests for the packaged session validator (`trace_mcp.validate`).

`trace-mcp validate` previously crashed on any installed package: server.py
loaded `scripts/validate_session.py` via a repo-relative path
(`parent.parent.parent / "scripts"`) that only exists in a source checkout,
and the JSON Schema itself wasn't shipped. The validator now lives in the
package (`trace_mcp.validate`) and loads `schemas/trace-v0.4.json` as package
data via importlib.resources, so the subcommand works from wheel installs,
editable installs, and source checkouts alike.

Real data: validates the shipped real-session fixture AND a session freshly
written by the actual storage layer (generator/validator coherence).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.validate import load_schema, main, validate_file

TRACE_ROOT = Path(__file__).parent.parent
REPO_SCHEMA = TRACE_ROOT / "schemas" / "trace-v0.4.json"
PACKAGE_SCHEMA = TRACE_ROOT / "src" / "trace_mcp" / "schemas" / "trace-v0.4.json"
FIXTURE_SESSION = TRACE_ROOT / "tests" / "fixtures" / "waggle_session_2026-05-13.json"


# ── Schema loading & single-source-of-truth guards ───────────────────────────


class TestSchemaPackaging:
    def test_load_schema_from_package_data(self) -> None:
        """The schema must load via importlib.resources — no repo paths."""
        schema = load_schema()
        assert schema["$id"] == "https://trace-protocol.org/schemas/trace-v0.4.json"
        assert "properties" in schema

    def test_packaged_schema_matches_repo_schema(self) -> None:
        """The packaged copy and the top-level spec artifact must be
        byte-identical — both are written by scripts/generate_schema.py."""
        assert PACKAGE_SCHEMA.exists(), f"{PACKAGE_SCHEMA} missing — run scripts/generate_schema.py"
        assert PACKAGE_SCHEMA.read_bytes() == REPO_SCHEMA.read_bytes(), (
            "src/trace_mcp/schemas/trace-v0.4.json has drifted from schemas/trace-v0.4.json — "
            "regenerate both with scripts/generate_schema.py"
        )

    def test_generated_schema_is_fresh(self) -> None:
        """Regenerating from the Pydantic models must reproduce both checked-in
        copies byte-for-byte (catches model drift without touching the tree)."""
        spec = importlib.util.spec_from_file_location(
            "generate_schema", TRACE_ROOT / "scripts" / "generate_schema.py"
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        payload = (json.dumps(mod.build_schema(), indent=2) + "\n").encode("utf-8")
        assert payload == REPO_SCHEMA.read_bytes(), "schemas/trace-v0.4.json is stale vs the Pydantic models"
        assert payload == PACKAGE_SCHEMA.read_bytes(), (
            "src/trace_mcp/schemas/trace-v0.4.json is stale vs the Pydantic models"
        )


# ── Validation behavior (real data, real execution) ─────────────────────────


class TestValidateFile:
    def test_real_session_fixture_passes(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert validate_file(FIXTURE_SESSION, load_schema()) is True
        assert "PASS" in capsys.readouterr().out

    async def test_freshly_written_session_passes(self, tmp_path: Path) -> None:
        """A session written by the actual storage layer must validate —
        generator and validator stay coherent."""
        storage = JsonFileStorage(directory=str(tmp_path))
        session = Session(id="trace_20260610_validcli", metadata=SessionMetadata(project="validate-cli-test"))
        await storage.create_session(session)
        assert validate_file(tmp_path / "trace_20260610_validcli.json", load_schema()) is True

    def test_rejects_invalid_document(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"not": "a session"}))
        assert validate_file(bad, load_schema()) is False
        assert "FAIL" in capsys.readouterr().out

    def test_rejects_malformed_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        garbage = tmp_path / "garbage.json"
        garbage.write_text("not json {{{")
        assert validate_file(garbage, load_schema()) is False
        assert "Invalid JSON" in capsys.readouterr().out

    def test_nonexistent_file_fails_without_traceback(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A missing file must produce a per-file FAIL line (so multi-file
        runs continue), never an unhandled traceback."""
        assert validate_file(tmp_path / "does-not-exist.json", load_schema()) is False
        assert "FAIL" in capsys.readouterr().out

    def test_directory_argument_fails_without_traceback(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert validate_file(tmp_path, load_schema()) is False
        assert "FAIL" in capsys.readouterr().out

    def test_non_utf8_file_fails_without_traceback(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        binary = tmp_path / "binary.json"
        binary.write_bytes(b"\xff\xfe\x00\x01")
        assert validate_file(binary, load_schema()) is False
        assert "FAIL" in capsys.readouterr().out

    def test_multi_file_run_continues_past_unreadable_file(self, tmp_path: Path) -> None:
        """One unreadable path must not abort validation of the rest."""
        assert main([str(tmp_path / "missing.json"), str(FIXTURE_SESSION)]) == 1


# ── CLI entry points ─────────────────────────────────────────────────────────


class TestCliMain:
    def test_exit_zero_on_valid(self) -> None:
        assert main([str(FIXTURE_SESSION)]) == 0

    def test_exit_one_on_invalid(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"not": "a session"}))
        assert main([str(bad)]) == 1

    def test_exit_one_on_no_args(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([]) == 1
        assert "Usage" in capsys.readouterr().err

    def test_mixed_args_reports_counts(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"not": "a session"}))
        assert main([str(FIXTURE_SESSION), str(bad)]) == 1
        assert "1/2 files valid" in capsys.readouterr().out

    def test_server_dispatches_validate_subcommand(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`trace-mcp validate <file>` must work from the installed package —
        no repo-relative path to scripts/."""
        from trace_mcp import server

        monkeypatch.setattr(sys, "argv", ["trace-mcp", "validate", str(FIXTURE_SESSION)])
        with pytest.raises(SystemExit) as excinfo:
            server.main()
        assert excinfo.value.code == 0

    def test_legacy_script_shim_still_works(self) -> None:
        """scripts/validate_session.py is documented usage; the shim must keep
        delegating to the package implementation."""
        spec = importlib.util.spec_from_file_location(
            "validate_session_shim", TRACE_ROOT / "scripts" / "validate_session.py"
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.main([str(FIXTURE_SESSION)]) == 0

    def test_shim_recovers_from_stale_installed_package(self, tmp_path: Path) -> None:
        """If an older trace_mcp WITHOUT validate.py shadows the source tree
        (stale editable/site-packages install), the shim's src/ fallback must
        still win — a failed `from trace_mcp.validate import ...` leaves the
        stale package in sys.modules, so the shim has to evict it before
        retrying."""
        stale = tmp_path / "stale_site"
        (stale / "trace_mcp").mkdir(parents=True)
        (stale / "trace_mcp" / "__init__.py").write_text('__version__ = "0.4.1"\n')

        result = subprocess.run(
            [sys.executable, str(TRACE_ROOT / "scripts" / "validate_session.py"), str(FIXTURE_SESSION)],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(stale)},
            cwd=str(TRACE_ROOT),
        )
        assert result.returncode == 0, (
            f"Shim failed with a stale trace_mcp shadowing the source tree:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "PASS" in result.stdout
