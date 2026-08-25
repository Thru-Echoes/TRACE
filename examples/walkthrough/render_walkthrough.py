"""Render the walkthrough scenario to the committed ``WALKTHROUGH.md``.

`render_markdown` is pure — same scenario in, same bytes out, no timestamps or
environment — so a test can re-render and diff against the committed file to
prove the doc has not drifted from the scenario. Running this module as a script
rewrites the file.

Usage:
    python examples/walkthrough/render_walkthrough.py

Exports:
    WALKTHROUGH_PATH   the committed markdown file this renders to
    render_markdown    scenario -> markdown string (pure)
    main               rewrite WALKTHROUGH_PATH from the current scenario
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

# Runnable as a plain script (`python examples/walkthrough/render_walkthrough.py`):
# put the repo root on the path so the `examples` package imports either way.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from examples.walkthrough.scenario import PROJECT, SESSION_ID_PLACEHOLDER, STEPS, Step  # noqa: E402

WALKTHROUGH_PATH = Path(__file__).resolve().parent / "WALKTHROUGH.md"

_PREAMBLE = f"""<!-- GENERATED FILE — do not edit by hand.
Regenerate with: python examples/walkthrough/render_walkthrough.py
Source of truth: examples/walkthrough/scenario.py -->

# TRACE walkthrough

A single pass through the TRACE provenance loop, from opening a session to a
cross-project denial. Every step below is one MCP tool call and the response to
look for. This same scenario is replayed against a live server by
`tests/test_walkthrough_e2e.py`, which asserts the responses shown here, so what
you read is what the server does.

To follow along hermetically — never touching your real `~/.trace` — launch a
server pinned to the `{PROJECT}` project with every data path redirected to a
fresh scratch location (knowledge store, sessions, scratchpads, the egress
ledger, and the registry):

```bash
DIR="$(mktemp -d)"; mkdir -p "$DIR/knowledge"
TRACE_PROJECT={PROJECT} \\
  TRACE_KNOWLEDGE_DIR="$DIR/knowledge" \\
  TRACE_SESSIONS_DIR="$DIR/sessions" \\
  TRACE_SCRATCHPAD_DIR="$DIR/scratchpads" \\
  TRACE_EGRESS_LOG="$DIR/egress.log" \\
  TRACE_REGISTRY_PATH="$DIR/projects.json" \\
  TRACE_EMBEDDING_BACKEND=none TRACE_LLM_ENABLED=false \\
  trace-mcp
```

`{SESSION_ID_PLACEHOLDER}` stands for the session id printed in step 1;
substitute the real one as you go.
"""


def _format_call(step: Step) -> str:
    """A tool call rendered as a compact, copy-pasteable JSON block."""
    payload = {"tool": step.tool, "arguments": step.arguments}
    return json.dumps(payload, indent=2, ensure_ascii=False)


def render_markdown(steps: Sequence[Step]) -> str:
    """Render *steps* to the walkthrough markdown. Pure: no time, no environment."""
    parts: list[str] = [_PREAMBLE.rstrip()]
    for i, step in enumerate(steps, start=1):
        expectations = "\n".join(f"- `{needle}`" for needle in step.expect_substrings)
        parts.append(
            f"## Step {i} — {step.name}\n\n"
            f"{step.narration}\n\n"
            f"```json\n{_format_call(step)}\n```\n\n"
            f"Look for in the response:\n\n{expectations}"
        )
    return "\n\n".join(parts) + "\n"


def main() -> None:
    """Rewrite WALKTHROUGH_PATH from the current scenario. Side effect: writes a file.

    Writes explicit LF bytes (``newline=""`` disables platform translation) so the
    committed file is identical on every OS and the byte-for-byte sync test holds.
    """
    WALKTHROUGH_PATH.write_text(render_markdown(STEPS), encoding="utf-8", newline="")
    print(f"wrote {WALKTHROUGH_PATH} ({len(STEPS)} steps)")


if __name__ == "__main__":
    main()
