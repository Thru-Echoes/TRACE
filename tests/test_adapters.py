"""Tests for host adapters (Claude Code, Codex).

The adapter layer is a pure installer — these tests exercise the filesystem
side effects in temp directories, not the MCP server.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from trace_mcp.adapters import detect_adapter, get_adapter, list_adapters
from trace_mcp.adapters.claude_code import MARKER_END, MARKER_START, ClaudeCodeAdapter
from trace_mcp.adapters.codex import CodexAdapter

# ── Registry ──────────────────────────────────────────────────────────────


class TestRegistry:
    def test_list_adapters_includes_claude_code_and_codex(self) -> None:
        names = list_adapters()
        assert "claude-code" in names
        assert "codex" in names

    def test_get_adapter_returns_instance(self) -> None:
        a = get_adapter("claude-code")
        assert isinstance(a, ClaudeCodeAdapter)

    def test_get_adapter_unknown_raises(self) -> None:
        with pytest.raises(KeyError):
            get_adapter("nonexistent-host")

    def test_detect_adapter_claude_code(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("# Project\n")
        a = detect_adapter(tmp_path)
        assert a is not None
        assert a.name == "claude-code"

    def test_detect_adapter_none_when_no_match(self, tmp_path: Path) -> None:
        # Empty dir — neither .claude/ nor CLAUDE.md — no auto-detect
        assert detect_adapter(tmp_path) is None


# ── Claude Code adapter ───────────────────────────────────────────────────


class TestClaudeCodeInstall:
    def test_detect_matches_claude_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        assert ClaudeCodeAdapter().detect(tmp_path) is True

    def test_detect_matches_claude_md(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("hi")
        assert ClaudeCodeAdapter().detect(tmp_path) is True

    def test_detect_false_on_empty_dir(self, tmp_path: Path) -> None:
        assert ClaudeCodeAdapter().detect(tmp_path) is False

    def test_install_fresh_dir_writes_everything(self, tmp_path: Path) -> None:
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        results = a.install(tmp_path)
        paths = {r.path.name: r.disposition for r in results}
        assert paths["session-reminder.sh"] == "installed"
        assert paths["decision-audit.sh"] == "installed"
        assert paths["settings.json"] == "installed"
        assert paths["CLAUDE.md"] == "updated"  # pre-existing, now appended
        assert a.validate(tmp_path) == []

    def test_install_creates_missing_claude_md(self, tmp_path: Path) -> None:
        a = ClaudeCodeAdapter()
        (tmp_path / ".claude").mkdir()
        a.install(tmp_path)
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.is_file()
        content = claude_md.read_text()
        assert MARKER_START in content
        assert MARKER_END in content

    def test_install_idempotent(self, tmp_path: Path) -> None:
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        a.install(tmp_path)
        second = a.install(tmp_path)
        for r in second:
            assert r.disposition == "skipped"

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        a.install(tmp_path, dry_run=True)
        assert not (tmp_path / ".claude" / "hooks").exists()
        assert not (tmp_path / ".claude" / "settings.json").exists()
        assert MARKER_START not in (tmp_path / "CLAUDE.md").read_text()

    def test_hook_scripts_are_executable(self, tmp_path: Path) -> None:
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        a.install(tmp_path)
        for script in (tmp_path / ".claude" / "hooks").glob("*.sh"):
            mode = script.stat().st_mode
            assert mode & stat.S_IXUSR, f"{script} is not executable"

    def test_settings_merge_preserves_existing_hooks(self, tmp_path: Path) -> None:
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        (tmp_path / ".claude").mkdir()
        existing = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Edit|Write",
                        "hooks": [{"type": "command", "command": "my-linter.sh"}],
                    }
                ]
            },
            "permissions": {"allow": ["Bash(git *)"]},
        }
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps(existing, indent=2))

        a.install(tmp_path)
        merged = json.loads(settings_path.read_text())

        # Existing permissions preserved
        assert merged["permissions"] == {"allow": ["Bash(git *)"]}

        # Existing PostToolUse entry preserved; TRACE entry appended
        post = merged["hooks"]["PostToolUse"]
        assert len(post) == 2
        matchers = [entry["matcher"] for entry in post]
        assert "Edit|Write" in matchers
        assert "trace_end_session" in matchers

        # SessionStart added
        assert "SessionStart" in merged["hooks"]

    def test_validate_reports_missing_hooks(self, tmp_path: Path) -> None:
        a = ClaudeCodeAdapter()
        # Nothing installed
        errors = a.validate(tmp_path)
        assert errors, "validate should report errors on empty dir"
        assert any("session-reminder.sh" in e for e in errors)

    def test_validate_reports_invalid_settings_json(self, tmp_path: Path) -> None:
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        a.install(tmp_path)
        # Corrupt the settings file
        (tmp_path / ".claude" / "settings.json").write_text("{not valid json")
        errors = a.validate(tmp_path)
        assert any("not valid JSON" in e for e in errors)

    def test_install_updates_changed_hook_script(self, tmp_path: Path) -> None:
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        a.install(tmp_path)
        # User tampers with the hook
        hook = tmp_path / ".claude" / "hooks" / "session-reminder.sh"
        original = hook.read_text()
        hook.write_text("#!/bin/bash\necho stale\n")
        # Re-install should replace it
        results = a.install(tmp_path)
        dispositions = {r.path.name: r.disposition for r in results}
        assert dispositions["session-reminder.sh"] == "updated"
        assert hook.read_text() == original


# ── Codex adapter ─────────────────────────────────────────────────────────


class TestCodexAdapter:
    def test_detect_always_false(self, tmp_path: Path) -> None:
        a = CodexAdapter()
        assert a.detect(tmp_path) is False
        # Even with .codex/ present (it's the expected dir) — detect is still
        # False because the adapter isn't ready; auto-detect should not pick it.
        (tmp_path / ".codex").mkdir()
        assert a.detect(tmp_path) is False

    def test_install_raises_not_implemented(self, tmp_path: Path) -> None:
        a = CodexAdapter()
        with pytest.raises(NotImplementedError):
            a.install(tmp_path)

    def test_validate_returns_placeholder_note(self, tmp_path: Path) -> None:
        a = CodexAdapter()
        errors = a.validate(tmp_path)
        assert errors == ["codex adapter is a placeholder — nothing to validate"]


# ── init_project integration ──────────────────────────────────────────────


class TestInitProjectDispatch:
    """init_project.py should dispatch to the right adapter and write .mcp.json."""

    def test_init_dry_run(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from trace_mcp.init_project import init_project

        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        init_project(str(tmp_path), client="claude-code", dry_run=True)

        # dry-run writes no files
        assert not (tmp_path / ".mcp.json").exists()
        assert not (tmp_path / ".claude" / "settings.json").exists()
        captured = capsys.readouterr().out
        assert "dry-run" in captured.lower()

    def test_init_writes_mcp_json(self, tmp_path: Path) -> None:
        from trace_mcp.init_project import init_project

        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        init_project(str(tmp_path), client="claude-code")

        mcp = json.loads((tmp_path / ".mcp.json").read_text())
        assert "trace" in mcp["mcpServers"]
        assert mcp["mcpServers"]["trace"]["command"] == "uvx"

    def test_init_none_skips_adapter(self, tmp_path: Path) -> None:
        from trace_mcp.init_project import init_project

        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        init_project(str(tmp_path), client="none")

        assert (tmp_path / ".mcp.json").exists()
        # Nothing host-specific written
        assert not (tmp_path / ".claude" / "hooks").exists()
