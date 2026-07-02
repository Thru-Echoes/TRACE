"""Embedding providers for trace-learn.

Generates vector embeddings for learning content.  Four providers:

1. **fastembed** ``snowflake/snowflake-arctic-embed-s`` — local ONNX transformer
   (no PyTorch), the "local-strong" tier: markedly stronger retrieval than static
   embeddings while staying condensed and offline-capable
2. **model2vec** ``potion-base-8M`` — local static, ~8 MB, no PyTorch, sub-ms
3. **OpenAI** ``text-embedding-3-small`` — cloud; EXPLICIT opt-in only. A custom
   ``base_url`` points this at any OpenAI-compatible local server (Ollama / vLLM /
   LM Studio / text-embeddings-inference) — a fully-local bring-your-own path
4. **None** — no embeddings, fall through to BM25 matching

Local-first auto-selection: fastembed (if pkg) → model2vec (if pkg) → None.
OpenAI is never auto-selected from a mere key — cloud egress is opt-in
(``TRACE_EMBEDDING_BACKEND=openai``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

    from trace_mcp.extensions.learn.config import LearnConfig

logger = logging.getLogger(__name__)

# ── Feature detection ────────────────────────────────────────────────
# NB: these are module-level imports (not importlib.util.find_spec) on purpose:
# the optional-backend symbols (AsyncOpenAI, StaticModel) double as the patch
# targets the provider unit tests mock, so they must exist as module attributes.

try:
    import numpy as _np  # noqa: F401  # pyright: ignore[reportUnusedImport]  (runtime probe)

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _HAS_NUMPY = False

try:
    from openai import AsyncOpenAI  # noqa: F401  # pyright: ignore[reportUnusedImport]

    _HAS_OPENAI = True
except ImportError:  # pragma: no cover
    _HAS_OPENAI = False

try:
    from model2vec import StaticModel  # noqa: F401  # pyright: ignore[reportUnusedImport]

    _HAS_MODEL2VEC = True
except ImportError:  # pragma: no cover
    _HAS_MODEL2VEC = False

try:
    from fastembed import TextEmbedding  # noqa: F401  # pyright: ignore[reportMissingImports,reportUnusedImport]

    _HAS_FASTEMBED = True
except ImportError:  # pragma: no cover
    TextEmbedding = None  # patch target must exist as a module attr even when fastembed is absent
    _HAS_FASTEMBED = False


# ── Curated fastembed model allowlist (local-strong tier) ────────────────
# Small, permissively-licensed (Apache-2.0/MIT) transformers that ship as
# quantized int8 ONNX (no PyTorch), run offline after a one-time model download,
# and produce compact vectors. Selected via ``TRACE_EMBEDDING_MODEL``. A model
# NOT listed here is still usable (with a warning) but its license and embedding
# dimension are the user's responsibility. Fully-arbitrary, TRACE-managed custom
# models are a deliberately deferred future plan (see docs/embeddings.md).
FASTEMBED_ALLOWLIST: dict[str, dict[str, Any]] = {
    "snowflake/snowflake-arctic-embed-s": {"dim": 384, "license": "Apache-2.0"},
    "snowflake/snowflake-arctic-embed-m": {"dim": 768, "license": "Apache-2.0"},
    "BAAI/bge-small-en-v1.5": {"dim": 384, "license": "MIT"},
    "BAAI/bge-base-en-v1.5": {"dim": 768, "license": "MIT"},
}
DEFAULT_FASTEMBED_MODEL = "snowflake/snowflake-arctic-embed-s"


# ── Provider protocol ────────────────────────────────────────────────


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Interface for embedding generation backends."""

    model_name: str

    @property
    def dimensions(self) -> int:
        """Embedding vector dimensionality. Read-only so providers may
        compute it lazily without violating Protocol invariance;
        a concrete ``int`` attribute also satisfies this."""
        ...

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
        base_url: str | None = None,
    ) -> None:
        if not _HAS_OPENAI:
            raise RuntimeError("openai package is required for OpenAI embeddings")
        from openai import AsyncOpenAI

        # A custom base_url points this backend at any OpenAI-compatible local
        # server (Ollama / LM Studio / text-embeddings-inference / vLLM) — a
        # fully-local, bring-your-own-model path that keeps TRACE out of weight
        # and license management. Omitted entirely when unset so the SDK default
        # (api.openai.com, or the SDK's own OPENAI_BASE_URL handling) applies.
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**client_kwargs)
        self.model_name = model
        self.dimensions = dimensions  # 0 = use model default
        self.base_url = base_url

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
        self.model_name = model_name
        # The StaticModel is loaded LAZILY on first embedding use, not
        # at construction. The provider is built in the extension's
        # register() at server startup; eager loading meant every concurrent
        # session held a resident model (large idle RAM) and paid a
        # cold-start latency (a root cause of E2E test flakiness).
        self._model: Any = None

    def _get_model(self) -> Any:
        """Load and cache the StaticModel on first use (lazy load)."""
        if self._model is None:
            from model2vec import StaticModel

            self._model = StaticModel.from_pretrained(self.model_name)
        return self._model

    @property
    def dimensions(self) -> int:
        return int(self._get_model().dim)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # model2vec encode is sync and <0.1 ms per query — safe inline.
        embeddings = self._get_model().encode(texts)
        return [emb.tolist() for emb in embeddings]


