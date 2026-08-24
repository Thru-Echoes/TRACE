"""Tests for the core project-identity module (ADR-006 step S2).

Covers the two guaranteed canonical-key properties (idempotence and the
``sanitize_name`` fixed point), the fail-closed alias registry (locking,
atomic writes, corrupt/unknown-major refusal), enrollment semantics
(never merge a near-miss into an existing project), (project, session)
coherence (INV-6), and the ``TRACE_PROJECT`` binding.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import cast

import pytest
from conftest import dead_pid

import trace_mcp.project_identity as pi
from trace_mcp.storage.base import TraceStorage
from trace_mcp.storage.json_file import sanitize_name

# Labels that must all reduce cleanly; used for the property checks.
_CANONICALIZABLE = [
    "TRACE",
    "trace-mcp",
    "coeqwal",
    "COEQWAL",
    "proj a",
    "proj/a",
    "proj_a",
    "My Project!",
    "  spaced  ",
    "a..b",
    "dots...",
    "Café Ω",
    "green-narrative",
    "when-algorithms-meet-artists",
    "REAP",
    "my.financial.advisor",
    "a/b/c",
]


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every test at a fresh registry file and a clean cache."""
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.delenv("TRACE_PROJECT", raising=False)
    pi._reset_registry_cache()


# ── canonical_project_key ─────────────────────────────────────────────────


def test_canonical_key_idempotent() -> None:
    for label in _CANONICALIZABLE:
        key = pi.canonical_project_key(label)
        assert pi.canonical_project_key(key) == key, label


def test_canonical_key_is_sanitize_name_fixed_point() -> None:
    for label in _CANONICALIZABLE:
        key = pi.canonical_project_key(label)
        assert sanitize_name(key) == key, label


def test_casefold_pairs_merge() -> None:
    assert pi.canonical_project_key("TRACE") == pi.canonical_project_key("trace") == "trace"
    assert pi.canonical_project_key("COEQWAL") == pi.canonical_project_key("coeqwal") == "coeqwal"


def test_separator_and_underscore_fold() -> None:
    keys = {pi.canonical_project_key(x) for x in ("proj a", "proj/a", "proj_a", "proj  a")}
    assert keys == {"proj-a"}


def test_degenerate_labels_raise() -> None:
    for bad in ("", "   ", "///", "...", "__", "-.-"):
        with pytest.raises(pi.ProjectKeyError):
            pi.canonical_project_key(bad)


# ── registry model ────────────────────────────────────────────────────────


def test_resolve_matches_key_alias_and_canonical() -> None:
    reg = pi.ProjectRegistry(
        projects={"waggle": pi.ProjectEntry(key="waggle", display_label="Waggle", aliases=["waggle-crm"])}
    )
    assert reg.resolve("waggle") == "waggle"  # verbatim key
    assert reg.resolve("waggle-crm") == "waggle"  # verbatim alias
    assert reg.resolve("Waggle") == "waggle"  # canonical of the display label
    assert reg.resolve("nope") is None


def test_assert_unique_aliases_raises_on_conflict() -> None:
    reg = pi.ProjectRegistry(
        projects={
            "a": pi.ProjectEntry(key="a", display_label="A", aliases=["shared-alias"]),
            "b": pi.ProjectEntry(key="b", display_label="B", aliases=["shared-alias"]),
        }
    )
    with pytest.raises(ValueError, match="resolves to both"):
        reg.assert_unique_aliases()


# ── registry persistence (fail closed) ────────────────────────────────────


def test_load_registry_absent() -> None:
    assert pi.load_registry(required=False) is None
    with pytest.raises(pi.RegistryUnavailableError):
        pi.load_registry(required=True)


def test_load_registry_unknown_major_fails_closed_untouched() -> None:
    path = pi.registry_path()
    payload = json.dumps({"version": "99", "projects": {}})
    path.write_text(payload)
    with pytest.raises(pi.RegistryUnavailableError, match="unknown major version"):
        pi.load_registry(required=False)
    assert path.read_text() == payload  # never rewritten


def test_load_registry_corrupt_fails_closed_untouched() -> None:
    path = pi.registry_path()
    path.write_text("{not json")
    with pytest.raises(pi.RegistryUnavailableError):
        pi.load_registry(required=False)
    assert path.read_text() == "{not json"


def test_locked_registry_enroll_persists_with_history() -> None:
    with pi.locked_registry() as reg:
        reg.projects["waggle"] = pi.ProjectEntry(key="waggle", display_label="Waggle", enrolled_by="test")
        reg.history.append(pi.RegistryChange(actor="test", action="enroll", details={"key": "waggle"}))

    pi._reset_registry_cache()
    reloaded = pi.load_registry(required=True)
    assert reloaded is not None
    assert "waggle" in reloaded.projects
    assert reloaded.history[-1].action == "enroll"


def test_locked_registry_caller_exception_does_not_write() -> None:
    with pytest.raises(RuntimeError):
        with pi.locked_registry() as reg:
            reg.projects["ghost"] = pi.ProjectEntry(key="ghost", display_label="ghost")
            raise RuntimeError("boom")
    assert not pi.registry_path().exists()  # nothing persisted


def test_atomic_write_leaves_no_tmp_files() -> None:
    with pi.locked_registry() as reg:
        reg.projects["a"] = pi.ProjectEntry(key="a", display_label="A")
    leftovers = list(pi.registry_path().parent.glob(".projects-*.tmp"))
    assert leftovers == []


