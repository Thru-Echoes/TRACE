"""Provenance marks: where an event or a learning came from, computed from the record.

A mark is a short tag (``[accepted]``, ``[proposed]``, ``[quote-verified]``, ...)
derived by a pure function of a logged event and, for the quote checks, the
person's own words as recorded by the host hooks. Marks are never stored: a
stored mark goes stale when a decision is resolved later, and the record already
holds every input (disposition, resolver, actor, proposal time, ``resolved_at``,
``resolution_snippet``). See ADR 008 (D1, D2).

The quote check is a substring test, never a model's judgment: whether a person
typed given words is a fact a substring test settles. A miss is not evidence of
an invented quote.

This module is core and imports nothing from ``trace_mcp.extensions`` (ADR 003).

Exports:
    MIN_FRAGMENT_CHARS, SOURCE_UNKNOWN_TAG, MARK_MEANINGS, QUOTE_TAG_MEANINGS, NO_MARK_MEANING, MARK_LEGEND,
    SOURCE_MARKS_LEAD
    BaseMark, QuoteStatus, ResolutionQuote, ResolutionTiming, WordsKind, QuotePosition
    WordsEntry, EventMarks
    normalize_quote, get_snippet_fragments, get_base_mark, get_turn_entries,
    get_quote_status, get_quote_position, get_quote_word_share,
    get_resolution_quote, get_resolution_timing, get_event_marks, is_human_resolution,
    get_mark_legend

Side effects: none. Every function is pure.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trace_mcp.schema import TraceEvent
from trace_mcp.tools.session_tools import _is_explicit_absence

MIN_FRAGMENT_CHARS = 8
SOURCE_UNKNOWN_TAG = "[source-unknown]"
# What each tag a session is shown means, in display order: the base marks, then the tag for a
# learning whose source event cannot be found.
MARK_MEANINGS: dict[str, str] = {
    "[accepted]": "a person accepted it",
    "[proposed]": "an AI proposal no person resolved (a suggestion, not a rule)",
    "[rejected]": "a person rejected it",
    "[ai-resolved]": "resolved by an AI, not a person",
    "[ai-note]": "an AI's own note",
    SOURCE_UNKNOWN_TAG: "the source event could not be found",
}
# What the quote-check tags mean. A person reads these in exports and the report; a session is
# not shown them at recall.
QUOTE_TAG_MEANINGS: dict[str, str] = {
    "[quote-verified]": "the quoted words were found in the person's recorded words",
    "[quote-not-found]": "the quoted words were not found in the person's recorded words (a miss is not proof of invention)",
    "[resolution-verified]": "the words recorded as resolving it were found in the person's recorded words",
    "[resolution-not-found]": "the words recorded as resolving it were not found in the person's recorded words",
    "[resolution-unquoted]": "a person resolved it and no words were recorded",
}
NO_MARK_MEANING = "No mark: a record that is neither a decision nor an AI's note."
# How a recalled learning's marks are introduced: they describe its source event. The learning's
# text is the extractor's wording, which no person approved as such (ADR 008 D10).
SOURCE_MARKS_LEAD = (
    "Source marks describe the event each learning was drawn from, not its wording, which is the extractor's:"
)
MARK_LEGEND = "Marks: " + "; ".join(f"{tag} {meaning}" for tag, meaning in MARK_MEANINGS.items()) + "."

BaseMark = Literal["accepted", "proposed", "rejected", "ai-resolved", "ai-note"]
QuoteStatus = Literal["verified", "not_found", "absent", "absence_marker", "too_short", "unchecked"]
ResolutionQuote = Literal[
    "verified", "not_found", "absent", "absence_marker", "too_short", "unchecked", "unknown", "not_applicable"
]
ResolutionTiming = Literal["person_spoke_between", "same_turn", "unknown", "not_applicable"]
WordsKind = Literal["prompt", "dialog_answer", "dialog_note"]
QuotePosition = Literal["turn", "older"]

_ELLIPSIS_RE = re.compile(r"\.\.\.|…")
_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-z0-9']{3,}")
_SHORT_STRIP = " .,!?;:'\""


class WordsEntry(BaseModel):
    """One entry of the person's words, as appended by a host hook."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ts: datetime = Field(..., description="UTC time the hook recorded the entry.")
    host_session_id: str = Field(..., description="The host's session id the entry arrived in.")
    text: str = Field(..., description="The submitted prompt, the dialog answer, or the dialog note.")
    kind: WordsKind = Field(default="prompt", description="Which hook input the entry came from.")
    question: str | None = Field(default=None, description="The dialog question, for dialog_answer and dialog_note.")
    transcript: str | None = Field(default=None, description="The host's transcript path, when the hook was given one.")


