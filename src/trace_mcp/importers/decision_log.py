"""Build a TRACE session from an RSI-Exam decision-gate log.

The producer is an evaluation gate that compares a candidate method against its
parent on a paired bootstrap and appends one JSON object per decision to
``decisions.jsonl``. Each line becomes one ``decision`` event: the rollout agent
proposes, the gate resolves. ``keep`` is accepted, ``revert`` is rejected, and a
``provisional`` line stays proposed until a replication on fresh seeds resolves
it — the replication is recorded as a revising decision.

The measurement that drove each decision travels on the event as
``decision.confidence`` (specification 3.6.1). The producer's own rule state —
its minimum effect, verdict, held-out result, confirmation policy, planning and
suite — rides along as extra keys identified by the ``contract`` key. This
importer copies them and interprets none of them: those rules belong to the
producer's verifier, and encoding them here would refuse a producer whose rule
had moved on.

What is checked here is only what the event graph needs: the schema id, line
ordering, and the three structural lineage rules. Verdict, minimum effect,
held-out result, direction, locator syntax, role vocabulary and
disposition-versus-verdict are all the producer's to enforce, and are deliberately
not re-checked.

A line carrying a top-level key outside :data:`LINE_KEYS` is refused rather than
copied or dropped: copying would break parity with the producer's converter,
which does not copy it either, and dropping would silently lose provenance.

Side effects: none. :func:`import_decision_log` is pure, and this module never
touches the session store — an imported record should be reviewable before it
becomes part of a project's provenance. Only :func:`main` reads and writes files.

Exports:
    LINE_KEYS                   the contract's top-level key set
    DecisionLogLine             one parsed log line
    UnknownSchemaError          the log is not this contract
    LineageError                the lines cannot be built into an event graph
    ContractDriftError          a line carries a key this importer does not know
    load_decision_log           read and validate a log file
    import_decision_log         map validated lines onto one Session
    main                        CLI entry point
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from trace_mcp import __version__
from trace_mcp.project_identity import RESERVED_KEYS, canonical_project_key
from trace_mcp.schema import SCHEMA_VERSION, Actor, DecisionData, Session, SessionMetadata, TraceEvent

SOURCE_SCHEMA = "rsi-exam-decision-log/v1"
LOCATOR_BASE = "artifacts/app/methods"

# The gate identifies itself as the resolver of every non-provisional decision.
GATE_ACTOR = Actor(type="system", id="rsi-exam-gate/decide.py", role="decision-gate")

# The seven keys a line written under a task profile carries. A replay-mode line
# omits them entirely (an older log) or carries them as nulls.
GATED_KEYS = (
    "confirm_policy",
    "profile_sha256",
    "look_index",
    "parent_method_tree_sha256",
    "candidate_method_tree_sha256",
    "sizing",
    "suite",
)

# The contract's required minimum, from the producer's own `LINE_KEYS`.
_REQUIRED_KEYS = (
    "schema",
    "line",
    "timestamp",
    "version_id",
    "parent_id",
    "replicates",
    "statistic",
    "unit",
    "direction",
    "estimate",
    "interval",
    "method",
    "sample_size",
    "min_effect",
    "verdict",
    "disposition",
    "evidence",
    "evidence_digests",
    "holdout",
)

# Every top-level key a line may carry. The producer's `decide.py` uses its own
# LINE_KEYS as a *missing*-key check, and the contract states that every line
# carries the seven gated keys on top of it, so the permitted set is the union.
LINE_KEYS: tuple[str, ...] = _REQUIRED_KEYS + GATED_KEYS

# The producer's projection into the confidence block, in its order.
_CONFIDENCE_KEYS = (
    "interval",
    "method",
    "sample_size",
    "evidence_digests",
    "contract",
    "statistic",
    "unit",
    "direction",
    "estimate",
    "min_effect",
    "verdict",
    "evidence",
    "holdout",
    *GATED_KEYS,
)

# A holdout block projects exactly these, whatever else the line's holdout holds.
_HOLDOUT_KEYS = ("estimate", "interval", "sample_size", "verdict", "evidence", "evidence_digests")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class UnknownSchemaError(ValueError):
    """The log does not declare this importer's contract."""


class LineageError(ValueError):
    """The lines cannot be assembled into a coherent decision graph."""


class ContractDriftError(ValueError):
    """A line carries a top-level key this importer does not know."""


