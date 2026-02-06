#!/usr/bin/env python3
"""
TRACE Analysis Utilities

Tools for analyzing TRACE data and generating publication-ready reports.

Usage:
    python analysis.py trace.json --report markdown
    python analysis.py trace.json --metrics code
    python analysis.py trace.json --export csv

Or import as module:
    from analysis import TRACEAnalyzer
    analyzer = TRACEAnalyzer("trace.json")
    report = analyzer.generate_full_report()
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class MetricsReport:
    """Container for computed metrics."""

    computed_at: str = field(default_factory=lambda: datetime.now().isoformat())
    code_metrics: dict[str, Any] = field(default_factory=dict)
    error_metrics: dict[str, Any] = field(default_factory=dict)
    idea_metrics: dict[str, Any] = field(default_factory=dict)
    intervention_metrics: dict[str, Any] = field(default_factory=dict)
    session_metrics: dict[str, Any] = field(default_factory=dict)
    validation_metrics: dict[str, Any] = field(default_factory=dict)
    attribution_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "computed_at": self.computed_at,
            "code_metrics": self.code_metrics,
            "error_metrics": self.error_metrics,
            "idea_metrics": self.idea_metrics,
            "intervention_metrics": self.intervention_metrics,
            "session_metrics": self.session_metrics,
            "validation_metrics": self.validation_metrics,
            "attribution_summary": self.attribution_summary,
        }


class TRACEAnalyzer:
    """Analyzer for TRACE data."""

    def __init__(self, trace_path: str | Path):
        self.trace_path = Path(trace_path)
        self.trace = self._load_trace()

    def _load_trace(self) -> dict:
        """Load TRACE data from file."""
        with open(self.trace_path, encoding="utf-8") as f:
            return json.load(f)

    # =========================================================
    # Code Metrics
    # =========================================================

    def compute_code_metrics(self) -> dict:
        """Compute detailed code contribution metrics."""
        contributions = self.trace.get("code_contributions", [])

        # Aggregate authorship
        total_ai = sum(c.get("authorship", {}).get("ai_authored_lines", 0) for c in contributions)
        total_human = sum(c.get("authorship", {}).get("human_authored_lines", 0) for c in contributions)
        total_ai_improved = sum(c.get("authorship", {}).get("ai_improved_lines", 0) for c in contributions)
        total_human_improved = sum(c.get("authorship", {}).get("human_improved_ai_lines", 0) for c in contributions)
        total_collaborative = sum(c.get("authorship", {}).get("collaborative_lines", 0) for c in contributions)

        total_lines = total_ai + total_human + total_collaborative

        # By file type
        by_extension = defaultdict(lambda: {"ai": 0, "human": 0})
        for c in contributions:
            ext = Path(c.get("file_path", "")).suffix or "unknown"
            by_extension[ext]["ai"] += c.get("authorship", {}).get("ai_authored_lines", 0)
            by_extension[ext]["human"] += c.get("authorship", {}).get("human_authored_lines", 0)

        # By contribution type
        by_type = Counter(c.get("contribution_type", "unknown") for c in contributions)

        # Quality metrics
        total_ai_errors = sum(c.get("quality", {}).get("ai_errors_caught_by_human", 0) for c in contributions)
        total_human_errors = sum(c.get("quality", {}).get("human_errors_caught_by_ai", 0) for c in contributions)

        return {
            "total_contributions": len(contributions),
            "total_lines": total_lines,
            "total_lines_by_ai": total_ai,
            "total_lines_by_human": total_human,
            "total_lines_improved_by_ai": total_ai_improved,
            "total_lines_improved_by_human": total_human_improved,
            "total_collaborative_lines": total_collaborative,
            "ai_authorship_ratio": total_ai / total_lines if total_lines > 0 else None,
            "human_authorship_ratio": total_human / total_lines if total_lines > 0 else None,
            "by_file_extension": dict(by_extension),
            "by_contribution_type": dict(by_type),
            "quality": {"ai_errors_caught_by_human": total_ai_errors, "human_errors_caught_by_ai": total_human_errors},
        }

    # =========================================================
    # Error Metrics
    # =========================================================

    def compute_error_metrics(self) -> dict:
        """Compute detailed error metrics."""
        errors = self.trace.get("errors", [])

        # By source
        ai_errors = [e for e in errors if e.get("source", {}).get("originated_from") == "ai"]
        human_errors = [e for e in errors if e.get("source", {}).get("originated_from") == "human"]

        # By detector
        ai_caught_by_human = sum(1 for e in ai_errors if e.get("detection", {}).get("detected_by") == "human")
        ai_caught_by_ai = sum(1 for e in ai_errors if e.get("detection", {}).get("detected_by") == "ai")
        ai_caught_by_test = sum(1 for e in ai_errors if e.get("detection", {}).get("detected_by") == "automated_test")

        human_caught_by_ai = sum(1 for e in human_errors if e.get("detection", {}).get("detected_by") == "ai")
        human_caught_by_human = sum(1 for e in human_errors if e.get("detection", {}).get("detected_by") == "human")

        # By type
        by_type = Counter(e.get("error_type", "unknown") for e in errors)

        # By severity
        by_severity = Counter(e.get("severity", "unknown") for e in errors)

        # Resolution
        resolved = sum(1 for e in errors if e.get("resolution", {}).get("resolved"))

        return {
            "total_errors": len(errors),
            "total_ai_errors": len(ai_errors),
            "total_human_errors": len(human_errors),
            "ai_errors_caught_by_human": ai_caught_by_human,
            "ai_errors_caught_by_ai": ai_caught_by_ai,
            "ai_errors_caught_by_test": ai_caught_by_test,
            "human_errors_caught_by_ai": human_caught_by_ai,
            "human_errors_caught_by_human": human_caught_by_human,
            "ai_error_rate": len(ai_errors) / len(errors) if errors else None,
            "human_catch_rate_for_ai": ai_caught_by_human / len(ai_errors) if ai_errors else None,
            "ai_catch_rate_for_human": human_caught_by_ai / len(human_errors) if human_errors else None,
            "by_error_type": dict(by_type),
            "by_severity": dict(by_severity),
            "resolution_rate": resolved / len(errors) if errors else None,
        }

    # =========================================================
    # Idea Metrics
    # =========================================================

    def compute_idea_metrics(self) -> dict:
        """Compute detailed idea metrics."""
        ideas = self.trace.get("ideas", [])

        # By source
        ai_ideas = [i for i in ideas if i.get("origin", {}).get("source") == "ai_suggested"]
        human_ideas = [i for i in ideas if i.get("origin", {}).get("source") == "human"]
        collab_ideas = [i for i in ideas if i.get("origin", {}).get("source") == "collaborative"]

        # AI idea outcomes
        ai_accepted = sum(1 for i in ai_ideas if i.get("outcome", {}).get("adopted") is True)
        ai_rejected = sum(1 for i in ai_ideas if i.get("outcome", {}).get("adopted") is False)
        ai_modified = sum(1 for i in ai_ideas if i.get("outcome", {}).get("modification_description"))
        ai_pending = sum(1 for i in ai_ideas if i.get("evaluation", {}).get("status") == "pending")

        # By type
        by_type = Counter(i.get("idea_type", "unknown") for i in ideas)

        # Rejection reasons
        rejection_reasons = [
            i.get("outcome", {}).get("rejection_reason")
            for i in ai_ideas
            if i.get("outcome", {}).get("rejection_reason")
        ]

        return {
            "total_ideas": len(ideas),
            "total_ai_ideas": len(ai_ideas),
            "total_human_ideas": len(human_ideas),
            "total_collaborative_ideas": len(collab_ideas),
            "ai_ideas_accepted": ai_accepted,
            "ai_ideas_rejected": ai_rejected,
            "ai_ideas_modified": ai_modified,
            "ai_ideas_pending": ai_pending,
            "ai_idea_acceptance_rate": ai_accepted / len(ai_ideas) if ai_ideas else None,
            "ai_idea_rejection_rate": ai_rejected / len(ai_ideas) if ai_ideas else None,
            "ai_idea_modification_rate": ai_modified / len(ai_ideas) if ai_ideas else None,
            "idea_contribution_ratio_ai": len(ai_ideas) / len(ideas) if ideas else None,
            "by_idea_type": dict(by_type),
            "rejection_reasons": rejection_reasons,
        }

    # =========================================================
    # Intervention Metrics
    # =========================================================

    def compute_intervention_metrics(self) -> dict:
        """Compute detailed intervention metrics."""
        interventions = self.trace.get("interventions", [])
        interactions = self.trace.get("interactions", [])

        # By type
        by_type = Counter(i.get("intervention_type", "unknown") for i in interventions)

        # By significance
        by_significance = Counter(i.get("impact", {}).get("significance", "unknown") for i in interventions)

        # Expertise applied
        all_expertise = []
        for i in interventions:
            all_expertise.extend(i.get("expertise_applied", []))
        expertise_counts = Counter(all_expertise)

        return {
            "total_interventions": len(interventions),
            "corrections": by_type.get("correction", 0),
            "overrides": by_type.get("override", 0),
            "rejections": by_type.get("rejection", 0),
            "refinements": by_type.get("refinement", 0),
            "intervention_rate": len(interventions) / len(interactions) if interactions else None,
            "by_intervention_type": dict(by_type),
            "by_significance": dict(by_significance),
            "expertise_applied": dict(expertise_counts),
        }

    # =========================================================
    # Session Metrics
    # =========================================================

    def compute_session_metrics(self) -> dict:
        """Compute detailed session metrics."""
        sessions = self.trace.get("sessions", [])
        interactions = self.trace.get("interactions", [])

        durations = [s.get("duration_minutes") for s in sessions if s.get("duration_minutes")]

        # By scientific stage
        by_stage = Counter(s.get("scientific_stage", "unknown") for s in sessions)

        # Helpfulness ratings
        ratings = [
            s.get("reflection", {}).get("ai_helpfulness_rating")
            for s in sessions
            if s.get("reflection", {}).get("ai_helpfulness_rating")
        ]

        return {
            "total_sessions": len(sessions),
            "completed_sessions": sum(1 for s in sessions if s.get("ended")),
            "total_time_minutes": sum(durations) if durations else 0,
            "total_time_hours": sum(durations) / 60 if durations else 0,
            "avg_session_duration_minutes": sum(durations) / len(durations) if durations else None,
            "avg_interactions_per_session": len(interactions) / len(sessions) if sessions else None,
            "by_scientific_stage": dict(by_stage),
            "ai_helpfulness_ratings": ratings,
            "avg_ai_helpfulness": sum(ratings) / len(ratings) if ratings else None,
        }

    # =========================================================
    # Validation Metrics
    # =========================================================

    def compute_validation_metrics(self) -> dict:
        """Compute validation metrics."""
        validations = self.trace.get("validations", [])

        by_result = Counter(v.get("result", "unknown") for v in validations)
        by_method = Counter(v.get("method", "unknown") for v in validations)

        return {
            "total_validations": len(validations),
            "passed": by_result.get("passed", 0),
            "failed": by_result.get("failed", 0),
            "inconclusive": by_result.get("inconclusive", 0),
            "pass_rate": by_result.get("passed", 0) / len(validations) if validations else None,
            "by_validation_method": dict(by_method),
        }

    # =========================================================
    # Attribution Summary
    # =========================================================

    def compute_attribution_summary(self) -> dict:
        """Compute overall attribution summary."""
        attributions = self.trace.get("attributions", [])
        code_metrics = self.compute_code_metrics()
        idea_metrics = self.compute_idea_metrics()

        ai_percentages = [
            a.get("ai_contribution", {}).get("percentage_estimate", 0)
            for a in attributions
            if a.get("ai_contribution", {}).get("percentage_estimate")
        ]

        return {
            "total_attributions": len(attributions),
            "avg_ai_contribution_percentage": sum(ai_percentages) / len(ai_percentages) if ai_percentages else None,
            "code_ai_ratio": code_metrics.get("ai_authorship_ratio"),
            "idea_ai_ratio": idea_metrics.get("idea_contribution_ratio_ai"),
            "summary": {
                "total_ai_code_lines": code_metrics.get("total_lines_by_ai", 0),
                "total_human_code_lines": code_metrics.get("total_lines_by_human", 0),
                "total_ai_ideas": idea_metrics.get("total_ai_ideas", 0),
                "total_human_ideas": idea_metrics.get("total_human_ideas", 0),
            },
        }

    # =========================================================
    # Full Report
    # =========================================================

    def generate_full_report(self) -> MetricsReport:
        """Generate comprehensive metrics report."""
        report = MetricsReport()
        report.code_metrics = self.compute_code_metrics()
        report.error_metrics = self.compute_error_metrics()
        report.idea_metrics = self.compute_idea_metrics()
        report.intervention_metrics = self.compute_intervention_metrics()
        report.session_metrics = self.compute_session_metrics()
        report.validation_metrics = self.compute_validation_metrics()
        report.attribution_summary = self.compute_attribution_summary()
        return report

    # =========================================================
    # Export Functions
    # =========================================================

    def export_to_markdown(self) -> str:
        """Export report as markdown."""
        report = self.generate_full_report()
        project = self.trace.get("metadata", {}).get("project", "Unknown")

        md = f"""# TRACE Analysis Report: {project}

