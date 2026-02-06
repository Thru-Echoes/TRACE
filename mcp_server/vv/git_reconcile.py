"""
TRACE V&V Git Cross-Validation

Reconciles TRACE logs with git history to detect:
- Unlogged changes (in git, not in TRACE)
- Phantom entries (in TRACE, not in git)
- Timestamp/authorship mismatches
"""

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


class GitReconciler:
    """Reconciles TRACE logs with git history."""

    HUMAN_EDIT_TAG = "[HUMAN-EDIT]"

    def __init__(self, project_dir: Path):
        """
        Initialize the git reconciler.

        Args:
            project_dir: Path to the project root (where .git is)
        """
        self.project_dir = Path(project_dir)

    def _run_git(self, args: list[str], capture_output: bool = True) -> subprocess.CompletedProcess:
        """Run a git command."""
        return subprocess.run(["git"] + args, capture_output=capture_output, text=True, cwd=self.project_dir)

    def _is_git_repo(self) -> bool:
        """Check if the project directory is a git repository."""
        result = self._run_git(["rev-parse", "--git-dir"])
        return result.returncode == 0

    def _parse_git_log(self, since: str = "1 week ago") -> list[dict[str, Any]]:
        """
        Parse git log for commits.

        Returns list of commits with file changes.
        """
        result = self._run_git(["log", f"--since={since}", "--name-status", "--format=%H|%s|%an|%ai|%P"])

        if result.returncode != 0:
            return []

        commits = []
        current_commit = None

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            if "|" in line and len(line.split("|")) >= 4:
                # New commit line
                if current_commit:
                    commits.append(current_commit)

                parts = line.split("|")
                current_commit = {
                    "hash": parts[0],
                    "message": parts[1],
                    "author": parts[2],
                    "date": parts[3].strip(),
                    "parents": parts[4].split() if len(parts) > 4 else [],
                    "files": [],
                    "has_human_edit_tag": self.HUMAN_EDIT_TAG in parts[1],
                    "is_merge": len(parts[4].split()) > 1 if len(parts) > 4 else False,
                }
            elif current_commit and "\t" in line:
                # File change line (status, filename)
                parts = line.split("\t")
                if len(parts) >= 2:
                    status = parts[0][0] if parts[0] else "M"
                    filename = parts[-1]
                    current_commit["files"].append(
                        {
                            "status": status,  # A=Added, M=Modified, D=Deleted, R=Renamed
                            "path": filename,
                        }
                    )

        if current_commit:
            commits.append(current_commit)

        return commits

    def _get_commit_stats(self, commit_hash: str) -> dict[str, int]:
        """Get line change statistics for a commit."""
        result = self._run_git(["show", "--numstat", "--format=", commit_hash])

        stats = {"lines_added": 0, "lines_removed": 0, "files_changed": 0}

        if result.returncode != 0:
            return stats

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                added = int(parts[0]) if parts[0].isdigit() else 0
                removed = int(parts[1]) if parts[1].isdigit() else 0
                stats["lines_added"] += added
                stats["lines_removed"] += removed
                stats["files_changed"] += 1

        return stats

    def _parse_date(self, date_str: str) -> datetime | None:
        """Parse various git date formats."""
        formats = ["%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]

        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue

        return None

    def reconcile(
        self, trace: dict[str, Any], since: str = "1 week ago", auto_log_missing: bool = False
    ) -> dict[str, Any]:
        """
        Reconcile TRACE logs with git history.

        Args:
            trace: The TRACE data
            since: Git date filter (e.g., "1 week ago", "2024-01-01")
            auto_log_missing: If True, suggest entries for missing changes

        Returns:
            Reconciliation report
        """
        if not self._is_git_repo():
            return {"error": "Not a git repository", "is_git_repo": False}

        # Get git commits
        commits = self._parse_git_log(since)

        # Get TRACE code contributions
        trace_contributions = trace.get("code_contributions", [])
        trace_manual_edits = trace.get("human_manual_edits", [])

        # Build sets of tracked files and commits
        trace_files: set[str] = set()
        trace_commits: set[str] = set()

        for cc in trace_contributions:
            if cc.get("file_path"):
                trace_files.add(cc["file_path"])
            if cc.get("git_commit"):
                trace_commits.add(cc["git_commit"])

        for me in trace_manual_edits:
            for commit in me.get("git_commits", []):
                trace_commits.add(commit.get("hash", ""))

        # Analyze commits
        unlogged_changes = []
        logged_commits = []
        human_edit_commits = []
        phantom_entries = []

        for commit in commits:
            commit_hash = commit["hash"]
            stats = self._get_commit_stats(commit_hash)
            commit["stats"] = stats

            if commit["has_human_edit_tag"]:
                human_edit_commits.append(commit)

            # Check if this commit is tracked
            is_tracked = commit_hash in trace_commits or commit_hash[:8] in [c[:8] for c in trace_commits]

            # Check if any files from this commit are tracked
            files_tracked = any(f["path"] in trace_files for f in commit["files"])

            if is_tracked or files_tracked:
                logged_commits.append({"commit": commit, "fully_tracked": is_tracked})
            else:
                # Skip merge commits and commits with only non-code files
                code_extensions = {
                    ".py",
                    ".js",
                    ".ts",
                    ".java",
                    ".c",
                    ".cpp",
                    ".h",
                    ".go",
                    ".rs",
                    ".rb",
                    ".php",
                    ".tex",
                    ".md",
                }
                has_code_files = any(Path(f["path"]).suffix in code_extensions for f in commit["files"])

                if has_code_files and not commit["is_merge"]:
                    unlogged_changes.append(
                        {
                            "commit": commit,
                            "stats": stats,
                            "code_files": [f for f in commit["files"] if Path(f["path"]).suffix in code_extensions],
                        }
                    )

        # Check for phantom entries (in TRACE but not in git)
        git_files: set[str] = set()
        git_commit_hashes: set[str] = set()

        for commit in commits:
            git_commit_hashes.add(commit["hash"])
            git_commit_hashes.add(commit["hash"][:8])
            for f in commit["files"]:
                git_files.add(f["path"])

        for cc in trace_contributions:
            file_path = cc.get("file_path")
            git_commit = cc.get("git_commit")

            # Check if file exists in git history
            if file_path and file_path not in git_files:
                # Could be a new file not yet committed
                if not (self.project_dir / file_path).exists():
                    phantom_entries.append(
                        {
                            "entry_id": cc.get("id"),
                            "type": "file_not_in_git",
                            "file_path": file_path,
                            "message": f"File {file_path} not found in git history or filesystem",
                        }
                    )

            # Check if referenced commit exists
            if git_commit and git_commit not in git_commit_hashes and git_commit[:8] not in git_commit_hashes:
                phantom_entries.append(
                    {
                        "entry_id": cc.get("id"),
                        "type": "commit_not_found",
                        "git_commit": git_commit,
                        "message": f"Referenced commit {git_commit[:8]} not found in git history",
                    }
                )

        # Calculate coverage metrics
        total_commits = len(commits)
        tracked_commits = len(logged_commits)
        coverage = round(tracked_commits / total_commits * 100, 1) if total_commits > 0 else 100

        # Generate suggestions for auto-logging
        suggestions = []
        if auto_log_missing:
            for unlogged in unlogged_changes:
                commit = unlogged["commit"]
                suggestion = {
                    "type": "log_code_contribution" if not commit["has_human_edit_tag"] else "log_manual_edit",
                    "commit_hash": commit["hash"],
                    "files": [f["path"] for f in unlogged["code_files"]],
                    "lines_added": unlogged["stats"]["lines_added"],
                    "lines_removed": unlogged["stats"]["lines_removed"],
                    "author": commit["author"],
                    "date": commit["date"],
                    "message": commit["message"],
                }
                suggestions.append(suggestion)

        return {
            "timestamp": datetime.now().isoformat(),
            "since": since,
            "is_git_repo": True,
            "summary": {
                "total_commits": total_commits,
                "tracked_commits": tracked_commits,
                "unlogged_commits": len(unlogged_changes),
                "human_edit_commits": len(human_edit_commits),
                "phantom_entries": len(phantom_entries),
                "coverage_percent": coverage,
            },
            "unlogged_changes": unlogged_changes[:20],  # Limit to 20
            "human_edit_commits": human_edit_commits,
            "phantom_entries": phantom_entries,
            "suggestions": suggestions if auto_log_missing else [],
            "logged_commits": [
                {"hash": lc["commit"]["hash"][:8], "message": lc["commit"]["message"][:50]}
                for lc in logged_commits[:10]
            ],
        }

    def detect_untagged_human_edits(self, trace: dict[str, Any], since: str = "1 week ago") -> list[dict[str, Any]]:
        """
        Detect commits that might be human edits but aren't tagged.

        Uses heuristics:
        - Commits with very few lines changed
        - Commits with messages suggesting manual fixes
        - Commits not associated with any TRACE session

        Returns:
            List of potentially untagged human edit commits
        """
        commits = self._parse_git_log(since)
        sessions = trace.get("sessions", [])

        # Get session time ranges
        session_ranges = []
        for session in sessions:
            start = self._parse_date(session.get("started", ""))
            end = self._parse_date(session.get("ended", "")) if session.get("ended") else None
            if start:
                session_ranges.append((start, end))

        potential_human_edits = []

        human_edit_keywords = [
            "fix",
            "typo",
            "oops",
            "mistake",
            "forgot",
            "manual",
            "quick",
            "hotfix",
            "patch",
            "revert",
            "undo",
            "correct",
        ]

        for commit in commits:
            if commit["has_human_edit_tag"]:
                continue

            stats = self._get_commit_stats(commit["hash"])
            commit_date = self._parse_date(commit["date"])

            # Check heuristics
            is_small_change = stats["lines_added"] + stats["lines_removed"] <= 10
            has_human_keyword = any(kw in commit["message"].lower() for kw in human_edit_keywords)

            # Check if commit is outside any session
            outside_session = True
            if commit_date:
                for start, end in session_ranges:
                    if start and commit_date >= start:
                        if end is None or commit_date <= end:
                            outside_session = False
                            break

            # Score the likelihood
            score = 0
            reasons = []

            if is_small_change:
                score += 2
                reasons.append("small change (<10 lines)")
            if has_human_keyword:
                score += 2
                reasons.append("message suggests manual edit")
            if outside_session:
                score += 1
                reasons.append("outside any AI session")

            if score >= 2:
                potential_human_edits.append(
                    {
                        "commit_hash": commit["hash"],
                        "message": commit["message"],
                        "author": commit["author"],
                        "date": commit["date"],
                        "stats": stats,
                        "likelihood_score": score,
                        "reasons": reasons,
                    }
                )

        return sorted(potential_human_edits, key=lambda x: x["likelihood_score"], reverse=True)

    def get_file_history(self, file_path: str, since: str = "1 month ago") -> list[dict[str, Any]]:
        """
        Get git history for a specific file.

        Args:
            file_path: Path to the file
            since: Git date filter

        Returns:
            List of commits affecting this file
        """
        result = self._run_git(
            ["log", f"--since={since}", "--follow", "--format=%H|%s|%an|%ai", "--numstat", "--", file_path]
        )

        if result.returncode != 0:
            return []

        history = []
        current_commit = None

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            if "|" in line and len(line.split("|")) >= 4:
                if current_commit:
                    history.append(current_commit)

                parts = line.split("|")
                current_commit = {
                    "hash": parts[0],
                    "message": parts[1],
                    "author": parts[2],
                    "date": parts[3].strip(),
                    "lines_added": 0,
                    "lines_removed": 0,
                }
            elif current_commit and "\t" in line:
                parts = line.split("\t")
                if len(parts) >= 2:
                    current_commit["lines_added"] = int(parts[0]) if parts[0].isdigit() else 0
                    current_commit["lines_removed"] = int(parts[1]) if parts[1].isdigit() else 0

        if current_commit:
            history.append(current_commit)

        return history


# Module-level convenience function
def reconcile_with_git(
    project_dir: Path, trace: dict[str, Any], since: str = "1 week ago", auto_log_missing: bool = False
) -> dict[str, Any]:
    """Reconcile TRACE with git using a new reconciler instance."""
    reconciler = GitReconciler(project_dir)
    return reconciler.reconcile(trace, since, auto_log_missing)
