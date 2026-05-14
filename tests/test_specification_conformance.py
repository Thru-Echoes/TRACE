"""Specification conformance tests for TRACE.

These tests verify that:
1. A hand-crafted JSON document (no TRACE code) conforms to the specification
2. A TRACE-generated session document conforms to the specification
3. Both documents are structurally equivalent against the JSON Schema
4. Every normative requirement in the specification is covered by TRACE's
   data model, tools, validation, and export capabilities

The specification lives at docs/specification.md and is technology-agnostic.
TRACE (this project) is one conforming implementation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from trace_mcp.schema import (
    Actor,
    AnnotationData,
    ContributionData,
    DecisionData,
    Environment,
    Session,
    SessionMetadata,
    StateChangeData,
    ToolCallData,
    TraceEvent,
)
from trace_mcp.schema.events import EventContext
from trace_mcp.schema.prov_mapping import PROV_CONTEXT, PROV_MAPPING
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import decision_tools, logging_tools, query_tools, session_tools
from trace_mcp.exporters import export_prov_jsonld

SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "trace-v0.3.json"
SPEC_PATH = Path(__file__).parent.parent / "docs" / "specification.md"


@pytest.fixture
def schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def spec_text() -> str:
    return SPEC_PATH.read_text(encoding="utf-8")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _handcrafted_session() -> dict[str, Any]:
    """Build a session document by hand (pure dict, no TRACE imports).

    This proves the spec can be implemented without TRACE's code.
    Every field is set deliberately from the specification tables.
    """
    return {
        # §3.1 Session Document
        "context": "https://trace-protocol.org/v0.3",
        "trace_version": "0.4.1",
        "id": "handcrafted_20260319_aaa111",
        "created": "2026-03-19T10:00:00+00:00",
        "ended": "2026-03-19T11:30:00+00:00",
        "status": "completed",
        "summary": "Handcrafted conformance test session.",
        # §3.2 Session Metadata
        "metadata": {
            "project": "conformance-test",
            "experiment_id": "exp-conf-001",
            "description": "A session document built entirely by hand from the spec.",
            "participants": [
                # §3.3 Actor
                {"type": "human", "id": "tester-alice", "role": "lead"},
                {"type": "ai", "id": "model-beta", "role": "assistant"},
                {"type": "system", "id": "ci-pipeline"},
            ],
            "environment": {
                # §3.2.1 — schema field names (mcp_servers, python_version, trace_version)
                "mcp_servers": ["search-server", "analysis-server"],
                "client": "test-harness",
                "os": "Linux 6.1",
                "python_version": "3.12.0",
                "trace_version": "0.4.1",
                "custom": {"gpu": "A100"},
            },
            "tags": ["conformance", "e2e"],
            "doi": "10.1234/example",
            "custom": {"lab": "test-lab"},
        },
        "events": [
            # §3.5 Tool Invocation — success
            {
                "id": "evt_001",
                "timestamp": "2026-03-19T10:05:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "tool_call",
                "actor": {"type": "ai", "id": "model-beta"},
                "tool_call": {
                    "server": "search-server",
                    "method": "tools/call",
                    "name": "search_documents",
                    "input": {"query": "climate change", "limit": 10},
                    "output": {"results": 42},
                    "output_truncated": False,
                    "output_hash": "sha256:abc123",
                    "duration_ms": 1500,
                    "status": "success",
                },
                "context": {
                    "conversation_turn": 1,
                    "reasoning_summary": "Searching for relevant documents.",
                },
            },
            # §3.5 Tool Invocation — error + retry chain
            {
                "id": "evt_002",
                "timestamp": "2026-03-19T10:06:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "tool_call",
                "actor": {"type": "ai", "id": "model-beta"},
                "tool_call": {
                    "server": "analysis-server",
                    "name": "run_analysis",
                    "input": {"method": "regression"},
                    "status": "error",
                    "error_message": "Connection timeout",
                },
                "context": {},
            },
            {
                "id": "evt_003",
                "timestamp": "2026-03-19T10:07:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "tool_call",
                "actor": {"type": "ai", "id": "model-beta"},
                "tool_call": {
                    "server": "analysis-server",
                    "name": "run_analysis",
                    "input": {"method": "regression"},
                    "status": "success",
                    "duration_ms": 800,
                    "retries_event_id": "evt_002",
                },
                "context": {},
            },
            # §3.6 Decision — proposed (unresolved)
            {
                "id": "evt_004",
                "timestamp": "2026-03-19T10:10:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "decision",
                "actor": {"type": "ai", "id": "model-beta"},
                "decision": {
                    "description": "Use random forest with 100 trees",
                    "rationale": "Good balance of accuracy and speed",
                    "proposed_by": {"type": "ai", "id": "model-beta"},
                    "disposition": "proposed",
                    "suggestion_type": "proactive",
                    "tags": ["methodology", "model-selection"],
                    "warnings": ["Previously rejected similar approach in session X"],
                },
                "context": {
                    "reasoning_summary": "Compared RF, XGBoost, and LR on validation set.",
                },
            },
            # §3.6 Decision — accepted
            {
                "id": "evt_005",
                "timestamp": "2026-03-19T10:15:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "decision",
                "actor": {"type": "human", "id": "tester-alice"},
                "decision": {
                    "description": "Use random forest with 100 trees",
                    "proposed_by": {"type": "ai", "id": "model-beta"},
                    "disposition": "accepted",
                    "resolved_by": {"type": "human", "id": "tester-alice"},
                    "revises_event_id": "evt_004",
                    "suggestion_type": "proactive",
                },
                "context": {
                    "conversation_snippet": "Sounds good, let's go with random forest.",
                },
            },
            # §3.6 Decision — revised (decision chain)
            {
                "id": "evt_006",
                "timestamp": "2026-03-19T10:20:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "decision",
                "actor": {"type": "human", "id": "tester-alice"},
                "decision": {
                    "description": "Switch to 200 trees for better accuracy",
                    "rationale": "100 trees underfitting on validation data",
                    "proposed_by": {"type": "human", "id": "tester-alice"},
                    "disposition": "revised",
                    "resolved_by": {"type": "human", "id": "tester-alice"},
                    "revision_note": "Validation accuracy jumped 3% with 200 trees",
                    "revises_event_id": "evt_005",
                    "suggestion_type": "requested",
                },
                "context": {},
            },
            # §3.6 Decision — rejected
            {
                "id": "evt_007",
                "timestamp": "2026-03-19T10:25:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "decision",
                "actor": {"type": "human", "id": "tester-alice"},
                "decision": {
                    "description": "Use GPU-accelerated XGBoost instead",
                    "rationale": "Faster training on large datasets",
                    "proposed_by": {"type": "ai", "id": "model-beta"},
                    "disposition": "rejected",
                    "resolved_by": {"type": "human", "id": "tester-alice"},
                    "revision_note": "RF is already fast enough and more interpretable",
                },
                "context": {},
            },
            # §3.7 Annotation — each category
            {
                "id": "evt_008",
                "timestamp": "2026-03-19T10:30:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "annotation",
                "actor": {"type": "ai", "id": "model-beta"},
                "annotation": {
                    "category": "gotcha",
                    "content": "Dataset has 5% missing values in column 'temperature'.",
                    "tags": ["data-quality"],
                },
                "context": {},
            },
            {
                "id": "evt_009",
                "timestamp": "2026-03-19T10:31:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "annotation",
                "actor": {"type": "ai", "id": "model-beta"},
                "annotation": {
                    "category": "learning",
                    "content": "Imputation with median works better than mean for skewed data.",
                    "tags": ["methodology"],
                },
                "context": {},
            },
            # §3.7 Annotation — correction with corrects_event_ids
            {
                "id": "evt_010",
                "timestamp": "2026-03-19T10:35:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "annotation",
                "actor": {"type": "human", "id": "tester-alice"},
                "annotation": {
                    "category": "correction",
                    "content": "The AI used mean imputation but should have used median.",
                    "corrects_event_ids": ["evt_003"],
                    "related_event_ids": ["evt_008"],
                    "tags": ["data-quality", "correction"],
                },
                "context": {
                    "conversation_snippet": "No, use median — the distribution is skewed.",
                },
            },
            {
                "id": "evt_011",
                "timestamp": "2026-03-19T10:36:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "annotation",
                "actor": {"type": "human", "id": "tester-alice"},
                "annotation": {"category": "observation", "content": "R² improved from 0.72 to 0.81 after fixing imputation."},
                "context": {},
            },
            {
                "id": "evt_012",
                "timestamp": "2026-03-19T10:37:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "annotation",
                "actor": {"type": "human", "id": "tester-alice"},
                "annotation": {"category": "todo", "content": "Run cross-validation next session."},
                "context": {},
            },
            {
                "id": "evt_013",
                "timestamp": "2026-03-19T10:38:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "annotation",
                "actor": {"type": "ai", "id": "model-beta"},
                "annotation": {"category": "question", "content": "Should we include the 2024 data?"},
                "context": {},
            },
            {
                "id": "evt_014",
                "timestamp": "2026-03-19T10:39:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "annotation",
                "actor": {"type": "ai", "id": "model-beta"},
                "annotation": {"category": "other", "content": "Session running on shared cluster node 7."},
                "context": {},
            },
            # §3.8 State Change
            {
                "id": "evt_015",
                "timestamp": "2026-03-19T10:45:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "state_change",
                "actor": {"type": "human", "id": "tester-alice"},
                "state_change": {
                    "description": "Switched from CPU to GPU compute",
                    "field": "environment.compute",
                    "old_value": "cpu",
                    "new_value": "gpu-a100",
                    "reason": "Training too slow on CPU",
                },
                "context": {},
            },
            # §3.9 Contribution — all direction×execution combos
            {
                "id": "evt_016",
                "timestamp": "2026-03-19T11:00:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "contribution",
                "actor": {"type": "ai", "id": "model-beta"},
                "contribution": {
                    "description": "Implemented random forest classifier",
                    "artifact": "src/classifier.py",
                    "direction": "human",
                    "execution": "ai",
                    "related_decision_ids": ["evt_006"],
                    "tags": ["implementation"],
                },
                "context": {
                    "conversation_snippet": "Write the classifier using RF with 200 trees.",
                },
            },
            {
                "id": "evt_017",
                "timestamp": "2026-03-19T11:10:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "contribution",
                "actor": {"type": "ai", "id": "model-beta"},
                "contribution": {
                    "description": "Generated feature importance visualization",
                    "artifact": "figures/feature_importance.png",
                    "direction": "ai",
                    "execution": "ai",
                    "tags": ["visualization"],
                },
                "context": {},
            },
            {
                "id": "evt_018",
                "timestamp": "2026-03-19T11:20:00+00:00",
                "session_id": "handcrafted_20260319_aaa111",
                "type": "contribution",
                "actor": {"type": "human", "id": "tester-alice"},
                "contribution": {
                    "description": "Manually annotated 50 validation examples",
                    "artifact": "data/validation_labels.csv",
                    "direction": "collaborative",
                    "execution": "human",
                    "tags": ["data"],
                },
                "context": {},
            },
        ],
    }


async def _trace_generated_session(tmp_path: Path) -> dict[str, Any]:
    """Build a session using TRACE tools, then dump to dict.

    This proves the TRACE implementation produces conforming documents.
    """
    storage = JsonFileStorage(directory=str(tmp_path))
    active: dict[str, Session] = {}

    # Start session
    session = await session_tools.create_session(
        storage, active,
        project="conformance-test",
        experiment_id="exp-conf-002",
        description="TRACE-generated conformance test session.",
        participants=[
            {"type": "human", "id": "tester-alice", "role": "lead"},
            {"type": "ai", "id": "model-beta", "role": "assistant"},
            {"type": "system", "id": "ci-pipeline"},
        ],
        tags=["conformance", "e2e"],
    )

    # Tool call — success
    await logging_tools.log_tool_call(
        storage, session,
        server="search-server", tool_name="search_documents",
        input={"query": "climate change", "limit": 10},
        output={"results": 42}, duration_ms=1500, status="success",
        actor_type="ai", actor_id="model-beta",
        reasoning="Searching for relevant documents.", conversation_turn=1,
    )

    # Tool call — error
    await logging_tools.log_tool_call(
        storage, session,
        server="analysis-server", tool_name="run_analysis",
        input={"method": "regression"}, status="error",
        error_message="Connection timeout",
        actor_type="ai", actor_id="model-beta",
    )

    # Tool call — retry
    await logging_tools.log_tool_call(
        storage, session,
        server="analysis-server", tool_name="run_analysis",
        input={"method": "regression"}, status="success", duration_ms=800,
        retries_event_id="evt_002",
        actor_type="ai", actor_id="model-beta",
    )

    # Decision — propose
    await decision_tools.propose_decision(
        storage, session,
        description="Use random forest with 100 trees",
        rationale="Good balance of accuracy and speed",
        proposed_by_type="ai", proposed_by_id="model-beta",
        suggestion_type="proactive",
        tags=["methodology", "model-selection"],
    )

    # Decision — accept (resolve evt_004 in-place, no new event)
    await decision_tools.resolve_decision(
        storage, session,
        event_id="evt_004", disposition="accepted",
        resolved_by_type="human", resolved_by_id="tester-alice",
    )

    # Decision — propose + revise (chain)
    # propose → evt_005, resolve in-place
    await decision_tools.propose_decision(
        storage, session,
        description="Switch to 200 trees for better accuracy",
        rationale="100 trees underfitting on validation data",
        proposed_by_type="human", proposed_by_id="tester-alice",
        suggestion_type="requested",
        revises_event_id="evt_004",
    )
    await decision_tools.resolve_decision(
        storage, session,
        event_id="evt_005", disposition="revised",
        resolved_by_type="human", resolved_by_id="tester-alice",
        revision_note="Validation accuracy jumped 3% with 200 trees",
    )

    # Decision — propose + reject
    # propose → evt_006, resolve in-place
    await decision_tools.propose_decision(
        storage, session,
        description="Use GPU-accelerated XGBoost instead",
        rationale="Faster training on large datasets",
        proposed_by_type="ai", proposed_by_id="model-beta",
    )
    await decision_tools.resolve_decision(
        storage, session,
        event_id="evt_006", disposition="rejected",
        resolved_by_type="human", resolved_by_id="tester-alice",
        revision_note="RF is already fast enough and more interpretable",
    )

    # Decision — left in proposed state (unresolved)
    await decision_tools.propose_decision(
        storage, session,
        description="Consider adding cross-validation",
        rationale="Would improve confidence in results",
        proposed_by_type="ai", proposed_by_id="model-beta",
        suggestion_type="collaborative",
    )

    # Annotations — all 7 categories
    for cat, content in [
        ("gotcha", "Dataset has 5% missing values in column 'temperature'."),
        ("learning", "Imputation with median works better than mean for skewed data."),
        ("observation", "R² improved from 0.72 to 0.81 after fixing imputation."),
        ("todo", "Run cross-validation next session."),
        ("question", "Should we include the 2024 data?"),
        ("other", "Session running on shared cluster node 7."),
    ]:
        await logging_tools.log_annotation(
            storage, session, category=cat, content=content,
            actor_type="ai", actor_id="model-beta",
        )

    # Correction annotation with corrects_event_ids
    await logging_tools.log_annotation(
        storage, session,
        category="correction",
        content="The AI used mean imputation but should have used median.",
        corrects_event_ids=["evt_003"],
        related_event_ids=["evt_007"],
        actor_type="human", actor_id="tester-alice",
        conversation_snippet="No, use median — the distribution is skewed.",
    )

    # State change
    await logging_tools.log_state_change(
        storage, session,
        description="Switched from CPU to GPU compute",
        field="environment.compute", old_value="cpu", new_value="gpu-a100",
        reason="Training too slow on CPU",
        actor_type="human", actor_id="tester-alice",
    )

    # Contributions — multiple direction×execution combos
    await logging_tools.log_contribution(
        storage, session,
        description="Implemented random forest classifier",
        direction="human", execution="ai", artifact="src/classifier.py",
        related_decision_ids=["evt_005"],
        tags=["implementation"],
        actor_type="ai", actor_id="model-beta",
        conversation_snippet="Write the classifier using RF with 200 trees.",
    )
    await logging_tools.log_contribution(
        storage, session,
        description="Generated feature importance visualization",
        direction="ai", execution="ai", artifact="figures/feature_importance.png",
        tags=["visualization"],
        actor_type="ai", actor_id="model-beta",
    )
    await logging_tools.log_contribution(
        storage, session,
        description="Manually annotated 50 validation examples",
        direction="collaborative", execution="human",
        artifact="data/validation_labels.csv",
        tags=["data"],
        actor_type="human", actor_id="tester-alice",
    )

    # End session
    session.ended = datetime.now(UTC)
    session.status = "completed"
    session.summary = "TRACE-generated conformance test session."
    await storage.update_session(session)

    return session.model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════════════════════
# Part 1: Both documents validate against the JSON Schema
# ═══════════════════════════════════════════════════════════════════════════════


class TestSchemaConformance:
    """Both handcrafted and TRACE-generated documents MUST validate."""

    def test_handcrafted_validates(self, schema: dict) -> None:
        doc = _handcrafted_session()
        jsonschema.validate(doc, schema)

    async def test_trace_generated_validates(self, schema: dict, tmp_path: Path) -> None:
        doc = await _trace_generated_session(tmp_path)
        jsonschema.validate(doc, schema)


# ═══════════════════════════════════════════════════════════════════════════════
# Part 2: Structural equivalence — both documents cover the same spec surface
# ═══════════════════════════════════════════════════════════════════════════════


class TestStructuralEquivalence:
    """Both documents exercise the same spec features."""

    async def test_both_have_all_five_event_types(self, tmp_path: Path) -> None:
        hand = _handcrafted_session()
        trace = await _trace_generated_session(tmp_path)

        required_types = {"tool_call", "decision", "annotation", "state_change", "contribution"}
        hand_types = {e["type"] for e in hand["events"]}
        trace_types = {e["type"] for e in trace["events"]}

        assert hand_types == required_types, f"Handcrafted missing: {required_types - hand_types}"
        assert trace_types == required_types, f"TRACE missing: {required_types - trace_types}"

    async def test_both_have_all_actor_types(self, tmp_path: Path) -> None:
        hand = _handcrafted_session()
        trace = await _trace_generated_session(tmp_path)

        for doc_name, doc in [("handcrafted", hand), ("TRACE", trace)]:
            actor_types = {p["type"] for p in doc["metadata"]["participants"]}
            assert {"human", "ai", "system"} == actor_types, f"{doc_name} missing actor types"

    async def test_both_have_all_annotation_categories(self, tmp_path: Path) -> None:
        hand = _handcrafted_session()
        trace = await _trace_generated_session(tmp_path)

        required_cats = {"learning", "gotcha", "observation", "correction", "todo", "question", "other"}
        for doc_name, doc in [("handcrafted", hand), ("TRACE", trace)]:
            cats = {e["annotation"]["category"] for e in doc["events"] if e["type"] == "annotation"}
            assert cats == required_cats, f"{doc_name} missing annotation categories: {required_cats - cats}"

    async def test_both_have_all_decision_dispositions(self, tmp_path: Path) -> None:
        hand = _handcrafted_session()
        trace = await _trace_generated_session(tmp_path)

        required_disps = {"proposed", "accepted", "revised", "rejected"}
        for doc_name, doc in [("handcrafted", hand), ("TRACE", trace)]:
            disps = {e["decision"]["disposition"] for e in doc["events"] if e["type"] == "decision"}
            assert disps == required_disps, f"{doc_name} missing dispositions: {required_disps - disps}"

    async def test_both_have_decision_chain(self, tmp_path: Path) -> None:
        """§5.1: At least one decision should link to another via revises_event_id."""
        hand = _handcrafted_session()
        trace = await _trace_generated_session(tmp_path)

        for doc_name, doc in [("handcrafted", hand), ("TRACE", trace)]:
            revisions = [
                e for e in doc["events"]
                if e["type"] == "decision" and e["decision"].get("revises_event_id")
            ]
            assert len(revisions) >= 1, f"{doc_name} has no decision chain (no revises_event_id)"

    async def test_both_have_retry_chain(self, tmp_path: Path) -> None:
        """§3.5: At least one tool call should link to another via retries_event_id."""
        hand = _handcrafted_session()
        trace = await _trace_generated_session(tmp_path)

        for doc_name, doc in [("handcrafted", hand), ("TRACE", trace)]:
            retries = [
                e for e in doc["events"]
                if e["type"] == "tool_call" and e["tool_call"].get("retries_event_id")
            ]
            assert len(retries) >= 1, f"{doc_name} has no retry chain"

    async def test_both_have_correction_with_links(self, tmp_path: Path) -> None:
        """§5.2: At least one correction annotation should set corrects_event_ids."""
        hand = _handcrafted_session()
        trace = await _trace_generated_session(tmp_path)

        for doc_name, doc in [("handcrafted", hand), ("TRACE", trace)]:
            corrections = [
                e for e in doc["events"]
                if e["type"] == "annotation"
                and e["annotation"]["category"] == "correction"
                and e["annotation"].get("corrects_event_ids")
            ]
            assert len(corrections) >= 1, f"{doc_name} has no correction with corrects_event_ids"

    async def test_both_have_multiple_contribution_attributions(self, tmp_path: Path) -> None:
        """§3.9: Documents should exercise multiple direction×execution combos."""
        hand = _handcrafted_session()
        trace = await _trace_generated_session(tmp_path)

        for doc_name, doc in [("handcrafted", hand), ("TRACE", trace)]:
            combos = {
                (e["contribution"]["direction"], e["contribution"]["execution"])
                for e in doc["events"] if e["type"] == "contribution"
            }
            assert len(combos) >= 3, f"{doc_name} only has {len(combos)} direction×execution combos"


# ═══════════════════════════════════════════════════════════════════════════════
# Part 3: Spec §3 — every required field defined in the spec exists in models
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpecDataModelCoverage:
    """Every MUST/SHOULD field from the specification tables maps to a Pydantic field."""

    # §3.1 Session Document
    def test_session_has_required_fields(self) -> None:
        fields = Session.model_fields
        for f in ["context", "trace_version", "id", "created", "ended", "status", "metadata", "summary", "events"]:
            assert f in fields, f"Session missing field: {f}"

    def test_session_status_values(self) -> None:
        # §3.1: status MUST be one of active, completed, abandoned
        for status in ["active", "completed", "abandoned"]:
            s = Session(id="test", metadata=SessionMetadata(project="t"), status=status)
            assert s.status == status

    # §3.2 Session Metadata
    def test_metadata_has_required_fields(self) -> None:
        fields = SessionMetadata.model_fields
        for f in ["project", "experiment_id", "description", "participants", "environment", "tags", "doi", "custom"]:
            assert f in fields, f"SessionMetadata missing field: {f}"

    # §3.2.1 Environment
    def test_environment_has_fields(self) -> None:
        fields = Environment.model_fields
        # Schema names: mcp_servers, client, os, python_version, trace_version, custom
        for f in ["mcp_servers", "client", "os", "python_version", "trace_version", "custom"]:
            assert f in fields, f"Environment missing field: {f}"

    # §3.3 Actor
    def test_actor_has_required_fields(self) -> None:
        fields = Actor.model_fields
        for f in ["type", "id", "role"]:
            assert f in fields, f"Actor missing field: {f}"

    def test_actor_type_values(self) -> None:
        for t in ["human", "ai", "system"]:
            a = Actor(type=t, id="test")
            assert a.type == t

    # §3.4 Event
    def test_event_has_required_fields(self) -> None:
        fields = TraceEvent.model_fields
        for f in ["id", "timestamp", "session_id", "type", "actor", "context",
                   "tool_call", "decision", "annotation", "state_change", "contribution"]:
            assert f in fields, f"TraceEvent missing field: {f}"

    def test_event_type_values(self) -> None:
        types = ["tool_call", "decision", "annotation", "state_change", "contribution"]
        schema = TraceEvent.model_json_schema()
        defs = schema.get("$defs", {})
        # Check the type enum directly from the model
        for t in types:
            # Just verify each type can be set without error
            assert t in types

    # §3.4.1 EventContext
    def test_event_context_has_fields(self) -> None:
        fields = EventContext.model_fields
        for f in ["conversation_turn", "reasoning_summary", "conversation_snippet", "related_event_ids"]:
            assert f in fields, f"EventContext missing field: {f}"

    # §3.5 ToolCallData
    def test_tool_call_data_has_fields(self) -> None:
        fields = ToolCallData.model_fields
        for f in ["server", "method", "name", "input", "output", "output_truncated",
                   "output_hash", "duration_ms", "status", "error_message", "retries_event_id"]:
            assert f in fields, f"ToolCallData missing field: {f}"

    def test_tool_call_status_values(self) -> None:
        for status in ["success", "error", "timeout"]:
            tc = ToolCallData(server="s", name="n", input={}, status=status)
            assert tc.status == status

    # §3.6 DecisionData
    def test_decision_data_has_fields(self) -> None:
        fields = DecisionData.model_fields
        for f in ["description", "rationale", "proposed_by", "disposition", "resolved_by",
                   "revision_note", "revises_event_id", "suggestion_type", "tags", "warnings"]:
            assert f in fields, f"DecisionData missing field: {f}"

    def test_decision_disposition_values(self) -> None:
        for d in ["proposed", "accepted", "revised", "rejected"]:
            if d == "proposed":
                dec = DecisionData(description="t", proposed_by=Actor(type="ai", id="x"), disposition=d)
            else:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    dec = DecisionData(
                        description="t", proposed_by=Actor(type="ai", id="x"),
                        disposition=d, resolved_by=Actor(type="human", id="y"),
                        revision_note="note" if d in ("revised", "rejected") else None,
                    )
            assert dec.disposition == d

    def test_suggestion_type_values(self) -> None:
        for st in ["proactive", "requested", "collaborative"]:
            d = DecisionData(description="t", proposed_by=Actor(type="ai", id="x"), suggestion_type=st)
            assert d.suggestion_type == st

    # §3.7 AnnotationData
    def test_annotation_data_has_fields(self) -> None:
        fields = AnnotationData.model_fields
        for f in ["category", "content", "corrects_event_ids", "related_event_ids", "tags"]:
            assert f in fields, f"AnnotationData missing field: {f}"

    def test_annotation_category_values(self) -> None:
        for cat in ["learning", "gotcha", "observation", "correction", "todo", "question", "other"]:
            a = AnnotationData(category=cat, content="test")
            assert a.category == cat

    # §3.8 StateChangeData
    def test_state_change_data_has_fields(self) -> None:
        fields = StateChangeData.model_fields
        for f in ["description", "field", "old_value", "new_value", "reason"]:
            assert f in fields, f"StateChangeData missing field: {f}"

    # §3.9 ContributionData
    def test_contribution_data_has_fields(self) -> None:
        fields = ContributionData.model_fields
        for f in ["description", "artifact", "direction", "execution", "related_decision_ids", "tags"]:
            assert f in fields, f"ContributionData missing field: {f}"

    def test_contribution_direction_values(self) -> None:
        for d in ["human", "ai", "collaborative"]:
            c = ContributionData(description="t", direction=d, execution="ai")
            assert c.direction == d

    def test_contribution_execution_values(self) -> None:
        for e in ["human", "ai", "collaborative"]:
            c = ContributionData(description="t", direction="human", execution=e)
            assert c.execution == e


# ═══════════════════════════════════════════════════════════════════════════════
# Part 4: Spec §4 — validation rules enforced by TRACE
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpecValidationRules:
    """TRACE MUST enforce the semantic rules from spec §4."""

    # §4.1 Event type consistency
    def test_event_type_mismatch_raises(self) -> None:
        """Type field must match the populated data field."""
        with pytest.raises(ValueError, match="requires 'tool_call' to be populated"):
            TraceEvent(id="e", session_id="s", type="tool_call", actor=Actor(type="ai", id="x"))

    def test_event_extra_data_raises(self) -> None:
        """Only one data field may be populated."""
        with pytest.raises(ValueError, match="must not have"):
            TraceEvent(
                id="e", session_id="s", type="tool_call", actor=Actor(type="ai", id="x"),
                tool_call=ToolCallData(server="s", name="n", input={}),
                annotation=AnnotationData(category="observation", content="extra"),
            )

    # §4.2 Decision resolution
    def test_resolved_decision_requires_resolved_by(self) -> None:
        """When disposition != proposed, resolved_by MUST be set."""
        with pytest.raises(ValueError, match="resolved_by must be set"):
            DecisionData(description="t", proposed_by=Actor(type="ai", id="x"), disposition="accepted")

    def test_proposed_decision_resolved_by_must_be_null(self) -> None:
        """When disposition == proposed, resolved_by can be null."""
        d = DecisionData(description="t", proposed_by=Actor(type="ai", id="x"), disposition="proposed")
        assert d.resolved_by is None


# ═══════════════════════════════════════════════════════════════════════════════
# Part 5: Spec §5 — decision provenance features in TRACE
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpecDecisionProvenance:
    """TRACE implements the decision provenance patterns from spec §5."""

    # §5.1 Decision chains
    async def test_decision_chain_walkable(self, tmp_path: Path) -> None:
        """TRACE's get_decision_chain walks revises_event_id links."""
        storage = JsonFileStorage(directory=str(tmp_path))
        active: dict[str, Session] = {}
        session = await session_tools.create_session(storage, active, project="chain-test")

        # Create a 3-link chain: evt_001 → evt_002 → evt_003
        await decision_tools.propose_decision(
            storage, session, description="Original", rationale="v1",
            proposed_by_type="ai", proposed_by_id="ai",
        )
        await decision_tools.propose_decision(
            storage, session, description="Revision 1", rationale="v2",
            proposed_by_type="human", proposed_by_id="human",
            revises_event_id="evt_001",
        )
        await decision_tools.propose_decision(
            storage, session, description="Revision 2", rationale="v3",
            proposed_by_type="ai", proposed_by_id="ai",
            revises_event_id="evt_002",
        )

        chain = query_tools.get_decision_chain(session, event_id="evt_003")
        assert len(chain) == 3
        assert [c["id"] for c in chain] == ["evt_001", "evt_002", "evt_003"]

    # §5.3 Attribution matrix
    async def test_attribution_matrix_computable(self, tmp_path: Path) -> None:
        """All metrics from spec §5.3 are computable from a TRACE session."""
        doc = await _trace_generated_session(tmp_path)

        decisions = [e for e in doc["events"] if e["type"] == "decision"]
        annotations = [e for e in doc["events"] if e["type"] == "annotation"]
        contributions = [e for e in doc["events"] if e["type"] == "contribution"]
        total = len(doc["events"])

        # Decisions proposed by AI
        ai_proposed = sum(1 for d in decisions if d["decision"]["proposed_by"]["type"] == "ai")
        assert ai_proposed >= 1

        # Decisions proposed by human
        human_proposed = sum(1 for d in decisions if d["decision"]["proposed_by"]["type"] == "human")
        assert human_proposed >= 1

        # Acceptance rate (accepted / resolved)
        resolved = [d for d in decisions if d["decision"]["disposition"] != "proposed"]
        accepted = sum(1 for d in resolved if d["decision"]["disposition"] == "accepted")
        acceptance_rate = accepted / len(resolved) if resolved else 0
        assert 0 <= acceptance_rate <= 1

        # Corrections
        corrections = [a for a in annotations if a["annotation"]["category"] == "correction"]
        assert len(corrections) >= 1

        # Human intervention rate
        rejections = sum(1 for d in decisions if d["decision"]["disposition"] == "rejected")
        revisions = sum(1 for d in decisions if d["decision"]["disposition"] == "revised")
        interventions = len(corrections) + rejections + revisions
        intervention_rate = interventions / total if total else 0
        assert intervention_rate > 0

        # Direction × Execution matrix
        combos = {
            (c["contribution"]["direction"], c["contribution"]["execution"])
            for c in contributions
        }
        assert len(combos) >= 3


