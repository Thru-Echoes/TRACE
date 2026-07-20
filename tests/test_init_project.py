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

from trace_mcp.init_project import (
    TraceInitError,
    _extract_with_packages,
    _mcp_server_config,
    _merge_trace_entry,
    _write_mcp_json,
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
    assert entry["args"] == ["--from", _SOURCE, "--refresh-package", "trace-mcp", "trace-mcp"]
    assert "env" not in entry  # no empty env block on a fresh install


def test_reinit_preserves_with_extras(tmp_path: Path) -> None:
    existing = {
        "mcpServers": {
            "trace": {
                "command": "uvx",
                "args": [
                    "--from", "/old/TRACE",
                    "--with", "openai",
                    "--with", "numpy",
                    "--with", "model2vec",
                    "--refresh", "trace-mcp",
                ],
            }
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(existing))

    status = _write_mcp_json(tmp_path)
    assert "updated" in status

    args = _read(tmp_path)["mcpServers"]["trace"]["args"]
    # Canonical source is rebuilt (old /old/TRACE gone), the command stays last,
    # and the hand-added extras are preserved in order.
    assert args[:4] == ["--from", _SOURCE, "--refresh-package", "trace-mcp"]
    assert args[-1] == "trace-mcp"
    with_pkgs = [args[i + 1] for i, a in enumerate(args) if a == "--with"]
    assert with_pkgs == ["openai", "numpy", "model2vec"]


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
