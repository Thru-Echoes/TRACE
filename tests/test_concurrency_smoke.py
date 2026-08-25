"""Pre-migration concurrency smoke: two real server processes on ONE TRACE home.

Every consumer on a machine — and the identity migration that consolidates
their stores — writes to the same ``~/.trace``. Between concurrent trace-mcp
processes and silent data loss stand exactly two mechanisms: the fail-closed
per-project knowledge-store lock (INV-8) and the temp-file + ``os.replace``
atomic store write. Both are pinned in-process elsewhere
(``test_project_identity.py``, ``test_integrity_hardening.py``,
``test_learn_containment.py``); this module exercises them across REAL server
processes speaking MCP over stdio — the deployed shape.

Claims exercised here:

1. Interleaved ``trace_learn_add`` under FORCED contention: each round the test
   holds the store lock itself, queues one add on each server behind it, and
   releases it only after checking that neither add completed — so a lock that
   is bypassed fails deterministically, and both requests are released
   together to contend. Every learning lands, ids are unique across writers,
   each response's id is on disk, and no lock or temp file is left behind.
2. SIGKILL of one process while an add is in flight — at fixed offsets after
   dispatch, and, deterministically, at the instant it is about to
   ``os.replace`` its temp file onto the store (a test-only gate installed in
   that one server holds it there). At that point the temp file exists and the
   lock names the victim; after the kill the store is exactly the pre-add
   state, and the surviving process keeps adding — the dead holder's lock is
   stolen, not waited on. The fixed-offset kills assert that every prior
   record is unchanged and that at most one validated record with the expected
   content was appended — nothing else.
3. A lock planted with a dead PID is stolen within a short timeout.
4. A lock held by a LIVE process, aged far past the time-based steal
   threshold, is never stolen: the peer's add errors with the lock's own
   refusal, and neither the store's bytes nor the lock's bytes change.

Scope, stated plainly. A green run is evidence for the paths and fixture
states exercised here — it is not a proof for every store state or every
mutation path. The module pins behavior across process death and atomic
rename visibility; ``save_store`` does not fsync, so nothing here speaks to
durability across a kernel crash or power loss. A kill between ``mkstemp``
and ``os.replace`` legitimately orphans a temp file — no cleanup runs on
SIGKILL — so only case 1, where every process exits cleanly, asserts the
absence of temp files. A green run is a precondition for consolidating live
stores, not the whole gate: the consolidation runbook rehearses on a cloned
home under its own checks.

Skipped on Windows (SIGKILL and PID-liveness semantics differ). Never touches
the live ``~/.trace``: every path is a per-test temp-directory override.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import dead_pid
from test_e2e_server import (
    TRACE_ROOT,
    _call_tool,
    _initialize_server,
    _make_jsonrpc_request,
    _shutdown_server,
    _start_server,
)

from trace_mcp.extensions.learn import store as learn_store
from trace_mcp.extensions.learn.models import KnowledgeStore

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL / PID-liveness semantics are POSIX"),
]

PROJECT = "smoke-proj"  # already canonical, so the store file is <PROJECT>.json
ROUNDS = 12  # adds per process in the interleaved case (24 learnings total)
TEST_TIMEOUT = 180.0  # whole-test bound; a broken environment must fail, not hang

# Lock timeouts stay BELOW the harness's 15s per-request timeout, so a
# regression that waits on a lock instead of stealing it surfaces as the tool's
# own error payload — attributable — rather than as a generic "no JSON-RPC
# response" timeout from the harness.
LOCK_TIMEOUT = "10"

# How long case 1 holds its barrier lock each round: long enough for both
# servers to have read their request and be polling for the lock.
BARRIER_HOLD = 0.25

# Seeding the store to several megabytes makes each critical section tens of
# milliseconds — wider than the lock's 20 ms poll interval — so two writers
# released together genuinely overlap instead of slipping past each other.
# Each seed learning is ONE giant token, so the dedup pass over it stays cheap.
SEED_LEARNINGS = 8
SEED_BYTES_EACH = 1_000_000

# Where to SIGKILL the writer, relative to dispatching its add: fixed offsets
# in seconds (before the request is read, and across the load→write→respond
# span), plus "at-replace", which kills at the gate described below.
KILL_POINTS: tuple[float | str, ...] = (0.0, 0.005, 0.02, 0.06, "at-replace")

# Test-only gate, installed in the "at-replace" victim through sitecustomize.
# Once ARMED, the first os.replace onto the store file drops a marker and holds
# there forever, so the test can kill the victim at a KNOWN point — temp file
# written, lock held, replace not yet done — instead of racing to observe a
# temp file that lives for milliseconds. Inert unless all three variables are
# set, and only that one server ever sees them.
_REPLACE_GATE = textwrap.dedent(
    """
    import os
    import time

    _target = os.environ.get("SMOKE_GATE_TARGET")
    _arm = os.environ.get("SMOKE_GATE_ARM")
    _marker = os.environ.get("SMOKE_GATE_MARKER")
    if _target and _arm and _marker:
        _real_replace = os.replace

        def _gated_replace(src, dst, *args, **kwargs):
            if os.fspath(dst) == _target and os.path.exists(_arm):
                with open(_marker, "w", encoding="utf-8") as f:
                    f.write("ready")
                while True:  # held here until the test SIGKILLs this process
                    time.sleep(1)
            return _real_replace(src, dst, *args, **kwargs)

        os.replace = _gated_replace
    """
)


# ── Fixtures and helpers ─────────────────────────────────────────────────────


@pytest.fixture()
def trace_home(tmp_path: Path) -> dict[str, str]:
    """One isolated TRACE home shared by every server this test spawns.

    Returns the env overrides to hand to ``_start_server``. The registry path
    points at a file that does not exist — the normal "no registry yet" state,
    which is exactly the state the migration starts from.
    """
    knowledge = tmp_path / "knowledge"
    sessions = tmp_path / "sessions"
    knowledge.mkdir()
    sessions.mkdir()
    return {
        "TRACE_KNOWLEDGE_DIR": str(knowledge),
        "TRACE_SESSIONS_DIR": str(sessions),
        "TRACE_REGISTRY_PATH": str(tmp_path / "projects.json"),
        "TRACE_PROJECT": PROJECT,
        "TRACE_LOCK_TIMEOUT": LOCK_TIMEOUT,
    }


def _store_path(env: dict[str, str]) -> Path:
    return Path(env["TRACE_KNOWLEDGE_DIR"]) / f"{PROJECT}.json"


def _lock_path(env: dict[str, str]) -> Path:
    return Path(str(_store_path(env)) + ".lock")


def _content(writer: str, i: int) -> str:
    """Deterministic, dedup-proof content: any two share at most half their tokens (Jaccard 0.5 < 0.85)."""
    return f"writer-{writer}-round-{i:03d}-token-{writer}{i:03d}"


def _payload(response: dict[str, Any]) -> dict[str, Any]:
    """Decode a tools/call response into the tool's JSON payload."""
    assert "result" in response, f"tools/call failed at the protocol level: {response}"
    text = response["result"]["content"][0]["text"]
    return json.loads(text)