**Generated**: {report.computed_at}
**TRACE Version**: {self.trace.get("schema_version", "unknown")}

---

## Executive Summary

| Category | Key Metric | Value |
|----------|-----------|-------|
| Code | AI-authored lines | {report.code_metrics.get("total_lines_by_ai", 0)} |
| Code | Human-authored lines | {report.code_metrics.get("total_lines_by_human", 0)} |
| Code | AI authorship ratio | {self._fmt_pct(report.code_metrics.get("ai_authorship_ratio"))} |
| Ideas | AI ideas proposed | {report.idea_metrics.get("total_ai_ideas", 0)} |
| Ideas | AI idea acceptance rate | {self._fmt_pct(report.idea_metrics.get("ai_idea_acceptance_rate"))} |
| Errors | AI errors caught by human | {report.error_metrics.get("ai_errors_caught_by_human", 0)} |
| Errors | Human errors caught by AI | {report.error_metrics.get("human_errors_caught_by_ai", 0)} |
| Interventions | Total interventions | {report.intervention_metrics.get("total_interventions", 0)} |
| Sessions | Total time (hours) | {report.session_metrics.get("total_time_hours", 0):.1f} |

---

## Code Contribution Analysis

### Authorship Breakdown

| Metric | Value |
|--------|-------|
| Total code contributions | {report.code_metrics.get("total_contributions", 0)} |
| Total lines | {report.code_metrics.get("total_lines", 0)} |
| AI-authored lines | {report.code_metrics.get("total_lines_by_ai", 0)} |
| Human-authored lines | {report.code_metrics.get("total_lines_by_human", 0)} |
| Collaborative lines | {report.code_metrics.get("total_collaborative_lines", 0)} |
| AI authorship ratio | {self._fmt_pct(report.code_metrics.get("ai_authorship_ratio"))} |

