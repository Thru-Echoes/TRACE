"""The read-only report and review sheet for provenance marks (ADR 008 D7).

``build_report`` reads the shadow directory (replay, recall, error and words
logs), the live knowledge stores and the session records, and returns counts
per project and in total. ``get_review_rows`` lists the items worth a person's
judgment; ``write_review_csv`` writes them as a sheet with empty reviewer
columns. Nothing here writes anything except the CSV the caller asks for.

Marks computed from session records here are *current* marks. A stored
learning whose source was still proposed when the learning was written, and
has been resolved since, is counted in ``stored_resolved_since``. The
whole-store against nearest-50 comparison is read from the replay records.

Exports:
    ArmCost, CaptureCount, ProjectReport, ShadowReport, ReviewRow
    build_report, format_report, get_review_rows, write_review_csv

Side effects: reads files; ``write_review_csv`` writes one CSV file.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

from trace_mcp import project_identity as pident
from trace_mcp.extensions.learn import matching, store
from trace_mcp.extensions.learn.models import Learning
from trace_mcp.extensions.learn.shadow.host_transcript import is_plain_prompt, read_host_inputs
from trace_mcp.extensions.learn.shadow.recall import RecallRecord
from trace_mcp.extensions.learn.shadow.replay import ARMS, LearningAtExtraction, ReplayRecord, load_sessions
from trace_mcp.extensions.learn.shadow.state import (
    ErrorRecord,
    ShadowState,
    ShadowStateError,
    get_project_dir,
    get_shadow_root,
    load_state,
)
from trace_mcp.extensions.learn.shadow.words import load_words
from trace_mcp.provenance_marks import (
    SOURCE_UNKNOWN_TAG,
    WordsEntry,
    get_base_mark,
    get_event_marks,
    get_quote_position,
    get_quote_word_share,
    get_resolution_timing,
    is_human_resolution,
)
from trace_mcp.schema import Session, TraceEvent

PAIR_OVERLAP = 0.5
CAPTURE_SLACK = timedelta(seconds=5)
_UNACCEPTED_FIRST_TAGS = {"[proposed]", "[ai-resolved]", "[ai-note]", SOURCE_UNKNOWN_TAG}
_NOT_CAPTURED_LINE = "not captured by any hook: text typed when declining a tool call"

ReviewKind = Literal[
    "unaccepted_source",
    "quote_not_found",
    "resolution_unquoted",
    "resolution_not_found",
    "resolution_older",
    "whole_only",
    "nearest_only",
]


def _arms_of_counts() -> dict[str, dict[str, int]]:
    return {arm: {} for arm in ARMS}


class ArmCost(BaseModel):
    """Extraction cost of one replay arm, summed over replayed sessions."""

    calls: int = Field(default=0, description="Replayed sessions for which the arm called the model.")
    calls_with_usage: int = Field(default=0, description="Of those, calls whose provider reported token usage.")
    prompt_tokens: int = Field(default=0, description="Prompt tokens, summed.")
    completion_tokens: int = Field(default=0, description="Completion tokens, summed.")
    seconds: float = Field(default=0.0, description="Request seconds, summed.")


class CaptureCount(BaseModel):
    """Recorded words against the host's own transcripts (measure 10)."""

    host_sessions: int = Field(default=0, description="Host sessions with recorded entries in range.")
    transcripts_missing: int = Field(default=0, description="Host sessions whose transcript is gone or unnamed.")
    transcripts_unreadable: int = Field(default=0, description="Host sessions whose transcript could not be read.")
    prompts_in_transcript: int = Field(default=0, description="Typed prompts the transcripts hold.")
    prompts_recorded: int = Field(default=0, description="Plain prompts the hooks recorded for those sessions.")
    dialog_answers_in_transcript: int = Field(default=0, description="Dialog answers the transcripts hold.")
    dialog_answers_recorded: int = Field(default=0, description="Dialog answers the hooks recorded.")


