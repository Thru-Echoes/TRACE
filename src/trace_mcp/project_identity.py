"""Canonical project identity and the fail-closed alias registry (ADR-006).

Core module — stdlib + pydantic only, with **zero imports from
``extensions/``** (it may be imported by both core and extensions; extensions
import core, never the reverse, per ADR-003). It gives every layer of TRACE one
authoritative notion of "same project", replacing today's two inconsistent
relations (exact case-sensitive label equality in session queries vs. a
lossy, case-folding filename derivation in the knowledge store).

Exports
-------
Identity:
    ``canonical_project_key`` — the single normalization every layer compares by.
    ``session_project_key`` / ``session_matches_project`` — a session record's key.
    ``resolve_project_key`` — label → key via the registry (with enrollment).
    ``validate_project_session`` — fail-closed (project, session) coherence (INV-6).
    ``get_bound_project`` — the process's ``TRACE_PROJECT`` pin, if any.
    ``RESERVED_KEYS`` — ``{"auto", "shared"}``.

Registry (``~/.trace/projects.json``, overridable via ``TRACE_REGISTRY_PATH``):
    ``ProjectRegistry`` / ``ProjectEntry`` / ``ProjectConfig`` / ``RegistryChange``.
    ``load_registry`` / ``get_registry_cached`` / ``registry_path``.
    ``locked_registry`` — fail-closed read-modify-write of the registry (INV-7).
    ``exclusive_file_lock`` — the reusable sync O_EXCL + PID-liveness lock.

Exceptions:
    ``ProjectKeyError`` — a label cannot form a valid canonical key.
    ``ProjectMismatchError`` — a (project, session) pair names two projects.
    ``RegistryUnavailableError`` — the registry is missing/corrupt/unknown-major.
    ``StoreIdentityError`` — a knowledge store's on-disk label ≠ its key (used in S4).

Side effects: reads/writes ``~/.trace/projects.json`` and a sibling
``projects.json.lock``; reads the ``TRACE_PROJECT`` / ``TRACE_REGISTRY_PATH``
environment variables.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import time
import unicodedata
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Core → core import. ``_holder_status`` is the single PID-liveness policy behind
# the session lock; reusing it (rather than re-deriving it) keeps lock-steal
# safety a single implementation — the same reasoning as INV-1.
from trace_mcp.storage.json_file import _holder_status

if TYPE_CHECKING:
    from trace_mcp.storage.base import TraceStorage


# ── Exceptions ────────────────────────────────────────────────────────────


class ProjectKeyError(ValueError):
    """A project label cannot be reduced to a non-empty canonical key."""


class ProjectMismatchError(Exception):
    """A (project, session) pair names two different canonical projects (INV-6)."""


class RegistryUnavailableError(Exception):
    """The project registry exists but is corrupt or an unknown major version.

    Distinct from "absent" (no file yet): absence is a normal pre-migration
    state that degrades gracefully, whereas an unreadable file must fail closed
    and MUST NOT be silently overwritten.
    """


class StoreIdentityError(Exception):
    """A knowledge store's on-disk project label disagrees with its key (used in S4)."""


# ── Canonical key ─────────────────────────────────────────────────────────

RESERVED_KEYS: frozenset[str] = frozenset({"auto", "shared"})
"""Keys that may exist as pre-seeded registry entries but can never be enrolled
from a user-supplied label: ``auto`` (unattributed-session quarantine) and
``shared`` (reserved against a future cross-project channel)."""

_SEP_RUN = re.compile(r"[\s/_]+")
_NON_KEY_RUN = re.compile(r"[^\w.-]+")
_DOT_RUN = re.compile(r"\.{2,}")
_DASH_RUN = re.compile(r"-{2,}")


