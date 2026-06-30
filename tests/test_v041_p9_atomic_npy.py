"""P9(c) / Round-3 A-R3-8: the .npy embedding sidecar is written atomically.

A crash (or a concurrent reader) mid-write must never leave a torn sidecar
that shadows the valid one. Verified by fault injection: a baseline file is
written, then `numpy.save` is forced to corrupt-then-raise; the original
file must remain intact and no temp residue may be left behind.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from trace_mcp.extensions.learn.models import KnowledgeStore  # noqa: E402
from trace_mcp.extensions.learn.store import (  # noqa: E402
    _embeddings_cache_path,
    add_learning,
    load_embeddings_cache,
    save_embeddings_cache,
)


def _store_with_embedding(project: str) -> KnowledgeStore:
    ks = KnowledgeStore(project=project)
    add_learning(ks, content="insight one", tags=["t"])
    ks.learnings[0].embedding = [0.1, 0.2, 0.3, 0.4]
    return ks


def test_atomic_npy_roundtrip(tmp_path: Path) -> None:
    ks = _store_with_embedding("rt")
    path = save_embeddings_cache(ks, directory=str(tmp_path))
    assert path is not None and path.exists()
    mat = load_embeddings_cache(ks, directory=str(tmp_path))
    assert mat is not None and mat.shape == (1, 4)
    assert not list(tmp_path.glob("*.npy.tmp")), "temp residue left behind"


def test_failed_save_does_not_corrupt_existing_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ks = _store_with_embedding("fault")
    # Baseline good sidecar.
    good_path = save_embeddings_cache(ks, directory=str(tmp_path))
    assert good_path is not None
    baseline = load_embeddings_cache(ks, directory=str(tmp_path))
    assert baseline is not None

    real_save = np.save

    def corrupt_then_raise(file: object, arr: object) -> None:  # noqa: ANN401
        if isinstance(file, (str, bytes, os.PathLike)):
            with open(file, "wb") as f:  # type: ignore[arg-type]
                f.write(b"TORN")
        else:
            file.write(b"TORN")  # type: ignore[attr-defined]
        raise RuntimeError("disk full mid-save")

    monkeypatch.setattr(np, "save", corrupt_then_raise)

    with pytest.raises(RuntimeError, match="disk full"):
        save_embeddings_cache(ks, directory=str(tmp_path))

    monkeypatch.setattr(np, "save", real_save)
    # Original sidecar must be intact (not "TORN") and loadable.
    recovered = load_embeddings_cache(ks, directory=str(tmp_path))
    assert recovered is not None and recovered.shape == (1, 4)
    assert _embeddings_cache_path("fault", str(tmp_path)).read_bytes()[:4] != b"TORN"
    assert not list(tmp_path.glob("*.npy.tmp")), "temp residue left behind"