### Code Improvements

| Metric | Value |
|--------|-------|
| Lines improved by AI | {report.code_metrics.get("total_lines_improved_by_ai", 0)} |
| AI lines improved by human | {report.code_metrics.get("total_lines_improved_by_human", 0)} |

### By Contribution Type

{self._dict_to_md_table(report.code_metrics.get("by_contribution_type", {}))}

---

## Error Analysis

### Error Distribution

| Metric | Value |
|--------|-------|
| Total errors | {report.error_metrics.get("total_errors", 0)} |
| AI-originated errors | {report.error_metrics.get("total_ai_errors", 0)} |
| Human-originated errors | {report.error_metrics.get("total_human_errors", 0)} |
| AI error rate | {self._fmt_pct(report.error_metrics.get("ai_error_rate"))} |

### Error Detection

| Metric | Value |
|--------|-------|
| AI errors caught by human | {report.error_metrics.get("ai_errors_caught_by_human", 0)} |
| AI errors caught by AI | {report.error_metrics.get("ai_errors_caught_by_ai", 0)} |
| AI errors caught by tests | {report.error_metrics.get("ai_errors_caught_by_test", 0)} |
| Human errors caught by AI | {report.error_metrics.get("human_errors_caught_by_ai", 0)} |

### Catch Rates