class DecisionLogLine(BaseModel):
    """One decision-log line.

    The seven gated-mode fields are typed by JSON type only — no hex pattern, no
    enumerated policy, no positive-integer bound. The producer's converter checks
    those and its verifier owns them; a pattern here would break this importer the
    next time the producer widened a vocabulary. All seven default to ``None``,
    which is what lets a pre-gated log and a gated log load through one model.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # `schema` shadows BaseModel.schema, so the attribute carries the alias.
    schema_id: str = Field(alias="schema")
    line: int
    timestamp: AwareDatetime
    version_id: str
    parent_id: str
    replicates: str | None = None
    statistic: str
    unit: str | None = None
    direction: str
    estimate: float
    interval: dict[str, Any]
    method: dict[str, Any]
    sample_size: int
    min_effect: float
    verdict: str
    disposition: str
    evidence: list[dict[str, Any]]
    evidence_digests: dict[str, Any]
    holdout: dict[str, Any] | None = None

    confirm_policy: str | None = None
    profile_sha256: str | None = None
    look_index: int | None = None
    parent_method_tree_sha256: str | None = None
    candidate_method_tree_sha256: str | None = None
    sizing: dict[str, Any] | None = None
    suite: dict[str, Any] | None = None


def _check_known_keys(keys: Any, line_number: Any) -> None:
    """Refuse a line carrying a key outside the contract, naming the key."""
    unknown = sorted(set(keys) - set(LINE_KEYS))
    if unknown:
        raise ContractDriftError(
            f"line {line_number}: unknown top-level key(s) {', '.join(repr(k) for k in unknown)}. "
            f"This importer was written against {SOURCE_SCHEMA}; a producer that added a key needs "
            "the importer re-derived rather than the key silently dropped."
        )


def load_decision_log(path: Path) -> list[DecisionLogLine]:
    """Read a decision log and validate it line by line.

    Side effects: reads *path*.

    Raises `UnknownSchemaError` for a foreign contract, `ContractDriftError` for
    an unknown top-level key, and `LineageError` when a line's ``line`` field
    disagrees with its physical position. A blank physical line is a `ValueError`:
    the contract says the log is appended, never edited.
    """
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    if not raw_lines:
        raise ValueError(f"{path} is empty")

    parsed: list[DecisionLogLine] = []
    for position, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            raise ValueError(f"line {position}: blank line in decision log")
        row = json.loads(raw)
        if row.get("schema") != SOURCE_SCHEMA:
            raise UnknownSchemaError(f"line {position}: schema is not {SOURCE_SCHEMA}, got {row.get('schema')!r}")
        _check_known_keys(row.keys(), position)
        if row.get("line") != position:
            raise LineageError(f"line {position}: line field {row.get('line')!r} does not match its position")
        parsed.append(DecisionLogLine.model_validate(row))
    return parsed


def _confidence_block(line: DecisionLogLine) -> dict[str, Any]:
    """The producer's projection: its key list, its order, its null-filling."""
    values = line.model_dump(by_alias=True)
    holdout = line.holdout
    block: dict[str, Any] = {}
    for key in _CONFIDENCE_KEYS:
        if key == "contract":
            block[key] = SOURCE_SCHEMA
        elif key == "holdout":
            block[key] = {k: holdout[k] for k in _HOLDOUT_KEYS} if holdout else None
        else:
            block[key] = values.get(key)
    return block


def _rationale(line: DecisionLogLine) -> str:
    """The producer's template, reproduced exactly."""
    low, high = line.interval["lower"], line.interval["upper"]
    statistic = line.statistic.replace("_", " ")
    statistic = statistic[:1].upper() + statistic[1:]
    unit = f" {line.unit}" if line.unit else ""
    level = f"{line.interval['level'] * 100:g}"
    method = line.method["name"].replace("_", " ")
    return (
        f"{statistic} {line.estimate:+.1f}{unit} (n={line.sample_size}); "
        f"{level} percent {method} interval [{low:+.1f}, {high:+.1f}]; verdict {line.verdict}."
    )


def _revert_note(line: DecisionLogLine) -> str:
    """Two cases: an under-planned confirmation, and an interval below zero."""
    sizing = line.sizing
    if isinstance(sizing, dict) and sizing.get("exploratory"):
        return "Confirmation would need more games than the profile allows; reverted without confirming."
    return "Interval entirely below zero."


def _event(
    event_id: str,
    session_id: str,
    line: DecisionLogLine,
    agent: Actor,
    description: str,
    disposition: str,
    revision_note: str | None,
    revises: str | None,
) -> TraceEvent:
    decision = DecisionData.model_validate(
        {
            "description": description,
            "rationale": _rationale(line),
            "proposed_by": agent.model_dump(),
            "disposition": disposition,
            "resolved_by": None if disposition == "proposed" else GATE_ACTOR.model_dump(),
            "revision_note": revision_note,
            "revises_event_id": revises,
            "suggestion_type": "proactive",
            "tags": ["rsi-exam", "decision-gate", line.version_id],
            "warnings": [],
            "confidence": _confidence_block(line),
        }
    )
    return TraceEvent(
        id=event_id,
        timestamp=line.timestamp,
        session_id=session_id,
        type="decision",
        actor=agent,
        decision=decision,
    )


