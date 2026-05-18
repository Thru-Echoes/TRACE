"""L9.1 — the waggle-session regression gate (FINAL plan's mandated gate).

The FINAL fix plan declared itself "invalid by construction" without this
test, and Round-1/2/3 found it had never been written (finding G3). It
loads the canonical waggle audit subject under the v0.4.1 schema and pins
the AttributionAudit counts the plan specified, independently re-derived
in Round 3 as exact (28 / 15 / 1 / 2).

This also guards P1↔P3: the waggle session's actor-type union is
{ai, human} (≥2 types) → multi-actor → the P1 / evt_016 guard passes →
attribution_warning_count stays 2 (evt_001, evt_025).
"""

from __future__ import annotations

from pathlib import Path

from trace_mcp.schema import Session
from trace_mcp.tools.session_tools import _build_attribution_audit

_WAGGLE_JSON = (
    Path(__file__).resolve().parents[1]
    / "audit_2026-05-13_waggle_session"
    / "trace_session_trace_20260513_446733.json"
)


def _load() -> Session:
    return Session.model_validate_json(_WAGGLE_JSON.read_text())


def test_waggle_session_loads_under_v041_schema() -> None:
    session = _load()
    assert session.trace_version  # loads cleanly (backwards-compat)
    assert len(session.events) == 28


def test_waggle_attribution_audit_counts() -> None:
    """The exact L9.1-mandated counts (Round-3 re-derived)."""
    audit = _build_attribution_audit(_load())
    assert audit.missing_snippet_contribution_count == 15
    assert audit.missing_snippet_correction_count == 1
    # Multi-actor session ({ai, human}) → P1 guard passes → both flagged.
    assert audit.attribution_warning_count == 2
    assert "evt_001" in audit.attribution_warning_ids
    assert "evt_025" in audit.attribution_warning_ids
