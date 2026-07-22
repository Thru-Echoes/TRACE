"""Anti-divergence guard for the bundled Claude Code hook scripts.

All four hooks embed one shared canonical-identity block. Divergence between
copies is not hypothetical: a pin-line regex that did not tolerate markdown
bold once made one hook read a repository's CLAUDE.md project name while
another fell through to the git basename, minting two labels — and therefore
two knowledge stores and two session pools — for a single repository.

These tests read the shipped assets as text and assert structural properties a
future edit could silently break: the block is byte-identical everywhere, the
embedded canonicalizer still agrees with the core implementation, every hook
stamps its version, and the project filter has not regressed to picking the
globally newest session.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from trace_mcp.project_identity import ProjectKeyError, canonical_project_key

_HOOKS_DIR = Path(__file__).parent.parent / "src" / "trace_mcp" / "adapters" / "claude_code" / "assets" / "hooks"

HOOK_NAMES = [
    "session-reminder.sh",
    "prompt-reminder.sh",
    "pretool-guard.sh",
    "decision-audit.sh",
]

BLOCK_BEGIN = "# --- trace-project-detect v0.5 begin ---"
BLOCK_END = "# --- trace-project-detect v0.5 end ---"

VERSION_STAMP = "[trace-hooks v0.5]"


def _hook_text(name: str) -> str:
    return (_HOOKS_DIR / name).read_text(encoding="utf-8")


def _extract_block(text: str, name: str) -> str:
    """Return the shared detection block, delimiters included."""
    start = text.find(BLOCK_BEGIN)
    end = text.find(BLOCK_END)
    assert start != -1, f"{name} has no shared-block begin delimiter {BLOCK_BEGIN!r}"
    assert end != -1, f"{name} has no shared-block end delimiter {BLOCK_END!r}"
    assert start < end, f"{name} has the shared-block delimiters in the wrong order"
    return text[start : end + len(BLOCK_END)]


def test_all_four_hooks_exist() -> None:
    """Positive control: the guard is blind if the assets move or get renamed."""
    for name in HOOK_NAMES:
        assert (_HOOKS_DIR / name).is_file(), f"missing hook asset {name}"


def test_detection_block_byte_identical_across_four_hooks() -> None:
    """The shared block must be one text, not four that merely resemble each other."""
    blocks = {name: _extract_block(_hook_text(name), name) for name in HOOK_NAMES}
    reference_name = HOOK_NAMES[0]
    reference = blocks[reference_name]
    assert len(reference) > 500, "extracted block is implausibly short — check the delimiters"
    for name, block in blocks.items():
        assert block == reference, (
            f"{name}'s shared detection block differs from {reference_name}'s. "
            "Edit every copy together — divergence here is what mints two project "
            "labels for one repository."
        )


def test_all_hooks_emit_version_stamp() -> None:
    """Every emitted message carries the stamp, so a stale fleet copy self-identifies."""
    for name in HOOK_NAMES:
        assert VERSION_STAMP in _hook_text(name), f"{name} emits no {VERSION_STAMP} version stamp"


def test_decision_audit_has_project_filter_no_bare_ls_t() -> None:
    """decision-audit must not go back to selecting the globally newest session."""
    text = _hook_text("decision-audit.sh")
    assert "ls -t" not in text, (
        "decision-audit.sh selects the newest session with `ls -t` again — that "
        "ignores the project and reports another project's audit findings whenever "
        "two servers run side by side."
    )
    assert "newest_for_project" in text, "decision-audit.sh no longer filters sessions by project key"
    assert "CLAUDE_PROJECT_DIR" in text, "decision-audit.sh no longer resolves a project directory"


def test_no_hook_defines_a_private_sanitizer() -> None:
    """A second name-folding function is exactly the divergence this PR removed."""
    for name in HOOK_NAMES:
        assert "_sanitize" not in _hook_text(name), (
            f"{name} defines a private sanitizer again — fold names through "
            "canonical_key in the shared block so filenames and identity agree."
        )


def _run_embedded_canonicalizer(labels: list[str]) -> list[str]:
    """Execute the shipped block in a fresh interpreter and canonicalize *labels*.

    Runs the real asset text rather than a copy, so the test measures what the
    hooks actually do.
    """
    block = _extract_block(_hook_text(HOOK_NAMES[0]), HOOK_NAMES[0])
    program = block + "\n\nimport json, sys\nprint(json.dumps([canonical_key(x) for x in json.loads(sys.argv[1])]))\n"
    import json

    result = subprocess.run(
        [sys.executable, "-c", program, json.dumps(labels)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


# A corpus spanning the drift classes ADR-006 closes: case variants, separator
# variants, unicode, punctuation runs, and degenerate input.
_LABEL_CORPUS = [
    "trace-mcp",
    "TRACE",
    "TRACE-mcp",
    "trace_mcp",
    "trace mcp",
    "trace/mcp",
    "  Trace   MCP  ",
    "my/weird name",
    "COEQWAL",
    "coeqwal-website",
    "green-narrative",
    "hye_in",
    "When-Algorithms-Meet-Artists",
    "café-project",
    "Ünicode_Näme",
    "proj..name",
    "proj--name",
    "---leading-and-trailing---",
    "...dots...",
    "a",
    "PROJECT 2026.07",
    "emoji-🎯-project",
]

_DEGENERATE_LABELS = ["", "   ", "---", "...", "///", "___"]


def test_embedded_canonicalizer_matches_core() -> None:
    """The hooks' canonicalizer must agree with canonical_project_key exactly.

    The hooks cannot import trace_mcp (they run as bare python3 with no
    installed package), so the algorithm is duplicated. This is the guard that
    keeps the duplicate honest — a divergence here splits one project's
    sessions between the hooks' view and the server's.
    """
    got = _run_embedded_canonicalizer(_LABEL_CORPUS)
    expected = [canonical_project_key(label) for label in _LABEL_CORPUS]
    assert got == expected, "embedded canonicalizer diverged from canonical_project_key"


def test_embedded_canonicalizer_returns_empty_where_core_raises() -> None:
    """Degenerate labels yield "" in a hook where the core raises.

    A hook advises; it must never fail the tool call it runs alongside. The
    empty key then matches no session, which is the safe outcome.
    """
    got = _run_embedded_canonicalizer(_DEGENERATE_LABELS)
    assert got == [""] * len(_DEGENERATE_LABELS)
    for label in _DEGENERATE_LABELS:
        with pytest.raises(ProjectKeyError):
            canonical_project_key(label)


@pytest.mark.parametrize(
    "line",
    [
        'TRACE project name: "trace-mcp"',
        '**TRACE project name**: "trace-mcp"',
        '> **TRACE project name**: "trace-mcp"',
        '- **TRACE project name**: "trace-mcp"',
        'TRACE project name:  "trace-mcp"',
        '**TRACE project name** : "trace-mcp"',
    ],
)
def test_regex_matches_plain_and_bolded_pin_lines(line: str) -> None:
    """The shipped pin regex must accept bolded forms.

    The plain-only regex is the verified mechanical cause of a live drift pair:
    a model reading the bolded marker saw one name while the hooks fell through
    to the git basename and saw another.
    """
    block = _extract_block(_hook_text(HOOK_NAMES[0]), HOOK_NAMES[0])
    match = re.search(r"^_PIN_LINE = re\.compile\((r'[^']+')\)$", block, re.MULTILINE)
    assert match, "could not find _PIN_LINE in the shared block"
    pattern = re.compile(eval(match.group(1)))  # noqa: S307 - literal from our own asset
    found = pattern.search(line)
    assert found, f"pin regex did not match {line!r}"
    assert found.group(1) == "trace-mcp"
