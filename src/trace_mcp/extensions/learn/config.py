"""Configuration for the trace-learn extension.

**A project's OpenAI key lives in that project's own ``.env``.** Each project
gets its own credential, so one leaked or exhausted key exposes one project
rather than every project on the machine — the same isolation ADR-006 gives
sessions and knowledge stores, applied to the credential that reaches a third
party.

Resolution order for OPENAI_API_KEY and every other setting (highest priority
first):

  1. Environment variable already exported in the process
  2. ``./.env`` — the project's own file, read from the working directory the
     host launches the MCP server in
  3. ``~/.trace/.env`` — machine-wide defaults, a fallback for projects that
     have not been given their own key

Precedence is a MERGE, not first-match-wins: for a setting present in more than
one source, the highest-priority source above supplies the value. The more
specific file wins, which is what "put the key in the project's .env" has to
mean for it to mean anything.

**One exception — ``TRACE_LOCAL_ONLY`` is a restrict-only ratchet.** It is ORed
across every source, so a project ``.env`` (or an exported variable) can turn
the no-egress kill switch ON but can never turn a machine-global one OFF.
Without that exception, the precedence rule above would hand every project a
way to opt out of a machine-wide privacy policy. This mirrors the registry
privacy ratchet in ``effective_learn_config`` (ADR-006).

Configuration is read ONCE, when the extension registers at server start:
editing a ``.env`` requires restarting the MCP server before the change is
live.

A key that cannot be found, or that the provider rejects, is reported loudly —
see ``missing_key_for_cloud``, ``key_search_description`` and
``ApiKeyRejectedError``. Silence there is the worst outcome available: recall
still answers, just without the semantic path, and nothing says so.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

logger = logging.getLogger(__name__)

_TRACE_ENV_PATH = Path.home() / ".trace" / ".env"


def _nonempty(values: dict[str, str]) -> dict[str, str]:
    """Drop blank values, so a placeholder cannot shadow a real setting."""
    return {k: v for k, v in values.items() if v and v.strip()}


def _truthy(value: str | None) -> bool:
    """Shared truthiness for the string flags every source supplies."""
    return (value or "").strip().lower() in ("true", "1", "yes")


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file.

    Handles:
    - Blank lines and full-line comments (``# comment``)
    - Inline comments (``KEY=value  # comment``) — stripped from the value
    - Quoted values (``KEY="value with spaces"``) — quotes preserved,
      inline ``#`` inside quotes is NOT treated as a comment
    """
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        # Strip inline comments. For quoted values, the quote terminates
        # the value and anything after (including `#`) is comment.
        if value and value[0] in ('"', "'"):
            quote = value[0]
            end = value.find(quote, 1)
            if end >= 0:
                value = value[1:end]  # content between quotes
            else:
                value = value[1:]  # unterminated — take rest as-is
        else:
            comment_idx = value.find("#")
            if comment_idx >= 0:
                value = value[:comment_idx].rstrip()
        result[key] = value
    return result


_AUTH_ERROR_NAMES = frozenset({"AuthenticationError"})
_AUTH_STATUS_CODES = frozenset({401})


def is_auth_error(exc: BaseException) -> bool:
    """True when *exc* is the provider rejecting the credential itself (401).

    Deliberately narrow. A 403 (``PermissionDeniedError``) is NOT treated as a
    rejected key: the provider also returns it for a model the account cannot
    access, an unsupported region, and a corporate proxy sitting in front of a
    custom ``base_url``. Reporting those as "your API key was rejected" would
    misdiagnose them, and raising unconditionally would turn an error that used
    to degrade gracefully into an outage. A 403 therefore stays on the ordinary
    strict-mode path — which is loud by default, since strict mode defaults ON
    whenever a key is configured.

    Matched by HTTP status and by exception class name rather than by importing
    the SDK's exception types, so this stays correct when ``openai`` is absent
    (the extension's optional dependency) and when the SDK reorganizes them.
    """
    status = getattr(exc, "status_code", None)
    if status in _AUTH_STATUS_CODES:
        return True
    return type(exc).__name__ in _AUTH_ERROR_NAMES