def import_decision_log(
    lines: list[DecisionLogLine],
    *,
    project: str,
    rollout_id: str,
    task: str,
    harness: str,
    model: str,
    recorded_decision_log_sha256: str,
    locator_base: str = LOCATOR_BASE,
    session_id: str | None = None,
) -> Session:
    """Map decision-log lines, in file order, onto one completed TRACE session.

    Inputs: *lines* in file order; *project*, the display label to record;
    *rollout_id*, *task*, *harness* and *model*, recorded as metadata;
    *recorded_decision_log_sha256*, the caller's assertion about the log's bytes
    (the CLI computes it, a library caller supplies it); *locator_base*, recorded
    in metadata and never applied to an evidence locator; *session_id*, an
    override for the generated id.

    Output: an unsaved `Session`. Pure — nothing is written, and no store is read.

    Raises `UnknownSchemaError` for a foreign contract, `LineageError` when the
    lines cannot be assembled into a decision graph, and `ValueError` for an empty
    log, a reserved project label, or a malformed digest.
    """
    if not lines:
        raise ValueError("decision log is empty")
    if not _HEX64.match(recorded_decision_log_sha256):
        raise ValueError("recorded_decision_log_sha256 must be 64 lowercase hexadecimal characters")

    project_key = canonical_project_key(project)
    if project_key in RESERVED_KEYS:
        raise ValueError(f"{project!r} resolves to the reserved project key {project_key!r}")

    agent = Actor(type="ai", id=f"{harness}:{model}", role="rollout-agent")
    sid = session_id or re.sub(r"[^A-Za-z0-9._-]", "-", f"rsiexam_{rollout_id}")

    events: list[TraceEvent] = []
    open_provisional: dict[str, tuple[str, str]] = {}
    counts = {"keep": 0, "revert": 0, "provisional": 0, "replicated": 0}
    previous_line_number: int | None = None

    for line in lines:
        if line.schema_id != SOURCE_SCHEMA:
            raise UnknownSchemaError(f"line {line.line}: schema is not {SOURCE_SCHEMA}, got {line.schema_id!r}")
        _check_known_keys(set(line.model_dump(by_alias=True)) | set(line.model_extra or {}), line.line)
        if previous_line_number is not None and line.line <= previous_line_number:
            raise LineageError(f"line field {line.line} does not increase on the previous line {previous_line_number}")
        previous_line_number = line.line

        event_id = f"evt_{len(events) + 1:03d}"
        version, parent = line.version_id, line.parent_id

        if line.replicates:
            if line.replicates != version:
                raise LineageError(
                    f"line {line.line}: replicates {line.replicates!r} must equal version_id {version!r}"
                )
            if line.disposition == "provisional":
                raise LineageError(f"line {line.line}: a replication line cannot be provisional")
            opened = open_provisional.get(line.replicates)
            if opened is None:
                raise LineageError(f"line {line.line} replicates {line.replicates} but no provisional decision is open")
            original_id, original_parent = opened
            if parent != original_parent:
                raise LineageError(
                    f"line {line.line}: replication parent {parent} differs from the provisional line's "
                    f"parent {original_parent}"
                )
            disposition = "accepted" if line.disposition == "keep" else "rejected"
            note = None if disposition == "accepted" else "Replication did not clear the minimum effect."
            events.append(
                _event(
                    event_id,
                    sid,
                    line,
                    agent,
                    f"Replication of {version} on fresh seeds (parent {parent})",
                    disposition,
                    note,
                    original_id,
                )
            )
            original = next(e for e in events if e.id == original_id)
            assert original.decision is not None
            original.decision = DecisionData.model_validate(
                {
                    **original.decision.model_dump(),
                    "disposition": disposition,
                    "resolved_by": GATE_ACTOR.model_dump(),
                    "revision_note": f"Resolved by replication {event_id}.",
                }
            )
            del open_provisional[line.replicates]
            counts["replicated"] += 1
            continue

        if line.disposition == "provisional" and open_provisional:
            raise LineageError(f"line {line.line} opens a second provisional decision")
        if parent in open_provisional:
            raise LineageError(f"line {line.line} builds on {parent} while its provisional decision is unresolved")

        if line.disposition == "keep":
            events.append(
                _event(event_id, sid, line, agent, f"Keep {version} (parent {parent})", "accepted", None, None)
            )
        elif line.disposition == "revert":
            events.append(
                _event(
                    event_id,
                    sid,
                    line,
                    agent,
                    f"Revert {version} (parent {parent})",
                    "rejected",
                    _revert_note(line),
                    None,
                )
            )
        else:
            events.append(
                _event(
                    event_id,
                    sid,
                    line,
                    agent,
                    f"Keep {version} provisionally (parent {parent})",
                    "proposed",
                    None,
                    None,
                )
            )
            open_provisional[version] = (event_id, parent)
        counts[line.disposition] = counts.get(line.disposition, 0) + 1

    summary = (
        f"RSI-Exam rollout {rollout_id} ({task}, {harness}, {model}): {counts['keep']} kept, "
        f"{counts['revert']} reverted, {counts['provisional']} provisional, {counts['replicated']} replicated."
    )
    return Session(
        id=sid,
        created=events[0].timestamp,
        ended=events[-1].timestamp,
        status="completed",
        metadata=SessionMetadata(
            project=project,
            project_key=project_key,
            experiment_id=rollout_id,
            description=f"Decision-gate record for RSI-Exam rollout {rollout_id}",
            participants=[agent, GATE_ACTOR],
            tags=["rsi-exam", "decision-gate"],
            custom={
                "source": SOURCE_SCHEMA,
                "importer": f"trace-mcp {__version__} schema {SCHEMA_VERSION}",
                "rollout_id": rollout_id,
                "task": task,
                "harness": harness,
                "model": model,
                "decision_log_sha256": recorded_decision_log_sha256,
                "locator_base": locator_base,
            },
        ),
        summary=summary,
        events=events,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``trace-mcp import decision-log``. Returns an exit code.

    Side effects: reads the log file; writes the session JSON to stdout, or to
    ``--output`` when given. It deliberately does not write into the session
    store: an imported record should be reviewed before it becomes part of a
    project's provenance.

    Exit codes: 0 on success; 2 for a foreign contract, contract drift, or a
    lineage violation; 1 for anything else, including a missing file, a reserved
    project label, and an unwritable output.
    """
    parser = argparse.ArgumentParser(
        prog="trace-mcp import decision-log",
        description="Build a TRACE session from an RSI-Exam decision-gate log.",
    )
    parser.add_argument("log", type=Path, help="Path to the decision-log JSONL file.")
    parser.add_argument("--project", required=True, help="TRACE project label to record.")
    parser.add_argument("--rollout", required=True, help="Rollout id; recorded as the experiment id.")
    parser.add_argument("--task", required=True, help="Task the rollout searched over.")
    parser.add_argument("--harness", required=True, help="Agent harness that drove the rollout.")
    parser.add_argument("--model", required=True, help="Model that drove the rollout.")
    parser.add_argument(
        "--locator-base", default=LOCATOR_BASE, help="Recorded in metadata; never applied to a locator."
    )
    parser.add_argument("--session-id", default=None, help="Override the generated session id.")
    parser.add_argument("--output", type=Path, default=None, help="Write here instead of stdout.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        # One read: the digest is computed over exactly the bytes that are parsed.
        data = args.log.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        raw_lines = data.decode("utf-8").splitlines()
        if not raw_lines:
            raise ValueError(f"{args.log} is empty")
        parsed: list[DecisionLogLine] = []
        for position, raw in enumerate(raw_lines, start=1):
            if not raw.strip():
                raise ValueError(f"line {position}: blank line in decision log")
            row = json.loads(raw)
            if row.get("schema") != SOURCE_SCHEMA:
                raise UnknownSchemaError(f"line {position}: schema is not {SOURCE_SCHEMA}, got {row.get('schema')!r}")
            _check_known_keys(row.keys(), position)
            if row.get("line") != position:
                raise LineageError(f"line {position}: line field {row.get('line')!r} does not match its position")
            parsed.append(DecisionLogLine.model_validate(row))

        session = import_decision_log(
            parsed,
            project=args.project,
            rollout_id=args.rollout,
            task=args.task,
            harness=args.harness,
            model=args.model,
            recorded_decision_log_sha256=digest,
            locator_base=args.locator_base,
            session_id=args.session_id,
        )
        payload = json.dumps(session.model_dump(mode="json"), indent=2) + "\n"
        if args.output is not None:
            args.output.write_text(payload, encoding="utf-8")
            print(f"Wrote {args.output} ({len(session.events)} events).", file=sys.stderr)
        else:
            sys.stdout.write(payload)
    except (UnknownSchemaError, LineageError, ContractDriftError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Error: could not import {args.log}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