| Metric | Value |
|--------|-------|
| Human catch rate for AI errors | {self._fmt_pct(report.error_metrics.get("human_catch_rate_for_ai"))} |
| AI catch rate for human errors | {self._fmt_pct(report.error_metrics.get("ai_catch_rate_for_human"))} |
| Resolution rate | {self._fmt_pct(report.error_metrics.get("resolution_rate"))} |

---

## Idea Provenance

### Idea Origins

| Metric | Value |
|--------|-------|
| Total ideas | {report.idea_metrics.get("total_ideas", 0)} |
| AI-suggested ideas | {report.idea_metrics.get("total_ai_ideas", 0)} |
| Human ideas | {report.idea_metrics.get("total_human_ideas", 0)} |
| Collaborative ideas | {report.idea_metrics.get("total_collaborative_ideas", 0)} |
| AI idea contribution ratio | {self._fmt_pct(report.idea_metrics.get("idea_contribution_ratio_ai"))} |

### AI Idea Outcomes

| Metric | Value |
|--------|-------|
| Accepted | {report.idea_metrics.get("ai_ideas_accepted", 0)} |
| Rejected | {report.idea_metrics.get("ai_ideas_rejected", 0)} |
| Modified | {report.idea_metrics.get("ai_ideas_modified", 0)} |
| Pending | {report.idea_metrics.get("ai_ideas_pending", 0)} |
| Acceptance rate | {self._fmt_pct(report.idea_metrics.get("ai_idea_acceptance_rate"))} |
| Rejection rate | {self._fmt_pct(report.idea_metrics.get("ai_idea_rejection_rate"))} |

