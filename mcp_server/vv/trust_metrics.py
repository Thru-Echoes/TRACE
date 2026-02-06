"""
TRACE V&V Trust Metrics

Computes reliability scores for publication based on:
- Verification accuracy
- Git sync coverage
- Chain integrity
- Temporal consistency
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from .git_reconcile import GitReconciler
from .integrity import IntegrityChain
from .verification import VerificationEngine


class TrustCalculator:
    """Calculates trust metrics for TRACE data."""

    # Default weights for trust score components
    DEFAULT_WEIGHTS = {
        "verification_accuracy": 0.30,
        "git_sync_coverage": 0.25,
        "chain_integrity": 0.25,
        "temporal_consistency": 0.20,
    }

    def __init__(self, trace_dir: Path, project_dir: Path | None = None, weights: dict[str, float] | None = None):
        """
        Initialize the trust calculator.

        Args:
            trace_dir: Path to the .trace directory
            project_dir: Path to the project root (for git ops)
            weights: Custom weights for trust components
        """
        self.trace_dir = Path(trace_dir)
        self.project_dir = project_dir or trace_dir.parent
        self.weights = weights or self.DEFAULT_WEIGHTS

        self.verification_engine = VerificationEngine(trace_dir)
        self.git_reconciler = GitReconciler(self.project_dir)
        self.integrity_chain = IntegrityChain(trace_dir)

    def compute_verification_score(self, trace: dict[str, Any], sample_size: int = 20) -> dict[str, Any]:
        """
        Compute verification accuracy score.

        Args:
            trace: The TRACE data
            sample_size: Number of entries to sample for verification

        Returns:
            Verification score and details
        """
        # Get recent code contributions
        contributions = trace.get("code_contributions", [])[-sample_size:]

        if not contributions:
            return {"score": 1.0, "verified_count": 0, "total_count": 0, "message": "No contributions to verify"}

        verified_count = 0
        total_checks = 0
        passed_checks = 0
        issues = []

        for contrib in contributions:
            entry_id = contrib.get("id")
            if not entry_id:
                continue

            result = self.verification_engine.verify_entry(trace, entry_id)

            if result.get("verified"):
                verified_count += 1

            total_checks += result.get("checks_passed", 0) + result.get("checks_failed", 0)
            passed_checks += result.get("checks_passed", 0)

            if not result.get("verified"):
                issues.append(
                    {"entry_id": entry_id, "errors": result.get("errors", 0), "warnings": result.get("warnings", 0)}
                )

        score = passed_checks / total_checks if total_checks > 0 else 1.0

        return {
            "score": round(score, 3),
            "verified_count": verified_count,
            "total_count": len(contributions),
            "total_checks": total_checks,
            "passed_checks": passed_checks,
            "issues": issues[:5],  # Top 5 issues
            "message": f"{verified_count}/{len(contributions)} entries fully verified",
        }

    def compute_git_sync_score(self, trace: dict[str, Any], since: str = "30 days ago") -> dict[str, Any]:
        """
        Compute git synchronization coverage score.

        Args:
            trace: The TRACE data
            since: Time period to check

        Returns:
            Git sync score and details
        """
        result = self.git_reconciler.reconcile(trace, since)

        if result.get("error"):
            return {"score": 0.0, "error": result["error"], "message": "Git reconciliation failed"}

        summary = result.get("summary", {})
        coverage = summary.get("coverage_percent", 0) / 100

        # Penalize phantom entries more heavily
        phantom_penalty = len(result.get("phantom_entries", [])) * 0.05

        score = max(0, coverage - phantom_penalty)

        return {
            "score": round(score, 3),
            "coverage_percent": summary.get("coverage_percent"),
            "total_commits": summary.get("total_commits"),
            "tracked_commits": summary.get("tracked_commits"),
            "unlogged_commits": summary.get("unlogged_commits"),
            "phantom_entries": len(result.get("phantom_entries", [])),
            "message": f"{summary.get('coverage_percent', 0)}% git coverage",
        }

    def compute_integrity_score(self, trace: dict[str, Any]) -> dict[str, Any]:
        """
        Compute chain integrity score.

        Args:
            trace: The TRACE data

        Returns:
            Integrity score and details
        """
        result = self.integrity_chain.verify_chain(trace)

        if result.get("verified"):
            score = 1.0
        else:
            # Calculate score based on error ratio
            chain_length = result.get("chain_length", 1)
            error_count = len(result.get("errors", []))
            score = max(0, 1 - (error_count / chain_length))

        return {
            "score": round(score, 3),
            "chain_verified": result.get("verified"),
            "chain_length": result.get("chain_length"),
            "errors": len(result.get("errors", [])),
            "warnings": len(result.get("warnings", [])),
            "message": "Chain intact" if result.get("verified") else f"{len(result.get('errors', []))} chain errors",
        }

    def compute_temporal_score(self, trace: dict[str, Any]) -> dict[str, Any]:
        """
        Compute temporal consistency score.

        Checks for:
        - Entries with future timestamps
        - Out-of-order timestamps within sessions
        - Large gaps that might indicate missed entries
        """
        issues = []
        total_entries = 0
        valid_entries = 0

        now = datetime.now()

        # Check all timestamped entries
        collections = [
            "code_contributions",
            "ai_suggestions",
            "decisions",
            "learnings",
            "gotchas",
            "ideas",
            "errors",
            "interventions",
        ]

        for collection in collections:
            entries = trace.get(collection, [])

            for entry in entries:
                total_entries += 1
                timestamp = entry.get("timestamp")

                if not timestamp:
                    issues.append({"entry_id": entry.get("id"), "issue": "missing_timestamp"})
                    continue

                try:
                    entry_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    entry_time = entry_time.replace(tzinfo=None)

                    if entry_time > now:
                        issues.append(
                            {"entry_id": entry.get("id"), "issue": "future_timestamp", "timestamp": timestamp}
                        )
                    else:
                        valid_entries += 1
                except (ValueError, TypeError):
                    issues.append({"entry_id": entry.get("id"), "issue": "invalid_timestamp", "timestamp": timestamp})

        # Check session ordering
        sessions = trace.get("sessions", [])
        for session in sessions:
            started = session.get("started")
            ended = session.get("ended")

            if started and ended:
                try:
                    start_time = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    end_time = datetime.fromisoformat(ended.replace("Z", "+00:00"))

                    if end_time < start_time:
                        issues.append({"session_id": session.get("id"), "issue": "session_end_before_start"})
                except (ValueError, TypeError):
                    pass

        score = valid_entries / total_entries if total_entries > 0 else 1.0

        return {
            "score": round(score, 3),
            "total_entries": total_entries,
            "valid_entries": valid_entries,
            "issues_count": len(issues),
            "issues": issues[:5],  # Top 5 issues
            "message": f"{valid_entries}/{total_entries} entries have valid timestamps",
        }

    def compute_trust_score(self, trace: dict[str, Any], period: str = "30 days") -> dict[str, Any]:
        """
        Compute overall trust score.

        Args:
            trace: The TRACE data
            period: Time period to analyze

        Returns:
            Comprehensive trust report
        """
        # Compute individual scores
        verification = self.compute_verification_score(trace)
        git_sync = self.compute_git_sync_score(trace, f"{period} ago" if not period.endswith("ago") else period)
        integrity = self.compute_integrity_score(trace)
        temporal = self.compute_temporal_score(trace)

        # Calculate weighted overall score
        overall_score = (
            verification["score"] * self.weights["verification_accuracy"]
            + git_sync["score"] * self.weights["git_sync_coverage"]
            + integrity["score"] * self.weights["chain_integrity"]
            + temporal["score"] * self.weights["temporal_consistency"]
        )

        # Determine trust level
        if overall_score >= 0.95:
            trust_level = "high"
            trust_message = "Excellent documentation integrity"
        elif overall_score >= 0.80:
            trust_level = "good"
            trust_message = "Good documentation with minor issues"
        elif overall_score >= 0.60:
            trust_level = "moderate"
            trust_message = "Acceptable documentation, some gaps exist"
        elif overall_score >= 0.40:
            trust_level = "low"
            trust_message = "Documentation has significant gaps"
        else:
            trust_level = "poor"
            trust_message = "Documentation integrity is questionable"

        # Identify top issues
        all_issues = []
        if verification.get("issues"):
            all_issues.extend([{"component": "verification", **i} for i in verification["issues"]])
        if git_sync.get("phantom_entries"):
            all_issues.append({"component": "git_sync", "issue": f"{git_sync['phantom_entries']} phantom entries"})
        if integrity.get("errors"):
            all_issues.append({"component": "integrity", "issue": f"{integrity['errors']} chain errors"})
        if temporal.get("issues"):
            all_issues.extend([{"component": "temporal", **i} for i in temporal["issues"]])

        return {
            "overall_score": round(overall_score, 3),
            "trust_level": trust_level,
            "trust_message": trust_message,
            "period": period,
            "timestamp": datetime.now().isoformat(),
            "weights": self.weights,
            "components": {
                "verification_accuracy": verification,
                "git_sync_coverage": git_sync,
                "chain_integrity": integrity,
                "temporal_consistency": temporal,
            },
            "top_issues": all_issues[:10],
            "recommendations": self._generate_recommendations(verification, git_sync, integrity, temporal),
        }

    def _generate_recommendations(
        self,
        verification: dict[str, Any],
        git_sync: dict[str, Any],
        integrity: dict[str, Any],
        temporal: dict[str, Any],
    ) -> list[str]:
        """Generate recommendations based on scores."""
        recommendations = []

        if verification["score"] < 0.8:
            recommendations.append("Run trace_verify on recent entries to identify logging discrepancies")

        if git_sync["score"] < 0.8:
            recommendations.append("Review unlogged git commits and add missing TRACE entries")
            if git_sync.get("phantom_entries", 0) > 0:
                recommendations.append("Investigate phantom entries that reference missing git commits")

        if integrity["score"] < 1.0:
            recommendations.append("Rebuild integrity chain with trace_verify_integrity")

        if temporal["score"] < 0.9:
            if any(i.get("issue") == "future_timestamp" for i in temporal.get("issues", [])):
                recommendations.append("Fix entries with future timestamps")
            if any(i.get("issue") == "missing_timestamp" for i in temporal.get("issues", [])):
                recommendations.append("Add timestamps to entries missing them")

        if not recommendations:
            recommendations.append("TRACE documentation is in good shape. Continue logging as normal.")

        return recommendations


# Module-level convenience function
def compute_trust_score(trace_dir: Path, trace: dict[str, Any], period: str = "30 days") -> dict[str, Any]:
    """Compute trust score using a new calculator instance."""
    calculator = TrustCalculator(trace_dir)
    return calculator.compute_trust_score(trace, period)