class ProjectReport(BaseModel):
    """Every count for one project (or the total). Status/band/kind maps hold only non-zero keys."""

    project_key: str = Field(..., description="Canonical project key, or TOTAL.")
    stored_learnings: int = Field(default=0, description="Learnings in the live store written in range.")
    stored_by_mark: dict[str, int] = Field(
        default_factory=dict, description="Current first mark of the source (or none) -> stored learnings."
    )
    stored_proposed_when_written: int = Field(
        default=0, description="Stored learnings whose source was a still-proposed decision when they were written."
    )
    stored_resolved_since: int = Field(default=0, description="Of those, sources resolved since.")
    replayed_sessions: int = Field(default=0, description="Replay records in range.")
    learnings_by_mark: dict[str, dict[str, int]] = Field(
        default_factory=_arms_of_counts, description="Arm -> first mark at the session's end (or none) -> learnings."
    )
    cost: dict[str, ArmCost] = Field(default_factory=lambda: {arm: ArmCost() for arm in ARMS})
    paired: int = Field(default=0, description="Learnings both replay arms made.")
    whole_only: int = Field(default=0, description="Learnings only the whole-store arm made.")
    nearest_only: int = Field(default=0, description="Learnings only the nearest arm made.")
    recall_calls: int = 0
    recall_items_by_mark: dict[str, int] = Field(default_factory=dict)
    recall_chars_without: int = 0
    recall_chars_with: int = 0
    quote: dict[str, int] = Field(default_factory=dict, description="Quote status -> events.")
    quote_position: dict[str, int] = Field(default_factory=dict, description="Where verified event quotes were.")
    quote_miss_share: dict[str, int] = Field(default_factory=dict, description="Word-share band -> missed quotes.")
    resolution: dict[str, int] = Field(default_factory=dict, description="Resolution quote -> human resolutions.")
    resolution_position: dict[str, int] = Field(default_factory=dict)
    resolution_by_quote: dict[str, dict[str, int]] = Field(default_factory=dict)
    resolution_timing: dict[str, int] = Field(default_factory=dict)
    similarity: dict[str, dict[str, int]] = Field(default_factory=_arms_of_counts)
    corrections_by_mark: dict[str, int] = Field(default_factory=dict)
    errors_by_step: dict[str, int] = Field(default_factory=dict)
    words_entries: int = 0
    words_by_kind: dict[str, int] = Field(default_factory=dict)
    suspect_entries: int = Field(default=0, description="Prompt entries beginning with a tag other than pasted text.")
    capture: CaptureCount = Field(default_factory=CaptureCount)
    unreadable_sessions: int = Field(default=0, description="Session files that could not be read (machine-wide).")


class ShadowReport(BaseModel):
    """The report: per project and in total."""

    started_at: datetime | None = None
    stopped_at: datetime | None = None
    date_from: date
    date_to: date
    projects: list[ProjectReport] = Field(default_factory=list)
    total: ProjectReport
    projects_without_words: list[str] = Field(default_factory=list)


class ReviewRow(BaseModel):
    """One row of the review sheet; the last four columns are for the reviewer.

    ``text`` holds the learning's text, or for quote and resolution rows the
    quoted words. ``learning_id`` is a stored learning's id, or for a replay
    row the arm's own ordinal; ``arm`` is set on replay rows only.
    """

    project: str
    kind: ReviewKind
    session_id: str
    event_id: str | None = None
    learning_id: str | None = None
    arm: str = ""
    marks: str = ""
    text: str = ""
    reviewer: str = ""
    mark_correct: str = ""
    useful: str = ""
    reworded_as_directive: str = ""


# ── reading ───────────────────────────────────────────────────────────────

_M = TypeVar("_M", bound=BaseModel)


def _read_jsonl(path: Path, model: type[_M]) -> list[_M]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[_M] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            out.append(model.model_validate_json(line))
        except ValidationError:
            continue
    return out