class EventMarks(BaseModel):
    """The marks of one event: a base mark plus the quote-check outcomes."""

    model_config = ConfigDict(frozen=True)

    base: BaseMark | None = Field(..., description="The base mark, at most one per event.")
    quote: QuoteStatus = Field(..., description="Outcome of the check on the event's conversation_snippet.")
    resolution: ResolutionQuote = Field(..., description="Outcome of the check on a human resolution's quote.")
    as_of_known: bool = Field(default=True, description="False when an as-of base mark could not be computed exactly.")

    def tags(self) -> list[str]:
        """Return the short tags for these marks, in display order.

        Only the base mark, a verified or missed conversation quote, and a
        verified, missed or absent resolution quote produce a tag.
        """
        out: list[str] = []
        if self.base:
            out.append(f"[{self.base}]")
        if self.quote == "verified":
            out.append("[quote-verified]")
        elif self.quote == "not_found":
            out.append("[quote-not-found]")
        if self.resolution == "verified":
            out.append("[resolution-verified]")
        elif self.resolution == "not_found":
            out.append("[resolution-not-found]")
        elif self.resolution == "absent":
            out.append("[resolution-unquoted]")
        return out


def get_mark_legend(tags: Iterable[str], *, any_unmarked: bool = False, lead: str = "Marks:") -> str:
    """Return one line giving the meaning of the tags present, in table order; empty when there is nothing to say.

    Tags in ``MARK_MEANINGS`` and ``QUOTE_TAG_MEANINGS`` are explained.
    ``any_unmarked`` adds what an item shown with no mark is. ``lead`` opens the
    line: ``SOURCE_MARKS_LEAD`` where the marks belong to a learning's source.
    """
    present = set(tags)
    meanings = {**MARK_MEANINGS, **QUOTE_TAG_MEANINGS}
    parts = [f"{tag} {meaning}" for tag, meaning in meanings.items() if tag in present]
    if parts:
        head = [f"{lead} " + "; ".join(parts) + "."]
    else:  # a lead that says what the marks describe still says it when every item is unmarked
        head = [lead.rstrip(":") + "."] if any_unmarked and lead != "Marks:" else []
    pieces = head + ([NO_MARK_MEANING] if any_unmarked else [])
    return " ".join(pieces)


