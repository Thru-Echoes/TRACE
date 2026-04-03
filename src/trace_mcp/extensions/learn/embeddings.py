"""Embedding providers for trace-learn.

Generates vector embeddings for learning content.  Three providers:

1. **OpenAI** ``text-embedding-3-small`` — best quality, requires API key
2. **model2vec** ``potion-base-8M`` — local, 8 MB model, no PyTorch
3. **None** — no embeddings, fall through to BM25 matching

Auto-selection: openai (if key + pkg) → model2vec (if pkg) → None.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

    from trace_mcp.extensions.learn.config import LearnConfig

logger = logging.getLogger(__name__)

# ── Feature detection ────────────────────────────────────────────────

try:
    import numpy as _np  # noqa: F401 (runtime probe)

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _HAS_NUMPY = False

try:
    from openai import AsyncOpenAI  # noqa: F401

    _HAS_OPENAI = True
except ImportError:  # pragma: no cover
    _HAS_OPENAI = False

try:
    from model2vec import StaticModel  # noqa: F401

    _HAS_MODEL2VEC = True
except ImportError:  # pragma: no cover
    _HAS_MODEL2VEC = False


# ── Provider protocol ────────────────────────────────────────────────


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Interface for embedding generation backends."""

    model_name: str
    dimensions: int

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
        ...


# ── OpenAI provider ──────────────────────────────────────────────────


class OpenAIEmbeddingProvider:
    """Generate embeddings via OpenAI API (``text-embedding-3-small``)."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        dimensions: int = 0,
    ) -> None:
        if not _HAS_OPENAI:
            raise RuntimeError("openai package is required for OpenAI embeddings")
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self.model_name = model
        self.dimensions = dimensions  # 0 = use model default

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        kwargs: dict = {"model": self.model_name, "input": texts}
        if self.dimensions > 0:
            kwargs["dimensions"] = self.dimensions
        response = await self._client.embeddings.create(**kwargs)
        return [item.embedding for item in response.data]


# ── model2vec provider ───────────────────────────────────────────────


class Model2VecEmbeddingProvider:
    """Generate embeddings locally via model2vec (``potion-base-8M``)."""

    def __init__(self, model_name: str = "minishlab/potion-base-8M") -> None:
        if not _HAS_MODEL2VEC:
            raise RuntimeError("model2vec package is required for local embeddings")
        from model2vec import StaticModel

        self._model = StaticModel.from_pretrained(model_name)
        self.model_name = model_name
        self.dimensions: int = int(self._model.dim)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # model2vec encode is sync and <0.1 ms per query — safe inline.
        embeddings = self._model.encode(texts)
        return [emb.tolist() for emb in embeddings]


# ── Auto-selection ───────────────────────────────────────────────────


def get_embedding_provider(config: LearnConfig | None = None) -> EmbeddingProvider | None:
    """Return the best available embedding provider, or ``None``.

    Selection order (``auto`` mode):
    1. OpenAI — if ``openai`` installed **and** API key configured.
    2. model2vec — if ``model2vec`` installed (no API key needed).
    3. None — fall through to BM25 matching.
    """
    if config is None:
        from trace_mcp.extensions.learn.config import load_config

        config = load_config()

    backend = config.embedding_backend

    if backend == "none":
        return None

    # --- OpenAI ---
    if backend in ("openai", "auto") and _HAS_OPENAI and config.openai_api_key:
        logger.debug("Using OpenAI embedding provider (model=%s)", config.embedding_model)
        return OpenAIEmbeddingProvider(
            api_key=config.openai_api_key,
            model=config.embedding_model,
        )
    if backend == "openai":
        logger.warning("OpenAI embeddings requested but openai package or API key missing — falling back")

    # --- model2vec ---
    if backend in ("model2vec", "auto") and _HAS_MODEL2VEC:
        # Use config model if it looks like a model2vec model, otherwise default
        m2v_model = config.embedding_model if "potion" in config.embedding_model else "minishlab/potion-base-8M"
        logger.debug("Using model2vec embedding provider (model=%s)", m2v_model)
        return Model2VecEmbeddingProvider(model_name=m2v_model)
    if backend == "model2vec":
        logger.warning("model2vec requested but not installed")

    logger.debug("No embedding provider available — falling through to BM25")
    return None


# ── Cosine similarity utilities ──────────────────────────────────────


def cosine_similarity_matrix(query_vec: list[float], matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity between *query_vec* and every row of *matrix*.

    Returns an array of shape ``(n,)`` with values in ``[-1, 1]``.
    Sub-millisecond for 5 000 × 768 on any modern CPU.
    """
    if not _HAS_NUMPY:
        raise RuntimeError("numpy is required for embedding search")
    import numpy as np

    q = np.asarray(query_vec, dtype=np.float32)
    q_norm = float(np.linalg.norm(q))
    if q_norm == 0.0:
        return np.zeros(matrix.shape[0], dtype=np.float32)
    q = q / q_norm

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)  # avoid div-by-zero
    normed = matrix / norms

    return normed @ q