def _added(response: dict[str, Any]) -> dict[str, Any]:
    """The full learning record the tool reported as added."""
    payload = _payload(response)
    assert "added" in payload, f"trace_learn_add did not add: {payload}"
    return payload["added"]


def _added_id(response: dict[str, Any]) -> str:
    return _added(response)["id"]


def _record(lrn: Any) -> dict[str, Any]:
    """A learning as the tool reports it (the bulky embedding fields excluded)."""
    return lrn.model_dump(mode="json", exclude=learn_store._TOOL_RESPONSE_EXCLUDE)


def _read_store(env: dict[str, str]) -> list[dict[str, Any]]:
    """Parse AND validate the store from disk, as the full tool-visible records (embedding fields excluded).

    A torn file fails at ``json.loads``; a structurally damaged one fails at
    model validation — either is the defect this module exists to catch.
    """
    raw = json.loads(_store_path(env).read_text(encoding="utf-8"))
    store = KnowledgeStore.model_validate(raw)
    return [_record(lrn) for lrn in store.learnings]


def _residue(env: dict[str, str], suffixes: frozenset[str] = frozenset({".lock", ".tmp"})) -> list[str]:
    """Files with one of *suffixes* left in the knowledge dir (none expected at rest)."""
    knowledge = Path(env["TRACE_KNOWLEDGE_DIR"])
    return sorted(p.name for p in knowledge.iterdir() if p.suffix in suffixes)


