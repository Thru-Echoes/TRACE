"""Knowledge-store containment tests (ADR-006 step S4).

Verifies that the knowledge store is keyed by canonical project key (so a
case-insensitive filesystem cannot merge two projects nor split one) and that
its lock is fail-closed (INV-8). The (project, session) extract-coherence check
(INV-6) is enforced structurally by
``tests/test_invariants.py :: test_inv6_project_session_sites_validate_coherence``
and behaviorally by ``validate_project_session`` in
``tests/test_project_identity.py``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from trace_mcp.extensions.learn import store
from trace_mcp.extensions.learn.models import KnowledgeStore
from trace_mcp.extensions.learn.store import add_learning, load_store, save_store


def test_store_path_is_canonical(tmp_path: Path) -> None:
    assert store._store_path("TRACE", str(tmp_path)).name == "trace.json"
    assert store._store_path("proj_a", str(tmp_path)).name == "proj-a.json"
    assert store._store_path("My Project", str(tmp_path)).name == "my-project.json"


def test_case_and_separator_variants_share_one_file(tmp_path: Path) -> None:
    variants = ("COEQWAL", "coeqwal", "Coeqwal")
    paths = {store._store_path(v, str(tmp_path)) for v in variants}
    assert len(paths) == 1
    sep = {store._store_path(v, str(tmp_path)) for v in ("proj a", "proj/a", "proj_a")}
    assert len(sep) == 1


def test_save_load_case_variant_roundtrip(tmp_path: Path) -> None:
    ks = KnowledgeStore(project="COEQWAL")
    add_learning(ks, content="a shared learning")
    save_store(ks, directory=str(tmp_path))
    # A case-variant label resolves to the same store (canonical merge).
    loaded = load_store("coeqwal", directory=str(tmp_path))
    assert len(loaded.learnings) == 1
    # Exactly one physical file exists for the project.
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["coeqwal.json"]


def test_project_lock_fails_closed_on_live_holder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_LOCK_TIMEOUT", "0.2")
    lock_path = Path(str(store._store_path("waggle", str(tmp_path))) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(f"{os.getpid()}:{time.time_ns()}".encode())  # this (alive) process holds it

    with pytest.raises(TimeoutError):
        with store.project_lock("waggle", directory=str(tmp_path)):
            pass


def test_project_lock_no_filelock_dependency() -> None:
    # INV-8: the store must not import the optional filelock package anywhere.
    source = Path(store.__file__).read_text(encoding="utf-8")
    assert "import filelock" not in source and "from filelock" not in source