class LLMFallbackError(RuntimeError):
    """Raised when an LLM operation fails and strict mode forbids falling back.

    In strict mode, the user has signalled that LLM features must work.
    Silent fallback to BM25/rule-based would hide degraded quality, so
    we surface the failure instead.
    """


def redact_key(text: str, api_key: str | None) -> str:
    """Remove *api_key* from *text* before it reaches a log or a tool response.

    Provider errors sometimes echo the credential they rejected, and these
    messages are written to logs and returned to clients.
    """
    if not api_key or len(api_key) < 8:
        return text
    return text.replace(api_key, "<redacted>")


class ApiKeyRejectedError(LLMFallbackError):
    """The provider refused the API key itself (401/403).

    Raised regardless of strict mode, which is the point: strict mode governs
    whether *degradation* is acceptable, not whether the user is told their
    credential was refused. Quietly returning keyword results from a rejected
    key hands back plausible output produced by a broken configuration — the
    one failure shape this project treats as worse than an error.
    """


@dataclass(frozen=True)
class LearnConfig:
    """Configuration for trace-learn matching and extraction backends."""

    # repr=False: the key must never leak into pytest failure output, logs,
    # or debugger dumps (all of which render the dataclass repr). It remains
    # reachable via direct attribute access and dataclasses.asdict() — code
    # and tests must never log/assert on a real key value through those.
    openai_api_key: str | None = field(default=None, repr=False)
    llm_model: str = "gpt-5.4-mini"
    llm_extraction_model: str = "gpt-5.4-mini"
    # Cloud LLM matching/extraction is OPT-IN (local-first): an OPENAI_API_KEY
    # on the machine must not, by itself, route session content to a third
    # party. Enable with TRACE_LLM_ENABLED=true (a key is still required).
    llm_enabled: bool = False
    # Strict mode: if True, LLM failures raise instead of falling back to
    # BM25/rule-based. Auto-defaults to True when an API key is present — the
    # assumption being "if you configured it, you expect it to work."
    strict_llm: bool = True
    # Unified kill switch: when True, ALL cloud egress is off — no OpenAI
    # embeddings and no LLM extraction/matching — regardless of key presence or
    # backend. Set via TRACE_LOCAL_ONLY; enforced in load_config().
    local_only: bool = False
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    tag_weight: float = 0.3
    decay_enabled: bool = True
    decay_half_life_days: float = 365.0
    evergreen_recall_threshold: int = 3
    evergreen_floor: float = 0.8
    dedup_enabled: bool = True
    dedup_threshold: float = 0.85
    embedding_backend: str = "auto"  # "auto" | "fastembed" | "model2vec" | "openai" | "none"
    embedding_model: str = "text-embedding-3-small"
    # Custom OpenAI-compatible endpoint for the "openai" backend, letting a user
    # point it at any local server (Ollama / LM Studio / text-embeddings-inference
    # / vLLM). None → the SDK default (api.openai.com or its own OPENAI_BASE_URL).
    embedding_base_url: str | None = None
    # Where the key came from — "environment", "project", "global", or None.
    # Reported (never the key itself) so a project silently borrowing the
    # machine-wide credential is visible rather than assumed intentional.
    key_source: str | None = None
    key_source_path: str | None = None
    # True when a cloud path was asked for (LLM features, or the OpenAI
    # embedding backend) but no key resolved anywhere. Deliberately offline
    # setups (TRACE_LOCAL_ONLY) are not flagged — they are not a mistake.
    missing_key_for_cloud: bool = False
    project_env_path: str | None = None
    global_env_path: str | None = None

    def key_search_description(self) -> str:
        """Every place a key was looked for, in priority order.

        A "no key found" message that does not say where it looked leaves the
        reader guessing which of three files to edit.
        """
        return (
            f"the OPENAI_API_KEY environment variable, "
            f"the project's own {self.project_env_path or './.env'}, "
            f"and the machine-global {self.global_env_path or '~/.trace/.env'}"
        )

    def key_origin(self) -> str:
        """One phrase naming where the active key came from."""
        if self.key_source == "environment":
            return "the OPENAI_API_KEY environment variable"
        if self.key_source == "project":
            return f"this project's {self.project_env_path or './.env'}"
        if self.key_source == "global":
            return f"the machine-global {self.global_env_path or '~/.trace/.env'}"
        return "no source (no key configured)"