# ── fastembed provider (local ONNX transformer, no PyTorch) ──────────────


class FastEmbedEmbeddingProvider:
    """Generate embeddings locally via fastembed (ONNX Runtime, no PyTorch).

    The "local-strong" tier: a small permissive transformer (default
    ``snowflake/snowflake-arctic-embed-s``, Apache-2.0, 384-dim) that is markedly
    stronger at retrieval than the static model2vec tier while staying condensed
    and offline-capable. Weights are downloaded once as quantized int8 ONNX and
    cached by fastembed; no API key and no third-party egress of user content.
    """

    def __init__(self, model_name: str = DEFAULT_FASTEMBED_MODEL) -> None:
        if not _HAS_FASTEMBED:
            raise RuntimeError("fastembed package is required for fastembed embeddings")
        self.model_name = model_name
        meta = FASTEMBED_ALLOWLIST.get(model_name, {})
        self.dimensions = int(meta.get("dim", 0))  # 0 = unknown (non-allowlisted model)
        # Lazy-load the ONNX model on first use (mirrors the model2vec provider):
        # avoids a resident model + cold-start per idle session at server startup.
        self._model: Any = None

    def _get_model(self) -> Any:
        """Load and cache the fastembed model on first use (lazy load)."""
        if self._model is None:
            assert TextEmbedding is not None  # guaranteed by _HAS_FASTEMBED gate in __init__
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # fastembed's ONNX encode is sync CPU work (tens of ms per short text);
        # fast enough inline for the small per-project stores TRACE holds.
        # ``.embed`` returns a generator of numpy arrays.
        model = self._get_model()
        return [[float(x) for x in vec] for vec in model.embed(list(texts))]


def _select_fastembed_model(requested: str) -> str:
    """Resolve the fastembed model id to load.

    - An allowlisted (vetted, permissive) model id is used as-is.
    - Any other explicit HF-style id (``org/model``) is honored with a warning —
      its license and embedding dimension are the user's responsibility.
    - Otherwise (e.g. the OpenAI default model string is still in place) the
      permissive default (``snowflake/snowflake-arctic-embed-s``) is used.
    """
    if requested in FASTEMBED_ALLOWLIST:
        return requested
    if requested and requested != "text-embedding-3-small" and "/" in requested:
        logger.warning(
            "fastembed model %r is not in the vetted allowlist; using it anyway — "
            "verify its license and embedding dimension yourself.",
            requested,
        )
        return requested
    return DEFAULT_FASTEMBED_MODEL


# ── Auto-selection ───────────────────────────────────────────────────