def _seed_store(env: dict[str, str], n: int = SEED_LEARNINGS, size: int = SEED_BYTES_EACH) -> list[dict[str, Any]]:
    """Write a store in-process through the same store module the server uses.

    Side effect: creates ``<knowledge>/<PROJECT>.json``. Returns the records written.
    """
    directory = env["TRACE_KNOWLEDGE_DIR"]
    ks = learn_store.load_store(PROJECT, directory)
    for i in range(n):
        learn_store.add_learning(ks, f"seed{i:04d}" + "x" * size)
    learn_store.save_store(ks, directory)
    return [_record(lrn) for lrn in ks.learnings]


def _replace_gate(env: dict[str, str]) -> dict[str, str]:
    """Write the gate module and return the env overrides that install it in ONE server.

    Side effect: creates ``<home>/gate/sitecustomize.py``. The harness's own
    PYTHONPATH (the ``src`` tree) is re-applied because an override replaces it.
    """
    home = Path(env["TRACE_KNOWLEDGE_DIR"]).parent
    gate_dir = home / "gate"
    gate_dir.mkdir()
    (gate_dir / "sitecustomize.py").write_text(_REPLACE_GATE, encoding="utf-8")
    return {
        "PYTHONPATH": str(gate_dir) + os.pathsep + str(TRACE_ROOT / "src"),
        "SMOKE_GATE_TARGET": str(_store_path(env)),
        "SMOKE_GATE_ARM": str(home / "gate.armed"),
        "SMOKE_GATE_MARKER": str(home / "gate.ready"),
    }


async def _reap(proc: asyncio.subprocess.Process) -> None:
    """Force-stop *proc* and release its pipes. Safe on a process that already exited."""
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=10)
    if proc.stdin is not None:
        proc.stdin.close()


async def _stop_all(*procs: asyncio.subprocess.Process) -> None:
    """Shut every process down independently: graceful first, kill as the fallback."""
    errors: list[BaseException] = []
    for proc in procs:
        try:
            await _shutdown_server(proc)
        except Exception as exc:  # noqa: BLE001 — every process must still be reaped
            errors.append(exc)
        finally:
            await _reap(proc)
    if errors:
        raise errors[0]


async def _server(env: dict[str, str], **overrides: str) -> asyncio.subprocess.Process:
    """Spawn + initialize a server; a failed handshake never leaks the child.

    The shared harness leaves stderr unread on success. These servers emit a
    few kilobytes over a whole test, far below the reader's buffer limit; a
    continuous drain belongs in the harness itself if that ever changes.
    """
    proc = await _start_server(env["TRACE_SESSIONS_DIR"], env_extra={**env, **overrides})
    try:
        await _initialize_server(proc)
    except BaseException:
        await _reap(proc)
        raise
    return proc


async def _add(proc: asyncio.subprocess.Process, content: str, request_id: int) -> dict[str, Any]:
    return await _call_tool(proc, "trace_learn_add", {"content": content}, request_id=request_id)


async def _stderr_after_exit(proc: asyncio.subprocess.Process) -> str:
    """Everything the (already exited) server wrote to stderr."""
    assert proc.stderr is not None
    return (await proc.stderr.read()).decode("utf-8", errors="replace")


