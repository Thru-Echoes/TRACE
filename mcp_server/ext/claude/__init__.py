"""
TRACE Claude Extension

Claude-specific features for enhanced AI collaboration:
- Knowledge check with NLP-based duplicate detection
- Automatic checkpoints
- Context refresh
"""

from datetime import datetime
from typing import Any


def calculate_text_similarity(text1: str, text2: str) -> float:
    """
    Calculate simple text similarity using word overlap.

    This is a lightweight alternative to heavy NLP libraries.
    """
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())

    if not words1 or not words2:
        return 0.0

    intersection = words1 & words2
    union = words1 | words2

    return len(intersection) / len(union) if union else 0.0


class ClaudeExtension:
    """Claude-specific extension functionality."""

    def __init__(self, trace: dict[str, Any]):
        """Initialize with trace data."""
        self.trace = trace
        self._ensure_structure()

    def _ensure_structure(self) -> None:
        """Ensure claude extension structure exists."""
        if "_extensions" not in self.trace:
            self.trace["_extensions"] = {}
        if "claude" not in self.trace["_extensions"]:
            self.trace["_extensions"]["claude"] = {
                "checkpoints": [],
                "knowledge_checks": [],
            }

    def _get_claude_data(self) -> dict[str, list]:
        """Get claude extension data."""
        return self.trace["_extensions"]["claude"]

    def knowledge_check(
        self,
        context: str,
        event_type: str | None = None,
        check_duplicates: bool = True,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Check if context should be logged and detect duplicates.

        Args:
            context: The context/content being checked
            event_type: Optional hint about event type
            check_duplicates: Whether to check for duplicates
            session_id: Current session ID

        Returns:
            Recommendation with similar entries and suggested fields
        """
        # Detect event types from context
        recommended_types = self._detect_event_types(context, event_type)

        # Find similar entries
        similar_entries = []
        if check_duplicates:
            similar_entries = self._find_similar_entries(context, recommended_types)

        # Generate suggested fields
        suggested_fields = self._generate_suggested_fields(context, recommended_types)

        # Determine if should log
        should_log = len(similar_entries) == 0 or all(s.get("similarity", 0) < 0.8 for s in similar_entries)

        result = {
            "should_log": should_log,
            "recommended_types": recommended_types,
            "confidence": "high" if len(recommended_types) == 1 else "medium",
            "reasoning": self._generate_reasoning(context, recommended_types, similar_entries),
            "similar_entries": similar_entries[:3],  # Top 3
            "suggested_fields": suggested_fields,
        }

        # Log the knowledge check
        claude_data = self._get_claude_data()
        check_entry = {
            "id": f"KC{len(claude_data['knowledge_checks']) + 1:03d}",
            "timestamp": datetime.now().isoformat(),
            "context": context[:200],  # Truncate for storage
            "event_type_hint": event_type,
            "result": {
                "should_log": result["should_log"],
                "recommended_types": result["recommended_types"],
                "confidence": result["confidence"],
            },
        }
        if session_id:
            check_entry["session_id"] = session_id
        claude_data["knowledge_checks"].append(check_entry)

        return result

    def _detect_event_types(self, context: str, hint: str | None = None) -> list[str]:
        """Detect likely event types from context."""
        context_lower = context.lower()
        detected = []

        # Gotcha indicators
        gotcha_keywords = [
            "unexpected",
            "gotcha",
            "pitfall",
            "careful",
            "watch out",
            "silently",
            "doesn't work",
            "fails",
            "bug",
            "workaround",
        ]
        if any(kw in context_lower for kw in gotcha_keywords):
            detected.append("gotcha")

        # Decision indicators
        decision_keywords = [
            "decided",
            "chose",
            "selected",
            "opted",
            "prefer",
            "instead of",
            "rather than",
            "trade-off",
        ]
        if any(kw in context_lower for kw in decision_keywords):
            detected.append("decision")

        # Learning indicators
        learning_keywords = [
            "learned",
            "discovered",
            "found out",
            "realized",
            "understand",
            "turns out",
            "apparently",
        ]
        if any(kw in context_lower for kw in learning_keywords):
            detected.append("learning")

        # Idea indicators
        idea_keywords = [
            "could",
            "might",
            "should",
            "idea",
            "suggestion",
            "improve",
            "optimize",
            "refactor",
            "feature",
        ]
        if any(kw in context_lower for kw in idea_keywords):
            detected.append("idea")

        # Use hint if provided and nothing detected
        if not detected and hint:
            detected.append(hint)

        return detected or ["learning"]  # Default to learning

    def _find_similar_entries(
        self,
        context: str,
        types: list[str],
        threshold: float = 0.3,
    ) -> list[dict[str, Any]]:
        """Find similar existing entries."""
        similar = []

        # Check knowledge extension entries
        knowledge = self.trace.get("_extensions", {}).get("knowledge", {})

        type_to_collection = {
            "decision": "decisions",
            "learning": "learnings",
            "gotcha": "gotchas",
            "idea": "ideas",
        }

        for event_type in types:
            collection_name = type_to_collection.get(event_type)
            if not collection_name:
                continue

            for entry in knowledge.get(collection_name, []):
                # Get searchable text from entry
                entry_text = " ".join(
                    str(v)
                    for k, v in entry.items()
                    if isinstance(v, str) and k not in ["id", "timestamp", "session_id"]
                )

                similarity = calculate_text_similarity(context, entry_text)
                if similarity >= threshold:
                    similar.append(
                        {
                            "entry_id": entry.get("id"),
                            "type": event_type,
                            "similarity": round(similarity, 3),
                            "preview": entry_text[:100],
                        }
                    )

        return sorted(similar, key=lambda x: x["similarity"], reverse=True)

    def _generate_suggested_fields(
        self,
        context: str,
        types: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Generate suggested fields for each type."""
        suggestions = {}

        for event_type in types:
            if event_type == "gotcha":
                suggestions["gotcha"] = {
                    "problem": context[:200],
                    "solution": "",
                    "severity": "medium",
                    "tags": [],
                }
            elif event_type == "decision":
                suggestions["decision"] = {
                    "decision": context[:200],
                    "rationale": "",
                    "tags": [],
                }
            elif event_type == "learning":
                suggestions["learning"] = {
                    "learning": context[:200],
                    "evidence": "",
                    "confidence": "medium",
                    "tags": [],
                }
            elif event_type == "idea":
                suggestions["idea"] = {
                    "idea": context[:200],
                    "idea_type": "feature",
                    "tags": [],
                }

        return suggestions

    def _generate_reasoning(
        self,
        context: str,
        types: list[str],
        similar: list[dict],
    ) -> str:
        """Generate reasoning for the recommendation."""
        if similar and similar[0].get("similarity", 0) >= 0.8:
            return f"Very similar to existing entry {similar[0]['entry_id']}"
        elif types:
            return f"Detected as {', '.join(types)} based on content analysis"
        else:
            return "No specific type detected, defaulting to learning"

    def checkpoint(
        self,
        session_id: str,
        trigger: str = "manual",
        notes: str | None = None,
        files_touched: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Create a session checkpoint.

        Args:
            session_id: Current session ID
            trigger: What triggered the checkpoint
            notes: Optional notes
            files_touched: Files modified since last checkpoint

        Returns:
            Checkpoint entry with recommendations
        """
        claude_data = self._get_claude_data()
        checkpoints = claude_data["checkpoints"]

        # Analyze session for unlogged items
        summary = self._analyze_session(session_id, files_touched)

        entry = {
            "id": f"CP{len(checkpoints) + 1:03d}",
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "trigger": trigger,
            "summary": summary,
            "recommendations": self._generate_recommendations(summary),
        }

        if notes:
            entry["notes"] = notes
        if files_touched:
            entry["files_touched"] = files_touched

        checkpoints.append(entry)
        return entry

    def _analyze_session(
        self,
        session_id: str,
        files_touched: list[str] | None = None,
    ) -> dict[str, Any]:
        """Analyze session for checkpoint summary."""
        # Count pending suggestions
        pending_suggestions = sum(
            1
            for s in self.trace.get("suggestions", [])
            if s.get("session_id") == session_id and s.get("status") == "pending"
        )

        # Count contributions in session
        contributions = sum(1 for c in self.trace.get("contributions", []) if c.get("session_id") == session_id)

        return {
            "pending_suggestions": pending_suggestions,
            "contributions_logged": contributions,
            "files_touched": len(files_touched) if files_touched else 0,
        }

    def _generate_recommendations(self, summary: dict[str, Any]) -> list[str]:
        """Generate recommendations based on summary."""
        recommendations = []

        if summary.get("pending_suggestions", 0) > 0:
            recommendations.append(f"Resolve {summary['pending_suggestions']} pending suggestion(s)")

        if summary.get("files_touched", 0) > summary.get("contributions_logged", 0):
            recommendations.append("Some file modifications may not be logged as contributions")

        if not recommendations:
            recommendations.append("Session looks well-documented")

        return recommendations


# Convenience functions
def knowledge_check(trace: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Check if content should be logged."""
    return ClaudeExtension(trace).knowledge_check(**kwargs)


def checkpoint(trace: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Create a session checkpoint."""
    return ClaudeExtension(trace).checkpoint(**kwargs)


__all__ = [
    "ClaudeExtension",
    "knowledge_check",
    "checkpoint",
    "calculate_text_similarity",
]