def _bump(counts: dict[str, int], key: str, n: int = 1) -> None:
    counts[key] = counts.get(key, 0) + n


def _get_range(state: ShadowState, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = datetime.now(UTC).date()
    start = state.started_at.astimezone(UTC).date() if state.started_at else today
    end = state.stopped_at.astimezone(UTC).date() if state.stopped_at else today
    return date_from or start, date_to or end


def _in_range(ts: datetime, date_from: date, date_to: date) -> bool:
    day = ts.astimezone(UTC).date() if ts.tzinfo else ts.date()
    return date_from <= day <= date_to


def _list_projects(project_key: str | None) -> list[str]:
    root = get_shadow_root()
    names = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    if project_key is not None:
        wanted = pident.canonical_project_key(project_key)
        names = [name for name in names if name == wanted]
    return names


def _stored_learnings(key: str) -> dict[str, Learning]:
    try:
        return {lrn.id: lrn for lrn in store.load_store(key).learnings}
    except Exception:
        return {}


def _find_event(session: Session | None, event_id: str | None) -> TraceEvent | None:
    if session is None or not event_id:
        return None
    return next((e for e in session.events if e.id == event_id), None)


def _first_tag(tags: Sequence[str]) -> str:
    return tags[0] if tags else "none"


def _similarity_band(cosine: float | None) -> str:
    if cosine is None:
        return "no_vector"
    if cosine >= 0.95:
        return "0.95+"
    if cosine >= 0.90:
        return "0.90-0.95"
    if cosine >= 0.85:
        return "0.85-0.90"
    return "<0.85"


def _share_band(share: float) -> str:
    if share >= 0.9:
        return "0.9+"
    if share >= 0.5:
        return "0.5-0.9"
    return "<0.5"


# ── one project ───────────────────────────────────────────────────────────


class _Context(BaseModel):
    """What every project's pass reads once."""

    date_from: date
    date_to: date
    sessions: list[Session]
    sessions_by_id: dict[str, Session]
    unreadable_sessions: int


def _pair(
    whole: Sequence[LearningAtExtraction], nearest: Sequence[LearningAtExtraction]
) -> tuple[int, list[LearningAtExtraction], list[LearningAtExtraction]]:
    """Pair one replay's whole and nearest learnings: same source event first, then token overlap >= 0.5."""
    free = list(nearest)
    unpaired: list[LearningAtExtraction] = []
    paired = 0
    for lrn in whole:
        match = next((n for n in free if lrn.source_event and n.source_event == lrn.source_event), None)
        if match is None:
            unpaired.append(lrn)
            continue
        free.remove(match)
        paired += 1
    whole_only: list[LearningAtExtraction] = []
    for lrn in unpaired:
        match = next(
            (
                n
                for n in free
                if lrn.content and n.content and matching.jaccard_similarity(lrn.content, n.content) >= PAIR_OVERLAP
            ),
            None,
        )
        if match is None:
            whole_only.append(lrn)
            continue
        free.remove(match)
        paired += 1
    return paired, whole_only, free


def _stored_part(
    key: str, report: ProjectReport, rows: list[ReviewRow], ctx: _Context, words: Sequence[WordsEntry]
) -> None:
    """Stored learnings written in range: by the current mark of their source, and the ones no person accepted."""
    for lrn in _stored_learnings(key).values():
        if not _in_range(lrn.created, ctx.date_from, ctx.date_to):
            continue
        report.stored_learnings += 1
        session = ctx.sessions_by_id.get(lrn.source_session) if lrn.source_session else None
        event = _find_event(session, lrn.source_event)
        if session is None or event is None:
            tags = [SOURCE_UNKNOWN_TAG]
        else:
            tags = get_event_marks(event, words, session_created=session.created).tags()
            if get_base_mark(event, as_of=lrn.created)[0] == "proposed":
                report.stored_proposed_when_written += 1
                if get_base_mark(event)[0] != "proposed":
                    report.stored_resolved_since += 1
        first = _first_tag(tags)
        _bump(report.stored_by_mark, first)
        if first in _UNACCEPTED_FIRST_TAGS:
            rows.append(
                ReviewRow(
                    project=key,
                    kind="unaccepted_source",
                    session_id=lrn.source_session or "",
                    event_id=lrn.source_event,
                    learning_id=lrn.id,
                    marks=" ".join(tags),
                    text=lrn.content,
                )
            )


def _replay_part(key: str, report: ProjectReport, rows: list[ReviewRow], ctx: _Context) -> None:
    """The whole-store against nearest-50 comparison, from the replay records."""
    for rec in _read_jsonl(get_project_dir(key) / "replay.jsonl", ReplayRecord):
        if not _in_range(rec.ts, ctx.date_from, ctx.date_to):
            continue
        report.replayed_sessions += 1
        for arm in ARMS:
            arm_record = getattr(rec, arm)
            cost = report.cost[arm]
            cost.calls += 1
            if arm_record.prompt_tokens is not None or arm_record.completion_tokens is not None:
                cost.calls_with_usage += 1
            cost.prompt_tokens += arm_record.prompt_tokens or 0
            cost.completion_tokens += arm_record.completion_tokens or 0
            cost.seconds += arm_record.seconds or 0.0
            for lrn in arm_record.learnings:
                _bump(report.learnings_by_mark[arm], _first_tag(lrn.tags))
                _bump(report.similarity[arm], _similarity_band(lrn.nearest_existing_cosine))
        paired, whole_only, nearest_only = _pair(rec.whole.learnings, rec.nearest.learnings)
        report.paired += paired
        report.whole_only += len(whole_only)
        report.nearest_only += len(nearest_only)
        one_arm: tuple[tuple[str, ReviewKind, list[LearningAtExtraction]], ...] = (
            ("whole", "whole_only", whole_only),
            ("nearest", "nearest_only", nearest_only),
        )
        for arm, kind, only in one_arm:
            for lrn in only:
                rows.append(
                    ReviewRow(
                        project=key,
                        kind=kind,
                        session_id=rec.session_id,
                        event_id=lrn.source_event,
                        learning_id=lrn.id,
                        arm=arm,
                        marks=" ".join(lrn.tags),
                        text=lrn.content,
                    )
                )


def _recall_and_errors_part(key: str, report: ProjectReport, ctx: _Context) -> None:
    for rec in _read_jsonl(get_project_dir(key) / "recall.jsonl", RecallRecord):
        if not _in_range(rec.ts, ctx.date_from, ctx.date_to):
            continue
        report.recall_calls += 1
        report.recall_chars_without += rec.chars_without
        report.recall_chars_with += rec.chars_with
        for item in rec.items:
            _bump(report.recall_items_by_mark, _first_tag(item.tags))
    for err in _read_jsonl(get_project_dir(key) / "errors.jsonl", ErrorRecord):
        if _in_range(err.ts, ctx.date_from, ctx.date_to):
            _bump(report.errors_by_step, err.step)


def _correction_target_tag(
    target: str, session: Session, words: Sequence[WordsEntry], ctx: _Context, learnings: dict[str, Learning]
) -> str:
    event = _find_event(session, target)
    if event is not None:
        return _first_tag(get_event_marks(event, words, session_created=session.created).tags())
    if target.startswith("lrn_"):
        lrn = learnings.get(target)
        source_session = ctx.sessions_by_id.get(lrn.source_session) if lrn and lrn.source_session else None
        source = _find_event(source_session, lrn.source_event if lrn else None)
        if source is None or source_session is None:
            return SOURCE_UNKNOWN_TAG
        return _first_tag(get_event_marks(source, words, session_created=source_session.created).tags())
    return "external"


def _session_part(
    key: str, report: ProjectReport, rows: list[ReviewRow], ctx: _Context, words: Sequence[WordsEntry]
) -> None:
    learnings = _stored_learnings(key)
    for session in ctx.sessions:
        if not pident.session_matches_project(session.metadata, key):
            continue
        if not _in_range(session.created, ctx.date_from, ctx.date_to):
            continue
        for event in session.events:
            marks = get_event_marks(event, words, session_created=session.created)
            _bump(report.quote, marks.quote)
            snippet = event.context.conversation_snippet or ""
            if marks.quote == "verified":
                _bump(report.quote_position, get_quote_position(snippet, words, not_after=event.timestamp))
            elif marks.quote == "not_found":
                _bump(
                    report.quote_miss_share,
                    _share_band(get_quote_word_share(snippet, words, not_after=event.timestamp)),
                )
                rows.append(
                    ReviewRow(
                        project=key,
                        kind="quote_not_found",
                        session_id=session.id,
                        event_id=event.id,
                        marks=" ".join(marks.tags()),
                        text=snippet,
                    )
                )
            if is_human_resolution(event) and event.decision is not None:
                decision = event.decision
                _bump(report.resolution, marks.resolution)
                _bump(report.resolution_by_quote.setdefault(marks.resolution, {}), marks.quote)
                _bump(report.resolution_timing, get_resolution_timing(event, words))
                quote = decision.resolution_snippet or ""
                kind: ReviewKind | None = None
                if marks.resolution == "verified" and decision.resolved_at is not None:
                    position = get_quote_position(quote, words, not_after=decision.resolved_at)
                    _bump(report.resolution_position, position)
                    kind = "resolution_older" if position == "older" else None
                elif marks.resolution == "absent":
                    kind = "resolution_unquoted"
                elif marks.resolution == "not_found":
                    kind = "resolution_not_found"
                if kind is not None:
                    rows.append(
                        ReviewRow(
                            project=key,
                            kind=kind,
                            session_id=session.id,
                            event_id=event.id,
                            marks=" ".join(marks.tags()),
                            text=quote,
                        )
                    )
            annotation = event.annotation
            if event.type == "annotation" and annotation is not None and annotation.category == "correction":
                for target in annotation.corrects_event_ids:
                    _bump(report.corrections_by_mark, _correction_target_tag(target, session, words, ctx, learnings))


def _words_part(key: str, report: ProjectReport, ctx: _Context, words: Sequence[WordsEntry]) -> None:
    in_range = [w for w in words if _in_range(w.ts, ctx.date_from, ctx.date_to)]
    report.words_entries = len(in_range)
    for entry in in_range:
        _bump(report.words_by_kind, entry.kind)
        stripped = entry.text.lstrip()
        if entry.kind == "prompt" and stripped.startswith("<") and not stripped.startswith("<pasted_content"):
            report.suspect_entries += 1
    until = datetime.combine(ctx.date_to, time.max, tzinfo=UTC)
    groups: dict[str, list[WordsEntry]] = {}
    for entry in in_range:
        groups.setdefault(entry.host_session_id, []).append(entry)
    capture = report.capture
    for entries in groups.values():
        capture.host_sessions += 1
        named = [e.transcript for e in entries if e.transcript]
        path = Path(named[-1]) if named else None
        if path is None or not path.is_file():
            capture.transcripts_missing += 1
            continue
        inputs = read_host_inputs(path, since=entries[0].ts - CAPTURE_SLACK, until=until)
        if inputs is None:
            capture.transcripts_unreadable += 1
            continue
        capture.prompts_in_transcript += inputs.prompts
        capture.dialog_answers_in_transcript += inputs.dialog_answers
        capture.prompts_recorded += sum(1 for e in entries if e.kind == "prompt" and is_plain_prompt(e.text))
        capture.dialog_answers_recorded += sum(1 for e in entries if e.kind == "dialog_answer")


def _project(key: str, ctx: _Context) -> tuple[ProjectReport, list[ReviewRow]]:
    report = ProjectReport(project_key=key, unreadable_sessions=ctx.unreadable_sessions)
    rows: list[ReviewRow] = []
    words = load_words(key)
    _stored_part(key, report, rows, ctx, words)
    _replay_part(key, report, rows, ctx)
    _recall_and_errors_part(key, report, ctx)
    _session_part(key, report, rows, ctx, words)
    _words_part(key, report, ctx, words)
    return report, rows


def _merge(a: Any, b: Any) -> Any:
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _merge(out[k], v) if k in out else v
        return out
    if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool):
        return a + b
    return a


