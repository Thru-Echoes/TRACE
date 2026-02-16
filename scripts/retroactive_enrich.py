"""Retroactively enrich existing TRACE sessions with contribution events and suggestion_type.

Only adds data that is clearly evidenced by existing log content.
Does NOT fabricate or guess — leaves fields blank when uncertain.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

SESSIONS_DIR = Path.home() / ".trace" / "sessions"


def load(session_id: str) -> dict:
    path = SESSIONS_DIR / f"{session_id}.json"
    with open(path) as f:
        return json.load(f)


def save(session_id: str, data: dict) -> None:
    path = SESSIONS_DIR / f"{session_id}.json"
    # Bump trace_version to indicate enrichment
    data["trace_version"] = "0.1.0"
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Saved {path.name}")


def next_evt_id(events: list[dict]) -> str:
    """Generate next sequential event ID."""
    max_n = 0
    for e in events:
        eid = e.get("id", "")
        if eid.startswith("evt_"):
            try:
                n = int(eid.split("_")[1])
                max_n = max(max_n, n)
            except (IndexError, ValueError):
                pass
    return f"evt_{max_n + 1:03d}"


def make_contribution(session_id: str, events: list[dict], **kwargs) -> dict:
    """Create a contribution event."""
    eid = next_evt_id(events)
    return {
        "id": eid,
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "type": "contribution",
        "actor": {"type": kwargs.get("actor_type", "ai"), "id": kwargs.get("actor_id", "ai-assistant"), "role": None},
        "tool_call": None,
        "decision": None,
        "annotation": None,
        "state_change": None,
        "contribution": {
            "description": kwargs["description"],
            "artifact": kwargs.get("artifact"),
            "direction": kwargs["direction"],
            "execution": kwargs["execution"],
            "related_decision_ids": kwargs.get("related_decision_ids", []),
            "tags": kwargs.get("tags", []),
        },
        "context": {
            "conversation_turn": None,
            "parent_event_id": None,
            "reasoning_summary": "Retroactively added during schema enrichment",
            "related_event_ids": [],
        },
        "verification": None,
    }


def add_suggestion_type(event: dict, suggestion_type: str) -> None:
    """Add suggestion_type to a decision event."""
    if event.get("decision"):
        event["decision"]["suggestion_type"] = suggestion_type


def ensure_suggestion_type_field(event: dict) -> None:
    """Ensure the suggestion_type field exists (as null) for schema consistency."""
    if event.get("decision") and "suggestion_type" not in event["decision"]:
        event["decision"]["suggestion_type"] = None


def ensure_contribution_field(event: dict) -> None:
    """Ensure the contribution field exists (as null) for schema consistency."""
    if "contribution" not in event:
        event["contribution"] = None


# ── Per-session enrichment functions ─────────────────────────────────────────


def enrich_20260206_8ed310(s: dict) -> None:
    """hypostimuli-news-summarizer: AI architecture planning session.
    3 decisions, all proactively proposed by AI. No execution happened here (planning only)."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)
        if evt["type"] == "decision" and evt["decision"]["proposed_by"]["type"] == "ai":
            add_suggestion_type(evt, "proactive")


def enrich_20260206_8116ce(s: dict) -> None:
    """hypostimuli-news-summarizer: User requirements + architecture decision.
    Decision proposed by AI after user stated requirements."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)
        if evt["type"] == "decision" and evt["decision"]["proposed_by"]["type"] == "ai":
            # AI synthesized user requirements into an architecture — this was requested
            add_suggestion_type(evt, "requested")


def enrich_20260206_3ae854(s: dict) -> None:
    """carbon-pulse: Architecture decision proposed by AI after discovering HTTP 403."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)
        if evt["type"] == "decision" and evt["decision"]["proposed_by"]["type"] == "ai":
            # AI proposed this approach after observing the 403 barrier
            add_suggestion_type(evt, "proactive")


def enrich_20260206_e4e1dd(s: dict) -> None:
    """carbon-pulse: Module structure + dependencies + discovery-first approach.
    3 decisions by AI. These were AI proactively designing the implementation."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)
        if evt["type"] == "decision" and evt["decision"]["proposed_by"]["type"] == "ai":
            add_suggestion_type(evt, "proactive")


def enrich_20260206_ed3db3(s: dict) -> None:
    """carbon-pulse: Test strategy designed and executed by AI.
    Decision accepted, learning logged — AI both proposed and executed."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)
        if evt["type"] == "decision":
            add_suggestion_type(evt, "proactive")

    # The annotation confirms tests were written: "When mocking Playwright locators..."
    # AI proposed the strategy and executed it
    s["events"].append(make_contribution(
        s["id"], s["events"],
        description="Implemented test suite for scraper with mocked Playwright objects",
        artifact="tests/",
        direction="ai",
        execution="ai",
        related_decision_ids=["evt_001"],
        tags=["testing", "scraper"],
    ))


