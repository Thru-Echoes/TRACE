"""Tests for the resolution time and resolution quote on decisions.

A resolved decision records when it was resolved (``resolved_at``, stamped by
the recorder) and the person's words that resolved it (``resolution_snippet``,
quoted by the resolving session).
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trace_mcp.schema import Actor, DecisionData


def test_a_proposed_decision_cannot_carry_a_resolution_time() -> None:
    with pytest.raises(ValidationError, match="resolved_at"):
        DecisionData(description="d", proposed_by=Actor(type="ai", id="a"), resolved_at=datetime.now(UTC))


def test_a_proposed_decision_cannot_carry_a_resolution_quote() -> None:
    with pytest.raises(ValidationError, match="resolution_snippet"):
        DecisionData(description="d", proposed_by=Actor(type="ai", id="a"), resolution_snippet="yes, do that")


def test_a_record_resolved_before_the_fields_existed_still_loads() -> None:
    decision = DecisionData.model_validate(
        {
            "description": "d",
            "proposed_by": {"type": "ai", "id": "a"},
            "disposition": "accepted",
            "resolved_by": {"type": "human", "id": "alice"},
        }
    )
    assert decision.resolved_at is None
    assert decision.resolution_snippet is None