def _total(projects: Iterable[ProjectReport], unreadable: int) -> ProjectReport:
    merged: dict[str, Any] = ProjectReport(project_key="TOTAL").model_dump()
    for report in projects:
        data = report.model_dump()
        data.pop("project_key")
        data.pop("unreadable_sessions")
        merged = _merge(merged, data)
    merged["project_key"] = "TOTAL"
    merged["unreadable_sessions"] = unreadable
    return ProjectReport.model_validate(merged)


def _gather(
    date_from: date | None, date_to: date | None, project_key: str | None
) -> tuple[ShadowState, _Context, list[tuple[ProjectReport, list[ReviewRow]]]]:
    try:
        state = load_state()
    except ShadowStateError:
        state = ShadowState()
    start, end = _get_range(state, date_from, date_to)
    sessions, unreadable = load_sessions()
    ctx = _Context(
        date_from=start,
        date_to=end,
        sessions=sessions,
        sessions_by_id={s.id: s for s in sessions},
        unreadable_sessions=unreadable,
    )
    return state, ctx, [_project(key, ctx) for key in _list_projects(project_key)]


def build_report(
    *, date_from: date | None = None, date_to: date | None = None, project_key: str | None = None
) -> ShadowReport:
    """Build the comparison report. Reads only; writes nothing.

    The default range is the period's start date to today, or to its stop date.
    """
    state, ctx, results = _gather(date_from, date_to, project_key)
    projects = [report for report, _ in results]
    return ShadowReport(
        started_at=state.started_at,
        stopped_at=state.stopped_at,
        date_from=ctx.date_from,
        date_to=ctx.date_to,
        projects=projects,
        total=_total(projects, ctx.unreadable_sessions),
        projects_without_words=[p.project_key for p in projects if p.words_entries == 0],
    )


