"""Tests for ``trace-mcp init`` .mcp.json merge behavior (ADR-006 step S1).

Re-running ``init`` must be non-destructive: an existing ``trace`` server
entry's hand-added ``--with`` extras, ``env`` block, and unknown keys are
preserved, and sibling MCP servers are left untouched. A pre-existing
``.mcp.json`` that cannot be parsed is left on disk and surfaces as a
``TraceInitError`` (fail closed) instead of being silently discarded and
replaced — the previous behavior would have destroyed every other server entry.

The fleet migration (ADR-006 S8) re-runs ``init`` across ~18 deployed configs
that all carry hand-added ``--with openai/numpy/model2vec`` extras (and, for at
least one, a ``TRACE_DEFAULT_PROJECT`` env pin); this behavior is the
prerequisite that keeps that sweep from breaking them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trace_mcp import project_identity as pident
from trace_mcp.init_project import (
    TraceInitError,
    TraceSourceUnresolvedError,
    _enroll_project,
    _extract_with_packages,
    _mcp_server_config,
    _merge_trace_entry,
    _write_claude_pin_line,
    _write_mcp_json,
    _write_pin_file,
    get_project_key,
    get_project_label,
    init_project,
    print_project_key,
)

_SOURCE = "/abs/path/to/TRACE"


@pytest.fixture(autouse=True)
def _fixed_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the resolved ``--from`` source so args assertions are deterministic.

    conftest isolates the four TRACE data-dir vars but not TRACE_SOURCE_PATH,
    so this per-test override is safe and does not fight the root conftest.
    """
    monkeypatch.setenv("TRACE_SOURCE_PATH", _SOURCE)


def _read(project_dir: Path) -> dict:
    return json.loads((project_dir / ".mcp.json").read_text())


def test_fresh_init_shape(tmp_path: Path) -> None:
    status = _write_mcp_json(tmp_path)
    assert "wrote" in status
    entry = _read(tmp_path)["mcpServers"]["trace"]
    assert entry["command"] == "uvx"
    # The trace-learn extras are part of the canonical entry: without them the
    # extension does not register and the server comes up with 17 tools instead
    # of the documented 22, with no error to explain the gap.
    assert entry["args"] == [
        "--from",
        _SOURCE,
        "--with",
        "openai",
        "--with",
        "numpy",
        "--with",
        "model2vec",
        "--refresh-package",
        "trace-mcp",
        "trace-mcp",
    ]
    assert "env" not in entry  # no empty env block on a fresh install