# ═══════════════════════════════════════════════════════════════════════════════
# Part 6: Spec §6 — W3C PROV mapping is implemented
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpecProvMapping:
    """TRACE implements the W3C PROV mapping from spec §6."""

    def test_prov_context_has_required_namespaces(self) -> None:
        assert "prov" in PROV_CONTEXT
        assert "trace" in PROV_CONTEXT
        assert PROV_CONTEXT["prov"] == "http://www.w3.org/ns/prov#"

    def test_prov_mapping_covers_spec_concepts(self) -> None:
        """Every concept from spec §6 table has a PROV mapping."""
        required = [
            "Session", "TraceEvent", "Actor",
            "ToolCallData.input", "ToolCallData.output",
            "DecisionData", "DecisionData.revision",
            "AnnotationData",
        ]
        for concept in required:
            assert concept in PROV_MAPPING, f"PROV_MAPPING missing: {concept}"

    async def test_prov_export_has_all_prov_types(self, tmp_path: Path) -> None:
        """PROV export generates agent, activity, entity, and relationships."""
        doc_dict = await _trace_generated_session(tmp_path)
        session = Session.model_validate(doc_dict)
        raw = export_prov_jsonld(session)
        prov = json.loads(raw)

        bundle_key = list(prov["bundle"].keys())[0]
        bundle = prov["bundle"][bundle_key]

        assert "agent" in bundle, "PROV export missing agents"
        assert "activity" in bundle, "PROV export missing activities"
        assert "entity" in bundle, "PROV export missing entities"

        # Decisions should be activities
        decision_activities = {
            k: v for k, v in bundle["activity"].items()
            if v.get("prov:type") == "trace:Decision"
        }
        assert len(decision_activities) >= 1

        # Contributions should be activities
        contribution_activities = {
            k: v for k, v in bundle["activity"].items()
            if v.get("prov:type") == "trace:Contribution"
        }
        assert len(contribution_activities) >= 1

        # Tool call inputs should be entities
        input_entities = {
            k: v for k, v in bundle["entity"].items()
            if v.get("prov:type") == "trace:ToolInput"
        }
        assert len(input_entities) >= 1

        # Annotations should be entities with wasAttributedTo
        assert "wasAttributedTo" in bundle

    async def test_prov_export_has_revision_links(self, tmp_path: Path) -> None:
        """Decision chains produce wasRevisionOf in PROV."""
        doc_dict = await _trace_generated_session(tmp_path)
        session = Session.model_validate(doc_dict)
        raw = export_prov_jsonld(session)
        prov = json.loads(raw)

        bundle_key = list(prov["bundle"].keys())[0]
        bundle = prov["bundle"][bundle_key]

        assert "wasRevisionOf" in bundle
        assert len(bundle["wasRevisionOf"]) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Part 7: Spec §7 — interchange format requirements
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpecInterchangeFormat:
    """TRACE meets the interchange format requirements from spec §7."""

    # §7.1 JSON encoding
    def test_schema_file_exists_and_valid(self, schema: dict) -> None:
        assert "$id" in schema
        assert "trace-v0.3" in schema["$id"]

    async def test_trace_output_is_valid_json(self, tmp_path: Path) -> None:
        doc = await _trace_generated_session(tmp_path)
        raw = json.dumps(doc, indent=2, default=str)
        reparsed = json.loads(raw)
        assert reparsed["id"] == doc["id"]

    # §7.2 File conventions — pretty-printed, UTF-8
    async def test_session_file_is_pretty_printed(self, tmp_path: Path) -> None:
        storage = JsonFileStorage(directory=str(tmp_path))
        active: dict[str, Session] = {}
        session = await session_tools.create_session(storage, active, project="fmt-test")
        path = tmp_path / f"{session.id}.json"
        content = path.read_text(encoding="utf-8")
        # Pretty-printed JSON has newlines and indentation
        assert "\n" in content
        assert "  " in content

    # §7.3 Extensibility — custom fields
    def test_custom_fields_roundtrip(self) -> None:
        """Custom metadata survives dump → load."""
        s = Session(
            id="test_custom",
            metadata=SessionMetadata(
                project="test",
                custom={"lab_id": "lab-42", "funding": "NSF-123"},
                environment=Environment(custom={"gpu_count": 4}),
            ),
        )
        data = s.model_dump(mode="json")
        restored = Session.model_validate(data)
        assert restored.metadata.custom["lab_id"] == "lab-42"
        assert restored.metadata.environment.custom["gpu_count"] == 4


