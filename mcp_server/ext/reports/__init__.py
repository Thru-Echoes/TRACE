"""
TRACE Reports Extension

Reporting and analysis features:
- Trust metrics computation
- Publication-ready reports
- Text analysis (LaTeX, Markdown)
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class ReportsExtension:
    """Reporting and analysis extension."""

    def __init__(self, trace: dict[str, Any], trace_dir: Path | None = None):
        """Initialize with trace data."""
        self.trace = trace
        self.trace_dir = trace_dir
        self._ensure_structure()

    def _ensure_structure(self) -> None:
        """Ensure reports extension structure exists."""
        if "_extensions" not in self.trace:
            self.trace["_extensions"] = {}
        if "reports" not in self.trace["_extensions"]:
            self.trace["_extensions"]["reports"] = {
                "trust_reports": [],
                "text_analyses": [],
            }

    def generate_trust_report(
        self,
        period: str = "all",
        format: str = "summary",
    ) -> dict[str, Any] | str:
        """
        Generate a trust report.

        Args:
            period: Time period to analyze
            format: Output format (summary, json, markdown)

        Returns:
            Trust report in requested format
        """
        # Compute trust components
        components = self._compute_trust_components()

        # Calculate overall score (weighted average)
        weights = {
            "contribution_coverage": 0.30,
            "suggestion_tracking": 0.25,
            "session_completeness": 0.25,
            "integrity": 0.20,
        }

        overall_score = sum(components[key]["score"] * weights[key] for key in weights)

        # Determine trust level
        if overall_score >= 0.95:
            trust_level = "high"
        elif overall_score >= 0.80:
            trust_level = "good"
        elif overall_score >= 0.60:
            trust_level = "moderate"
        elif overall_score >= 0.40:
            trust_level = "low"
        else:
            trust_level = "poor"

        report = {
            "timestamp": datetime.now().isoformat(),
            "period": period,
            "overall_score": round(overall_score, 3),
            "trust_level": trust_level,
            "components": components,
            "weights": weights,
            "recommendations": self._generate_recommendations(components),
        }

        # Store report
        reports_data = self.trace["_extensions"]["reports"]
        report_entry = {
            "id": f"TR{len(reports_data['trust_reports']) + 1:03d}",
            **report,
        }
        reports_data["trust_reports"].append(report_entry)

        if format == "json":
            return json.dumps(report, indent=2)
        elif format == "markdown":
            return self._format_markdown(report)
        else:
            return report

    def _compute_trust_components(self) -> dict[str, dict[str, Any]]:
        """Compute individual trust components."""
        contributions = self.trace.get("contributions", [])
        suggestions = self.trace.get("suggestions", [])
        sessions = self.trace.get("sessions", [])

        # Contribution coverage
        total_contributions = len(contributions)
        contributions_with_authorship = sum(1 for c in contributions if c.get("authorship", {}).get("direction"))
        contribution_score = contributions_with_authorship / total_contributions if total_contributions > 0 else 1.0

        # Suggestion tracking
        total_suggestions = len(suggestions)
        resolved_suggestions = sum(1 for s in suggestions if s.get("status") != "pending")
        suggestion_score = resolved_suggestions / total_suggestions if total_suggestions > 0 else 1.0

        # Session completeness
        total_sessions = len(sessions)
        completed_sessions = sum(1 for s in sessions if s.get("ended"))
        session_score = completed_sessions / total_sessions if total_sessions > 0 else 1.0

        # Integrity (check if integrity chain exists and is consistent)
        chain = self.trace.get("integrity_chain", {})
        chain_entries = len(chain.get("entries", []))
        total_entries = total_contributions + total_suggestions
        integrity_score = chain_entries / total_entries if total_entries > 0 else 1.0

        return {
            "contribution_coverage": {
                "score": round(contribution_score, 3),
                "total": total_contributions,
                "with_authorship": contributions_with_authorship,
                "message": f"{contributions_with_authorship}/{total_contributions} contributions have authorship data",
            },
            "suggestion_tracking": {
                "score": round(suggestion_score, 3),
                "total": total_suggestions,
                "resolved": resolved_suggestions,
                "message": f"{resolved_suggestions}/{total_suggestions} suggestions resolved",
            },
            "session_completeness": {
                "score": round(session_score, 3),
                "total": total_sessions,
                "completed": completed_sessions,
                "message": f"{completed_sessions}/{total_sessions} sessions completed",
            },
            "integrity": {
                "score": round(integrity_score, 3),
                "chain_entries": chain_entries,
                "expected_entries": total_entries,
                "message": f"{chain_entries}/{total_entries} entries in integrity chain",
            },
        }

    def _generate_recommendations(
        self,
        components: dict[str, dict[str, Any]],
    ) -> list[str]:
        """Generate recommendations based on component scores."""
        recommendations = []

        if components["contribution_coverage"]["score"] < 0.9:
            recommendations.append(
                "Some contributions are missing authorship data. Use trace_log_contribution with authorship fields."
            )

        if components["suggestion_tracking"]["score"] < 0.9:
            pending = components["suggestion_tracking"]["total"] - components["suggestion_tracking"]["resolved"]
            recommendations.append(f"Resolve {pending} pending suggestion(s) using trace_resolve_suggestion.")

        if components["session_completeness"]["score"] < 0.9:
            incomplete = components["session_completeness"]["total"] - components["session_completeness"]["completed"]
            recommendations.append(f"End {incomplete} incomplete session(s) using trace_end_session.")

        if components["integrity"]["score"] < 0.9:
            recommendations.append(
                "Some entries are missing from the integrity chain. Ensure integrity is enabled for new entries."
            )

        if not recommendations:
            recommendations.append("Documentation integrity is excellent!")

        return recommendations

    def _format_markdown(self, report: dict[str, Any]) -> str:
        """Format report as Markdown."""
        components = report["components"]

        return f"""# TRACE Trust Report