def test_reinit_preserves_with_extras(tmp_path: Path) -> None:
    existing = {
        "mcpServers": {
            "trace": {
                "command": "uvx",
                "args": [
                    "--from",
                    "/old/TRACE",
                    "--with",
                    "openai",
                    "--with",
                    "numpy",
                    "--with",
                    "model2vec",
                    "--refresh",
                    "trace-mcp",
                ],
            }
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(existing))

    status = _write_mcp_json(tmp_path)
    assert "updated" in status

    args = _read(tmp_path)["mcpServers"]["trace"]["args"]
    # Canonical source is rebuilt (old /old/TRACE gone) and the command stays last.
    assert args[:2] == ["--from", _SOURCE]
    assert args[-1] == "trace-mcp"
    assert "--refresh-package" in args and "--refresh" not in args
    # These three are now canonical, so they must appear exactly once each —
    # the existing entry carried the same packages, and re-running init must
    # not append a second copy of every one.
    with_pkgs = [args[i + 1] for i, a in enumerate(args) if a == "--with"]
    assert with_pkgs == ["openai", "numpy", "model2vec"]


def test_reinit_preserves_hand_added_extra_alongside_canonical(tmp_path: Path) -> None:
    """A package the user added themselves survives, appended after the canonical set."""
    existing = {
        "mcpServers": {
            "trace": {
                "command": "uvx",
                "args": ["--from", "/old/TRACE", "--with", "numpy", "--with", "pandas", "--refresh", "trace-mcp"],
            }
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(existing))

    _write_mcp_json(tmp_path)

    args = _read(tmp_path)["mcpServers"]["trace"]["args"]
    with_pkgs = [args[i + 1] for i, a in enumerate(args) if a == "--with"]
    assert with_pkgs == ["openai", "numpy", "model2vec", "pandas"], (
        "canonical extras first, no duplicate of the overlapping one, hand-added package kept"
    )
    assert args[-1] == "trace-mcp"


def test_reinit_preserves_env_block(tmp_path: Path) -> None:
    existing = {
        "mcpServers": {
            "trace": {
                "command": "uvx",
                "args": ["--from", "/old/TRACE", "--refresh-package", "trace-mcp", "trace-mcp"],
                "env": {"TRACE_DEFAULT_PROJECT": "coeqwal"},
            }
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(existing))

    _write_mcp_json(tmp_path)

    assert _read(tmp_path)["mcpServers"]["trace"]["env"] == {"TRACE_DEFAULT_PROJECT": "coeqwal"}


def test_reinit_preserves_unknown_keys(tmp_path: Path) -> None:
    existing = {
        "mcpServers": {
            "trace": {
                "command": "uvx",
                "args": ["--from", "/old/TRACE", "--refresh-package", "trace-mcp", "trace-mcp"],
                "customField": {"keep": True},
            }
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(existing))

    _write_mcp_json(tmp_path)

    assert _read(tmp_path)["mcpServers"]["trace"]["customField"] == {"keep": True}


def test_reinit_is_idempotent(tmp_path: Path) -> None:
    """Re-running init on an already-merged config must be stable (S8 re-inits configs)."""
    existing = {
        "mcpServers": {
            "trace": {
                "command": "uvx",
                "args": ["--from", "/old/TRACE", "--with", "openai", "--refresh", "trace-mcp"],
                "env": {"TRACE_DEFAULT_PROJECT": "coeqwal"},
            }
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(existing))

    _write_mcp_json(tmp_path)
    once = (tmp_path / ".mcp.json").read_text()
    _write_mcp_json(tmp_path)
    twice = (tmp_path / ".mcp.json").read_text()

    assert once == twice


def test_other_servers_preserved(tmp_path: Path) -> None:
    existing = {
        "mcpServers": {
            "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]},
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(existing))

    _write_mcp_json(tmp_path)

    cfg = _read(tmp_path)
    assert cfg["mcpServers"]["playwright"] == {"command": "npx", "args": ["@playwright/mcp@latest"]}
    assert "trace" in cfg["mcpServers"]


def test_unparseable_mcp_json_fails_closed(tmp_path: Path) -> None:
    mcp = tmp_path / ".mcp.json"
    mcp.write_text("this is not json {")
    original = mcp.read_bytes()

    with pytest.raises(TraceInitError):
        _write_mcp_json(tmp_path)

    assert mcp.read_bytes() == original  # file left untouched


def test_non_object_json_fails_closed(tmp_path: Path) -> None:
    mcp = tmp_path / ".mcp.json"
    mcp.write_text("[1, 2, 3]")
    original = mcp.read_bytes()

    with pytest.raises(TraceInitError):
        _write_mcp_json(tmp_path)

    assert mcp.read_bytes() == original


def test_non_object_mcpservers_fails_closed(tmp_path: Path) -> None:
    mcp = tmp_path / ".mcp.json"
    mcp.write_text(json.dumps({"mcpServers": ["not", "a", "dict"]}))
    original = mcp.read_bytes()

    with pytest.raises(TraceInitError):
        _write_mcp_json(tmp_path)

    assert mcp.read_bytes() == original


def test_missing_mcpservers_key_is_created(tmp_path: Path) -> None:
    # A valid JSON object without an mcpServers key is a normal (empty) config.
    (tmp_path / ".mcp.json").write_text(json.dumps({"other": 1}))

    _write_mcp_json(tmp_path)

    cfg = _read(tmp_path)
    assert cfg["other"] == 1
    assert cfg["mcpServers"]["trace"]["command"] == "uvx"


def test_extract_with_packages_dedup_and_tolerant() -> None:
    assert _extract_with_packages(["--with", "a", "--with", "b", "--with", "a"]) == ["a", "b"]
    assert _extract_with_packages("not a list") == []
    assert _extract_with_packages(["--with"]) == []  # trailing --with, no value
    assert _extract_with_packages(["--with", 123]) == []  # non-string value skipped
    assert _extract_with_packages(["--from", "x", "trace-mcp"]) == []


def test_merge_non_dict_existing_returns_fresh() -> None:
    fresh = _mcp_server_config()["trace"]
    assert _merge_trace_entry("garbage", fresh) == fresh
    assert _merge_trace_entry(None, fresh) == fresh


# ── project identity: derivation, enrollment, and the three pin locations ──


def test_label_prefers_pin_file_then_claude_md(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text('TRACE project name: "md-name"\n')
    assert get_project_label(tmp_path) == "md-name"

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "trace.project").write_text("pin-name\n")
    assert get_project_label(tmp_path) == "pin-name"


def test_label_reads_a_bolded_pin_line(tmp_path: Path) -> None:
    """A bolded marker must be readable; missing it is what minted drift pairs."""
    (tmp_path / "CLAUDE.md").write_text('> **TRACE project name**: "bolded-name"\n')
    assert get_project_label(tmp_path) == "bolded-name"


def test_label_falls_back_to_directory_name(tmp_path: Path) -> None:
    project_dir = tmp_path / "some-project"
    project_dir.mkdir()
    assert get_project_label(project_dir) == "some-project"


def test_key_is_canonical_and_explicit_label_wins(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text('TRACE project name: "Ignored Name"\n')
    assert get_project_key(tmp_path, "My Project") == "my-project"
    assert get_project_key(tmp_path) == "ignored-name"


def test_key_rejects_reserved_names(tmp_path: Path) -> None:
    for reserved in ("auto", "AUTO", "shared"):
        with pytest.raises(TraceInitError, match="reserved"):
            get_project_key(tmp_path, reserved)


def test_key_rejects_a_degenerate_label(tmp_path: Path) -> None:
    with pytest.raises(TraceInitError, match="cannot derive"):
        get_project_key(tmp_path, "---")


def test_key_resolves_through_an_existing_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A registered alias wins over bare canonicalization.

    Without this, re-initializing a renamed project would mint a second key
    for it — precisely the split the registry exists to prevent.
    """
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    pident._reset_registry_cache()
    with pident.locked_registry() as registry:
        registry.projects["trace-mcp"] = pident.ProjectEntry(
            key="trace-mcp", display_label="trace-mcp", aliases=["TRACE"]
        )
    pident._reset_registry_cache()

    assert get_project_key(tmp_path, "TRACE") == "trace-mcp"


def test_enroll_is_idempotent_and_records_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    pident._reset_registry_cache()

    assert "enrolled" in _enroll_project("my-project", "My Project")
    pident._reset_registry_cache()
    assert "already enrolled" in _enroll_project("my-project", "My Project")

    pident._reset_registry_cache()
    registry = pident.load_registry(required=False)
    assert registry is not None
    entry = registry.projects["my-project"]
    assert entry.display_label == "My Project"
    assert [h.action for h in registry.history] == ["enroll"]


def test_enroll_fails_closed_on_an_unreadable_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A damaged registry must never be silently replaced."""
    registry_file = tmp_path / "projects.json"
    registry_file.write_text("{not valid json")
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(registry_file))
    pident._reset_registry_cache()

    with pytest.raises(TraceInitError, match="unreadable"):
        _enroll_project("my-project", "My Project")
    assert registry_file.read_text() == "{not valid json", "the damaged registry was overwritten"


def test_write_pin_file_creates_and_is_idempotent(tmp_path: Path) -> None:
    assert "wrote" in _write_pin_file(tmp_path, "my-project")
    pin = tmp_path / ".claude" / "trace.project"
    assert pin.read_text() == "my-project\n"
    assert "skipped" in _write_pin_file(tmp_path, "my-project")
    assert "updated" in _write_pin_file(tmp_path, "other-project")
    assert pin.read_text() == "other-project\n"


def test_claude_pin_line_written_once(tmp_path: Path) -> None:
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Project Instructions\n\nSome guidance.\n")

    assert "updated" in _write_claude_pin_line(tmp_path, "my-project")
    text = claude_md.read_text()
    assert 'TRACE project name: "my-project"' in text
    assert text.startswith("# Project Instructions")

    assert "skipped" in _write_claude_pin_line(tmp_path, "my-project")
    assert claude_md.read_text() == text, "a second pin line was appended"


def test_claude_pin_line_respects_an_existing_bolded_line(tmp_path: Path) -> None:
    """The absence check is bold-tolerant, so a bolded declaration is not duplicated."""
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text('# Project\n\n> **TRACE project name**: "already-named"\n')
    before = claude_md.read_text()

    status = _write_claude_pin_line(tmp_path, "my-project")
    assert "skipped" in status
    assert "already-named" in status
    assert claude_md.read_text() == before


def test_mcp_json_carries_the_env_pin(tmp_path: Path) -> None:
    _write_mcp_json(tmp_path, "my-project")
    entry = _read(tmp_path)["mcpServers"]["trace"]
    assert entry["env"] == {"TRACE_PROJECT": "my-project"}


def test_mcp_json_pin_updates_but_preserves_sibling_env(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "trace": {
                        "command": "uvx",
                        "args": ["--from", "/old", "trace-mcp"],
                        "env": {"TRACE_PROJECT": "stale-name", "OTHER_VAR": "keep-me"},
                    }
                }
            }
        )
    )
    _write_mcp_json(tmp_path, "fresh-name")
    env = _read(tmp_path)["mcpServers"]["trace"]["env"]
    assert env["TRACE_PROJECT"] == "fresh-name", "init must own the pin it wrote"
    assert env["OTHER_VAR"] == "keep-me", "a hand-added env var was dropped"


def test_no_pin_leaves_env_absent(tmp_path: Path) -> None:
    _write_mcp_json(tmp_path)
    assert "env" not in _read(tmp_path)["mcpServers"]["trace"]


def test_init_writes_pin_file_env_and_claude_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: one init makes all three pin locations agree."""
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    pident._reset_registry_cache()
    project_dir = tmp_path / "My Project"
    project_dir.mkdir()

    init_project(str(project_dir), client="none")

    assert (project_dir / ".claude" / "trace.project").read_text() == "my-project\n"
    entry = json.loads((project_dir / ".mcp.json").read_text())["mcpServers"]["trace"]
    assert entry["env"] == {"TRACE_PROJECT": "my-project"}
    assert 'TRACE project name: "my-project"' in (project_dir / "CLAUDE.md").read_text()

    pident._reset_registry_cache()
    registry = pident.load_registry(required=False)
    assert registry is not None
    assert "my-project" in registry.projects


def test_init_project_flag_overrides_derivation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    pident._reset_registry_cache()
    project_dir = tmp_path / "dirname-would-be-this"
    project_dir.mkdir()

    init_project(str(project_dir), client="none", project="Chosen Name")

    assert (project_dir / ".claude" / "trace.project").read_text() == "chosen-name\n"


def test_init_is_idempotent_for_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running init must not mint a second key or a second pin line."""
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    pident._reset_registry_cache()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    init_project(str(project_dir), client="none")
    first_md = (project_dir / "CLAUDE.md").read_text()
    first_mcp = (project_dir / ".mcp.json").read_text()

    pident._reset_registry_cache()
    init_project(str(project_dir), client="none")

    assert (project_dir / "CLAUDE.md").read_text() == first_md
    assert (project_dir / ".mcp.json").read_text() == first_mcp
    pident._reset_registry_cache()
    registry = pident.load_registry(required=False)
    assert registry is not None
    assert list(registry.projects) == ["proj"]
    assert [h.action for h in registry.history] == ["enroll"]


def test_init_dry_run_touches_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry_file = tmp_path / "projects.json"
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(registry_file))
    pident._reset_registry_cache()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    init_project(str(project_dir), client="none", dry_run=True)

    assert not (project_dir / ".claude" / "trace.project").exists()
    assert not (project_dir / ".mcp.json").exists()
    assert not registry_file.exists()


def test_project_key_cli_prints_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "CLAUDE.md").write_text('> **TRACE project name**: "My Project"\n')

    assert print_project_key(str(project_dir)) == 0
    assert capsys.readouterr().out.strip() == "my-project"


def test_project_key_cli_reports_a_bad_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert print_project_key(str(tmp_path / "nope")) == 1
    assert "not a directory" in capsys.readouterr().err


def test_key_lookup_fails_closed_on_an_unreadable_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading identity must fail closed too, not just writing it.

    A corrupt registry cannot be distinguished from "this project has no
    aliases", so guessing the bare canonical key here would silently split a
    renamed project off from its own history.
    """
    registry_file = tmp_path / "projects.json"
    registry_file.write_text("{not valid json")
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(registry_file))
    pident._reset_registry_cache()

    with pytest.raises(TraceInitError, match="unreadable"):
        get_project_key(tmp_path, "My Project")


def test_init_preflights_the_source_before_writing_anything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unresolvable `--from` source must not leave a half-initialized project."""
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.delenv("TRACE_SOURCE_PATH", raising=False)
    monkeypatch.setattr(
        "trace_mcp.init_project._resolve_trace_source",
        lambda: (_ for _ in ()).throw(TraceSourceUnresolvedError("no safe source")),
    )
    pident._reset_registry_cache()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    with pytest.raises(SystemExit):
        init_project(str(project_dir), client="none")

    assert not (project_dir / ".claude" / "trace.project").exists()
    assert not (project_dir / "CLAUDE.md").exists()
    assert not (tmp_path / "projects.json").exists()