def canonical_project_key(label: str) -> str:
    """Return the stable canonical key for a project display *label*.

    Algorithm: Unicode NFC-normalize → strip → casefold → fold every run of
    whitespace / ``/`` / ``_`` and every run of characters outside ``[\\w.-]``
    to a single ``-`` → collapse ``..`` and ``--`` runs → strip leading/trailing
    ``.``/``-``. Underscore is folded (not preserved) so ``proj a``, ``proj/a``
    and ``proj_a`` share one key.

    Two properties are guaranteed (and pinned by tests):

    * **idempotent** — ``canonical_project_key(k) == k`` when *k* is already a key.
    * **``sanitize_name`` fixed point** — ``sanitize_name(k) == k``, so filename
      identity equals semantic identity: a case-insensitive filesystem can
      neither merge two distinct keys nor split one (the drift/collision class
      ADR-006 closes).

    Raises:
        ProjectKeyError: *label* has no usable characters.
    """
    s = unicodedata.normalize("NFC", label).strip().casefold()
    s = _SEP_RUN.sub("-", s)
    s = _NON_KEY_RUN.sub("-", s)
    s = _DOT_RUN.sub(".", s)
    s = _DASH_RUN.sub("-", s)
    s = s.strip(".-")
    if not s:
        raise ProjectKeyError(f"project label {label!r} has no usable characters for a canonical key")
    return s


# ── Registry models ───────────────────────────────────────────────────────


class ProjectConfig(BaseModel):
    """Per-project privacy posture — a RESTRICT-ONLY ratchet (ADR-006 §6).

    A registry entry may *tighten* a global setting (force a client project
    local-only) but can never loosen a global restriction; the ratchet itself
    is applied by the learn extension in S4, this is only the carrier.
    """

    model_config = ConfigDict(extra="allow")

    local_only: bool = False
    llm_enabled: bool | None = None
    embedding_backend_max: Literal["local", "any"] = "any"


class ProjectEntry(BaseModel):
    """One canonical project: its key, human label, and every historical alias."""

    model_config = ConfigDict(extra="allow")

    key: str
    display_label: str
    aliases: list[str] = Field(default_factory=list)
    status: Literal["active", "retired", "quarantined", "merged"] = "active"
    merged_into: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    enrolled_by: str = ""
    config: ProjectConfig = Field(default_factory=ProjectConfig)
    notes: str = ""


class RegistryChange(BaseModel):
    """An append-only audit entry for every registry mutation."""

    model_config = ConfigDict(extra="allow")

    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str = ""
    action: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ProjectRegistry(BaseModel):
    """The alias registry: canonical keys → entries, plus an append-only history.

    ``version`` is the registry format line, versioned independently of the
    session ``SCHEMA_VERSION`` (it evolves on a different cadence); a major
    mismatch fails closed on load. ``extra='allow'`` round-trips unknown fields
    from a newer writer.
    """

    model_config = ConfigDict(extra="allow")

    version: str = "1"
    strict: bool = False
    projects: dict[str, ProjectEntry] = Field(default_factory=dict)
    history: list[RegistryChange] = Field(default_factory=list)

    def resolve(self, label: str) -> str | None:
        """Return the canonical key *label* belongs to, or None if unknown.

        Match order: verbatim key → verbatim alias → canonical-key equality →
        canonicalized-alias equality. Tolerant of a *label* that cannot form a
        key (treated as no match).
        """
        if label in self.projects:
            return label
        for key, entry in self.projects.items():
            if label in entry.aliases:
                return key
        try:
            canon = canonical_project_key(label)
        except ProjectKeyError:
            return None
        if canon in self.projects:
            return canon
        for key, entry in self.projects.items():
            if canon == key:
                return key
            for alias in entry.aliases:
                try:
                    if canonical_project_key(alias) == canon:
                        return key
                except ProjectKeyError:
                    continue
        return None

    def assert_unique_aliases(self) -> None:
        """Raise if any identifier (key or alias, verbatim or canonical) maps to two entries.

        Enforced before every persist so the registry cannot drift into an
        ambiguous state where one label resolves to two projects.
        """
        seen: dict[str, str] = {}
        for key, entry in self.projects.items():
            idents: set[str] = {key}
            for raw in (key, *entry.aliases):
                idents.add(raw)
                try:
                    idents.add(canonical_project_key(raw))
                except ProjectKeyError:
                    continue
            for ident in idents:
                owner = seen.get(ident)
                if owner is not None and owner != key:
                    raise ValueError(
                        f"registry inconsistency: identifier {ident!r} resolves to both '{owner}' and '{key}'"
                    )
                seen[ident] = key


