"""`trace_propose_decision` accepts the measurement that motivated a decision.

Before this, `decision.confidence` could only enter a record through an importer,
so a measurement made in conversation — a bootstrap run in a notebook, a benchmark
whose numbers are on screen — had no path into TRACE except hand-writing a log in
one specific producer's schema.

The block is validated through `DecisionConfidence`, the same model the importers
use, so a measurement logged in conversation and one built by an importer cannot be
validated two different ways. A malformed block is refused outright rather than
stored partially: a half-written measurement misstates what was measured.

Nothing here verifies the numbers or the evidence digests against any file. That is
true of an imported block too; what differs is that an imported one carries
`contract`, naming whose rules govern it, and a block logged in conversation does
not — which is what distinguishes the two in a record.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from test_e2e_server import _call_tool, _initialize_server, _shutdown_server, _start_server

from trace_mcp.schema import Session
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import decision_tools, session_tools

# A real measurement's shape: the fixture's replication line, which cleared.
MEASUREMENT: dict[str, Any] = {
    "statistic": "mean_paired_delta",
    "estimate": 518.75,
    "unit": "game_score",
    "direction": "higher",
    "interval": {"lower": 467.5, "upper": 575.0, "level": 0.9},
    "method": {
        "name": "percentile_bootstrap",
        "algorithm": "rsi-exam-gate/percentile-bootstrap/1",
        "resamples": 5000,
        "seed": 20260902,
    },
    "sample_size": 8,
}


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(directory=str(tmp_path))


@pytest.fixture
def active() -> dict[str, Session]:
    return {}


async def _session(storage: JsonFileStorage, active: dict[str, Session]) -> Session:
    result = await session_tools.start_session(
        storage, active, project="confidence-test", description="Measured decision test session"
    )
    return active[result.split("Session: ")[1].split("\n")[0]]


class TestMeasurementIsRecorded:
    async def test_a_decision_carries_the_measurement(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        session = await _session(storage, active)

        event_id = await decision_tools.propose_decision(
            storage,
            session,
            description="Adopt the v3 policy",
            proposed_by_type="ai",
            proposed_by_id="assistant",
            confidence=MEASUREMENT,
        )

        stored = await storage.get_session(session.id)
        event = next(e for e in stored.events if e.id == event_id)
        assert event.decision is not None
        block = event.decision.confidence
        assert block is not None
        assert block.estimate == 518.75
        assert block.interval.lower == 467.5
        assert block.interval.level == 0.9
        assert block.method.name == "percentile_bootstrap"
        assert block.sample_size == 8
        assert block.direction == "higher"

    async def test_a_decision_without_one_is_unchanged(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """The field is optional and its absence must stay the ordinary case."""
        session = await _session(storage, active)

        event_id = await decision_tools.propose_decision(
            storage,
            session,
            description="Rename the module",
            proposed_by_type="human",
            proposed_by_id="user",
        )

        stored = await storage.get_session(session.id)
        event = next(e for e in stored.events if e.id == event_id)
        assert event.decision is not None
        assert event.decision.confidence is None

    async def test_evidence_entries_are_carried(self, storage: JsonFileStorage, active: dict[str, Session]) -> None:
        session = await _session(storage, active)
        digest = "a" * 64
        measured = {
            **MEASUREMENT,
            "evidence": [{"role": "parent", "locator": "results/v1/result.json", "sha256": digest}],
            "evidence_digests": {"parent": f"sha256:{digest}"},
        }

        event_id = await decision_tools.propose_decision(
            storage,
            session,
            description="Adopt the v3 policy",
            proposed_by_type="ai",
            proposed_by_id="assistant",
            confidence=measured,
        )

        stored = await storage.get_session(session.id)
        event = next(e for e in stored.events if e.id == event_id)
        assert event.decision is not None and event.decision.confidence is not None
        evidence = event.decision.confidence.evidence
        assert evidence is not None and evidence[0].role == "parent"
        assert evidence[0].sha256 == digest

    async def test_a_conversational_block_carries_no_contract(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """What distinguishes a logged measurement from an imported one.

        An importer sets `contract` to the producer schema whose rules govern the
        block's extra keys. A measurement logged in conversation has no such
        producer, so the field stays absent — and that absence is the honest
        signal, without inventing a field to carry it.
        """
        session = await _session(storage, active)

        event_id = await decision_tools.propose_decision(
            storage,
            session,
            description="Adopt the v3 policy",
            proposed_by_type="ai",
            proposed_by_id="assistant",
            confidence=MEASUREMENT,
        )

        stored = await storage.get_session(session.id)
        event = next(e for e in stored.events if e.id == event_id)
        assert event.decision is not None and event.decision.confidence is not None
        assert event.decision.confidence.contract is None


class TestMalformedBlocksAreRefused:
    """A measurement that cannot be constructed is not recorded at all."""

    @pytest.mark.parametrize(
        ("mutation", "why"),
        [
            ({"interval": {"lower": 600.0, "upper": 500.0, "level": 0.9}}, "bounds out of order"),
            ({"interval": {"lower": 1.0, "upper": 2.0, "level": 1.5}}, "coverage outside the unit interval"),
            ({"sample_size": 0}, "sample size below one"),
            ({"direction": "sideways"}, "direction outside the vocabulary"),
            ({"statistic": ""}, "empty statistic"),
            ({"estimate": float("inf")}, "non-finite estimate"),
        ],
    )
    async def test_a_malformed_block_raises(
        self, storage: JsonFileStorage, active: dict[str, Session], mutation: dict[str, Any], why: str
    ) -> None:
        session = await _session(storage, active)

        with pytest.raises(ValueError, match="not a valid measurement block"):
            await decision_tools.propose_decision(
                storage,
                session,
                description=f"Should be refused: {why}",
                proposed_by_type="ai",
                proposed_by_id="assistant",
                confidence={**MEASUREMENT, **mutation},
            )

    async def test_nothing_is_written_when_the_block_is_refused(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        """The refusal happens before the event is appended, not after."""
        session = await _session(storage, active)
        before = len((await storage.get_session(session.id)).events)

        with pytest.raises(ValueError):
            await decision_tools.propose_decision(
                storage,
                session,
                description="Adopt the v3 policy",
                proposed_by_type="ai",
                proposed_by_id="assistant",
                confidence={**MEASUREMENT, "sample_size": 0},
            )

        assert len((await storage.get_session(session.id)).events) == before


class TestValidatedTheSameWayAsAnImport:
    """One model validates both paths, so the two cannot disagree."""

    async def test_an_importer_block_is_accepted_verbatim(
        self, storage: JsonFileStorage, active: dict[str, Session]
    ) -> None:
        fixture = Path(__file__).parent / "fixtures" / "decision_log_v1" / "expected_session_nullfilled.json"
        imported = json.loads(fixture.read_text(encoding="utf-8"))
        block = next(e["decision"]["confidence"] for e in imported["events"] if e.get("decision"))
        session = await _session(storage, active)

        event_id = await decision_tools.propose_decision(
            storage,
            session,
            description="A block an importer produced, logged through the tool",
            proposed_by_type="ai",
            proposed_by_id="assistant",
            confidence=block,
        )

        stored = await storage.get_session(session.id)
        event = next(e for e in stored.events if e.id == event_id)
        assert event.decision is not None and event.decision.confidence is not None
        # The producer's rule-state extras survive, uninterpreted.
        dumped = event.decision.confidence.model_dump(mode="json")
        assert dumped["contract"] == "rsi-exam-decision-log/v1"
        assert "verdict" in dumped and "min_effect" in dumped


class TestOverTheWire:
    """The argument reaches the tool through a real MCP server."""

    async def test_a_measured_decision_round_trips_over_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                started = await _call_tool(
                    proc,
                    "trace_start_session",
                    {"project": "confidence-e2e", "description": "Measured decision over the wire"},
                    request_id=2,
                )
                session_id = json.dumps(started).split("Session: ")[1].split("\\n")[0]
                proposed = await _call_tool(
                    proc,
                    "trace_propose_decision",
                    {
                        "description": "Adopt the v3 policy",
                        "proposed_by_type": "ai",
                        "proposed_by_id": "assistant",
                        "confidence": MEASUREMENT,
                    },
                    request_id=3,
                )
                assert "Decision proposed" in json.dumps(proposed)

                events = await _call_tool(
                    proc, "trace_get_events", {"session_id": session_id, "type": "decision"}, request_id=4
                )
                text = json.dumps(events)
                assert "mean_paired_delta" in text
                assert "518.75" in text or "518.8" in text
            finally:
                await _shutdown_server(proc)

    async def test_a_malformed_block_is_reported_not_stored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await _start_server(tmpdir)
            try:
                await _initialize_server(proc)
                await _call_tool(
                    proc,
                    "trace_start_session",
                    {"project": "confidence-e2e", "description": "Refusal over the wire"},
                    request_id=2,
                )
                result = await _call_tool(
                    proc,
                    "trace_propose_decision",
                    {
                        "description": "Should be refused",
                        "proposed_by_type": "ai",
                        "proposed_by_id": "assistant",
                        "confidence": {**MEASUREMENT, "sample_size": 0},
                    },
                    request_id=3,
                )
                assert "not a valid measurement block" in json.dumps(result)
            finally:
                await _shutdown_server(proc)