async def _dispatch_add_then_kill(
    victim: asyncio.subprocess.Process,
    env: dict[str, str],
    kill_at: float | str,
    content: str,
    gate: dict[str, str],
) -> None:
    """Send an add to *victim* without awaiting the response, then SIGKILL it at *kill_at*.

    Side effects: kills and reaps *victim*; for ``"at-replace"`` arms the gate.
    In that mode the kill lands only once the victim is held at ``os.replace``,
    and the call FAILS if the victim never gets there, if no temp file exists at
    that point, or if the victim does not hold the store lock at that point.
    """
    assert victim.stdin is not None
    if kill_at == "at-replace":
        Path(gate["SMOKE_GATE_ARM"]).write_bytes(b"1")
    req = _make_jsonrpc_request("tools/call", {"name": "trace_learn_add", "arguments": {"content": content}}, id=2)
    victim.stdin.write((req + "\n").encode("utf-8"))
    await victim.stdin.drain()

    if kill_at == "at-replace":
        marker = Path(gate["SMOKE_GATE_MARKER"])
        deadline = time.monotonic() + 15.0
        while not marker.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
        reached = marker.exists()
        tmp_present = any(p.suffix == ".tmp" for p in Path(env["TRACE_KNOWLEDGE_DIR"]).iterdir())
        lock = _lock_path(env)
        held = lock.exists() and lock.read_bytes().startswith(f"{victim.pid}:".encode())
        await _reap(victim)  # SIGKILL: no cleanup handlers, no lock release
        assert reached, (
            "the victim never reached os.replace onto the store: save_store no longer ends in a temp-file replace"
        )
        assert tmp_present, "at the replace no temp file existed: the store is no longer written through a temp file"
        assert held, "at the replace the victim did not hold the store lock: the lock span no longer covers the write"
        return

    assert isinstance(kill_at, float)
    if kill_at:
        await asyncio.sleep(kill_at)
    await _reap(victim)


# ── 1. Forced contention: no lost update, no id aliasing ─────────────────────