# ── exclusive_file_lock ────────────────────────────────────────────────────


def test_exclusive_lock_timeout_raises_on_live_holder(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    lock.write_bytes(f"{__import__('os').getpid()}:{time.time_ns()}".encode())  # this (alive) process
    with pytest.raises(TimeoutError):
        with pi.exclusive_file_lock(lock, timeout=0.2, steal_after=10_000):
            pass


def test_exclusive_lock_steals_dead_holder(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    lock.write_bytes(f"{dead_pid()}:{time.time_ns()}".encode())  # reaped: its pid is provably gone
    with pi.exclusive_file_lock(lock, timeout=0.5):
        pass  # acquired by stealing the dead holder's lock — no TimeoutError


# ── resolve_project_key ────────────────────────────────────────────────────


def test_resolve_no_registry_returns_canonical() -> None:
    assert pi.resolve_project_key("My Project", allow_enroll=False, actor="t") == "my-project"


def test_resolve_enrolls_new_key_never_merges_near_miss() -> None:
    with pi.locked_registry() as reg:
        reg.projects["waggle"] = pi.ProjectEntry(key="waggle", display_label="waggle")
    pi._reset_registry_cache()

    # "wagle" is a typo, a DIFFERENT canonical key — it must get its own entry,
    # never be fused into "waggle".
    key = pi.resolve_project_key("wagle", allow_enroll=True, actor="t")
    assert key == "wagle"
    reg = pi.load_registry(required=True)
    assert reg is not None
    assert set(reg.projects) == {"waggle", "wagle"}


def test_resolve_strict_mode_refuses_unknown() -> None:
    with pi.locked_registry() as reg:
        reg.strict = True
    pi._reset_registry_cache()
    with pytest.raises(pi.ProjectKeyError, match="not enrolled"):
        pi.resolve_project_key("brand-new", allow_enroll=True, actor="t")


def test_resolve_disallow_enroll_refuses_unknown() -> None:
    with pi.locked_registry() as reg:
        reg.projects["known"] = pi.ProjectEntry(key="known", display_label="known")
    pi._reset_registry_cache()
    with pytest.raises(pi.ProjectKeyError):
        pi.resolve_project_key("unknown", allow_enroll=False, actor="t")


def test_resolve_reserved_key_rejected() -> None:
    with pytest.raises(pi.ProjectKeyError, match="reserved"):
        pi.resolve_project_key("auto", allow_enroll=True, actor="t")


# ── coherence + binding ────────────────────────────────────────────────────


class _Meta:
    def __init__(self, project: str, project_key: str | None = None) -> None:
        self.project = project
        if project_key is not None:
            self.project_key = project_key


class _Sess:
    def __init__(self, metadata: _Meta) -> None:
        self.metadata = metadata


class _Storage:
    def __init__(self, sessions: dict[str, _Sess]) -> None:
        self._sessions = sessions

    async def get_session(self, session_id: str) -> _Sess:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise FileNotFoundError(session_id) from exc


def _enroll(*keys: str) -> None:
    with pi.locked_registry() as reg:
        for key in keys:
            reg.projects[key] = pi.ProjectEntry(key=key, display_label=key)
    pi._reset_registry_cache()


async def test_validate_project_session_match_returns_key() -> None:
    _enroll("waggle")
    storage = cast(TraceStorage, _Storage({"s1": _Sess(_Meta("Waggle"))}))
    assert await pi.validate_project_session(storage, "waggle", "s1") == "waggle"


async def test_validate_project_session_mismatch_names_both() -> None:
    _enroll("waggle", "chemmasters")
    storage = cast(TraceStorage, _Storage({"s1": _Sess(_Meta("Waggle"))}))
    with pytest.raises(pi.ProjectMismatchError) as exc:
        await pi.validate_project_session(storage, "chemmasters", "s1")
    message = str(exc.value)
    assert "waggle" in message and "chemmasters" in message


def test_session_project_key_prefers_project_key() -> None:
    assert pi.session_project_key(_Meta("Display Label", project_key="the-key")) == "the-key"


def test_session_project_key_from_dict_meta() -> None:
    assert pi.session_project_key({"project": "COEQWAL"}) == "coeqwal"
    assert pi.session_project_key({"project": "x", "project_key": "y"}) == "y"


def test_session_matches_project_label_fallback() -> None:
    assert pi.session_matches_project(_Meta("TRACE"), "trace") is True
    assert pi.session_matches_project(_Meta("TRACE"), "coeqwal") is False
    assert pi.session_matches_project(_Meta(""), "trace") is False


def test_get_bound_project_unpinned_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACE_PROJECT", raising=False)
    assert pi.get_bound_project() is None


def test_get_bound_project_enrolled_is_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    _enroll("waggle")
    monkeypatch.setenv("TRACE_PROJECT", "Waggle")
    bound = pi.get_bound_project()
    assert bound is not None
    assert bound.key == "waggle" and bound.degraded is False


def test_get_bound_project_unenrolled_is_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    _enroll("waggle")
    monkeypatch.setenv("TRACE_PROJECT", "some-other-project")
    bound = pi.get_bound_project()
    assert bound is not None
    assert bound.key == "some-other-project" and bound.degraded is True


def test_get_bound_project_no_registry_is_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_PROJECT", "anything")
    bound = pi.get_bound_project()
    assert bound is not None
    assert bound.degraded is True
