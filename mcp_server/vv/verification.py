"""
TRACE V&V Change Verification Engine

Compares actual file diffs to TRACE-logged claims.
Provides tolerance-based verification with configurable thresholds.
"""

import difflib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .snapshots import SnapshotManager


class VerificationResult:
    """Result of a verification check."""

    def __init__(
        self,
        entry_id: str,
        verification_type: str,
        passed: bool,
        claimed: Any,
        actual: Any,
        tolerance: float,
        difference: float,
        message: str,
        severity: str = "info",
    ):
        self.entry_id = entry_id
        self.verification_type = verification_type
        self.passed = passed
        self.claimed = claimed
        self.actual = actual
        self.tolerance = tolerance
        self.difference = difference
        self.message = message
        self.severity = severity  # info, warning, error
        self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "verification_type": self.verification_type,
            "passed": self.passed,
            "claimed": self.claimed,
            "actual": self.actual,
            "tolerance": self.tolerance,
            "difference": self.difference,
            "message": self.message,
            "severity": self.severity,
            "timestamp": self.timestamp,
        }


class VerificationEngine:
    """Engine for verifying TRACE claims against actual changes."""

    def __init__(self, trace_dir: Path, tolerance_percent: float = 5.0, tolerance_lines: int = 2):
        """
        Initialize the verification engine.

        Args:
            trace_dir: Path to the .trace directory
            tolerance_percent: Percentage tolerance for line/word counts (default: 5%)
            tolerance_lines: Absolute tolerance for small counts (default: 2 lines)
        """
        self.trace_dir = Path(trace_dir)
        self.verifications_dir = self.trace_dir / "verifications"
        self.verifications_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_manager = SnapshotManager(trace_dir)
        self.tolerance_percent = tolerance_percent
        self.tolerance_lines = tolerance_lines

    def _within_tolerance(self, claimed: int, actual: int) -> tuple[bool, float]:
        """
        Check if actual value is within tolerance of claimed value.

        Returns:
            Tuple of (within_tolerance, difference_percent)
        """
        if claimed == 0 and actual == 0:
            return True, 0.0

        if claimed == 0:
            return actual <= self.tolerance_lines, 100.0

        difference = abs(actual - claimed)
        difference_percent = (difference / claimed) * 100

        # Use absolute tolerance for small counts, percentage for larger
        if claimed <= 10:
            within = difference <= self.tolerance_lines
        else:
            within = difference_percent <= self.tolerance_percent or difference <= self.tolerance_lines

        return within, round(difference_percent, 2)

    def _count_diff_lines(self, before: str, after: str) -> dict[str, int]:
        """
        Count lines added, removed, and modified between two versions.

        Returns:
            Dict with 'added', 'removed', 'modified' counts
        """
        before_lines = before.splitlines(keepends=True) if before else []
        after_lines = after.splitlines(keepends=True) if after else []

        diff = list(difflib.unified_diff(before_lines, after_lines, lineterm=""))

        added = 0
        removed = 0

        for line in diff[2:]:  # Skip headers
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1

        return {"added": added, "removed": removed, "net_change": added - removed, "total_changed": added + removed}

    def _count_words(self, text: str) -> int:
        """Count words in text."""
        return len(text.split())

    def _count_word_diff(self, before: str, after: str) -> dict[str, int]:
        """Count word-level changes between two versions."""
        before_words = set(before.split()) if before else set()
        after_words = set(after.split()) if after else set()

        added = len(after_words - before_words)
        removed = len(before_words - after_words)

        return {"words_added": added, "words_removed": removed, "net_word_change": added - removed}

    def verify_entry(self, trace: dict[str, Any], entry_id: str, pre_snapshot_id: str | None = None) -> dict[str, Any]:
        """
        Verify a single TRACE entry against actual file changes.

        Args:
            trace: The TRACE data
            entry_id: ID of the entry to verify (e.g., "CC001")
            pre_snapshot_id: Optional snapshot ID taken before the change

        Returns:
            Verification report
        """
        results = []
        entry = None
        entry_type = None

        # Find the entry
        for category in ["code_contributions", "ai_suggestions"]:
            for e in trace.get(category, []):
                if e.get("id") == entry_id:
                    entry = e
                    entry_type = category
                    break
            if entry:
                break

        if not entry:
            return {"entry_id": entry_id, "verified": False, "error": f"Entry {entry_id} not found", "results": []}

        # Get file path and content type
        file_path = entry.get("file_path")
        content_type = entry.get("content_type", "code")

        # For AI suggestions, file_path is optional (in suggestion.scope.files_affected)
        if entry_type == "ai_suggestions":
            # Verify suggestion without requiring file_path
            results.extend(self._verify_ai_suggestion(entry, None, None))
        elif not file_path:
            return {"entry_id": entry_id, "verified": False, "error": "No file_path in entry", "results": []}
        else:
            file_path = Path(file_path)
            if not file_path.is_absolute():
                file_path = self.trace_dir.parent / file_path

            # Get pre-snapshot if available
            pre_content = None
            if pre_snapshot_id:
                pre_content_bytes = self.snapshot_manager.get_snapshot_file_content(pre_snapshot_id, str(file_path))
                if pre_content_bytes:
                    pre_content = pre_content_bytes.decode("utf-8", errors="replace")

            # Get current file content
            post_content = None
            if file_path.exists():
                with open(file_path, encoding="utf-8", errors="replace") as f:
                    post_content = f.read()

            # Verify code contribution
            if entry_type == "code_contributions":
                results.extend(self._verify_code_contribution(entry, pre_content, post_content, content_type))

        # Compute overall verification status
        failed_checks = [r for r in results if not r.passed]
        warning_checks = [r for r in results if r.severity == "warning"]
        error_checks = [r for r in results if r.severity == "error"]

        verification = {
            "entry_id": entry_id,
            "entry_type": entry_type,
            "file_path": str(file_path) if file_path else None,
            "verified": len(error_checks) == 0,
            "timestamp": datetime.now().isoformat(),
            "pre_snapshot_id": pre_snapshot_id,
            "checks_passed": len(results) - len(failed_checks),
            "checks_failed": len(failed_checks),
            "warnings": len(warning_checks),
            "errors": len(error_checks),
            "results": [r.to_dict() for r in results],
            "tolerance": {"percent": self.tolerance_percent, "lines": self.tolerance_lines},
        }

        # Save verification result
        self._save_verification(verification)

        return verification

    def _verify_code_contribution(
        self, entry: dict[str, Any], pre_content: str | None, post_content: str | None, content_type: str
    ) -> list[VerificationResult]:
        """Verify a code contribution entry."""
        results = []
        entry_id = entry.get("id", "unknown")
        authorship = entry.get("authorship", {})

        # Calculate actual diff if we have both versions
        if pre_content is not None and post_content is not None:
            diff_stats = self._count_diff_lines(pre_content, post_content)

            # Sum up all claimed lines
            claimed_lines = 0
            for category in ["human_directed", "ai_suggested", "human_manual_edit", "collaborative"]:
                cat_data = authorship.get(category, {})
                if isinstance(cat_data, dict):
                    claimed_lines += cat_data.get("ai_executed_lines", 0)
                    claimed_lines += cat_data.get("human_executed_lines", 0)
                    claimed_lines += cat_data.get("accepted_lines", 0)
                    claimed_lines += cat_data.get("modified_lines", 0)
                    claimed_lines += cat_data.get("lines_added", 0)
                    claimed_lines += cat_data.get("lines", 0)

            # Verify total line count
            actual_total = diff_stats["total_changed"]
            within, diff_pct = self._within_tolerance(claimed_lines, actual_total)

            results.append(
                VerificationResult(
                    entry_id=entry_id,
                    verification_type="line_count",
                    passed=within,
                    claimed=claimed_lines,
                    actual=actual_total,
                    tolerance=self.tolerance_percent,
                    difference=diff_pct,
                    message=f"Line count {'matches' if within else 'differs'}: claimed {claimed_lines}, actual {actual_total} ({diff_pct}% diff)",
                    severity="info" if within else "warning",
                )
            )

            # For text content, also verify word count
            if content_type == "text":
                word_diff = self._count_word_diff(pre_content, post_content)

                # Sum claimed words
                claimed_words = 0
                for category in ["human_directed", "ai_suggested", "human_manual_edit", "collaborative"]:
                    cat_data = authorship.get(category, {})
                    if isinstance(cat_data, dict):
                        for key in cat_data:
                            if "words" in key.lower():
                                claimed_words += cat_data.get(key, 0)

                actual_words = word_diff["words_added"]
                within_words, word_diff_pct = self._within_tolerance(claimed_words, actual_words)

                results.append(
                    VerificationResult(
                        entry_id=entry_id,
                        verification_type="word_count",
                        passed=within_words,
                        claimed=claimed_words,
                        actual=actual_words,
                        tolerance=self.tolerance_percent,
                        difference=word_diff_pct,
                        message=f"Word count {'matches' if within_words else 'differs'}: claimed {claimed_words}, actual {actual_words}",
                        severity="info" if within_words else "warning",
                    )
                )

        # Verify file exists
        file_path = entry.get("file_path")
        if file_path:
            file_exists = (
                (self.trace_dir.parent / file_path).exists()
                if not Path(file_path).is_absolute()
                else Path(file_path).exists()
            )
            results.append(
                VerificationResult(
                    entry_id=entry_id,
                    verification_type="file_exists",
                    passed=file_exists,
                    claimed=True,
                    actual=file_exists,
                    tolerance=0,
                    difference=0 if file_exists else 100,
                    message=f"File {'exists' if file_exists else 'not found'}: {file_path}",
                    severity="info" if file_exists else "error",
                )
            )

        # Verify timestamp ordering
        timestamp = entry.get("timestamp")
        if timestamp:
            try:
                entry_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                now = datetime.now(entry_time.tzinfo) if entry_time.tzinfo else datetime.now()
                is_valid = entry_time <= now
                results.append(
                    VerificationResult(
                        entry_id=entry_id,
                        verification_type="timestamp_valid",
                        passed=is_valid,
                        claimed=timestamp,
                        actual=now.isoformat(),
                        tolerance=0,
                        difference=0,
                        message=f"Timestamp {'valid' if is_valid else 'in future (invalid)'}",
                        severity="info" if is_valid else "error",
                    )
                )
            except (ValueError, TypeError):
                pass

        return results

    def _verify_ai_suggestion(
        self, entry: dict[str, Any], pre_content: str | None, post_content: str | None
    ) -> list[VerificationResult]:
        """Verify an AI suggestion entry."""
        results = []
        entry_id = entry.get("id", "unknown")

        suggestion = entry.get("suggestion", {})
        outcome = entry.get("outcome", {})

        # Check scope for lines_proposed
        scope = suggestion.get("scope", {})
        lines_proposed = scope.get("lines_proposed", 0) or suggestion.get("lines_proposed", 0)

        # Check outcome for line counts (both old and new formats)
        lines_final = outcome.get("lines_final", {})
        lines_accepted = lines_final.get("accepted_as_is", 0) or outcome.get("lines_accepted_as_is", 0)
        lines_modified = lines_final.get("modified_by_human", 0) or outcome.get("lines_modified", 0)
        lines_rejected = lines_final.get("rejected", 0) or outcome.get("lines_rejected", 0)
        total_outcome = lines_accepted + lines_modified + lines_rejected

        # Always add a balance check for suggestions
        if lines_proposed > 0 or total_outcome > 0:
            within, diff_pct = self._within_tolerance(lines_proposed, total_outcome)
            results.append(
                VerificationResult(
                    entry_id=entry_id,
                    verification_type="suggestion_lines_balance",
                    passed=within,
                    claimed=lines_proposed,
                    actual=total_outcome,
                    tolerance=self.tolerance_percent,
                    difference=diff_pct,
                    message=f"Suggestion lines {'balance' if within else 'mismatch'}: proposed {lines_proposed}, outcome total {total_outcome}",
                    severity="info" if within else "warning",
                )
            )
        else:
            # No lines info available
            results.append(
                VerificationResult(
                    entry_id=entry_id,
                    verification_type="suggestion_lines_balance",
                    passed=True,
                    claimed=0,
                    actual=0,
                    tolerance=self.tolerance_percent,
                    difference=0,
                    message="No line count information available for balance check",
                    severity="info",
                )
            )

        # Verify outcome status is set if resolved
        status = outcome.get("status", "pending")
        if status != "pending":
            has_rationale = bool(outcome.get("human_rationale"))
            results.append(
                VerificationResult(
                    entry_id=entry_id,
                    verification_type="outcome_documented",
                    passed=has_rationale,
                    claimed=True,
                    actual=has_rationale,
                    tolerance=0,
                    difference=0 if has_rationale else 100,
                    message=f"Outcome rationale {'provided' if has_rationale else 'missing'}",
                    severity="info" if has_rationale else "warning",
                )
            )

        return results

    def verify_session(self, trace: dict[str, Any], session_id: str) -> dict[str, Any]:
        """
        Verify all entries from a session.

        Args:
            trace: The TRACE data
            session_id: Session ID to verify

        Returns:
            Session verification report
        """
        session_entries = []

        # Collect all entries from this session
        for category in ["code_contributions", "ai_suggestions"]:
            for entry in trace.get(category, []):
                if entry.get("session_id") == session_id:
                    session_entries.append(
                        {"id": entry.get("id"), "category": category, "snapshot_id": entry.get("pre_snapshot_id")}
                    )

        # Verify each entry
        entry_results = []
        for entry_info in session_entries:
            result = self.verify_entry(trace, entry_info["id"], entry_info.get("snapshot_id"))
            entry_results.append(result)

        # Compute session-level summary
        total_checks = sum(r.get("checks_passed", 0) + r.get("checks_failed", 0) for r in entry_results)
        passed_checks = sum(r.get("checks_passed", 0) for r in entry_results)
        entries_verified = sum(1 for r in entry_results if r.get("verified", False))
        entries_failed = len(entry_results) - entries_verified

        return {
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "entries_total": len(session_entries),
            "entries_verified": entries_verified,
            "entries_with_issues": entries_failed,
            "total_checks": total_checks,
            "checks_passed": passed_checks,
            "checks_failed": total_checks - passed_checks,
            "verification_rate": round(passed_checks / total_checks * 100, 1) if total_checks > 0 else 100,
            "entry_results": entry_results,
        }

    def _save_verification(self, verification: dict[str, Any]) -> None:
        """Save a verification result."""
        entry_id = verification.get("entry_id", "unknown")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"verify_{entry_id}_{timestamp}.json"

        with open(self.verifications_dir / filename, "w", encoding="utf-8") as f:
            json.dump(verification, f, indent=2)

    def get_verification_history(self, entry_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Get verification history for an entry or all entries."""
        results = []

        for vfile in sorted(self.verifications_dir.glob("verify_*.json"), reverse=True):
            if len(results) >= limit:
                break

            with open(vfile, encoding="utf-8") as f:
                verification = json.load(f)

            if entry_id and verification.get("entry_id") != entry_id:
                continue

            results.append(
                {
                    "entry_id": verification.get("entry_id"),
                    "timestamp": verification.get("timestamp"),
                    "verified": verification.get("verified"),
                    "checks_passed": verification.get("checks_passed"),
                    "checks_failed": verification.get("checks_failed"),
                    "file": vfile.name,
                }
            )

        return results


# Module-level convenience functions
def verify_entry(trace_dir: Path, trace: dict[str, Any], entry_id: str, **kwargs) -> dict[str, Any]:
    """Verify an entry using a new engine instance."""
    engine = VerificationEngine(trace_dir)
    return engine.verify_entry(trace, entry_id, **kwargs)


def verify_session(trace_dir: Path, trace: dict[str, Any], session_id: str) -> dict[str, Any]:
    """Verify a session using a new engine instance."""
    engine = VerificationEngine(trace_dir)
    return engine.verify_session(trace, session_id)
