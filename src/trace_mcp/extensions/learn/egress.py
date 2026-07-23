"""Egress-as-provenance: an append-only ledger of every cloud call trace-learn makes.

Records the FACT of egress — provider, endpoint, model, purpose, item count,
and project/session when known at the call site — never the content itself.

Project attribution (v0.5): every ledger row carries a ``project_key`` column.
The matching and embedding call sites sit in layers that deliberately know
nothing about projects, so the key travels in a context variable set at the
tool boundary (``egress_project``) rather than being threaded through every
signature between the tool and the provider. Context variables propagate
through ``await``, so the attribution survives the async call chain. Rows
written before v0.5 carry no ``project_key`` and are never rewritten.
One JSONL line is appended BEFORE each cloud call (an intent attestation):
if the attestation cannot be written, the cloud call must not happen.
Unrecorded egress is the exact failure mode this ledger exists to prevent, so
the writer fails closed (``EgressAttestationError``) rather than warning and
proceeding. Every call site sits inside the existing strict/permissive LLM
error handling, so a failed attestation degrades exactly like a failed
provider: strict mode raises, permissive mode falls back to the local path
(BM25 / rule-based / un-embedded) — either way, nothing leaves the machine.

The record is written pre-call on purpose: content reaches the provider even
when the response later fails, so a post-call record could miss real egress.
A ledger line therefore means "content was about to be sent", not "the call
succeeded".

Ledger location: ``~/.trace/egress.jsonl``; override with ``TRACE_EGRESS_LOG``.

Exports: ``attest_egress`` (the pre-call writer), ``egress_log_path``
(path resolution), ``EgressAttestationError``.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["EgressAttestationError", "attest_egress", "egress_log_path", "egress_project"]

_current_project_key: ContextVar[str | None] = ContextVar("trace_egress_project_key", default=None)


class EgressAttestationError(RuntimeError):
    """The egress ledger could not be written; the cloud call must not proceed."""


@contextlib.contextmanager
def egress_project(project_key: str | None) -> Iterator[None]:
    """Attribute every egress attested inside this context to *project_key*.

    Set at the tool/hook boundary (the one layer that knows the project), read
    by ``attest_egress`` in whatever provider layer the cloud call happens.
    ``None`` — e.g. a degenerate label that yields no canonical key — leaves
    rows unattributed rather than guessing.
    """
    token = _current_project_key.set(project_key)
    try:
        yield
    finally:
        _current_project_key.reset(token)


def egress_log_path() -> Path:
    """Resolve the ledger file path. No side effects.

    ``TRACE_EGRESS_LOG`` (a file path) overrides the default
    ``~/.trace/egress.jsonl``.
    """
    override = os.environ.get("TRACE_EGRESS_LOG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".trace" / "egress.jsonl"


def attest_egress(
    *,
    provider: str,
    endpoint: str,
    model: str,
    purpose: str,
    content_class: str,
    item_count: int,
    project: str | None = None,
    session_id: str | None = None,
    base_url: str | None = None,
    project_key: str | None = None,
) -> None:
    """Append one intent record to the egress ledger, BEFORE the cloud call.

    ``project_key`` is the canonical-key attribution column (additive, v0.5).
    An explicit argument wins; otherwise the value set by the enclosing
    ``egress_project`` context applies — which is how the matching/embedding
    sites, whose layers know nothing about projects, still write attributed
    rows. ``project`` remains the display label as before.

    Inputs describe the call about to be made: ``provider`` (e.g. "openai"),
    ``endpoint`` ("chat.completions" | "embeddings"), ``model``, ``purpose``
    ("extraction" | "matching" | "embedding"), ``content_class`` (what KIND of
    content is being sent — never the content), ``item_count`` (events/texts/
    learnings in the payload), and ``project``/``session_id``/``base_url``
    when the call site knows them (``base_url`` set means the "cloud" call
    targets a user-configured, possibly local, OpenAI-compatible endpoint).

    Side effects: creates the ledger's parent directory if missing and appends
    one JSON line to the ledger (file created with mode 0600 — it reveals
    project names and usage patterns, so treat it like the session store).

    Raises ``EgressAttestationError`` when the record cannot be appended —
    callers MUST treat that as "the cloud call is not allowed to happen".
    """
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "provider": provider,
        "endpoint": endpoint,
        "model": model,
        "purpose": purpose,
        "content_class": content_class,
        "item_count": item_count,
        "project": project,
        "session_id": session_id,
        "base_url": base_url,
        "project_key": project_key if project_key is not None else _current_project_key.get(),
    }
    line = json.dumps(entry, separators=(",", ":")) + "\n"
    path = egress_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_APPEND + one write() call: concurrent writers (multiple MCP server
        # processes share the ledger) cannot interleave within a line.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as exc:
        raise EgressAttestationError(
            f"Cannot write the egress ledger at {path}: {exc}. Refusing to make "
            f"the cloud call — egress without a provenance record defeats the "
            f"ledger. Fix the path/permissions or point TRACE_EGRESS_LOG at a "
            f"writable file."
        ) from exc
