"""
TRACE Protocol Metrics Computation

Computes authorship and collaboration metrics from trace data.
"""

from typing import Any


class MetricsComputer:
    """Computes metrics from TRACE data."""

    def __init__(self, trace: dict[str, Any]):
        """
        Initialize metrics computer.

        Args:
            trace: The trace data dictionary
        """
        self.trace = trace

    def compute_all(self) -> dict[str, Any]:
        """Compute all metrics."""
        return {
            "authorship": self.compute_authorship_metrics(),
            "suggestions": self.compute_suggestion_metrics(),
            "evaluations": self.compute_evaluation_metrics(),
            "sessions": self.compute_session_metrics(),
        }

    def compute_authorship_metrics(self) -> dict[str, Any]:
        """
        Compute authorship metrics from contributions.

        Returns metrics like:
        - Total lines by direction (human_directed, ai_suggested, collaborative)
        - Total lines by execution (ai, human)
        - Percentages for each category
        """
        contributions = self.trace.get("contributions", [])

        # Initialize counters
        by_direction = {
            "human_directed": {"ai_lines": 0, "human_lines": 0, "ai_words": 0, "human_words": 0},
            "ai_suggested": {"ai_lines": 0, "human_lines": 0, "ai_words": 0, "human_words": 0},
            "collaborative": {"ai_lines": 0, "human_lines": 0, "ai_words": 0, "human_words": 0},
        }

        by_content_type = {"code": 0, "text": 0, "data": 0}
        total_contributions = len(contributions)

        for contrib in contributions:
            authorship = contrib.get("authorship", {})
            direction = authorship.get("direction", "human_directed")
            execution = authorship.get("execution", {})
            content_type = contrib.get("content_type", "code")

            # Count by direction
            if direction in by_direction:
                by_direction[direction]["ai_lines"] += execution.get("ai_lines", 0)
                by_direction[direction]["human_lines"] += execution.get("human_lines", 0)
                by_direction[direction]["ai_words"] += execution.get("ai_words", 0)
                by_direction[direction]["human_words"] += execution.get("human_words", 0)

            # Count by content type
            if content_type in by_content_type:
                by_content_type[content_type] += 1

        # Compute totals
        total_ai_lines = sum(d["ai_lines"] for d in by_direction.values())
        total_human_lines = sum(d["human_lines"] for d in by_direction.values())
        total_lines = total_ai_lines + total_human_lines

        total_ai_words = sum(d["ai_words"] for d in by_direction.values())
        total_human_words = sum(d["human_words"] for d in by_direction.values())
        total_words = total_ai_words + total_human_words

        # Compute percentages
        def safe_percent(part: int, whole: int) -> float:
            return round(part / whole * 100, 1) if whole > 0 else 0.0

        return {
            "total_contributions": total_contributions,
            "by_content_type": by_content_type,
            "lines": {
                "total": total_lines,
                "ai_executed": total_ai_lines,
                "human_executed": total_human_lines,
                "ai_percentage": safe_percent(total_ai_lines, total_lines),
                "human_percentage": safe_percent(total_human_lines, total_lines),
                "by_direction": {
                    "human_directed": {
                        "total": by_direction["human_directed"]["ai_lines"]
                        + by_direction["human_directed"]["human_lines"],
                        "ai_executed": by_direction["human_directed"]["ai_lines"],
                        "human_executed": by_direction["human_directed"]["human_lines"],
                    },
                    "ai_suggested": {
                        "total": by_direction["ai_suggested"]["ai_lines"] + by_direction["ai_suggested"]["human_lines"],
                        "ai_executed": by_direction["ai_suggested"]["ai_lines"],
                        "human_executed": by_direction["ai_suggested"]["human_lines"],
                    },
                    "collaborative": {
                        "total": by_direction["collaborative"]["ai_lines"]
                        + by_direction["collaborative"]["human_lines"],
                        "ai_executed": by_direction["collaborative"]["ai_lines"],
                        "human_executed": by_direction["collaborative"]["human_lines"],
                    },
                },
            },
            "words": {
                "total": total_words,
                "ai_executed": total_ai_words,
                "human_executed": total_human_words,
                "ai_percentage": safe_percent(total_ai_words, total_words),
                "human_percentage": safe_percent(total_human_words, total_words),
            },
            "direction_breakdown": {
                "human_directed_percentage": safe_percent(
                    by_direction["human_directed"]["ai_lines"] + by_direction["human_directed"]["human_lines"],
                    total_lines,
                ),
                "ai_suggested_percentage": safe_percent(
                    by_direction["ai_suggested"]["ai_lines"] + by_direction["ai_suggested"]["human_lines"], total_lines
                ),
                "collaborative_percentage": safe_percent(
                    by_direction["collaborative"]["ai_lines"] + by_direction["collaborative"]["human_lines"],
                    total_lines,
                ),
            },
        }

    def compute_suggestion_metrics(self) -> dict[str, Any]:
        """
        Compute metrics about AI suggestions.

        Returns metrics like:
        - Total suggestions
        - Acceptance/rejection/modification rates
        - Lines proposed vs accepted
        """
        suggestions = self.trace.get("suggestions", [])

        total = len(suggestions)
        by_status = {"pending": 0, "accepted": 0, "rejected": 0, "modified": 0}
        by_type: dict[str, int] = {}
        by_confidence = {"high": 0, "medium": 0, "low": 0}

        lines_proposed = 0
        lines_accepted = 0
        lines_modified = 0
        lines_rejected = 0

        for sug in suggestions:
            status = sug.get("status", "pending")
            sug_type = sug.get("suggestion_type", "unknown")
            confidence = sug.get("confidence", "medium")

            by_status[status] = by_status.get(status, 0) + 1
            by_type[sug_type] = by_type.get(sug_type, 0) + 1
            if confidence in by_confidence:
                by_confidence[confidence] += 1

            # Count lines
            proposed = sug.get("proposed", {})
            lines_proposed += proposed.get("lines", 0)

            resolution = sug.get("resolution", {})
            if resolution:
                lines_accepted += resolution.get("lines_accepted", 0)
                lines_modified += resolution.get("lines_modified", 0)
                lines_rejected += resolution.get("lines_rejected", 0)

        # Compute rates
        resolved = by_status["accepted"] + by_status["rejected"] + by_status["modified"]

        def safe_rate(count: int, total: int) -> float:
            return round(count / total, 3) if total > 0 else 0.0

        return {
            "total_suggestions": total,
            "by_status": by_status,
            "by_type": by_type,
            "by_confidence": by_confidence,
            "rates": {
                "acceptance_rate": safe_rate(by_status["accepted"], resolved),
                "rejection_rate": safe_rate(by_status["rejected"], resolved),
                "modification_rate": safe_rate(by_status["modified"], resolved),
                "pending_rate": safe_rate(by_status["pending"], total),
            },
            "lines": {
                "proposed": lines_proposed,
                "accepted": lines_accepted,
                "modified": lines_modified,
                "rejected": lines_rejected,
                "acceptance_efficiency": safe_rate(lines_accepted + lines_modified, lines_proposed),
            },
        }

    def compute_evaluation_metrics(self) -> dict[str, Any]:
        """Compute metrics about evaluations."""
        evaluations = self.trace.get("evaluations", [])

        total = len(evaluations)
        by_type: dict[str, int] = {}
        passed = 0
        failed = 0
        total_tests_passed = 0
        total_tests_failed = 0

        for eval_entry in evaluations:
            eval_type = eval_entry.get("evaluation_type", "unknown")
            by_type[eval_type] = by_type.get(eval_type, 0) + 1

            if eval_entry.get("passed") is True:
                passed += 1
            elif eval_entry.get("passed") is False:
                failed += 1

            metrics = eval_entry.get("metrics", {})
            total_tests_passed += metrics.get("tests_passed", 0)
            total_tests_failed += metrics.get("tests_failed", 0)

        return {
            "total_evaluations": total,
            "by_type": by_type,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / total, 3) if total > 0 else 0.0,
            "aggregate_tests": {
                "passed": total_tests_passed,
                "failed": total_tests_failed,
                "total": total_tests_passed + total_tests_failed,
            },
        }

    def compute_session_metrics(self) -> dict[str, Any]:
        """Compute metrics about sessions."""
        sessions = self.trace.get("sessions", [])

        total = len(sessions)
        completed = sum(1 for s in sessions if s.get("ended"))
        by_stage: dict[str, int] = {}

        helpfulness_ratings = []
        for session in sessions:
            stage = session.get("scientific_stage", "unknown")
            by_stage[stage] = by_stage.get(stage, 0) + 1

            rating = session.get("ai_helpfulness_rating")
            if rating is not None:
                helpfulness_ratings.append(rating)

        avg_helpfulness = round(sum(helpfulness_ratings) / len(helpfulness_ratings), 2) if helpfulness_ratings else None

        return {
            "total_sessions": total,
            "completed_sessions": completed,
            "by_scientific_stage": by_stage,
            "ai_helpfulness": {
                "average_rating": avg_helpfulness,
                "ratings_count": len(helpfulness_ratings),
            },
        }

    def compute_intervention_metrics(self) -> dict[str, Any]:
        """Compute metrics about human interventions."""
        interventions = self.trace.get("interventions", [])

        total = len(interventions)
        by_type: dict[str, int] = {}
        by_significance: dict[str, int] = {}
        total_lines_affected = 0

        for intervention in interventions:
            int_type = intervention.get("type", "unknown")
            significance = intervention.get("significance", "unknown")

            by_type[int_type] = by_type.get(int_type, 0) + 1
            by_significance[significance] = by_significance.get(significance, 0) + 1
            total_lines_affected += intervention.get("lines_affected", 0)

        return {
            "total_interventions": total,
            "by_type": by_type,
            "by_significance": by_significance,
            "total_lines_affected": total_lines_affected,
        }

    def compute_error_metrics(self) -> dict[str, Any]:
        """Compute metrics about errors."""
        errors = self.trace.get("errors", [])

        total = len(errors)
        by_type: dict[str, int] = {}
        by_origin: dict[str, int] = {}
        by_detector: dict[str, int] = {}

        for error in errors:
            error_type = error.get("error_type", "unknown")
            origin = error.get("originated_from", "unknown")
            detector = error.get("detected_by", "unknown")

            by_type[error_type] = by_type.get(error_type, 0) + 1
            by_origin[origin] = by_origin.get(origin, 0) + 1
            by_detector[detector] = by_detector.get(detector, 0) + 1

        return {
            "total_errors": total,
            "by_type": by_type,
            "by_origin": by_origin,
            "by_detector": by_detector,
        }


def compute_authorship_metrics(trace: dict[str, Any]) -> dict[str, Any]:
    """Convenience function to compute authorship metrics."""
    return MetricsComputer(trace).compute_authorship_metrics()


def compute_suggestion_metrics(trace: dict[str, Any]) -> dict[str, Any]:
    """Convenience function to compute suggestion metrics."""
    return MetricsComputer(trace).compute_suggestion_metrics()