**Generated**: {report["timestamp"]}
**Period**: {report["period"]}

## Summary

| Metric | Value |
|--------|-------|
| **Overall Score** | {report["overall_score"]:.1%} |
| **Trust Level** | {report["trust_level"].upper()} |

## Component Scores

| Component | Score | Details |
|-----------|-------|---------|
| Contribution Coverage | {components["contribution_coverage"]["score"]:.1%} | {components["contribution_coverage"]["message"]} |
| Suggestion Tracking | {components["suggestion_tracking"]["score"]:.1%} | {components["suggestion_tracking"]["message"]} |
| Session Completeness | {components["session_completeness"]["score"]:.1%} | {components["session_completeness"]["message"]} |
| Integrity Chain | {components["integrity"]["score"]:.1%} | {components["integrity"]["message"]} |

## Recommendations

{chr(10).join(f"- {r}" for r in report["recommendations"])}

---

*Generated by TRACE Protocol v3.0*
"""

    def generate_publication_summary(self) -> str:
        """Generate a publication-ready summary statement."""
        # Import metrics from core
        from ...core.metrics import MetricsComputer

        computer = MetricsComputer(self.trace)
        authorship = computer.compute_authorship_metrics()
        suggestions = computer.compute_suggestion_metrics()

        lines = authorship.get("lines", {})
        total_lines = lines.get("total", 0)
        ai_pct = lines.get("ai_percentage", 0)
        human_pct = lines.get("human_percentage", 0)

        direction = authorship.get("direction_breakdown", {})
        human_directed_pct = direction.get("human_directed_percentage", 0)
        ai_suggested_pct = direction.get("ai_suggested_percentage", 0)

        sug_rates = suggestions.get("rates", {})
        acceptance_rate = sug_rates.get("acceptance_rate", 0) * 100
        modification_rate = sug_rates.get("modification_rate", 0) * 100

        return f"""AI assistance was documented using the TRACE protocol (v3.0).

**Authorship Summary:**
- Total lines: {total_lines}
- AI-executed: {ai_pct:.1f}% | Human-executed: {human_pct:.1f}%
- Human-directed: {human_directed_pct:.1f}% | AI-suggested: {ai_suggested_pct:.1f}%

**AI Suggestions:**
- Total suggestions: {suggestions.get("total_suggestions", 0)}
- Acceptance rate: {acceptance_rate:.1f}%
- Modification rate: {modification_rate:.1f}%

Full TRACE logs are available in the project repository."""


# Convenience functions
def generate_trust_report(trace: dict[str, Any], **kwargs) -> dict[str, Any] | str:
    """Generate a trust report."""
    return ReportsExtension(trace).generate_trust_report(**kwargs)


def generate_publication_summary(trace: dict[str, Any]) -> str:
    """Generate a publication-ready summary."""
    return ReportsExtension(trace).generate_publication_summary()


__all__ = [
    "ReportsExtension",
    "generate_trust_report",
    "generate_publication_summary",
]