def _utc(ts: datetime) -> datetime:
    """Read a timestamp that carries no timezone as UTC, which is what every writer records."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def normalize_quote(text: str) -> str:
    """Return ``text`` in Unicode NFC, case-folded, with whitespace runs collapsed and the ends stripped."""
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFC", text).casefold()).strip()


def get_snippet_fragments(snippet: str) -> list[str]:
    """Split a snippet at ellipses and return the normalized pieces of at least MIN_FRAGMENT_CHARS, in order."""
    pieces = (normalize_quote(piece) for piece in _ELLIPSIS_RE.split(snippet))
    return [piece for piece in pieces if len(piece) >= MIN_FRAGMENT_CHARS]


def get_base_mark(event: TraceEvent, *, as_of: datetime | None = None) -> tuple[BaseMark | None, bool]:
    """Return ``(base mark, as_of_known)`` for an event, optionally as of a past time.

    A resolved decision is ``proposed`` as of any time before its ``resolved_at``.
    A decision resolved before the record held ``resolved_at`` takes its current
    mark with ``as_of_known=False`` when an as-of time is asked for.
    """
    if event.type == "decision" and event.decision is not None:
        decision = event.decision
        if decision.disposition == "proposed":
            return "proposed", True
        if as_of is not None and decision.resolved_at is not None and _utc(as_of) < _utc(decision.resolved_at):
            return "proposed", True
        resolver = decision.resolved_by
        if resolver is None or resolver.type != "human":
            mark: BaseMark = "ai-resolved"
        elif decision.disposition in ("accepted", "revised"):
            mark = "accepted"
        else:
            mark = "rejected"
        return mark, not (as_of is not None and decision.resolved_at is None)
    if event.type == "annotation" and event.actor.type == "ai":
        return "ai-note", True
    return None, True


def _candidates(words: Sequence[WordsEntry], not_after: datetime) -> list[WordsEntry]:
    limit = _utc(not_after)
    return sorted((w for w in words if _utc(w.ts) <= limit), key=lambda w: _utc(w.ts))


def get_turn_entries(words: Sequence[WordsEntry], *, not_after: datetime) -> list[WordsEntry]:
    """Return the person's input in the turn ``not_after`` falls in, in time order.

    The turn is the latest ``prompt`` entry at or before ``not_after`` plus every
    entry recorded after it (dialog answers and notes). With no prompt entry at
    or before ``not_after``, every entry at or before it.
    """
    candidates = _candidates(words, not_after)
    last_prompt = max((i for i, w in enumerate(candidates) if w.kind == "prompt"), default=None)
    return candidates if last_prompt is None else candidates[last_prompt:]


def _short_form(text: str) -> str:
    return normalize_quote(text).strip(_SHORT_STRIP)


def _all_fragments_found(fragments: Sequence[str], entries: Sequence[WordsEntry]) -> bool:
    texts = [normalize_quote(w.text) for w in entries]
    return all(any(fragment in text for text in texts) for fragment in fragments)


def _short_quote_in_turn(snippet: str, words: Sequence[WordsEntry], not_after: datetime) -> bool:
    short = _short_form(snippet)
    if not short:
        return False
    return any(_short_form(w.text) == short for w in get_turn_entries(words, not_after=not_after))


def get_quote_status(
    snippet: str | None, words: Sequence[WordsEntry], *, not_after: datetime, session_created: datetime
) -> QuoteStatus:
    """Check a quoted snippet against the person's words recorded no later than ``not_after``.

    Returns ``absent`` for no snippet, ``absence_marker`` for an explicit absence
    marker (spec §3.4.1), ``unchecked`` when no words were recorded by the time
    the session was created, ``verified`` or ``not_found`` for a snippet with at
    least one fragment, and for a snippet with none, ``verified`` only when it
    equals a whole entry of the turn, otherwise ``too_short``.
    """
    if snippet is None or not snippet.strip():
        return "absent"
    if _is_explicit_absence(snippet):
        return "absence_marker"
    created = _utc(session_created)
    if not any(_utc(w.ts) <= created for w in words):
        return "unchecked"
    fragments = get_snippet_fragments(snippet)
    if fragments:
        return "verified" if _all_fragments_found(fragments, _candidates(words, not_after)) else "not_found"
    return "verified" if _short_quote_in_turn(snippet, words, not_after) else "too_short"


def get_quote_position(snippet: str, words: Sequence[WordsEntry], *, not_after: datetime) -> QuotePosition:
    """Return where a verified quote was found: in the turn, or only in older words. Report use only."""
    fragments = get_snippet_fragments(snippet)
    if not fragments:
        return "turn"
    return "turn" if _all_fragments_found(fragments, get_turn_entries(words, not_after=not_after)) else "older"


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(normalize_quote(text))


def get_quote_word_share(snippet: str, words: Sequence[WordsEntry], *, not_after: datetime) -> float:
    """Return how much of a missed quote is present in a single entry. Report use only.

    For each fragment with tokens, the best share over candidate entries of the
    fragment's tokens (with repeats) found in the entry's token set; the
    smallest such share over the fragments. ``0.0`` with nothing to score.
    """
    candidate_sets = [set(_tokens(w.text)) for w in _candidates(words, not_after)]
    if not candidate_sets:
        return 0.0
    shares: list[float] = []
    for fragment in get_snippet_fragments(snippet):
        tokens = _tokens(fragment)
        if not tokens:
            continue
        shares.append(max(sum(1 for t in tokens if t in found) / len(tokens) for found in candidate_sets))
    return min(shares) if shares else 0.0


def is_human_resolution(event: TraceEvent) -> bool:
    """Say whether the event is a decision the record shows as resolved by a human actor."""
    decision = event.decision
    return (
        event.type == "decision"
        and decision is not None
        and decision.disposition != "proposed"
        and decision.resolved_by is not None
        and decision.resolved_by.type == "human"
    )


def get_resolution_quote(
    event: TraceEvent, words: Sequence[WordsEntry], *, session_created: datetime
) -> ResolutionQuote:
    """Check a human resolution's ``resolution_snippet`` against the words recorded by ``resolved_at``.

    ``not_applicable`` for anything but a human-resolved decision; ``unknown``
    when the decision was resolved before the record held a resolution time.
    """
    if not is_human_resolution(event) or event.decision is None:
        return "not_applicable"
    decision = event.decision
    if decision.resolved_at is None:
        return "unknown"
    return get_quote_status(
        decision.resolution_snippet, words, not_after=decision.resolved_at, session_created=session_created
    )


def get_resolution_timing(event: TraceEvent, words: Sequence[WordsEntry]) -> ResolutionTiming:
    """Say whether the person's words were recorded between proposal and human resolution.

    A count for the report only, never a tag: most real approvals are proposed
    and resolved in the same turn (ADR 008 D1).
    """
    if not is_human_resolution(event) or event.decision is None:
        return "not_applicable"
    if event.decision.resolved_at is None:
        return "unknown"
    resolved_at, proposed_at = _utc(event.decision.resolved_at), _utc(event.timestamp)
    if not any(_utc(w.ts) <= resolved_at for w in words):
        return "unknown"
    if any(proposed_at < _utc(w.ts) <= resolved_at for w in words):
        return "person_spoke_between"
    return "same_turn"


def get_event_marks(
    event: TraceEvent, words: Sequence[WordsEntry], *, session_created: datetime, as_of: datetime | None = None
) -> EventMarks:
    """Compute an event's marks from the record and the person's words, optionally as of a past time."""
    base, as_of_known = get_base_mark(event, as_of=as_of)
    quote = get_quote_status(
        event.context.conversation_snippet, words, not_after=event.timestamp, session_created=session_created
    )
    resolution: ResolutionQuote = (
        "not_applicable" if base == "proposed" else get_resolution_quote(event, words, session_created=session_created)
    )
    return EventMarks(base=base, quote=quote, resolution=resolution, as_of_known=as_of_known)