async def test_interleaved_adds_under_forced_contention_lose_nothing(trace_home: dict[str, str]) -> None:
    """Two servers released together to contend every round: 2*ROUNDS learnings, distinct ids, disk == responses."""
    async with asyncio.timeout(TEST_TIMEOUT):
        seeded = {rec["id"]: rec["content"] for rec in _seed_store(trace_home, n=SEED_LEARNINGS // 2)}
        a = await _server(trace_home)
        try:
            b = await _server(trace_home)
        except BaseException:
            await _reap(a)
            raise
        barrier = _lock_path(trace_home)
        sent: dict[str, str] = {}  # response id -> content
        tasks: list[asyncio.Task[dict[str, Any]]] = []
        try:
            rid = 100
            for i in range(ROUNDS):
                ca, cb = _content("A", i), _content("B", i)
                # Hold the store lock ourselves (this PID is alive, so it cannot
                # be stolen), queue one add on each server behind it, confirm
                # neither got through, then release: both critical sections
                # now race for the same lock.
                barrier.write_bytes(f"{os.getpid()}:{time.time_ns()}".encode())
                ta = asyncio.create_task(_add(a, ca, rid))
                tb = asyncio.create_task(_add(b, cb, rid + 1))
                tasks += [ta, tb]
                rid += 2
                await asyncio.sleep(BARRIER_HOLD)
                assert not ta.done() and not tb.done(), f"round {i}: an add completed while the store lock was held"
                barrier.unlink()
                ra, rb = await asyncio.gather(ta, tb)
                ida, idb = _added_id(ra), _added_id(rb)
                assert ida != idb, f"round {i}: both writers were handed the same id {ida}"
                assert ida not in sent and idb not in sent, f"round {i}: id reused across rounds"
                sent[ida] = ca
                sent[idb] = cb
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await _stop_all(a, b)

    on_disk = {rec["id"]: rec["content"] for rec in _read_store(trace_home)}
    expected = {**seeded, **sent}
    assert len(on_disk) == len(expected), f"lost update or duplicate id: {len(on_disk)} distinct ids of {len(expected)}"
    assert on_disk == expected, "disk disagrees with what the writers were told they added"
    assert _residue(trace_home) == []


# ── 2. Kill mid-add: store is exactly old or new, survivor continues ─────────


@pytest.mark.parametrize("kill_at", KILL_POINTS)
async def test_kill_mid_add_leaves_old_or_new_store_and_survivor_continues(
    trace_home: dict[str, str], kill_at: float | str
) -> None:
    """Kill writer A with an add in flight; the store is intact and writer B keeps adding."""
    async with asyncio.timeout(TEST_TIMEOUT):
        expected = _seed_store(trace_home)
        gate = _replace_gate(trace_home) if kill_at == "at-replace" else {}
        survivor = await _server(trace_home)
        victim: asyncio.subprocess.Process | None = None
        try:
            victim = await _server(trace_home, **gate)
            expected.append(_added(await _add(victim, _content("victim", 0), 1)))  # a normal add through the victim
            inflight = _content("victim", 1)
            await _dispatch_add_then_kill(victim, trace_home, kill_at, inflight, gate)

            # (a) exactly the pre-add state, or exactly the post-add state — nothing
            #     else; a kill held at the replace can only have left the pre-add state
            got = _read_store(trace_home)
            post_add = len(got) == len(expected) + 1 and got[:-1] == expected and got[-1]["content"] == inflight
            if kill_at == "at-replace":
                assert got == expected, f"store changed although the replace never ran: {[r['id'] for r in got]}"
            else:
                assert got == expected or post_add, (
                    f"store is neither pre-add nor post-add state: {[r['id'] for r in got]}"
                )

            # (b) the survivor continues — a lock orphaned by the dead PID is
            #     stolen, not waited on (a wait would exceed LOCK_TIMEOUT and error)
            added = [_added_id(await _add(survivor, _content("survivor", i), 10 + i)) for i in range(3)]
        finally:
            if victim is not None:
                await _reap(victim)
            await _stop_all(survivor)

    final = _read_store(trace_home)
    ids = [rec["id"] for rec in final]
    assert len(final) == len(got) + 3
    assert len(set(ids)) == len(ids), f"duplicate ids after kill + recovery: {ids}"
    assert set(added) <= set(ids)
    # The survivor's steal + release must leave no lock behind. Temp files are
    # deliberately NOT asserted here — see the module docstring.
    assert _residue(trace_home, frozenset({".lock"})) == []


# ── 3. Planted dead-holder lock is stolen (deterministic) ────────────────────


async def test_lock_from_dead_pid_is_stolen(trace_home: dict[str, str]) -> None:
    """A lock whose holder PID is gone must not block the next writer."""
    async with asyncio.timeout(TEST_TIMEOUT):
        lock = _lock_path(trace_home)
        lock.write_bytes(f"{dead_pid()}:{time.time_ns()}".encode())

        proc = await _server(trace_home)
        try:
            _added_id(await _add(proc, _content("after-dead-lock", 0), 1))
        finally:
            await _stop_all(proc)
    assert not lock.exists(), "stolen lock was not released after the write"
    assert len(_read_store(trace_home)) == 1


# ── 4. Live-holder lock is never stolen, however old: fail closed ────────────


async def test_lock_from_live_pid_is_never_stolen_however_old(trace_home: dict[str, str]) -> None:
    """A lock held by a live process makes the peer's add ERROR — never a silent unlocked write."""
    async with asyncio.timeout(TEST_TIMEOUT):
        _seed_store(trace_home, n=2, size=1_000)
        store_before = _store_path(trace_home).read_bytes()
        lock = _lock_path(trace_home)
        token = f"{os.getpid()}:{time.time_ns()}".encode()  # this test process: alive
        lock.write_bytes(token)
        aged = time.time() - 600  # far past the time-based steal threshold for UNKNOWN holders
        os.utime(lock, (aged, aged))

        proc = await _server(trace_home, TRACE_LOCK_TIMEOUT="1")
        try:
            payload = _payload(await _add(proc, _content("blocked", 0), 1))
            assert "error" in payload and "added" not in payload, f"wrote past a live holder's lock: {payload}"
        finally:
            await _stop_all(proc)
        # The tool reports a generic add failure; the lock's own refusal is what
        # the server logged. Assert on that, so an unrelated failure cannot pass.
        stderr = await _stderr_after_exit(proc)
        assert "Refusing to write unlocked" in stderr, (
            f"the add failed for a reason other than the lock:\n{stderr[-2000:]}"
        )
    assert _store_path(trace_home).read_bytes() == store_before, "store bytes changed despite the lock being held"
    assert lock.read_bytes() == token, "a live holder's lock was removed or rewritten"
