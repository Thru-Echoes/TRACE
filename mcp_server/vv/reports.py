"""
TRACE V&V Report Generation

Generates publication-ready reports for verification and trust metrics.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .trust_metrics import TrustCalculator


class ReportGenerator:
    """Generates V&V reports for TRACE data."""

    def __init__(self, trace_dir: Path, project_dir: Path | None = None):
        """
        Initialize the report generator.

        Args:
            trace_dir: Path to the .trace directory
            project_dir: Path to the project root
        """
        self.trace_dir = Path(trace_dir)
        self.project_dir = project_dir or trace_dir.parent
        self.trust_calculator = TrustCalculator(trace_dir, project_dir)

    def generate_trust_report(self, trace: dict[str, Any], period: str = "30 days", format: str = "markdown") -> str:
        """
        Generate a trust report.

        Args:
            trace: The TRACE data
            period: Time period to analyze
            format: Output format (markdown, json, summary)

        Returns:
            Formatted report string
        """
        trust_data = self.trust_calculator.compute_trust_score(trace, period)

        if format == "json":
            return json.dumps(trust_data, indent=2)
        elif format == "summary":
            return self._generate_summary(trust_data)
        else:
            return self._generate_markdown(trust_data, trace)

    def _generate_summary(self, trust_data: dict[str, Any]) -> str:
        """Generate a brief summary."""
        return f"""TRACE V&V Trust Report Summary
==============================
Overall Score: {trust_data["overall_score"]:.1%}
Trust Level: {trust_data["trust_level"].upper()}
Period: {trust_data["period"]}

Component Scores:
- Verification Accuracy: {trust_data["components"]["verification_accuracy"]["score"]:.1%}
- Git Sync Coverage: {trust_data["components"]["git_sync_coverage"]["score"]:.1%}
- Chain Integrity: {trust_data["components"]["chain_integrity"]["score"]:.1%}
- Temporal Consistency: {trust_data["components"]["temporal_consistency"]["score"]:.1%}

{trust_data["trust_message"]}

Recommendations:
{chr(10).join("- " + r for r in trust_data["recommendations"])}
"""

    def _generate_markdown(self, trust_data: dict[str, Any], trace: dict[str, Any]) -> str:
        """Generate a detailed Markdown report."""
        components = trust_data["components"]
        verification = components["verification_accuracy"]
        git_sync = components["git_sync_coverage"]
        integrity = components["chain_integrity"]
        temporal = components["temporal_consistency"]

        # Get TRACE statistics
        code_contributions = len(trace.get("code_contributions", []))
        ai_suggestions = len(trace.get("ai_suggestions", []))
        sessions = len(trace.get("sessions", []))

        report = f"""# TRACE Verification & Validation Report

**Generated**: {trust_data["timestamp"]}
**Period**: {trust_data["period"]}
**Schema Version**: {trace.get("schema_version", "unknown")}

---

## Executive Summary

| Metric | Value |
|--------|-------|
| **Overall Trust Score** | {trust_data["overall_score"]:.1%} |
| **Trust Level** | {trust_data["trust_level"].upper()} |
| **Assessment** | {trust_data["trust_message"]} |

---

## Trust Score Components

The overall trust score is computed from four weighted components:

| Component | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Verification Accuracy | {verification["score"]:.1%} | {trust_data["weights"]["verification_accuracy"]:.0%} | {verification["score"] * trust_data["weights"]["verification_accuracy"]:.3f} |
| Git Sync Coverage | {git_sync["score"]:.1%} | {trust_data["weights"]["git_sync_coverage"]:.0%} | {git_sync["score"] * trust_data["weights"]["git_sync_coverage"]:.3f} |
| Chain Integrity | {integrity["score"]:.1%} | {trust_data["weights"]["chain_integrity"]:.0%} | {integrity["score"] * trust_data["weights"]["chain_integrity"]:.3f} |
| Temporal Consistency | {temporal["score"]:.1%} | {trust_data["weights"]["temporal_consistency"]:.0%} | {temporal["score"] * trust_data["weights"]["temporal_consistency"]:.3f} |
| **Total** | | 100% | **{trust_data["overall_score"]:.3f}** |

---

## Component Details

### 1. Verification Accuracy ({verification["score"]:.1%})

Measures how accurately TRACE entries reflect actual file changes.

| Metric | Value |
|--------|-------|
| Entries Verified | {verification["verified_count"]}/{verification["total_count"]} |
| Checks Passed | {verification.get("passed_checks", "N/A")}/{verification.get("total_checks", "N/A")} |
| Status | {verification["message"]} |

{self._format_issues("Verification Issues", verification.get("issues", []))}

### 2. Git Sync Coverage ({git_sync["score"]:.1%})

Measures synchronization between TRACE logs and git history.

| Metric | Value |
|--------|-------|
| Git Coverage | {git_sync.get("coverage_percent", "N/A")}% |
| Total Commits | {git_sync.get("total_commits", "N/A")} |
| Tracked Commits | {git_sync.get("tracked_commits", "N/A")} |
| Unlogged Commits | {git_sync.get("unlogged_commits", "N/A")} |
| Phantom Entries | {git_sync.get("phantom_entries", 0)} |

### 3. Chain Integrity ({integrity["score"]:.1%})

Verifies the cryptographic hash chain for tamper detection.

| Metric | Value |
|--------|-------|
| Chain Verified | {"Yes" if integrity.get("chain_verified") else "No"} |
| Chain Length | {integrity.get("chain_length", 0)} entries |
| Errors | {integrity.get("errors", 0)} |
| Warnings | {integrity.get("warnings", 0)} |