def enrich_20260209_2f6b35(s: dict) -> None:
    """WAMA: Likert phrases documentation. AI proposed approach, human accepted Option C,
    AI executed the changes across 4 manuscript versions."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)
        if evt["type"] == "decision":
            add_suggestion_type(evt, "proactive")

    # Annotation evt_002 explicitly says AI added the documentation to all 4 versions
    s["events"].append(make_contribution(
        s["id"], s["events"],
        description="Added Likert thematic phrases documentation (Supplementary Table S4) to 4 manuscript versions",
        artifact="NHB2026/, ACM2026/, general_manuscript/, arxiv/",
        direction="ai",
        execution="ai",
        related_decision_ids=["evt_001"],
        tags=["manuscript", "likert-phrases", "supplementary"],
    ))


def enrich_20260209_99430f(s: dict) -> None:
    """WAMA: Natbib citation fix. AI proposed the 4 rules and executed 55 replacements.
    Both proposed and self-accepted (resolved_by is also AI)."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)
        if evt["type"] == "decision":
            # AI identified the problem and proposed the fix rules
            add_suggestion_type(evt, "proactive")

    # Annotation confirms 55 replacements across 4 files
    s["events"].append(make_contribution(
        s["id"], s["events"],
        description="Fixed 55 natbib citation commands across 4 NHB2026 manuscript sections",
        artifact="NHB2026/sections/",
        direction="ai",
        execution="ai",
        related_decision_ids=["evt_001"],
        tags=["natbib", "citations", "latex"],
    ))


def enrich_20260209_ee5541(s: dict) -> None:
    """WAMA: Fix arxiv citations using NHB2026 as reference. AI proposed and auto-accepted."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)
        if evt["type"] == "decision":
            add_suggestion_type(evt, "proactive")

    s["events"].append(make_contribution(
        s["id"], s["events"],
        description="Fixed natbib citation commands in arxiv/main.tex using NHB2026 patterns as reference",
        artifact="arxiv/main.tex",
        direction="ai",
        execution="ai",
        related_decision_ids=["evt_001"],
        tags=["natbib", "citations", "latex", "arxiv"],
    ))


def enrich_20260209_76cdd0(s: dict) -> None:
    """WAMA: Synced arxiv/main.tex to match NHB2026 structure.
    Human directed (asked for sync), AI executed."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)

    # Annotation says "Successfully synced arxiv/main.tex to match NHB2026 figure/table structure"
    s["events"].append(make_contribution(
        s["id"], s["events"],
        description="Synced arxiv/main.tex figure/table structure to match NHB2026 sections",
        artifact="arxiv/main.tex",
        direction="human",
        execution="ai",
        tags=["manuscript", "sync", "arxiv"],
    ))


def enrich_20260211_694ff3(s: dict) -> None:
    """green-narrative: Legacy knowledge bank migration. 9 decisions all by human:researcher.
    These are migrated records of the researcher's own methodological choices."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)
        # All decisions in this session were proposed by human:researcher
        # They are the researcher's own choices — no suggestion_type applies


def enrich_20260211_ac652b(s: dict) -> None:
    """green-narrative: Projection head planning. 5 decisions all proposed by AI.
    This was a planning session — AI proactively designed the implementation approach."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)
        if evt["type"] == "decision" and evt["decision"]["proposed_by"]["type"] == "ai":
            add_suggestion_type(evt, "proactive")


def enrich_20260211_cc217f(s: dict) -> None:
    """green-narrative: Projection head implementation. Human directed the task,
    AI executed the entire pipeline (model, training, inference)."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)

    # Session summary explicitly lists files created and artifacts generated
    # Human directed (asked for projection head), AI executed everything
    s["events"].append(make_contribution(
        s["id"], s["events"],
        description="Implemented consensus UMAP target generation pipeline (30-seed, distance-matrix consensus)",
        artifact="hye_in/projection/generate_targets.py",
        direction="human",
        execution="ai",
        tags=["consensus-umap", "target-generation"],
    ))
    s["events"].append(make_contribution(
        s["id"], s["events"],
        description="Implemented ProjectionHead (384->15) and VisualizationHead (15->2) PyTorch models with custom loss",
        artifact="hye_in/projection/model.py",
        direction="collaborative",
        execution="ai",
        related_decision_ids=[],  # Decisions are in trace_20260211_ac652b, different session
        tags=["neural-network", "pytorch", "model"],
    ))
    s["events"].append(make_contribution(
        s["id"], s["events"],
        description="Implemented 5-fold stratified CV training pipeline with early stopping",
        artifact="hye_in/projection/train.py",
        direction="collaborative",
        execution="ai",
        tags=["training", "cross-validation"],
    ))
    s["events"].append(make_contribution(
        s["id"], s["events"],
        description="Implemented inference pipeline and deliverable builder for Hye-In",
        artifact="hye_in/projection/inference.py",
        direction="human",
        execution="ai",
        tags=["inference", "deliverable"],
    ))


def enrich_20260213_058f4f(s: dict) -> None:
    """carbon-pulse: Phase 0 recon script approach. AI proposed and auto-accepted."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)
        if evt["type"] == "decision":
            add_suggestion_type(evt, "proactive")


