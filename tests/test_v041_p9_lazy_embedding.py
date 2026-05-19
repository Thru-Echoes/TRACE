"""P9(a): the model2vec embedding model must load LAZILY.

Round-1/2/3 (and the FM7 misdiagnosis correction) traced a real cost:
`Model2VecEmbeddingProvider.__init__` eagerly called
`StaticModel.from_pretrained` at construction, and the provider is built
in the extension's `register()` at server startup. With N concurrent
Claude sessions that is N resident models (large idle RAM) plus the
subprocess cold-start latency that produced the FM7 E2E flake.

The model must materialize only on first real embedding use.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from trace_mcp.extensions.learn import embeddings as emb


@pytest.fixture
def fake_static_model(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace model2vec.StaticModel with a call-counting fake (the real
    one downloads/loads an on-disk model — a true external, mock-eligible)."""
    if not emb._HAS_MODEL2VEC:
        pytest.skip("model2vec not installed")

    loaded = MagicMock(name="from_pretrained")

    class _FakeModel:
        dim = 256

        def encode(self, texts: list[str]) -> list[Any]:
            import numpy as np

            return [np.zeros(self.dim) for _ in texts]

    loaded.return_value = _FakeModel()
    import model2vec

    monkeypatch.setattr(model2vec.StaticModel, "from_pretrained", loaded)
    return loaded


def test_construction_does_not_load_model(fake_static_model: MagicMock) -> None:
    """Constructing the provider must NOT call StaticModel.from_pretrained."""
    provider = emb.Model2VecEmbeddingProvider()
    assert fake_static_model.call_count == 0, (
        "model loaded eagerly at __init__ — must be lazy"
    )
    assert provider.model_name == "minishlab/potion-base-8M"


async def test_model_loads_once_on_first_embed(fake_static_model: MagicMock) -> None:
    """The model loads on first embed_texts and is cached thereafter."""
    provider = emb.Model2VecEmbeddingProvider()
    assert fake_static_model.call_count == 0

    out1 = await provider.embed_texts(["hello"])
    assert fake_static_model.call_count == 1
    assert len(out1) == 1

    await provider.embed_texts(["world", "again"])
    assert fake_static_model.call_count == 1  # cached, not reloaded
