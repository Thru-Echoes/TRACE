"""Refreshing the TRACE instruction block in a consumer's CLAUDE.md.

An installed block used to be left alone forever: the installer returned
`skipped` the moment it saw the opening marker. Every edit to the template
therefore reached only projects that did not yet have a block, which in a
deployed fleet is none of them. Measured on ten real consumer projects, a
protocol change reached zero of them, across two distinct block vintages, while
`trace-mcp doctor` reported all ten clean.

That is the shape the hook scripts already guard against with a version stamp
and a `hooks.stamp` check. These tests pin the same treatment for the block: it
is stamped with a digest of the shipped template, the installer replaces the
marked region when the stamp differs, and the doctor reports a block that is not
this build's.

The constraint that shapes the implementation: a consumer's CLAUDE.md holds
their own instructions around the block — 157 lines of them in one real project.
Only the marked region may ever be touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trace_mcp.adapters.claude_code import (
    MARKER_END,
    MARKER_START,
    ClaudeCodeAdapter,
    get_block_stamp,
    read_installed_block_stamp,
    render_claude_block,
)
from trace_mcp.conformance.probes import check_instruction_block

OWN_HEADER = "# My Project\n\nMy own instructions that are not TRACE's to rewrite.\n"
OWN_FOOTER = "\n## After the block\n\nMore of my own text.\n"


def _install(project: Path) -> str:
    ClaudeCodeAdapter().install(project, dry_run=False)
    return (project / "CLAUDE.md").read_text()


def _legacy_block(body: str = "\n## TRACE Audit Protocol (old)\n\nOutdated guidance.\n") -> str:
    """A block as installed before stamping existed: bare opening marker."""
    return f"{MARKER_START}{body}{MARKER_END}\n"


class TestStamping:
    def test_the_stamp_tracks_the_template_contents(self) -> None:
        """Any edit to the asset must change the stamp, or staleness hides again."""
        assert read_installed_block_stamp(render_claude_block()) == get_block_stamp()

    def test_an_unstamped_block_reads_as_no_stamp(self) -> None:
        assert read_installed_block_stamp(_legacy_block()) is None

    def test_text_with_no_block_reads_as_no_stamp(self) -> None:
        assert read_installed_block_stamp(OWN_HEADER) is None


class TestRefresh:
    def test_a_legacy_block_is_replaced(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(OWN_HEADER + "\n" + _legacy_block())

        result = _install(tmp_path)

        assert "Outdated guidance." not in result
        assert read_installed_block_stamp(result) == get_block_stamp()

    def test_the_projects_own_text_is_preserved_on_both_sides(self, tmp_path: Path) -> None:
        """The reason the markers exist: a consumer writes in this file too."""
        (tmp_path / "CLAUDE.md").write_text(OWN_HEADER + "\n" + _legacy_block() + OWN_FOOTER)

        result = _install(tmp_path)

        assert result.startswith(OWN_HEADER)
        assert result.endswith(OWN_FOOTER)
        assert "More of my own text." in result

    def test_exactly_one_block_survives_a_refresh(self, tmp_path: Path) -> None:
        """The old bug's other face: appending a second block beside the first."""
        (tmp_path / "CLAUDE.md").write_text(OWN_HEADER + "\n" + _legacy_block())

        result = _install(tmp_path)

        assert result.count(MARKER_END) == 1

    def test_refreshing_is_idempotent(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(OWN_HEADER + "\n" + _legacy_block())
        first = _install(tmp_path)
        second = _install(tmp_path)

        assert first == second

    def test_repeated_refreshes_do_not_grow_the_file(self, tmp_path: Path) -> None:
        """A refresh reflows the marked region; it must not accumulate blank lines.

        Caught on a real consumer whose file continues after the block: the
        asset's own trailing newline was added on top of the one the following
        text already supplied, so every run left one more blank line behind.
        """
        (tmp_path / "CLAUDE.md").write_text(OWN_HEADER + "\n" + _legacy_block() + OWN_FOOTER)

        first = _install(tmp_path)
        second = _install(tmp_path)
        ClaudeCodeAdapter().install(tmp_path, dry_run=False)
        third = (tmp_path / "CLAUDE.md").read_text()

        assert first == second == third
        assert "\n\n\n" not in first

    def test_a_current_block_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(OWN_HEADER + "\n" + render_claude_block())
        before = (tmp_path / "CLAUDE.md").read_text()

        results = ClaudeCodeAdapter().install(tmp_path, dry_run=False)
        claude_results = [r for r in results if r.path.name == "CLAUDE.md"]

        assert (tmp_path / "CLAUDE.md").read_text() == before
        assert any(r.disposition == "skipped" for r in claude_results)

    def test_a_file_with_no_block_gets_one_appended(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(OWN_HEADER)

        result = _install(tmp_path)

        assert result.startswith(OWN_HEADER)
        assert read_installed_block_stamp(result) == get_block_stamp()

    def test_a_missing_file_is_created(self, tmp_path: Path) -> None:
        result = _install(tmp_path)

        assert read_installed_block_stamp(result) == get_block_stamp()

    def test_an_unterminated_block_is_left_alone(self, tmp_path: Path) -> None:
        """Guessing the block's extent could eat a project's own instructions."""
        damaged = OWN_HEADER + "\n" + MARKER_START + "\n## Half a block, no closing marker\n"
        (tmp_path / "CLAUDE.md").write_text(damaged)

        result = _install(tmp_path)

        assert result == damaged

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        original = OWN_HEADER + "\n" + _legacy_block()
        (tmp_path / "CLAUDE.md").write_text(original)

        ClaudeCodeAdapter().install(tmp_path, dry_run=True)

        assert (tmp_path / "CLAUDE.md").read_text() == original


class TestDoctorReportsStaleness:
    def test_a_current_block_passes(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(render_claude_block())

        findings = check_instruction_block(tmp_path)

        assert [f.status for f in findings] == ["pass"]

    def test_an_unstamped_block_fails_and_names_the_remedy(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(_legacy_block())

        findings = check_instruction_block(tmp_path)

        assert findings[0].status == "fail"
        assert "unstamped" in findings[0].detail
        assert "trace-mcp-init" in findings[0].detail

    def test_a_differently_stamped_block_fails(self, tmp_path: Path) -> None:
        stale = render_claude_block().replace(f"block={get_block_stamp()}", "block=0123456789ab", 1)
        (tmp_path / "CLAUDE.md").write_text(stale)

        findings = check_instruction_block(tmp_path)

        assert findings[0].status == "fail"
        assert "0123456789ab" in findings[0].detail
        assert get_block_stamp() in findings[0].detail

    def test_no_block_at_all_fails(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(OWN_HEADER)

        findings = check_instruction_block(tmp_path)

        assert findings[0].status == "fail"
        assert "no TRACE instruction block" in findings[0].detail

    def test_an_unterminated_block_fails(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(MARKER_START + "\n## no closing marker\n")

        findings = check_instruction_block(tmp_path)

        assert findings[0].status == "fail"
        assert "closing" in findings[0].detail

    def test_a_missing_file_is_not_evaluated(self, tmp_path: Path) -> None:
        """Absent CLAUDE.md is another check's business, not this one's."""
        findings = check_instruction_block(tmp_path)

        assert [f.status for f in findings] == ["skip"]

    def test_the_check_id_is_always_emitted(self, tmp_path: Path) -> None:
        """Check ids are stable API; one must never silently vanish."""
        for setup in (lambda: None, lambda: (tmp_path / "CLAUDE.md").write_text(OWN_HEADER)):
            setup()
            assert [f.check for f in check_instruction_block(tmp_path)] == ["docs.block_stamp"]


class TestInstallerAndCheckerAgree:
    """A freshly installed project must satisfy the checker (INV-11's premise)."""

    def test_install_then_check_is_clean(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text(OWN_HEADER + "\n" + _legacy_block())
        _install(tmp_path)

        findings = check_instruction_block(tmp_path)

        assert [f.status for f in findings] == ["pass"], findings[0].detail

    @pytest.mark.parametrize(
        "starting",
        [
            "",
            OWN_HEADER,
            OWN_HEADER + "\n" + _legacy_block(),
            OWN_HEADER + "\n" + _legacy_block() + OWN_FOOTER,
        ],
        ids=["no-file", "no-block", "legacy-block", "legacy-block-with-trailing-text"],
    )
    def test_every_starting_state_ends_current(self, tmp_path: Path, starting: str) -> None:
        if starting:
            (tmp_path / "CLAUDE.md").write_text(starting)

        _install(tmp_path)

        assert [f.status for f in check_instruction_block(tmp_path)] == ["pass"]