---

## Human Interventions

### Intervention Summary

| Metric | Value |
|--------|-------|
| Total interventions | {report.intervention_metrics.get("total_interventions", 0)} |
| Corrections | {report.intervention_metrics.get("corrections", 0)} |
| Overrides | {report.intervention_metrics.get("overrides", 0)} |
| Rejections | {report.intervention_metrics.get("rejections", 0)} |
| Refinements | {report.intervention_metrics.get("refinements", 0)} |
| Intervention rate | {self._fmt_pct(report.intervention_metrics.get("intervention_rate"))} |

### By Significance

{self._dict_to_md_table(report.intervention_metrics.get("by_significance", {}))}

### Expertise Applied

{self._dict_to_md_table(report.intervention_metrics.get("expertise_applied", {}))}

---

## Session Analysis

### Overview

| Metric | Value |
|--------|-------|
| Total sessions | {report.session_metrics.get("total_sessions", 0)} |
| Completed sessions | {report.session_metrics.get("completed_sessions", 0)} |
| Total time (minutes) | {report.session_metrics.get("total_time_minutes", 0)} |
| Total time (hours) | {report.session_metrics.get("total_time_hours", 0):.1f} |
| Avg session duration (min) | {self._fmt_num(report.session_metrics.get("avg_session_duration_minutes"))} |
| Avg AI helpfulness (1-5) | {self._fmt_num(report.session_metrics.get("avg_ai_helpfulness"))} |

### By Scientific Stage

{self._dict_to_md_table(report.session_metrics.get("by_scientific_stage", {}))}

---

## Publication-Ready Statement

> **AI Assistance Disclosure**
>
> This research utilized AI assistance documented via the TRACE protocol (v1.0).
> Over {report.session_metrics.get("total_sessions", 0)} sessions totaling {report.session_metrics.get("total_time_hours", 0):.1f} hours,
> the AI assistant contributed {self._fmt_pct(report.code_metrics.get("ai_authorship_ratio"))} of code
> ({report.code_metrics.get("total_lines_by_ai", 0)} lines) and proposed
> {report.idea_metrics.get("total_ai_ideas", 0)} ideas
> ({self._fmt_pct(report.idea_metrics.get("ai_idea_acceptance_rate"))} acceptance rate).
> Human researchers caught {report.error_metrics.get("ai_errors_caught_by_human", 0)} AI-generated errors
> and made {report.intervention_metrics.get("total_interventions", 0)} interventions to AI output.
> Full TRACE logs are available in the supplementary materials.

---