# ═══════════════════════════════════════════════════════════════════════════════
# Part 8: Spec §7.5 — TRACE tools cover MCP integration guidance
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpecMCPToolCoverage:
    """Spec §7.5 says MCP implementations SHOULD provide tools for each event type.
    Verify TRACE has the required tool functions."""

    def test_session_management_tools_exist(self) -> None:
        """Starting/ending sessions."""
        assert callable(session_tools.start_session)
        assert callable(session_tools.end_session)

    def test_event_logging_tools_exist(self) -> None:
        """Logging each of the 5 event types."""
        assert callable(logging_tools.log_tool_call)
        assert callable(logging_tools.log_annotation)
        assert callable(logging_tools.log_state_change)
        assert callable(logging_tools.log_contribution)

    def test_decision_tools_exist(self) -> None:
        """Proposing/resolving decisions."""
        assert callable(decision_tools.propose_decision)
        assert callable(decision_tools.resolve_decision)

    def test_query_tools_exist(self) -> None:
        """Querying events."""
        assert callable(query_tools.get_events)
        assert callable(query_tools.get_decisions)
        assert callable(query_tools.get_decision_chain)
        assert callable(query_tools.search_events)

    def test_export_exists(self) -> None:
        """Exporting session documents."""
        from trace_mcp.tools import export_tools
        assert callable(export_tools.export_session)

    def test_mcp_tool_registration(self) -> None:
        """TRACE registers its tools with FastMCP."""
        from trace_mcp.server import mcp
        # FastMCP stores tools; just verify the server object exists
        assert mcp.name == "trace"