def load_config() -> LearnConfig:
    """Load trace-learn config from env vars and .env files.

    The user puts their OpenAI key in ONE place — ``~/.trace/.env`` — and
    every TRACE project picks it up automatically.  Env vars take precedence
    so CI / containers can override.

    The key alone activates nothing: cloud LLM matching/extraction also
    requires the explicit ``TRACE_LLM_ENABLED=true`` opt-in, and cloud
    embeddings require an explicit ``TRACE_EMBEDDING_BACKEND=openai``.
    ``TRACE_LOCAL_ONLY=1`` overrides both (no egress anywhere).
    """
    # Low-priority → high-priority merge. The project's own file wins over the
    # machine-global one: "put the key in the project's .env" has to mean the
    # project's key is the one used, or it means nothing. (The order was once
    # reversed, which silently ignored every per-project key.)
    project_env_path = Path.cwd() / ".env"
    project_env = _parse_dotenv(project_env_path)
    global_env = _parse_dotenv(_TRACE_ENV_PATH)

    # An EMPTY value never overrides a real one. Copying a template that
    # contains a bare `OPENAI_API_KEY=` would otherwise mask a working key from
    # a lower-priority source — and, because the same template leaves the cloud
    # flags off, without tripping the missing-key warning either. Same hazard
    # for an exported-but-empty variable (`docker run -e OPENAI_API_KEY`).
    # To deliberately stop a project using an inherited key, set
    # TRACE_LOCAL_ONLY=true rather than blanking the value.
    merged = {**_nonempty(global_env), **_nonempty(project_env)}
    for key in (
        "OPENAI_API_KEY",
        "TRACE_LLM_MODEL",
        "TRACE_LLM_EXTRACTION_MODEL",
        "TRACE_LLM_ENABLED",
        "TRACE_STRICT_LLM",
        "TRACE_LOCAL_ONLY",
        "TRACE_BM25_K1",
        "TRACE_BM25_B",
        "TRACE_TAG_WEIGHT",
        "TRACE_DECAY_ENABLED",
        "TRACE_DECAY_HALF_LIFE_DAYS",
        "TRACE_EVERGREEN_RECALL_THRESHOLD",
        "TRACE_EVERGREEN_FLOOR",
        "TRACE_DEDUP_ENABLED",
        "TRACE_DEDUP_THRESHOLD",
        "TRACE_EMBEDDING_BACKEND",
        "TRACE_EMBEDDING_MODEL",
        "OPENAI_BASE_URL",
        "TRACE_OPENAI_BASE_URL",
    ):
        env_val = os.environ.get(key)
        if env_val is not None and env_val.strip():
            merged[key] = env_val

    api_key = merged.get("OPENAI_API_KEY") or None

    # Which source supplied the key. Reported (never the key) so that a project
    # quietly borrowing the machine-wide credential is visible.
    key_source: str | None = None
    key_source_path: str | None = None
    if api_key:
        if os.environ.get("OPENAI_API_KEY"):
            key_source = "environment"
        elif project_env.get("OPENAI_API_KEY"):
            key_source, key_source_path = "project", str(project_env_path)
        else:
            key_source, key_source_path = "global", str(_TRACE_ENV_PATH)

    # RESTRICT-ONLY RATCHET: any source may switch no-egress ON; none may
    # switch it OFF. Without this, the precedence flip above would let a
    # project .env opt out of a machine-wide privacy policy.
    local_only = any(_truthy(source.get("TRACE_LOCAL_ONLY")) for source in (global_env, project_env, dict(os.environ)))

    # Cloud LLM matching/extraction is OPT-IN (local-first): unset means OFF,
    # even when an API key is present. Distinguish "unset" from an explicit
    # false so the informational nudge below never nags a user who opted out.
    raw_llm_enabled = merged.get("TRACE_LLM_ENABLED")
    llm_enabled = (raw_llm_enabled or "false").lower() in ("true", "1", "yes")

    # Strict mode: default ON when API key is present.
    # If the user bothered to configure an API key, fall-backs should fail
    # loudly rather than silently degrade to BM25.
    strict_default = "true" if api_key else "false"
    strict_llm = merged.get("TRACE_STRICT_LLM", strict_default).lower() in ("true", "1", "yes")

    embedding_backend_requested = merged.get("TRACE_EMBEDDING_BACKEND", "auto")
    cloud_requested = llm_enabled or embedding_backend_requested == "openai"
    # A cloud path was asked for and there is no credential for it. Recorded on
    # the config rather than swallowed here: registration must still succeed
    # (a server that refuses to start over a missing optional key is a worse
    # failure than a loud one), so the callers surface it — the session-start
    # banner and every learn-tool response that would have used the cloud.
    # Deliberately-offline setups are not a mistake and are not flagged.
    missing_key_for_cloud = cloud_requested and not api_key and not local_only

    if missing_key_for_cloud:
        logger.error(
            "No OPENAI_API_KEY found, but a cloud path was requested "
            "(TRACE_LLM_ENABLED=%s, TRACE_EMBEDDING_BACKEND=%s). Looked in: the OPENAI_API_KEY "
            "environment variable, the project's own %s, and the machine-global %s. "
            "Falling back to local keyword matching — results will NOT use semantic ranking. "
            "Put this project's key in %s, then restart the MCP server.",
            raw_llm_enabled,
            embedding_backend_requested,
            project_env_path,
            _TRACE_ENV_PATH,
            project_env_path,
        )
    if llm_enabled and not api_key:
        llm_enabled = False
    elif llm_enabled and api_key and strict_llm:
        logger.warning(
            "TRACE strict LLM mode is ON (model=%s). "
            "LLM failures will raise errors instead of silently falling back to BM25. "
            "Set TRACE_STRICT_LLM=false to allow silent fallback.",
            merged.get("TRACE_LLM_MODEL", "gpt-5.4-mini"),
        )
    elif api_key and raw_llm_enabled is None and not local_only:
        # A key exists but the user never chose: say so once at config load,
        # instead of silently ignoring the key (the pre-opt-in default used it).
        logger.info(
            "OPENAI_API_KEY found, but cloud LLM matching/extraction is OFF by default "
            "(local-first). Set TRACE_LLM_ENABLED=true to enable it."
        )
    embedding_backend = merged.get("TRACE_EMBEDDING_BACKEND", "auto")
    if local_only:
        # One switch, all three egress paths: force cloud LLM features off and
        # override an explicit OpenAI embedding backend to the local-first "auto".
        # Closes the off-switch trap (TRACE_LLM_ENABLED=false alone still egressed
        # via embeddings; TRACE_EMBEDDING_BACKEND=none alone still egressed via
        # LLM matching/extraction).
        if llm_enabled:
            logger.info("TRACE_LOCAL_ONLY is set — forcing LLM features off (no cloud extraction/matching).")
        llm_enabled = False
        if embedding_backend == "openai":
            logger.warning("TRACE_LOCAL_ONLY is set — overriding TRACE_EMBEDDING_BACKEND=openai to local 'auto'.")
            embedding_backend = "auto"

    decay_enabled = merged.get("TRACE_DECAY_ENABLED", "true").lower() in ("true", "1", "yes")
    dedup_enabled = merged.get("TRACE_DEDUP_ENABLED", "true").lower() in ("true", "1", "yes")

    return LearnConfig(
        openai_api_key=api_key,
        llm_model=merged.get("TRACE_LLM_MODEL", "gpt-5.4-mini"),
        llm_extraction_model=merged.get("TRACE_LLM_EXTRACTION_MODEL", "gpt-5.4-mini"),
        llm_enabled=llm_enabled,
        strict_llm=strict_llm,
        local_only=local_only,
        bm25_k1=float(merged.get("TRACE_BM25_K1", "1.5")),
        bm25_b=float(merged.get("TRACE_BM25_B", "0.75")),
        tag_weight=float(merged.get("TRACE_TAG_WEIGHT", "0.3")),
        decay_enabled=decay_enabled,
        decay_half_life_days=float(merged.get("TRACE_DECAY_HALF_LIFE_DAYS", "365.0")),
        evergreen_recall_threshold=int(merged.get("TRACE_EVERGREEN_RECALL_THRESHOLD", "3")),
        evergreen_floor=float(merged.get("TRACE_EVERGREEN_FLOOR", "0.8")),
        dedup_enabled=dedup_enabled,
        dedup_threshold=float(merged.get("TRACE_DEDUP_THRESHOLD", "0.85")),
        embedding_backend=embedding_backend,
        embedding_model=merged.get("TRACE_EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_base_url=merged.get("TRACE_OPENAI_BASE_URL") or merged.get("OPENAI_BASE_URL") or None,
        key_source=key_source,
        key_source_path=key_source_path,
        missing_key_for_cloud=missing_key_for_cloud,
        project_env_path=str(project_env_path),
        global_env_path=str(_TRACE_ENV_PATH),
    )


def _clear_cloud_expectation(config: LearnConfig) -> LearnConfig:
    """Drop the missing-key flag once a config has been ratcheted offline.

    A project the registry forces local-only is not asking for a cloud path, so
    warning that it has no key for one would be noise on every response — and
    noise is how a real warning stops being read.
    """
    if not config.missing_key_for_cloud:
        return config
    return replace(config, missing_key_for_cloud=False)


def effective_learn_config(base: LearnConfig, project: str) -> LearnConfig:
    """Apply the project's registry privacy posture to *base* — a RESTRICT-ONLY ratchet.

    A registry entry may TIGHTEN the machine-global config for one project (force
    a confidentiality-bound client project local-only against a permissive
    global) but can never loosen a global restriction: fields here are only ever
    set to their more-restrictive value (ADR-006 §6). ``.env``/env-var precedence
    inside ``load_config`` is untouched — this layers on top of its result.

    Applied per call at the three egress decision points (extraction, LLM
    matching, embedding-backend selection), pinned or not: the learn tools
    resolve their ``project`` argument either way, so a restrictive entry
    protects the project from every process that touches it.

    Returns *base* itself (identity, not a copy) when no restriction applies —
    callers use ``eff is base`` to keep the common path on the process-wide
    backend instead of constructing per-project ones.

    Raises:
        RegistryUnavailableError: the registry exists but cannot be read. The
            posture that would forbid egress may live in that unreadable file,
            so knowledge/egress paths must fail closed rather than proceed on
            the global config. (An ABSENT registry is the normal pre-migration
            state and applies no restriction.) Session capture never calls
            this, so capture is never blocked by registry damage.
    """
    from trace_mcp.project_identity import get_registry_cached, key_for_label

    key = key_for_label(project)
    if not key:
        return base
    registry = get_registry_cached()  # raises RegistryUnavailableError on damage
    if registry is None:
        return base
    entry = registry.projects.get(key)
    if entry is None:
        return base

    pc = entry.config
    force_local = bool(pc.local_only)
    force_llm_off = force_local or pc.llm_enabled is False
    downgrade_embedding = (force_local or pc.embedding_backend_max == "local") and base.embedding_backend == "openai"

    if (
        not (force_local and not base.local_only)
        and not (force_llm_off and base.llm_enabled)
        and not downgrade_embedding
    ):
        return base

    eff = base
    if force_local and not base.local_only:
        # Mirror load_config's own TRACE_LOCAL_ONLY semantics: one switch, all
        # three egress paths.
        eff = replace(eff, local_only=True, llm_enabled=False)
        # A project ratcheted offline is not asking for a cloud path, so it must
        # not be warned about lacking a key for one.
        eff = _clear_cloud_expectation(eff)
        if eff.embedding_backend == "openai":
            eff = replace(eff, embedding_backend="auto")
    if force_llm_off and eff.llm_enabled:
        eff = replace(eff, llm_enabled=False)
    if downgrade_embedding and eff.embedding_backend == "openai":
        eff = replace(eff, embedding_backend="auto")
    logger.info(
        "Per-project privacy ratchet active for '%s': local_only=%s llm_enabled=%s embedding_backend=%s",
        key,
        eff.local_only,
        eff.llm_enabled,
        eff.embedding_backend,
    )
    return eff