*Report generated by TRACE Analysis Utilities*
"""
        return md

    def export_to_csv(self, output_dir: Path) -> list[str]:
        """Export all data to CSV files."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        files_created = []

        # Export each category
        categories = [
            ("sessions", self.trace.get("sessions", [])),
            ("code_contributions", self.trace.get("code_contributions", [])),
            ("ideas", self.trace.get("ideas", [])),
            ("errors", self.trace.get("errors", [])),
            ("interventions", self.trace.get("interventions", [])),
            ("decisions", self.trace.get("decisions", [])),
            ("learnings", self.trace.get("learnings", [])),
        ]

        for name, data in categories:
            if not data:
                continue

            filepath = output_dir / f"{name}.csv"
            self._write_csv(filepath, data)
            files_created.append(str(filepath))

        # Export summary metrics
        report = self.generate_full_report()
        metrics_path = output_dir / "metrics_summary.json"
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2)
        files_created.append(str(metrics_path))

        return files_created

    def _write_csv(self, filepath: Path, data: list[dict]) -> None:
        """Write list of dicts to CSV, flattening nested structures."""
        if not data:
            return

        # Flatten nested dicts
        flat_data = [self._flatten_dict(d) for d in data]

        # Get all keys
        all_keys = set()
        for d in flat_data:
            all_keys.update(d.keys())
        fieldnames = sorted(all_keys)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(flat_data)

    def _flatten_dict(self, d: dict, parent_key: str = "", sep: str = ".") -> dict:
        """Flatten nested dictionary."""
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep).items())
            elif isinstance(v, list):
                items.append((new_key, json.dumps(v)))
            else:
                items.append((new_key, v))
        return dict(items)

    def _fmt_pct(self, value: float | None) -> str:
        """Format as percentage."""
        if value is None:
            return "N/A"
        return f"{value * 100:.1f}%"

    def _fmt_num(self, value: float | None) -> str:
        """Format number."""
        if value is None:
            return "N/A"
        return f"{value:.1f}"

    def _dict_to_md_table(self, d: dict) -> str:
        """Convert dict to markdown table."""
        if not d:
            return "*No data*"
        lines = ["| Key | Value |", "|-----|-------|"]
        for k, v in sorted(d.items()):
            lines.append(f"| {k} | {v} |")
        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Analyze TRACE data")
    parser.add_argument("trace_path", help="Path to trace.json")
    parser.add_argument("--report", choices=["json", "markdown"], help="Generate full report")
    parser.add_argument(
        "--metrics",
        choices=["code", "errors", "ideas", "interventions", "sessions", "all"],
        help="Show specific metrics",
    )
    parser.add_argument("--export", choices=["csv"], help="Export data format")
    parser.add_argument("-o", "--output", help="Output path")

    args = parser.parse_args()

    analyzer = TRACEAnalyzer(args.trace_path)

    if args.report:
        if args.report == "markdown":
            output = analyzer.export_to_markdown()
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(output)
                print(f"Report written to: {args.output}")
            else:
                print(output)
        elif args.report == "json":
            report = analyzer.generate_full_report()
            output = json.dumps(report.to_dict(), indent=2)
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(output)
                print(f"Report written to: {args.output}")
            else:
                print(output)

    elif args.metrics:
        if args.metrics == "code":
            print(json.dumps(analyzer.compute_code_metrics(), indent=2))
        elif args.metrics == "errors":
            print(json.dumps(analyzer.compute_error_metrics(), indent=2))
        elif args.metrics == "ideas":
            print(json.dumps(analyzer.compute_idea_metrics(), indent=2))
        elif args.metrics == "interventions":
            print(json.dumps(analyzer.compute_intervention_metrics(), indent=2))
        elif args.metrics == "sessions":
            print(json.dumps(analyzer.compute_session_metrics(), indent=2))
        elif args.metrics == "all":
            report = analyzer.generate_full_report()
            print(json.dumps(report.to_dict(), indent=2))

    elif args.export == "csv":
        output_dir = Path(args.output or "trace_export")
        files = analyzer.export_to_csv(output_dir)
        print(f"Exported {len(files)} files to: {output_dir}")
        for f in files:
            print(f"  - {f}")

    else:
        # Default: show summary
        report = analyzer.generate_full_report()
        print(f"TRACE Summary for: {analyzer.trace.get('metadata', {}).get('project', 'Unknown')}")
        print(f"Sessions: {report.session_metrics.get('total_sessions', 0)}")
        print(f"Code contributions: {report.code_metrics.get('total_contributions', 0)}")
        print(f"AI-authored lines: {report.code_metrics.get('total_lines_by_ai', 0)}")
        print(f"Human-authored lines: {report.code_metrics.get('total_lines_by_human', 0)}")
        print(f"AI ideas: {report.idea_metrics.get('total_ai_ideas', 0)}")
        print(f"Interventions: {report.intervention_metrics.get('total_interventions', 0)}")


if __name__ == "__main__":
    main()