def get_embedding_provider(config: LearnConfig | None = None) -> EmbeddingProvider | None:
    """Return the best available embedding provider, or ``None``.

    Local-first selection order (``auto`` mode):
    1. fastembed — if ``fastembed`` installed (local ONNX, no key, no egress).
    2. model2vec — if ``model2vec`` installed (local static, no key).
    3. None — fall through to BM25 matching.

    OpenAI is **never** auto-selected: a mere API key must not route embedding
    content off-machine. Cloud egress requires an explicit
    ``TRACE_EMBEDDING_BACKEND=openai`` (which then honors a custom ``base_url``
    for OpenAI-compatible local servers).
    """
    if config is None:
        from trace_mcp.extensions.learn.config import load_config

        config = load_config()

    from trace_mcp.extensions.learn.config import LLMFallbackError

    backend = config.embedding_backend

    # Belt-and-suspenders for the unified kill switch: a local-only config must
    # never reach the cloud provider even if backend=="openai". load_config()
    # already downgrades this, but library callers / tests may build a
    # LearnConfig directly and bypass that enforcement.
    if config.local_only and backend == "openai":
        backend = "auto"

    if backend == "none":
        return None

    # --- fastembed (local ONNX transformer, "local-strong" tier) ---
    if backend in ("fastembed", "auto") and _HAS_FASTEMBED:
        fe_model = _select_fastembed_model(config.embedding_model)
        logger.info("Using fastembed embedding provider (model=%s)", fe_model)
        return FastEmbedEmbeddingProvider(model_name=fe_model)
    if backend == "fastembed":
        msg = "fastembed embeddings requested (TRACE_EMBEDDING_BACKEND=fastembed) but package not installed"
        if config.strict_llm:
            logger.error("%s. Refusing to fall back.", msg)
            raise LLMFallbackError(
                f"{msg}. Install with: pip install 'trace-mcp[local-embed]'. Or set TRACE_EMBEDDING_BACKEND=auto."
            )
        logger.warning("%s. Falling back to next available backend.", msg)

    # --- model2vec (local static) ---
    if backend in ("model2vec", "auto") and _HAS_MODEL2VEC:
        # Use config model if it looks like a model2vec model, otherwise default
        m2v_model = config.embedding_model if "potion" in config.embedding_model else "minishlab/potion-base-8M"
        logger.info("Using model2vec embedding provider (model=%s)", m2v_model)
        return Model2VecEmbeddingProvider(model_name=m2v_model)
    if backend == "model2vec":
        msg = "model2vec embeddings requested but package not installed"
        if config.strict_llm:
            logger.error("%s. Refusing to fall back.", msg)
            raise LLMFallbackError(f"{msg}. Install with: pip install model2vec. Or set TRACE_EMBEDDING_BACKEND=auto.")
        logger.warning("%s. Falling back.", msg)

    # --- OpenAI (cloud) — EXPLICIT opt-in only; never auto-selected ---
    # Local-first posture: a mere OPENAI_API_KEY on the machine must NOT route
    # embedding content to a third party. Cloud egress requires an explicit
    # TRACE_EMBEDDING_BACKEND=openai (which honors base_url for local, OpenAI-
    # compatible servers). This is why "auto" above never reaches OpenAI.
    if backend == "openai" and _HAS_OPENAI and config.openai_api_key:
        logger.info("Using OpenAI embedding provider (model=%s)", config.embedding_model)
        return OpenAIEmbeddingProvider(
            api_key=config.openai_api_key,
            model=config.embedding_model,
            base_url=config.embedding_base_url,
        )
    if backend == "openai":
        # User explicitly asked for OpenAI embeddings but they're unavailable.
        msg = (
            f"OpenAI embeddings requested (TRACE_EMBEDDING_BACKEND=openai) but "
            f"unavailable: openai_package={_HAS_OPENAI}, api_key_present="
            f"{bool(config.openai_api_key)}."
        )
        if config.strict_llm:
            logger.error("%s Refusing to fall back.", msg)
            raise LLMFallbackError(
                f"{msg} Install 'openai' and set OPENAI_API_KEY, or set "
                f"TRACE_EMBEDDING_BACKEND=auto/fastembed/model2vec to use a local backend."
            )
        logger.warning("%s Falling back to next available backend.", msg)

    logger.info("No embedding provider available — falling through to BM25 matching")
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
