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
    # ADR-006 S6: `adopt` re-homes an auto session into a real project. It is a
    # session write (stamps project_key + appends a state_change) and therefore
    # routes through locked_disk_session like every other writer.
    ("identity_cli.py", "adopt_session"),
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


# ── INV-4: project scoping matches by CANONICAL KEY across core query filters ──
# The core query filters (list_sessions, session_brief) must match projects by
# canonical key (project_identity.session_project_key), never a case-insensitive
# SUBSTRING match (which merged distinct projects) and never raw case-sensitive
# label equality (which SPLITS one project across drifted labels — TRACE vs
# trace-mcp, coeqwal vs COEQWAL). See INV-4 and ADR-006.


def test_inv4_project_filter_uses_canonical_key() -> None:
    source = (SRC / "storage" / "json_file.py").read_text(encoding="utf-8")
    assert "not in proj.lower()" not in source, (
        "INV-4 regression: json_file.py filters projects by case-insensitive "
        "SUBSTRING again — match by canonical key, or distinct projects merge."
    )
    assert "proj != project" not in source, (
        "INV-4 regression: json_file.py compares RAW project labels again — a "
        "display-label variant would split one project. Match by canonical key "
        "(session_project_key(meta) != query_key)."
    )
    assert source.count("session_project_key(meta) != query_key") >= 2, (
        "INV-4: both list_sessions and session_brief must filter by canonical "
        "project key (session_project_key(meta) != query_key) so drifted labels "
        "resolve to one project."
    )


# ── INV-5: no cloud egress without a pre-call ledger attestation ───────────
# Every OpenAI-SDK network call site in the trace-learn extension
# (`...completions.create(...)` / `...embeddings.create(...)`) must call
# attest_egress() in the SAME function, before the request. The egress ledger
# (~/.trace/egress.jsonl) is only trustworthy if no call site can bypass it —
# an unattested site is unrecorded egress, the exact failure mode the ledger
# exists to prevent.
#
# Registered as (module-relative-path, function-name). The key is the function
# NAME, not the qualified class method — good enough as a tripwire: a new
# `.create` site in a new or existing function fails the first test until it is
# registered AND attests.

INV5_EGRESS_CALL_SITES = {
    ("extensions/learn/extraction.py", "extract_from_session_llm"),
    ("extensions/learn/matching.py", "_llm_score"),
    ("extensions/learn/embeddings.py", "embed_texts"),
}


def _openai_network_call_sites() -> set[tuple[str, str]]:
    """Every (relpath, function) under extensions/learn/ whose body makes an
    OpenAI-SDK network call (an attribute call ``<x>.completions.create(...)``
    or ``<x>.embeddings.create(...)``)."""
    found: set[tuple[str, str]] = set()
    learn = SRC / "extensions" / "learn"
    for path in sorted(learn.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "create"
                        and isinstance(sub.func.value, ast.Attribute)
                        and sub.func.value.attr in ("completions", "embeddings")
                    ):
                        found.add((_rel(path), node.name))
    return found


def test_inv5_every_openai_call_site_is_registered() -> None:
    """A new OpenAI network call site must be registered (and attest) before it
    can merge; a registered site that disappears must be de-registered so the
    registry cannot rot."""
    sites = _openai_network_call_sites()
    assert sites, (
        "INV-5 positive control failed: no OpenAI call sites found at all — "
        "the AST pattern in _openai_network_call_sites no longer matches the "
        "codebase idiom and the guard is blind. Fix the pattern."
    )
    unregistered = sites - INV5_EGRESS_CALL_SITES
    assert not unregistered, (
        "INV-5 violation (docs/INVARIANTS.md): these functions make OpenAI "
        f"network calls but are not registered egress sites: {sorted(unregistered)}. "
        "Call attest_egress() before the request and add them to INV5_EGRESS_CALL_SITES."
    )
    stale = INV5_EGRESS_CALL_SITES - sites
    assert not stale, f"INV-5 registry is stale — registered sites with no OpenAI call anymore: {sorted(stale)}."


def test_inv5_every_egress_site_attests_first() -> None:
    """Each registered egress site must actually call attest_egress()."""
    attesters = _functions_calling("attest_egress")
    missing = INV5_EGRESS_CALL_SITES - attesters
    assert not missing, (
        f"INV-5 violation: egress call sites that never call attest_egress(): {sorted(missing)} — "
        "cloud content would leave the machine with no ledger record."
    )


# ── INV-7: every project-registry WRITE routes through locked_registry ────
# The registry's sole serializer is project_identity._atomic_write_registry; it
# must be called ONLY from locked_registry, which holds the fail-closed lock and
# writes atomically (temp + os.replace). A new caller elsewhere is an
# unlocked/non-atomic registry write — the lost-update/drift failure the lock
# exists to prevent.
INV7_REGISTRY_WRITE_SITES = {
    ("project_identity.py", "locked_registry"),
}