# ── Registry persistence (fail-closed) ────────────────────────────────────

_DEFAULT_REGISTRY = os.path.expanduser("~/.trace/projects.json")
_REGISTRY_MAJOR = "1"
_registry_cache: tuple[str, float, ProjectRegistry | None] | None = None


def registry_path() -> Path:
    """Path to the project registry (``TRACE_REGISTRY_PATH`` or ``~/.trace/projects.json``)."""
    return Path(os.environ.get("TRACE_REGISTRY_PATH", _DEFAULT_REGISTRY))


@contextlib.contextmanager
def exclusive_file_lock(
    lock_path: str | Path,
    *,
    timeout: float = 10.0,
    steal_after: float = 60.0,
    poll: float = 0.02,
) -> Iterator[None]:
    """Sync fail-closed advisory lock — the ``JsonFileStorage.lock`` pattern.

    Exclusive lock file (``O_CREAT | O_EXCL``), no fcntl/filelock dependency. A
    held lock is stolen only if its holder process is provably dead (single-host
    PID liveness via ``_holder_status``) or — for an unparseable/legacy token —
    if it is older than *steal_after*; a live holder is never stolen. A byte +
    mtime re-verify guards the steal against a TOCTOU where a fresh lock replaces
    the stale one mid-check. **Fail closed:** raises ``TimeoutError`` rather than
    proceeding unlocked (S4/registry writes must be visible, not silently raced).

    Reused by the knowledge-store lock in S4 (keyed by canonical key there).

    Raises:
        TimeoutError: the lock could not be acquired within *timeout*.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}:{time.time_ns()}".encode()
    acquired = False
    waited = 0.0
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, token)
            finally:
                os.close(fd)
            acquired = True
            break
        except FileExistsError:
            pass  # held — decide below whether to steal

        stole = False
        try:
            token_seen = lock_path.read_bytes()
            before = os.stat(lock_path)
            holder = _holder_status(token_seen)
            stale_by_time = (time.time() - before.st_mtime) > steal_after
            if holder == "dead" or (holder == "unknown" and stale_by_time):
                after = os.stat(lock_path)
                if after.st_mtime_ns == before.st_mtime_ns and lock_path.read_bytes() == token_seen:
                    os.unlink(lock_path)
                    stole = True
        except OSError:
            pass  # lock vanished mid-check — retry (counts against the budget)
        if stole:
            continue
        if waited >= timeout:
            raise TimeoutError(
                f"Could not acquire lock {lock_path} within {timeout}s; another writer holds it. "
                "Refusing to write unlocked (fail-closed)."
            )
        time.sleep(poll)
        waited += poll
    try:
        yield
    finally:
        if acquired:
            try:
                os.unlink(lock_path)
            except OSError:
                pass


def load_registry(*, required: bool) -> ProjectRegistry | None:
    """Load the registry from disk. Fail closed on corruption; never rewrite.

    Returns the parsed ``ProjectRegistry``; ``None`` when the file is absent and
    *required* is False (the normal pre-migration state).

    Raises:
        RegistryUnavailableError: file absent and *required* is True, or the file
            is unreadable, not a JSON object, an unknown major version, or fails
            schema validation. The on-disk file is never modified on this path.
    """
    path = registry_path()
    if not path.exists():
        if required:
            raise RegistryUnavailableError(f"project registry not found at {path}")
        return None
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise RegistryUnavailableError(f"project registry at {path} is unreadable/corrupt: {exc}") from exc
    if not isinstance(raw, dict):
        raise RegistryUnavailableError(f"project registry at {path} is not a JSON object")
    version = str(raw.get("version", "1"))
    if version.split(".", 1)[0] != _REGISTRY_MAJOR:
        raise RegistryUnavailableError(
            f"project registry at {path} has unknown major version {version!r} "
            f"(this build understands major {_REGISTRY_MAJOR}); refusing to read or rewrite it."
        )
    try:
        return ProjectRegistry.model_validate(raw)
    except ValidationError as exc:
        raise RegistryUnavailableError(f"project registry at {path} failed validation: {exc}") from exc


def get_registry_cached() -> ProjectRegistry | None:
    """Return the registry, cached by (path, mtime). ``None`` if absent.

    Cheap enough to call per-operation; the cache is keyed on the path so a
    test's ``TRACE_REGISTRY_PATH`` override cannot see another test's registry.
    A corrupt/unknown-major file raises ``RegistryUnavailableError`` (never
    cached as "absent").
    """
    global _registry_cache
    path = registry_path()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return None
    key = str(path)
    if _registry_cache is not None and _registry_cache[0] == key and _registry_cache[1] == mtime:
        return _registry_cache[2]
    reg = load_registry(required=False)
    _registry_cache = (key, mtime, reg)
    return reg


def _reset_registry_cache() -> None:
    """Clear the mtime cache. For tests that write a registry then read it back."""
    global _registry_cache
    _registry_cache = None


def _atomic_write_registry(path: Path, registry: ProjectRegistry) -> None:
    """Serialize *registry* to *path* atomically (temp + ``os.replace``).

    The SOLE registry write path — every mutation routes through
    ``locked_registry`` which calls this under the lock (INV-7).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(registry.model_dump(mode="json"), indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".projects-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def locked_registry() -> Iterator[ProjectRegistry]:
    """Fail-closed read-modify-write of the registry (INV-7).

    Acquires the registry lock, yields the freshest on-disk ``ProjectRegistry``
    (an empty default if none exists), and on CLEAN exit validates alias
    uniqueness and persists it atomically. If the caller raises, nothing is
    written. If the on-disk registry is corrupt, ``load_registry`` raises
    ``RegistryUnavailableError`` and the file is left untouched.

    Raises:
        TimeoutError: the registry lock could not be acquired (fail-closed).
        RegistryUnavailableError: the on-disk registry is corrupt/unknown-major.
    """
    path = registry_path()
    lock_path = path.with_name(path.name + ".lock")
    with exclusive_file_lock(lock_path):
        registry = load_registry(required=False) or ProjectRegistry()
        yield registry
        registry.assert_unique_aliases()
        _atomic_write_registry(path, registry)
        _reset_registry_cache()


# ── Resolution, binding, coherence ────────────────────────────────────────


def resolve_project_key(label: str, *, allow_enroll: bool, actor: str) -> str:
    """Resolve *label* to a canonical project key via the registry.

    - A registry hit (key/alias/canonical) returns that key.
    - No registry yet → the canonical key (degraded; the caller should warn).
    - Registry present but *label* unknown:
        * strict mode or ``allow_enroll=False`` → ``ProjectKeyError`` with guidance.
        * otherwise enroll a **new** entry (never an alias of an existing key —
          a typo must not be fused into another project) and return its key.

    A label whose canonical form is reserved (``auto``/``shared``) is rejected
    unless it already exists as a registry entry.

    Raises:
        ProjectKeyError: label unusable, reserved, or unknown while enrollment
            is disallowed / strict.
    """
    canon = canonical_project_key(label)
    registry = get_registry_cached()
    if registry is None:
        if canon in RESERVED_KEYS:
            raise ProjectKeyError(f"'{canon}' is a reserved project key and cannot be used as a label")
        return canon  # degraded: no registry yet
    hit = registry.resolve(label)
    if hit is not None:
        return hit
    if canon in RESERVED_KEYS:
        raise ProjectKeyError(f"'{canon}' is a reserved project key and cannot be used as a label")
    if registry.strict or not allow_enroll:
        raise ProjectKeyError(
            f"project {label!r} (key '{canon}') is not enrolled in the registry at {registry_path()}. "
            "Run `trace-mcp init --project <name>` to enroll it, or omit the project argument to use the pin."
        )
    with locked_registry() as live:
        hit2 = live.resolve(label)  # re-check under the lock (another process may have enrolled)
        if hit2 is not None:
            return hit2
        live.projects[canon] = ProjectEntry(key=canon, display_label=label, enrolled_by=actor)
        live.history.append(RegistryChange(actor=actor, action="enroll", details={"key": canon, "label": label}))
        return canon


def _meta_get(meta: Any, name: str) -> Any:
    """Read *name* from a session's metadata, whether a model or a raw dict.

    Handles the two shapes the codebase carries: a ``SessionMetadata`` model
    (with ``extra='allow'`` extras in ``model_extra``) and a parsed JSON dict
    (what ``json_file`` filtering operates on).
    """
    if isinstance(meta, Mapping):
        return meta.get(name)
    value = getattr(meta, name, None)
    if value is None:
        extra = getattr(meta, "model_extra", None)
        if isinstance(extra, Mapping):
            return extra.get(name)
    return value


def key_for_label(label: str) -> str:
    """Resolve a plain project *label* to a canonical key for read-side matching.

    Registry alias/canonical resolution when a registry exists, else the bare
    canonical key. **Non-raising**: returns ``""`` for a degenerate/empty label
    (which then matches no real key). Unlike ``resolve_project_key`` this never
    enrolls and never raises on an unknown label — it is for query filtering,
    not project creation.
    """
    if not isinstance(label, str) or not label.strip():
        return ""
    registry = get_registry_cached()
    if registry is not None:
        hit = registry.resolve(label)
        if hit is not None:
            return hit
    try:
        return canonical_project_key(label)
    except ProjectKeyError:
        return ""


def session_project_key(meta: Any) -> str:
    """The canonical key a session record belongs to.

    Uses ``project_key`` when present (authoritative), else resolves the
    display ``project`` label through the registry / canonicalization.
    Returns ``""`` for a degenerate/empty label (which then matches no real key).
    """
    project_key = _meta_get(meta, "project_key")
    if isinstance(project_key, str) and project_key:
        return project_key
    label = _meta_get(meta, "project")
    return key_for_label(label if isinstance(label, str) else "")


def session_matches_project(meta: Any, key: str) -> bool:
    """True when a session record (model or dict) belongs to canonical *key*."""
    return bool(key) and session_project_key(meta) == key


async def validate_project_session(storage: TraceStorage, project: str, session_id: str) -> str:
    """Fail-closed (project, session) coherence check (INV-6).

    Resolves *project* to a canonical key, loads *session_id*, and raises
    ``ProjectMismatchError`` unless the session belongs to the same key. Returns
    the resolved key so callers can key their store off the validated identity.
    Call this BEFORE any store is touched.

    Raises:
        ProjectMismatchError: the session belongs to a different project.
        ProjectKeyError: *project* is unusable/unenrolled.
        FileNotFoundError: no such session.
    """
    project_key = resolve_project_key(project, allow_enroll=False, actor="validate")
    session = await storage.get_session(session_id)
    session_key = session_project_key(session.metadata)
    if session_key != project_key:
        raise ProjectMismatchError(
            f"session {session_id!r} belongs to project '{session_key or '<unknown>'}' but the call "
            f"named '{project_key}'. Refusing to cross project boundaries (INV-6)."
        )
    return project_key


class BoundProject(BaseModel):
    """The process's pinned project (from ``TRACE_PROJECT``)."""

    key: str
    display_label: str
    degraded: bool = False
    """True when the pin is set but the registry is unavailable or the key is
    unenrolled: session capture may proceed on the canonical key, but knowledge
    and egress paths must fail closed (ADR-006 §6 / D4)."""


def get_bound_project() -> BoundProject | None:
    """Return the process's ``TRACE_PROJECT`` pin, or ``None`` if unpinned.

    Recomputed from the environment on each call (the pin is stable per process,
    the registry is mtime-cached); if the pin is set but the registry is absent,
    corrupt, or the key is unenrolled, returns ``degraded=True``.
    """
    raw = os.environ.get("TRACE_PROJECT")
    if not raw or not raw.strip():
        return None
    try:
        canon = canonical_project_key(raw)
    except ProjectKeyError:
        return None
    try:
        registry = get_registry_cached()
    except RegistryUnavailableError:
        return BoundProject(key=canon, display_label=raw, degraded=True)
    if registry is None:
        return BoundProject(key=canon, display_label=raw, degraded=True)
    hit = registry.resolve(raw)
    if hit is None:
        return BoundProject(key=canon, display_label=raw, degraded=True)
    entry = registry.projects.get(hit)
    return BoundProject(key=hit, display_label=entry.display_label if entry else raw)
