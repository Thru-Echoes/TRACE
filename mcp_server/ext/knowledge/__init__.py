"""
TRACE Knowledge Extension

Knowledge management for tracking decisions, learnings, gotchas, and ideas.
"""

from datetime import datetime
from typing import Any


class KnowledgeManager:
    """Manages knowledge entries (decisions, learnings, gotchas, ideas)."""

    def __init__(self, trace: dict[str, Any]):
        """Initialize with trace data."""
        self.trace = trace
        self._ensure_structure()

    def _ensure_structure(self) -> None:
        """Ensure knowledge extension structure exists."""
        if "_extensions" not in self.trace:
            self.trace["_extensions"] = {}
        if "knowledge" not in self.trace["_extensions"]:
            self.trace["_extensions"]["knowledge"] = {
                "decisions": [],
                "learnings": [],
                "gotchas": [],
                "ideas": [],
            }

    def _get_knowledge(self) -> dict[str, list]:
        """Get knowledge extension data."""
        return self.trace["_extensions"]["knowledge"]

    def _generate_id(self, prefix: str, items: list) -> str:
        """Generate unique ID."""
        existing_ids = {item.get("id", "") for item in items}
        counter = 1
        while True:
            new_id = f"{prefix}{counter:03d}"
            if new_id not in existing_ids:
                return new_id
            counter += 1

    def add_decision(
        self,
        decision: str,
        rationale: str,
        session_id: str | None = None,
        alternatives_considered: str | None = None,
        proposed_by: str = "human",
        related_suggestion_id: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add a decision entry."""
        knowledge = self._get_knowledge()
        decisions = knowledge["decisions"]

        entry: dict[str, Any] = {
            "id": self._generate_id("D", decisions),
            "timestamp": datetime.now().isoformat(),
            "decision": decision,
            "rationale": rationale,
            "proposed_by": proposed_by,
        }

        if session_id:
            entry["session_id"] = session_id
        if alternatives_considered:
            entry["alternatives_considered"] = alternatives_considered
        if related_suggestion_id:
            entry["related_suggestion_id"] = related_suggestion_id
        if tags:
            entry["tags"] = tags

        decisions.append(entry)
        return entry

    def add_learning(
        self,
        learning: str,
        session_id: str | None = None,
        evidence: str | None = None,
        confidence: str = "medium",
        discovered_by: str = "collaborative",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add a learning entry."""
        knowledge = self._get_knowledge()
        learnings = knowledge["learnings"]

        entry: dict[str, Any] = {
            "id": self._generate_id("L", learnings),
            "timestamp": datetime.now().isoformat(),
            "learning": learning,
            "confidence": confidence,
            "discovered_by": discovered_by,
        }

        if session_id:
            entry["session_id"] = session_id
        if evidence:
            entry["evidence"] = evidence
        if tags:
            entry["tags"] = tags

        learnings.append(entry)
        return entry

    def add_gotcha(
        self,
        problem: str,
        solution: str,
        session_id: str | None = None,
        severity: str = "medium",
        discovered_by: str = "collaborative",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add a gotcha entry."""
        knowledge = self._get_knowledge()
        gotchas = knowledge["gotchas"]

        entry: dict[str, Any] = {
            "id": self._generate_id("G", gotchas),
            "timestamp": datetime.now().isoformat(),
            "problem": problem,
            "solution": solution,
            "severity": severity,
            "discovered_by": discovered_by,
        }

        if session_id:
            entry["session_id"] = session_id
        if tags:
            entry["tags"] = tags

        gotchas.append(entry)
        return entry

    def add_idea(
        self,
        idea: str,
        source: str,
        session_id: str | None = None,
        idea_type: str = "feature",
        triggered_by: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add an idea entry."""
        knowledge = self._get_knowledge()
        ideas = knowledge["ideas"]

        entry: dict[str, Any] = {
            "id": self._generate_id("IDEA", ideas),
            "timestamp": datetime.now().isoformat(),
            "idea": idea,
            "source": source,
            "idea_type": idea_type,
            "status": "proposed",
        }

        if session_id:
            entry["session_id"] = session_id
        if triggered_by:
            entry["triggered_by"] = triggered_by
        if tags:
            entry["tags"] = tags

        ideas.append(entry)
        return entry

    def evaluate_idea(
        self,
        idea_id: str,
        adopted: bool,
        evaluation_notes: str | None = None,
        rejection_reason: str | None = None,
        modification_description: str | None = None,
    ) -> dict[str, Any] | None:
        """Evaluate an existing idea."""
        knowledge = self._get_knowledge()
        ideas = knowledge["ideas"]

        for idea in ideas:
            if idea.get("id") == idea_id:
                idea["status"] = "adopted" if adopted else "rejected"
                idea["evaluation"] = {
                    "adopted": adopted,
                    "evaluated_at": datetime.now().isoformat(),
                }
                if evaluation_notes:
                    idea["evaluation"]["evaluation_notes"] = evaluation_notes
                if rejection_reason:
                    idea["evaluation"]["rejection_reason"] = rejection_reason
                if modification_description:
                    idea["evaluation"]["modification_description"] = modification_description
                return idea

        return None

    def query(
        self,
        query: str,
        categories: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Query knowledge entries."""
        knowledge = self._get_knowledge()
        results = []

        search_categories = categories or ["decisions", "learnings", "gotchas", "ideas"]
        query_lower = query.lower()

        for category in search_categories:
            if category not in knowledge:
                continue

            for entry in knowledge[category]:
                # Check text match
                searchable_text = " ".join(str(v) for v in entry.values() if isinstance(v, str)).lower()

                if query_lower in searchable_text:
                    # Check tag filter
                    if tags:
                        entry_tags = entry.get("tags", [])
                        if not any(t in entry_tags for t in tags):
                            continue

                    results.append({"category": category, **entry})

                    if len(results) >= limit:
                        return results

        return results

    def get_metrics(self) -> dict[str, Any]:
        """Get knowledge metrics."""
        knowledge = self._get_knowledge()

        return {
            "total_entries": {
                "decisions": len(knowledge.get("decisions", [])),
                "learnings": len(knowledge.get("learnings", [])),
                "gotchas": len(knowledge.get("gotchas", [])),
                "ideas": len(knowledge.get("ideas", [])),
            },
            "ideas_adopted": sum(1 for idea in knowledge.get("ideas", []) if idea.get("status") == "adopted"),
            "ideas_rejected": sum(1 for idea in knowledge.get("ideas", []) if idea.get("status") == "rejected"),
        }


# Convenience functions
def add_decision(trace: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Add a decision entry."""
    return KnowledgeManager(trace).add_decision(**kwargs)


def add_learning(trace: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Add a learning entry."""
    return KnowledgeManager(trace).add_learning(**kwargs)


def add_gotcha(trace: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Add a gotcha entry."""
    return KnowledgeManager(trace).add_gotcha(**kwargs)


def add_idea(trace: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Add an idea entry."""
    return KnowledgeManager(trace).add_idea(**kwargs)


__all__ = [
    "KnowledgeManager",
    "add_decision",
    "add_learning",
    "add_gotcha",
    "add_idea",
]