def get_review_rows(
    *, date_from: date | None = None, date_to: date | None = None, project_key: str | None = None
) -> list[ReviewRow]:
    """List the items worth a person's judgment. Reads only; writes nothing."""
    _, _, results = _gather(date_from, date_to, project_key)
    return [row for _, rows in results for row in rows]


def write_review_csv(path: Path, rows: Sequence[ReviewRow]) -> int:
    """Write the review sheet (a header row, then one row per item) and return the rows written.

    Side effects: creates or replaces ``path``.
    """
    fields = list(ReviewRow.model_fields)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if v is None else v for k, v in row.model_dump().items()})
    return len(rows)


# ── text ──────────────────────────────────────────────────────────────────


def _dist(counts: dict[str, int], denominator: int | None = None) -> str:
    total = sum(counts.values()) if denominator is None else denominator
    if not counts:
        return f"none (0/{total})"
    return ", ".join(f"{k} {v}/{total}" for k, v in sorted(counts.items()))


def _project_block(p: ProjectReport) -> list[str]:
    lines = [
        p.project_key,
        f"  stored learnings written in range, by current mark of the source: "
        f"{_dist(p.stored_by_mark, p.stored_learnings)}",
        f"  source still proposed when written, and resolved since: "
        f"{p.stored_resolved_since}/{p.stored_proposed_when_written}",
        f"  replayed sessions: {p.replayed_sessions}",
    ]
    for arm in ARMS:
        lines.append(f"  replay, learnings by mark at the session's end, {arm}: {_dist(p.learnings_by_mark[arm])}")
    for arm in ARMS:
        c = p.cost[arm]
        lines.append(
            f"  replay cost, {arm}: model calls {c.calls}/{p.replayed_sessions}, usage reported "
            f"{c.calls_with_usage}/{c.calls}, prompt tokens {c.prompt_tokens}, completion tokens "
            f"{c.completion_tokens}, seconds {c.seconds:.1f}"
        )
    whole_n = p.paired + p.whole_only
    nearest_n = p.paired + p.nearest_only
    lines.append(
        f"  replay, made by one arm only: whole only {p.whole_only}/{whole_n}, nearest only "
        f"{p.nearest_only}/{nearest_n}, paired {p.paired}"
    )
    items = sum(p.recall_items_by_mark.values())
    lines.append(
        f"  recall: calls {p.recall_calls}, items by mark {_dist(p.recall_items_by_mark, items)}, "
        f"rendered characters as shown (with ids and source marks)/as bare text {p.recall_chars_with}/{p.recall_chars_without}"
    )
    events = sum(p.quote.values())
    verified = p.quote.get("verified", 0)
    missed = p.quote.get("not_found", 0)
    lines.append(f"  quote check, events: {_dist(p.quote, events)}")
    lines.append(f"  verified quotes by position: {_dist(p.quote_position, verified)}")
    lines.append(f"  missed quotes by word share in one entry: {_dist(p.quote_miss_share, missed)}")
    resolutions = sum(p.resolution.values())
    lines.append(f"  human resolutions by resolution quote: {_dist(p.resolution, resolutions)}")
    for status, by_quote in sorted(p.resolution_by_quote.items()):
        lines.append(f"    {status}, by the decision's quote check: {_dist(by_quote, p.resolution.get(status, 0))}")
    lines.append(
        f"  verified resolution quotes by position: {_dist(p.resolution_position, p.resolution.get('verified', 0))}"
    )
    lines.append(f"  human resolutions by timing (a count, never a mark): {_dist(p.resolution_timing, resolutions)}")
    lines.append(f"  corrections by mark of the corrected item: {_dist(p.corrections_by_mark)}")
    for arm in ARMS:
        new = sum(p.learnings_by_mark[arm].values())
        lines.append(f"  replay, similarity to the closest stored learning, {arm}: {_dist(p.similarity[arm], new)}")
    lines.append(f"  recorded failures by step: {_dist(p.errors_by_step)}")
    c = p.capture
    lines.append(f"  words entries by kind: {_dist(p.words_by_kind, p.words_entries)}")
    lines.append(
        f"  prompt entries that begin with a tag other than pasted text: {p.suspect_entries}/{p.words_entries}"
    )
    lines.append(
        f"  capture against host transcripts: prompts recorded/in transcript {c.prompts_recorded}/"
        f"{c.prompts_in_transcript}, dialog answers recorded/in transcript {c.dialog_answers_recorded}/"
        f"{c.dialog_answers_in_transcript}; host sessions {c.host_sessions}, transcript missing "
        f"{c.transcripts_missing}/{c.host_sessions}, unreadable {c.transcripts_unreadable}/{c.host_sessions}"
    )
    lines.append(f"  {_NOT_CAPTURED_LINE}")
    lines.append(f"  unreadable session files: {p.unreadable_sessions}")
    return lines


def format_report(report: ShadowReport) -> str:
    """Render the report as text: one block per project, then TOTAL. Counts with denominators, no percentages."""
    started = report.started_at.date().isoformat() if report.started_at else "not started"
    stopped = f"stopped {report.stopped_at.date().isoformat()}" if report.stopped_at else "still running"
    lines = [
        f"TRACE provenance-marks report — {report.date_from.isoformat()} to {report.date_to.isoformat()}",
        f"started {started}; {stopped}",
        "",
    ]
    for project in report.projects:
        lines.extend(_project_block(project))
        lines.append("")
    lines.extend(_project_block(report.total))
    lines.append("")
    without = ", ".join(report.projects_without_words) or "none"
    lines.append(f"projects with no words recorded: {without}")
    return "\n".join(lines)