def enrich_20260211_0f1778(s: dict) -> None:
    """green-narrative: Single AI-proposed decision about projection head architecture."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)
        if evt["type"] == "decision" and evt["decision"]["proposed_by"]["type"] == "ai":
            add_suggestion_type(evt, "proactive")


def enrich_schema_only(s: dict) -> None:
    """Add the new fields (suggestion_type, contribution) as null to all events
    for schema consistency, but don't add any substantive data."""
    for evt in s["events"]:
        ensure_suggestion_type_field(evt)
        ensure_contribution_field(evt)


# ── Main ─────────────────────────────────────────────────────────────────────


ENRICHMENT_MAP = {
    # Sessions with substantive enrichment
    "trace_20260206_8ed310": enrich_20260206_8ed310,
    "trace_20260206_8116ce": enrich_20260206_8116ce,
    "trace_20260206_3ae854": enrich_20260206_3ae854,
    "trace_20260206_e4e1dd": enrich_20260206_e4e1dd,
    "trace_20260206_ed3db3": enrich_20260206_ed3db3,
    "trace_20260209_2f6b35": enrich_20260209_2f6b35,
    "trace_20260209_99430f": enrich_20260209_99430f,
    "trace_20260209_ee5541": enrich_20260209_ee5541,
    "trace_20260209_76cdd0": enrich_20260209_76cdd0,
    "trace_20260211_694ff3": enrich_20260211_694ff3,
    "trace_20260211_ac652b": enrich_20260211_ac652b,
    "trace_20260211_cc217f": enrich_20260211_cc217f,
    "trace_20260213_058f4f": enrich_20260213_058f4f,
    "trace_20260211_0f1778": enrich_20260211_0f1778,
    # Sessions with events but no AI decisions — schema-only
    "trace_20260206_6a691d": enrich_schema_only,
    "trace_20260206_9ec50d": enrich_schema_only,
    "trace_20260206_b45760": enrich_schema_only,
    "trace_20260209_1d36dd": enrich_schema_only,
    "trace_20260209_33ac1f": enrich_schema_only,
    "trace_20260209_d344d8": enrich_schema_only,
    "trace_20260209_e3486b": enrich_schema_only,
    "trace_20260213_969a99": enrich_schema_only,
    "trace_20260213_c949eb": enrich_schema_only,
    "trace_20260216_0bfd45": enrich_schema_only,
}

# Sessions with 0 events — skip entirely
SKIP = {
    "trace_20260206_3a44c1",  # 0 events
    "trace_20260209_1fb94a",  # 0 events
    "trace_20260209_9e9604",  # 0 events
    "trace_20260209_f99976",  # 0 events
    "trace_20260216_7fd1df",  # 0 events (our own session)
    "trace_20260216_d4ac30",  # 0 events
    # v1 files — different format, skip
    "trace_v1_orphan_when_algorithms_meet_artists",
    "trace_v1_S001_when_algorithms_meet_artists",
    "trace_v1_S002_when_algorithms_meet_artists",
    "trace_v1_S003_when_algorithms_meet_artists",
}


def main() -> None:
    print("TRACE Retroactive Enrichment")
    print("=" * 60)

    enriched = 0
    contributions_added = 0
    suggestion_types_added = 0

    for session_id, enrich_fn in ENRICHMENT_MAP.items():
        path = SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            print(f"  SKIP {session_id} — file not found")
            continue

        print(f"\n{session_id}:")
        s = load(session_id)
        old_event_count = len(s.get("events", []))

        # Count decisions before
        old_suggestion_types = sum(
            1 for e in s.get("events", [])
            if e.get("decision", {}) and e["decision"].get("suggestion_type")
        )

        enrich_fn(s)

        new_event_count = len(s.get("events", []))
        new_suggestion_types = sum(
            1 for e in s.get("events", [])
            if e.get("decision") and e["decision"] and e["decision"].get("suggestion_type")
        )

        added = new_event_count - old_event_count
        st_added = new_suggestion_types - old_suggestion_types

        if added > 0:
            print(f"  +{added} contribution events")
            contributions_added += added
        if st_added > 0:
            print(f"  +{st_added} suggestion_type fields")
            suggestion_types_added += st_added
        if added == 0 and st_added == 0:
            print(f"  Schema fields updated (no substantive changes)")

        save(session_id, s)
        enriched += 1

    print(f"\n{'=' * 60}")
    print(f"Sessions enriched: {enriched}")
    print(f"Contribution events added: {contributions_added}")
    print(f"suggestion_type fields added: {suggestion_types_added}")
    print(f"Sessions skipped (0 events or v1): {len(SKIP)}")


if __name__ == "__main__":
    main()