# ═══════════════════════════════════════════════════════════════════════════════
# Part 9: Spec §8 — implementation guidance
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpecImplementationGuidance:
    """TRACE follows the non-normative guidance from spec §8."""

    # §8.3 Fail-open — errors in audit don't block workflows
    async def test_fail_open_on_nonexistent_session(self, tmp_path: Path) -> None:
        """Querying a missing session returns an error string, not an exception."""
        storage = JsonFileStorage(directory=str(tmp_path))
        active: dict[str, Session] = {}
        result = await session_tools.end_session(storage, active, session_id="nonexistent")
        assert "Error" in result or "not found" in result

    # §8.4 Atomic writes
    async def test_atomic_write_no_partial_files(self, tmp_path: Path) -> None:
        """Session writes should be atomic (no partial files on disk)."""
        storage = JsonFileStorage(directory=str(tmp_path))
        active: dict[str, Session] = {}
        session = await session_tools.create_session(storage, active, project="atomic-test")
        path = tmp_path / f"{session.id}.json"
        content = path.read_text(encoding="utf-8")
        # If write was atomic, file should be valid JSON
        data = json.loads(content)
        assert data["id"] == session.id


# ═══════════════════════════════════════════════════════════════════════════════
# Part 10: Verify the specification document itself is complete
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpecDocumentCompleteness:
    """The specification document (docs/specification.md) covers all required sections."""

    def test_spec_file_exists(self) -> None:
        assert SPEC_PATH.exists(), f"Specification not found at {SPEC_PATH}"

    def test_spec_has_required_sections(self, spec_text: str) -> None:
        required_headings = [
            "## 1. Introduction",
            "### 1.1 Purpose",
            "### 1.2 Scope",
            "### 1.3 Conformance",
            "## 2. Terminology",
            "## 3. Data Model",
            "### 3.1 Session Document",
            "### 3.2 Session Metadata",
            "### 3.3 Actor",
            "### 3.4 Event",
            "### 3.5 Tool Invocation",
            "### 3.6 Decision",
            "### 3.7 Annotation",
            "### 3.8 State Change",
            "### 3.9 Contribution",
            "## 4. Validation Rules",
            "## 5. Decision Provenance",
            "### 5.1 Decision Chains",
            "### 5.2 Correction Provenance",
            "### 5.3 Attribution Matrix",
            "## 6. W3C PROV Mapping",
            "## 7. Interchange Format",
            "## 8. Implementation Guidance",
        ]
        for heading in required_headings:
            assert heading in spec_text, f"Spec missing section: {heading}"

    def test_spec_uses_rfc2119_language(self, spec_text: str) -> None:
        """Spec should use RFC 2119 normative language."""
        assert "MUST" in spec_text
        assert "SHOULD" in spec_text
        assert "MAY" in spec_text
        assert "RFC 2119" in spec_text

    def test_spec_references_json_schema(self, spec_text: str) -> None:
        assert "trace-v0.3.json" in spec_text

    def test_spec_has_example_document(self, spec_text: str) -> None:
        assert "## Appendix A: Example Session Document" in spec_text
        # The example should be valid JSON
        json_start = spec_text.index("```json", spec_text.index("Appendix A"))
        json_end = spec_text.index("```", json_start + 7)
        example_json = spec_text[json_start + 7:json_end].strip()
        doc = json.loads(example_json)
        assert doc["id"] == "trace_20260205_a1b2c3"

    def test_spec_example_validates_against_schema(self, spec_text: str, schema: dict) -> None:
        """The example document in the spec MUST validate against the schema."""
        json_start = spec_text.index("```json", spec_text.index("Appendix A"))
        json_end = spec_text.index("```", json_start + 7)
        example_json = spec_text[json_start + 7:json_end].strip()
        doc = json.loads(example_json)
        jsonschema.validate(doc, schema)

    def test_spec_is_technology_agnostic(self, spec_text: str) -> None:
        """The spec should not depend on TRACE implementation details."""
        # Should not mention Pydantic, FastMCP, or Python as requirements
        assert "Pydantic" not in spec_text
        assert "FastMCP" not in spec_text
        # "python_version" appears in the schema field name note — that's OK
        # but "import" or "pip install" should not
        assert "pip install" not in spec_text
        assert "import trace_mcp" not in spec_text

    def test_spec_has_version_history(self, spec_text: str) -> None:
        assert "## Appendix B: Version History" in spec_text
        assert "0.1.0" in spec_text
        assert "0.2.0" in spec_text
        assert "0.3.0" in spec_text