def test_inv7_registry_write_only_via_locked_registry() -> None:
    callers = _functions_calling("_atomic_write_registry")
    assert callers, (
        "INV-7 positive control failed: no caller of _atomic_write_registry found — "
        "the guard is blind (was it renamed?)."
    )
    unregistered = callers - INV7_REGISTRY_WRITE_SITES
    assert not unregistered, (
        "INV-7 violation (docs/INVARIANTS.md): these functions write the project registry "
        f"outside locked_registry: {sorted(unregistered)}. Route the write through "
        "project_identity.locked_registry (fail-closed lock + atomic write)."
    )
    stale = INV7_REGISTRY_WRITE_SITES - callers
    assert not stale, f"INV-7 registry is stale — registered writers with no call anymore: {sorted(stale)}."


# ── INV-6: (project, session) coherence for learn extraction ──────────────
# Every function in extensions/learn whose signature carries BOTH `project` and
# `session_id` (i.e. it extracts a session into a project's store) MUST call
# project_identity.validate_project_session first — else project B's store is
# written from project A's session and shipped to the cloud as dedup context.
def _project_session_functions() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    learn = SRC / "extensions" / "learn"
    for path in sorted(learn.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                params = {a.arg for a in node.args.args}
                if "project" in params and "session_id" in params:
                    found.add((_rel(path), node.name))
    return found


def test_inv6_project_session_sites_validate_coherence() -> None:
    sites = _project_session_functions()
    assert sites, (
        "INV-6 positive control failed: no (project, session_id) functions found in "
        "extensions/learn — the AST pattern no longer matches; the guard is blind."
    )
    validators = _functions_calling("validate_project_session")
    missing = sites - validators
    assert not missing, (
        "INV-6 violation (docs/INVARIANTS.md): these learn functions take both a project "
        f"and a session_id but never call validate_project_session: {sorted(missing)}. "
        "A session could be extracted into another project's store."
    )


# ── INV-8: the knowledge-store lock is fail-closed and dependency-free ────
# project_lock must use the O_EXCL + PID-liveness lock (raises on timeout, no
# optional filelock dependency, no warn-and-proceed) — the same fail-closed
# standard as the session lock (INV-1).
def test_inv8_knowledge_lock_is_fail_closed_and_dependency_free() -> None:
    source = (SRC / "extensions" / "learn" / "store.py").read_text(encoding="utf-8")
    assert "import filelock" not in source and "from filelock" not in source, (
        "INV-8 regression: store.py imports the optional 'filelock' package again — the "
        "knowledge-store lock must be the dependency-free fail-closed exclusive_file_lock."
    )
    assert "exclusive_file_lock" in source, (
        "INV-8: project_lock must acquire project_identity.exclusive_file_lock (fail-closed, "
        "raises TimeoutError rather than proceeding unlocked)."
    )


# ── INV-9: every learn tool guards its free-form project label ────────────
# Each @mcp.tool in extensions/learn taking a `project` param MUST call
# _reserved_project_error at entry, so a reserved-key (auto/shared) or degenerate
# label cannot reach a knowledge store through a tool.
def _learn_tool_functions_with_project() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    path = SRC / "extensions" / "learn" / "__init__.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            is_tool = any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "tool"
                for d in node.decorator_list
            )
            params = {a.arg for a in node.args.args}
            if is_tool and "project" in params:
                found.add((_rel(path), node.name))
    return found


def test_inv9_learn_tools_guard_reserved_projects() -> None:
    tools = _learn_tool_functions_with_project()
    assert tools, "INV-9 positive control: no @mcp.tool with a project param found — pattern rot."
    guards = _functions_calling("_resolve_project")
    missing = tools - guards
    assert not missing, (
        "INV-9 violation (docs/INVARIANTS.md): these learn tools take a free-form project but do not "
        f"call _resolve_project at entry: {sorted(missing)}. A foreign label could bypass the TRACE_PROJECT "
        "pin, or a reserved-key label could reach a store."
    )


# ── INV-12: learning ids are never reused ────────────────────────────────
# `next_learning_id` used to return max(existing) + 1, so forgetting the highest
# learning handed its id to different content on the next add — aliasing every
# reference that named it. The durable fix is a PERSISTED counter plus a refusal
# to write an already-aliased store; these guards fail if either half regresses
# or if a new appender mints ids some other way.
INV12_LEARNING_APPENDERS = {
    ("extensions/learn/store.py", "add_learning"),
    ("identity_cli.py", "cmd_merge_stores"),
}

