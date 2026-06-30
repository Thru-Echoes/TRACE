"""Invariant enumeration guard — mechanically enforces docs/INVARIANTS.md.

Serious data-integrity defects in this codebase share one shape: *an invariant
enforced in one place but not uniformly*. The durable fix for that defect CLASS
is to name each invariant, enumerate its sites once, and fail CI the moment a
NEW site violates it — so the gap cannot silently reappear between manual
audits. This generalizes ``tests/test_v041_core_extension_boundary.py`` (already
in CI), which guards the core/extension boundary the same way.

These are static (AST) structural assertions over ``src/trace_mcp``; they pass
on correct code and fail loudly when someone adds an unguarded write path or
drops a validation round-trip. See docs/INVARIANTS.md for the human-readable
registry each test enforces.
"""

from __future__ import annotations

import ast
from pathlib import Path

import trace_mcp

SRC = Path(trace_mcp.__file__).resolve().parent

# ── INV-1: every session WRITE routes through locked_disk_session ─────────
# The registered write-path functions, as (module-relative-path, function).
# Adding a new function that calls storage.update_session WITHOUT registering
# it here (and routing it through the fail-closed locked helper) fails the
# first test below — forcing the author to honor INV-1 or consciously amend it.
INV1_REGISTERED_WRITERS = {
    ("tools/session_tools.py", "append_event"),
    ("tools/session_tools.py", "end_session"),
    ("tools/decision_tools.py", "resolve_decision"),
}


def _src_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _rel(p: Path) -> str:
    return p.relative_to(SRC).as_posix()


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _functions_calling(callee: str) -> set[tuple[str, str]]:
    """Every (relpath, function-name) across src/ whose body calls ``callee()``.

    Only call *expressions* count — a function that merely *defines* ``callee``
    (e.g. the storage backend defining ``update_session``) is not a caller.
    """
    found: set[tuple[str, str]] = set()
    for path in _src_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if any(isinstance(sub, ast.Call) and _call_name(sub) == callee for sub in ast.walk(node)):
                    found.add((_rel(path), node.name))
    return found


def test_inv1_no_unregistered_session_writer() -> None:
    """No function may call ``storage.update_session`` unless it is a registered
    INV-1 write path. A new, unregistered writer is exactly how the H1/H2
    immutability gaps arose."""
    writers = _functions_calling("update_session")
    unregistered = writers - INV1_REGISTERED_WRITERS
    assert not unregistered, (
        "INV-1 violation (docs/INVARIANTS.md): these functions write sessions but are "
        f"not registered write paths: {sorted(unregistered)}. Route the write through "
        "storage.locked.locked_disk_session and add it to INV1_REGISTERED_WRITERS "
        "(and docs/INVARIANTS.md)."
    )


def test_inv1_registered_writers_use_the_locked_helper() -> None:
    """Each registered INV-1 writer must actually route through the fail-closed
    ``locked_disk_session`` helper (not hand-roll its own lock block)."""
    helper_users = _functions_calling("locked_disk_session")
    missing = INV1_REGISTERED_WRITERS - helper_users
    assert not missing, (
        f"INV-1 violation: registered write paths that do NOT route through locked_disk_session: {sorted(missing)}."
    )


def test_inv3_resolve_decision_validates_before_write() -> None:
    """INV-3: ``resolve_decision`` must round-trip the decision through
    ``model_validate`` (the C1 guarantee) — never assignment-bypass that could
    write an invalid disposition and brick the session file."""
    validators = _functions_calling("model_validate")
    assert ("tools/decision_tools.py", "resolve_decision") in validators, (
        "INV-3 violation: resolve_decision no longer validates the decision via "
        "model_validate before writing — an invalid disposition could reach disk."
    )
