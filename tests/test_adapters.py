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
from trace_mcp.adapters.base import MCP_SERVER_KEY
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
        assert paths["prompt-reminder.sh"] == "installed"
        assert paths["pretool-guard.sh"] == "installed"
        assert paths["decision-audit.sh"] == "installed"
        assert paths["settings.json"] == "installed"
        assert paths["CLAUDE.md"] == "updated"  # pre-existing, now appended
        assert a.validate(tmp_path) == []

    def test_settings_registers_all_four_hook_events(self, tmp_path: Path) -> None:
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        a.install(tmp_path)
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        events = settings["hooks"]
        assert "SessionStart" in events
        assert "UserPromptSubmit" in events
        assert "PreToolUse" in events
        assert "PostToolUse" in events

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
        assert f"mcp__{MCP_SERVER_KEY}__trace_end_session" in matchers

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


# ── _resolve_trace_source ────────────────────────────────────────────────


class TestResolveTraceSource:
    """`_resolve_trace_source` decides what `--from` value lands in .mcp.json.

    Bug fixed in this PR: when `trace-mcp-init` was invoked via `uvx`, the
    naive `Path(__file__).parent.parent.parent` resolved to the uvx cache
    (e.g. `~/.cache/uv/archive-v0/<hash>/lib/python3.13`), producing a
    `.mcp.json` whose `--from` path was unusable for future invocations.
    """

    def test_env_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from trace_mcp.init_project import _resolve_trace_source

        monkeypatch.setenv("TRACE_SOURCE_PATH", "/explicit/path")
        assert _resolve_trace_source() == "/explicit/path"

    def test_env_override_wins_even_inside_site_packages(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from trace_mcp import init_project as ip

        fake = tmp_path / "lib" / "python3.13" / "site-packages" / "trace_mcp" / "init_project.py"
        fake.parent.mkdir(parents=True)
        fake.touch()
        monkeypatch.setattr(ip, "__file__", str(fake))
        monkeypatch.setenv("TRACE_SOURCE_PATH", "/override")

        assert ip._resolve_trace_source() == "/override"

    def test_site_packages_without_override_fails_closed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """An installed wheel must NEVER fall back to the PyPI name 'trace-mcp'.

        That name belongs to an unrelated package, so writing
        `uvx --from trace-mcp` into .mcp.json would make the next MCP server
        start download and execute third-party code (dependency confusion).
        With no TRACE_SOURCE_PATH, resolution must raise — never guess.
        """
        from trace_mcp import init_project as ip

        fake = tmp_path / "lib" / "python3.13" / "site-packages" / "trace_mcp" / "init_project.py"
        fake.parent.mkdir(parents=True)
        fake.touch()
        monkeypatch.setattr(ip, "__file__", str(fake))
        monkeypatch.delenv("TRACE_SOURCE_PATH", raising=False)

        with pytest.raises(ip.TraceSourceUnresolvedError, match="TRACE_SOURCE_PATH"):
            ip._resolve_trace_source()

    def test_init_project_surfaces_unresolvable_source_as_exit_1(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`trace-mcp init` (even --dry-run) exits 1 with the remedy in the message,
        instead of writing a dependency-confused .mcp.json."""
        from trace_mcp import init_project as ip

        fake = tmp_path / "lib" / "python3.13" / "site-packages" / "trace_mcp" / "init_project.py"
        fake.parent.mkdir(parents=True)
        fake.touch()
        monkeypatch.setattr(ip, "__file__", str(fake))
        monkeypatch.delenv("TRACE_SOURCE_PATH", raising=False)

        project = tmp_path / "some-project"
        project.mkdir()

        with pytest.raises(SystemExit) as excinfo:
            ip.init_project(str(project), client="none", dry_run=True)
        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "TRACE_SOURCE_PATH" in out
        assert not (project / ".mcp.json").exists()

    def test_uses_repo_root_for_editable_install(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from trace_mcp import init_project as ip

        repo = tmp_path / "TRACE-checkout"
        fake = repo / "src" / "trace_mcp" / "init_project.py"
        fake.parent.mkdir(parents=True)
        fake.touch()
        monkeypatch.setattr(ip, "__file__", str(fake))
        monkeypatch.delenv("TRACE_SOURCE_PATH", raising=False)

        # Three levels up from src/trace_mcp/init_project.py is the repo root
        assert ip._resolve_trace_source() == str(repo)


# ── Decision-audit matcher (PostToolUse) ──────────────────────────────────


class TestDecisionAuditMatcher:
    """Claude Code hook matchers match the FULL tool name exactly (simple
    strings are not substrings), and MCP tools are namespaced
    ``mcp__<server-key>__<tool>``. A bare ``trace_end_session`` matcher
    therefore never fires — the decision-audit hook was dead in every
    project installed from the old template. The matcher must be derived
    from the ``.mcp.json`` server key the installer itself writes."""

    def _installed_settings(self, tmp_path: Path) -> dict:
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        a.install(tmp_path)
        return json.loads((tmp_path / ".claude" / "settings.json").read_text())

    def test_matcher_is_full_namespaced_tool_name(self, tmp_path: Path) -> None:
        settings = self._installed_settings(tmp_path)
        matchers = [e.get("matcher") for e in settings["hooks"]["PostToolUse"]]
        assert f"mcp__{MCP_SERVER_KEY}__trace_end_session" in matchers
        assert "trace_end_session" not in matchers

    def test_reinstall_migrates_stale_short_matcher(self, tmp_path: Path) -> None:
        """A consumer initialized from the old template carries the dead
        short-matcher entry; re-running install must replace it, not stack a
        second registration beside it."""
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        (tmp_path / ".claude").mkdir()
        stale = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "trace_end_session",
                        "hooks": [
                            {
                                "type": "command",
                                "command": '"$CLAUDE_PROJECT_DIR/.claude/hooks/decision-audit.sh"',
                                "timeout": 10,
                            }
                        ],
                    }
                ]
            }
        }
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps(stale, indent=2))

        a.install(tmp_path)
        merged = json.loads(settings_path.read_text())
        audit_entries = [
            e
            for e in merged["hooks"]["PostToolUse"]
            if any("decision-audit" in h.get("command", "") for h in e.get("hooks", []))
        ]
        assert len(audit_entries) == 1, f"stale matcher entry not migrated: {audit_entries}"
        assert audit_entries[0]["matcher"] == f"mcp__{MCP_SERVER_KEY}__trace_end_session"

    def test_reinstall_preserves_unrelated_posttooluse_entries(self, tmp_path: Path) -> None:
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        (tmp_path / ".claude").mkdir()
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": [
                            {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "my-linter.sh"}]}
                        ]
                    }
                }
            )
        )
        a.install(tmp_path)
        merged = json.loads(settings_path.read_text())
        commands = [h["command"] for e in merged["hooks"]["PostToolUse"] for h in e["hooks"]]
        assert "my-linter.sh" in commands

    def test_init_and_adapter_share_one_server_key(self) -> None:
        """The matcher is only correct if the adapter derives it from the SAME
        key init writes into .mcp.json — a drift here silently kills the hook."""
        from trace_mcp import init_project as ip

        entry = ip._mcp_server_config(None)
        assert set(entry) == {MCP_SERVER_KEY}

    def test_user_tuned_current_entry_is_respected(self, tmp_path: Path) -> None:
        """A user who raised the timeout on the CURRENT registration keeps their
        tuning — reinstall neither resets nor duplicates it."""
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        (tmp_path / ".claude").mkdir()
        tuned = {
            "matcher": f"mcp__{MCP_SERVER_KEY}__trace_end_session",
            "hooks": [
                {
                    "type": "command",
                    "command": '"$CLAUDE_PROJECT_DIR/.claude/hooks/decision-audit.sh"',
                    "timeout": 60,
                }
            ],
        }
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"hooks": {"PostToolUse": [tuned]}}))

        a.install(tmp_path)
        merged = json.loads(settings_path.read_text())
        audit_entries = [
            e
            for e in merged["hooks"]["PostToolUse"]
            if any("decision-audit" in h.get("command", "") for h in e.get("hooks", []))
        ]
        assert len(audit_entries) == 1, f"user-tuned entry duplicated or removed: {audit_entries}"
        assert audit_entries[0]["hooks"][0]["timeout"] == 60

    def test_stale_entry_removed_even_when_desired_already_present(self, tmp_path: Path) -> None:
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        (tmp_path / ".claude").mkdir()
        cmd = '"$CLAUDE_PROJECT_DIR/.claude/hooks/decision-audit.sh"'
        stale = {"matcher": "trace_end_session", "hooks": [{"type": "command", "command": cmd, "timeout": 10}]}
        fixed = {
            "matcher": f"mcp__{MCP_SERVER_KEY}__trace_end_session",
            "hooks": [{"type": "command", "command": cmd, "timeout": 10}],
        }
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"hooks": {"PostToolUse": [stale, fixed]}}))

        a.install(tmp_path)
        merged = json.loads(settings_path.read_text())
        audit_entries = [
            e
            for e in merged["hooks"]["PostToolUse"]
            if any("decision-audit" in h.get("command", "") for h in e.get("hooks", []))
        ]
        assert len(audit_entries) == 1, f"stale entry survived beside the fixed one: {audit_entries}"

    def test_user_authored_namespaced_registration_preserved(self, tmp_path: Path) -> None:
        """A user who wired the installer's script under their OWN server key's
        namespace (e.g. a renamed server entry) keeps that registration — only
        the exact matcher form historical templates shipped is migrated."""
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        (tmp_path / ".claude").mkdir()
        user_entry = {
            "matcher": "mcp__trace-prod__trace_end_session",
            "hooks": [
                {
                    "type": "command",
                    "command": '"$CLAUDE_PROJECT_DIR/.claude/hooks/decision-audit.sh"',
                    "timeout": 10,
                }
            ],
        }
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"hooks": {"PostToolUse": [user_entry]}}))

        a.install(tmp_path)
        merged = json.loads(settings_path.read_text())
        matchers = [e.get("matcher") for e in merged["hooks"]["PostToolUse"] if isinstance(e, dict)]
        assert "mcp__trace-prod__trace_end_session" in matchers, "user-authored registration was deleted"
        assert f"mcp__{MCP_SERVER_KEY}__trace_end_session" in matchers

    def test_malformed_settings_entries_do_not_crash_install(self, tmp_path: Path) -> None:
        """Settings files are user-edited JSON: non-dict entries and malformed
        hooks values must be skipped in place, never dereferenced."""
        a = ClaudeCodeAdapter()
        (tmp_path / "CLAUDE.md").write_text("# Example\n")
        (tmp_path / ".claude").mkdir()
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": ["not-a-dict", 7, {"matcher": "X", "hooks": "nope"}],
                        "SessionStart": "also-not-a-list",
                    }
                }
            )
        )
        a.install(tmp_path)  # must not raise
        merged = json.loads(settings_path.read_text())
        assert "not-a-dict" in merged["hooks"]["PostToolUse"]  # left in place
        matchers = [e.get("matcher") for e in merged["hooks"]["PostToolUse"] if isinstance(e, dict)]
        assert f"mcp__{MCP_SERVER_KEY}__trace_end_session" in matchers
        assert merged["hooks"]["SessionStart"] == "also-not-a-list"  # malformed event untouched