### 4. Temporal Consistency ({temporal["score"]:.1%})

Validates timestamp ordering and consistency.

| Metric | Value |
|--------|-------|
| Valid Timestamps | {temporal.get("valid_entries", 0)}/{temporal.get("total_entries", 0)} |
| Issues Found | {temporal.get("issues_count", 0)} |

{self._format_issues("Temporal Issues", temporal.get("issues", []))}

---

## TRACE Data Summary

| Category | Count |
|----------|-------|
| Sessions | {sessions} |
| Code Contributions | {code_contributions} |
| AI Suggestions | {ai_suggestions} |
| Decisions | {len(trace.get("decisions", []))} |
| Learnings | {len(trace.get("learnings", []))} |
| Gotchas | {len(trace.get("gotchas", []))} |
| Ideas | {len(trace.get("ideas", []))} |
| Errors | {len(trace.get("errors", []))} |
| Interventions | {len(trace.get("interventions", []))} |

---

## Recommendations

{chr(10).join(f"{i + 1}. {r}" for i, r in enumerate(trust_data["recommendations"]))}

---

## Publication Disclosure Statement

Based on this verification, the following disclosure statement is recommended:

> This research utilized AI assistance documented using the TRACE protocol (v2.0)
> with Verification & Validation (V&V) system. The documentation achieved a trust
> score of **{trust_data["overall_score"]:.1%}** ({trust_data["trust_level"]} confidence).
>
> **Verification Summary:**
> - {verification["verified_count"]} of {verification["total_count"]} logged entries verified against file changes
> - {git_sync.get("coverage_percent", "N/A")}% coverage of git commit history
> - Cryptographic integrity chain: {"Verified" if integrity.get("chain_verified") else "Issues detected"}
>
> Full TRACE logs and V&V reports are available in the project repository.

---

## Methodology

This report was generated using the TRACE V&V system which:

1. **Verification Accuracy**: Compares logged line/word counts against actual file diffs
2. **Git Sync Coverage**: Cross-validates TRACE entries with git commit history
3. **Chain Integrity**: Verifies SHA-256 hash chain for tamper detection
4. **Temporal Consistency**: Validates timestamp ordering and completeness

Trust score weights are configurable. Default weights:
- Verification Accuracy: 30%
- Git Sync Coverage: 25%
- Chain Integrity: 25%
- Temporal Consistency: 20%

---

*Report generated by TRACE V&V System v1.0*
"""
        return report

    def _format_issues(self, title: str, issues: list) -> str:
        """Format a list of issues for the report."""
        if not issues:
            return f"**{title}**: None detected\n"

        lines = [f"**{title}**:\n"]
        for issue in issues[:5]:
            if isinstance(issue, dict):
                entry_id = issue.get("entry_id", issue.get("session_id", "unknown"))
                issue_type = issue.get("issue", issue.get("error", "unknown"))
                lines.append(f"- {entry_id}: {issue_type}")
            else:
                lines.append(f"- {issue}")

        if len(issues) > 5:
            lines.append(f"- ... and {len(issues) - 5} more")

        return "\n".join(lines) + "\n"

    def generate_verification_log(self, trace: dict[str, Any], session_id: str | None = None) -> str:
        """
        Generate a detailed verification log.

        Args:
            trace: The TRACE data
            session_id: Optional session to filter by

        Returns:
            Verification log as formatted string
        """
        from .verification import VerificationEngine

        engine = VerificationEngine(self.trace_dir)

        if session_id:
            result = engine.verify_session(trace, session_id)
            return self._format_session_verification(result)
        else:
            # Verify recent entries
            contributions = trace.get("code_contributions", [])[-20:]

            lines = ["# TRACE Verification Log\n"]
            lines.append(f"Generated: {datetime.now().isoformat()}\n")
            lines.append(f"Entries verified: {len(contributions)}\n\n")

            for contrib in contributions:
                entry_id = contrib.get("id")
                if not entry_id:
                    continue

                result = engine.verify_entry(trace, entry_id)
                lines.append(self._format_entry_verification(result))

            return "\n".join(lines)

    def _format_session_verification(self, result: dict[str, Any]) -> str:
        """Format session verification result."""
        lines = [
            f"# Session Verification: {result['session_id']}\n",
            f"Timestamp: {result['timestamp']}\n",
            f"Entries Total: {result['entries_total']}\n",
            f"Verified: {result['entries_verified']}\n",
            f"With Issues: {result['entries_with_issues']}\n",
            f"Verification Rate: {result['verification_rate']}%\n\n",
            "## Entry Results\n",
        ]

        for entry_result in result.get("entry_results", []):
            lines.append(self._format_entry_verification(entry_result))

        return "\n".join(lines)

    def _format_entry_verification(self, result: dict[str, Any]) -> str:
        """Format single entry verification result."""
        status = "✓" if result.get("verified") else "✗"
        lines = [
            f"### {status} {result['entry_id']}\n",
            f"File: {result.get('file_path', 'unknown')}\n",
            f"Checks: {result.get('checks_passed', 0)}/{result.get('checks_passed', 0) + result.get('checks_failed', 0)}\n",
        ]

        for check in result.get("results", []):
            check_status = "✓" if check.get("passed") else "✗"
            lines.append(f"- {check_status} {check.get('verification_type')}: {check.get('message')}\n")

        lines.append("\n")
        return "".join(lines)


# Module-level convenience function
def generate_trust_report(
    trace_dir: Path, trace: dict[str, Any], period: str = "30 days", format: str = "markdown"
) -> str:
    """Generate trust report using a new generator instance."""
    generator = ReportGenerator(trace_dir)
    return generator.generate_trust_report(trace, period, format)