INV12_DUPLICATE_REFUSERS = {
    ("extensions/learn/store.py", "save_store"),
    ("extensions/learn/store.py", "add_learning"),
}


_LIST_GROWERS = {"append", "extend", "insert"}


def _is_learnings_attr(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "learnings"


def _grows_learnings(sub: ast.AST) -> bool:
    """True if *sub* adds entries to a ``.learnings`` list.

    Covers ``x.learnings.append/extend/insert(...)``, ``x.learnings += [...]``,
    and slice assignment ``x.learnings[a:b] = ...``. A local alias
    (``lst = x.learnings; lst.append(...)``) is beyond a check of this shape; the
    behavioral tests in tests/test_learn_store.py are the backstop there, and the
    id-minting assertion below fails for any registered site that stops using the
    counter regardless of how it appends.
    """
    if (
        isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr in _LIST_GROWERS
        and _is_learnings_attr(sub.func.value)
    ):
        return True
    if isinstance(sub, ast.AugAssign) and _is_learnings_attr(sub.target):
        return True
    if isinstance(sub, ast.Assign) and any(
        isinstance(t, ast.Subscript) and _is_learnings_attr(t.value) for t in sub.targets
    ):
        return True
    return False


def _functions_appending_learnings() -> set[tuple[str, str]]:
    """Every (relpath, function) whose body grows a ``.learnings`` list."""
    found: set[tuple[str, str]] = set()
    for path in _src_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if any(_grows_learnings(sub) for sub in ast.walk(node)):
                found.add((_rel(path), node.name))
    return found


def _knowledge_store_method(name: str) -> ast.FunctionDef:
    tree = ast.parse((SRC / "extensions" / "learn" / "models.py").read_text())
    fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name),
        None,
    )
    assert fn is not None, f"KnowledgeStore.{name} disappeared — INV-12's guard has nothing to check."
    return fn


def test_inv12_next_learning_id_reads_and_advances_the_persisted_counter() -> None:
    """The id must come from ``next_id``, never from arithmetic over existing ids."""
    fn = _knowledge_store_method("next_learning_id")
    calls = {_call_name(c) for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "max" not in calls, (
        "INV-12 violation: next_learning_id derives an id from max() over existing ids again. "
        "Forgetting the highest learning then re-adding reissues its id, aliasing every "
        "reference that named it. Mint from the persisted next_id counter instead."
    )
    assert any(isinstance(n, ast.Attribute) and n.attr == "next_id" for n in ast.walk(fn)), (
        "INV-12 violation: next_learning_id no longer reads the persisted next_id counter."
    )
    assert any(
        isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Attribute) and n.target.attr == "next_id"
        for n in ast.walk(fn)
    ), "INV-12 violation: next_learning_id does not advance next_id, so two calls can collide."


def test_inv12_counter_healing_never_lowers_the_counter() -> None:
    """The load-time validator may only raise the counter — lowering it reissues ids."""
    fn = _knowledge_store_method("_raise_counter_above_existing_ids")
    assigns = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "next_id" for t in n.targets)
    ]
    assert assigns, "INV-12 violation: the counter validator no longer initializes next_id."
    for node in assigns:
        assert isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.Add), (
            "INV-12 violation: next_id is assigned something other than <highest existing> + 1; "
            "the validator must only ever raise the counter."
        )


def test_inv12_every_learning_appender_is_registered() -> None:
    appenders = _functions_appending_learnings()
    assert appenders, "the learnings-append detector matched nothing — the AST pattern has rotted."
    unregistered = appenders - INV12_LEARNING_APPENDERS
    assert not unregistered, (
        f"INV-12 violation (docs/INVARIANTS.md): unregistered learning appender(s): {sorted(unregistered)}. "
        "Mint the id with KnowledgeStore.next_learning_id() and register the site in "
        "INV12_LEARNING_APPENDERS."
    )
    stale = INV12_LEARNING_APPENDERS - appenders
    assert not stale, f"INV-12 registry is stale — these no longer append learnings: {sorted(stale)}."


def test_inv12_registered_appenders_mint_from_the_counter() -> None:
    minters = _functions_calling("next_learning_id")
    missing = INV12_LEARNING_APPENDERS - minters
    assert not missing, (
        f"INV-12 violation: these append learnings without minting an id from the store counter: {sorted(missing)}."
    )


def test_inv12_store_writes_refuse_an_aliased_store() -> None:
    """Reads stay permissive; every write path must fail closed on duplicate ids."""
    refusers = _functions_calling("_refuse_duplicate_ids")
    missing = INV12_DUPLICATE_REFUSERS - refusers
    assert not missing, (
        f"INV-12 violation: these write paths no longer refuse an aliased store: {sorted(missing)}. "
        "Writing to a store with duplicate learning ids spreads the aliasing."
    )
