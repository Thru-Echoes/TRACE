"""
TRACE Protocol Evaluation Logging

Logs test results, benchmarks, and other evaluations.
"""

from datetime import datetime
from typing import Any

from .types import EvaluationScope, EvaluationType


class EvaluationLogger:
    """Logs evaluation results (tests, benchmarks, etc.)."""

    def __init__(self, trace: dict[str, Any]):
        """
        Initialize evaluation logger.

        Args:
            trace: The trace data dictionary
        """
        self.trace = trace
        if "evaluations" not in self.trace:
            self.trace["evaluations"] = []

    def log_evaluation(
        self,
        evaluation_type: str | EvaluationType,
        session_id: str | None = None,
        target_files: list[str] | None = None,
        target_scope: str | EvaluationScope | None = None,
        target_description: str | None = None,
        metrics: dict[str, Any] | None = None,
        tool: str | None = None,
        command: str | None = None,
        output_summary: str | None = None,
        passed: bool | None = None,
    ) -> dict[str, Any]:
        """
        Log an evaluation result.

        Args:
            evaluation_type: Type of evaluation (unit_test, benchmark, etc.)
            session_id: Parent session ID
            target_files: Files being evaluated
            target_scope: Scope of evaluation (function, module, system)
            target_description: Description of what's being evaluated
            metrics: Evaluation metrics (tests_passed, coverage, etc.)
            tool: Tool used (pytest, jest, etc.)
            command: Command used to run evaluation
            output_summary: Summary of output
            passed: Whether evaluation passed overall

        Returns:
            The created evaluation entry
        """
        eval_type = evaluation_type.value if isinstance(evaluation_type, EvaluationType) else evaluation_type
        scope = target_scope.value if isinstance(target_scope, EvaluationScope) else target_scope

        eval_id = self._generate_id()

        entry: dict[str, Any] = {
            "id": eval_id,
            "timestamp": datetime.now().isoformat(),
            "evaluation_type": eval_type,
        }

        if session_id:
            entry["session_id"] = session_id

        # Build target
        target = {}
        if target_files:
            target["files"] = target_files
        if scope:
            target["scope"] = scope
        if target_description:
            target["description"] = target_description
        if target:
            entry["target"] = target

        # Add metrics
        if metrics:
            entry["metrics"] = metrics

        if tool:
            entry["tool"] = tool
        if command:
            entry["command"] = command
        if output_summary:
            entry["output_summary"] = output_summary
        if passed is not None:
            entry["passed"] = passed

        self.trace["evaluations"].append(entry)
        return entry

    def log_test_run(
        self,
        tests_passed: int,
        tests_failed: int,
        tests_skipped: int = 0,
        coverage: float | None = None,
        duration_ms: int | None = None,
        tool: str = "pytest",
        session_id: str | None = None,
        target_files: list[str] | None = None,
        output_summary: str | None = None,
    ) -> dict[str, Any]:
        """
        Convenience method to log a test run.

        Args:
            tests_passed: Number of tests passed
            tests_failed: Number of tests failed
            tests_skipped: Number of tests skipped
            coverage: Code coverage (0-1)
            duration_ms: Duration in milliseconds
            tool: Testing tool used
            session_id: Parent session ID
            target_files: Files being tested
            output_summary: Summary of test output

        Returns:
            The created evaluation entry
        """
        metrics: dict[str, Any] = {
            "tests_passed": tests_passed,
            "tests_failed": tests_failed,
            "tests_skipped": tests_skipped,
        }

        if coverage is not None:
            metrics["coverage"] = coverage
        if duration_ms is not None:
            metrics["duration_ms"] = duration_ms

        return self.log_evaluation(
            evaluation_type=EvaluationType.UNIT_TEST,
            session_id=session_id,
            target_files=target_files,
            metrics=metrics,
            tool=tool,
            output_summary=output_summary,
            passed=(tests_failed == 0),
        )

    def log_benchmark(
        self,
        score: float,
        metric_name: str = "score",
        duration_ms: int | None = None,
        tool: str | None = None,
        session_id: str | None = None,
        target_description: str | None = None,
        output_summary: str | None = None,
    ) -> dict[str, Any]:
        """
        Convenience method to log a benchmark result.

        Args:
            score: Benchmark score
            metric_name: Name of the metric
            duration_ms: Duration in milliseconds
            tool: Benchmarking tool used
            session_id: Parent session ID
            target_description: Description of what was benchmarked
            output_summary: Summary of benchmark output

        Returns:
            The created evaluation entry
        """
        metrics: dict[str, Any] = {
            metric_name: score,
        }

        if duration_ms is not None:
            metrics["duration_ms"] = duration_ms

        return self.log_evaluation(
            evaluation_type=EvaluationType.BENCHMARK,
            session_id=session_id,
            target_description=target_description,
            metrics=metrics,
            tool=tool,
            output_summary=output_summary,
        )

    def log_code_review(
        self,
        target_files: list[str],
        passed: bool,
        issues_found: int = 0,
        reviewer: str = "human",
        session_id: str | None = None,
        output_summary: str | None = None,
    ) -> dict[str, Any]:
        """
        Log a code review result.

        Args:
            target_files: Files reviewed
            passed: Whether review passed
            issues_found: Number of issues found
            reviewer: Who reviewed (human, ai, tool)
            session_id: Parent session ID
            output_summary: Summary of review

        Returns:
            The created evaluation entry
        """
        return self.log_evaluation(
            evaluation_type=EvaluationType.CODE_REVIEW,
            session_id=session_id,
            target_files=target_files,
            metrics={
                "issues_found": issues_found,
                "reviewer": reviewer,
            },
            passed=passed,
            output_summary=output_summary,
        )

    def _generate_id(self) -> str:
        """Generate unique evaluation ID."""
        existing_ids = {e.get("id", "") for e in self.trace.get("evaluations", [])}
        counter = 1
        while True:
            eval_id = f"EVAL{counter:03d}"
            if eval_id not in existing_ids:
                return eval_id
            counter += 1

    def get_evaluation_summary(self) -> dict[str, Any]:
        """Get summary of all evaluations."""
        evaluations = self.trace.get("evaluations", [])

        by_type: dict[str, int] = {}
        passed_count = 0
        failed_count = 0
        total_tests_passed = 0
        total_tests_failed = 0

        for eval_entry in evaluations:
            eval_type = eval_entry.get("evaluation_type", "unknown")
            by_type[eval_type] = by_type.get(eval_type, 0) + 1

            if eval_entry.get("passed") is True:
                passed_count += 1
            elif eval_entry.get("passed") is False:
                failed_count += 1

            metrics = eval_entry.get("metrics", {})
            total_tests_passed += metrics.get("tests_passed", 0)
            total_tests_failed += metrics.get("tests_failed", 0)

        return {
            "total_evaluations": len(evaluations),
            "by_type": by_type,
            "passed": passed_count,
            "failed": failed_count,
            "total_tests_passed": total_tests_passed,
            "total_tests_failed": total_tests_failed,
        }


def log_evaluation(trace: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Convenience function to log an evaluation."""
    logger = EvaluationLogger(trace)
    return logger.log_evaluation(**kwargs)
