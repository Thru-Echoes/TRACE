"""
TRACE V&V Snapshot System

Captures file states before AI operations to enable verification.
Supports compressed storage, SHA-256 hashes, and automatic triggers.
"""

import hashlib
import json
import subprocess
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any


class SnapshotManager:
    """Manages content snapshots for verification."""

    def __init__(self, trace_dir: Path):
        """
        Initialize the snapshot manager.

        Args:
            trace_dir: Path to the .trace directory
        """
        self.trace_dir = Path(trace_dir)
        self.snapshots_dir = self.trace_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA-256 hash of a file."""
        sha256 = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256.update(chunk)
            return f"sha256:{sha256.hexdigest()}"
        except FileNotFoundError:
            return "sha256:FILE_NOT_FOUND"
        except Exception as e:
            return f"sha256:ERROR:{str(e)[:50]}"

    def _count_lines(self, file_path: Path) -> int:
        """Count lines in a file."""
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    def _count_words(self, file_path: Path) -> int:
        """Count words in a file."""
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                content = f.read()
                return len(content.split())
        except OSError:
            return 0

    def _get_git_state(self) -> dict[str, Any]:
        """Get current git state."""
        git_state = {
            "branch": None,
            "commit_hash": None,
            "commit_message": None,
            "is_dirty": None,
            "timestamp": datetime.now().isoformat(),
        }

        try:
            # Get current branch
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, cwd=self.trace_dir.parent
            )
            if result.returncode == 0:
                git_state["branch"] = result.stdout.strip()

            # Get current commit hash
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=self.trace_dir.parent
            )
            if result.returncode == 0:
                git_state["commit_hash"] = result.stdout.strip()

            # Get commit message
            result = subprocess.run(
                ["git", "log", "-1", "--pretty=%B"], capture_output=True, text=True, cwd=self.trace_dir.parent
            )
            if result.returncode == 0:
                git_state["commit_message"] = result.stdout.strip()[:200]

            # Check if working directory is dirty
            result = subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=self.trace_dir.parent
            )
            if result.returncode == 0:
                git_state["is_dirty"] = bool(result.stdout.strip())

        except Exception as e:
            git_state["error"] = str(e)

        return git_state

    def _compress_content(self, content: bytes) -> bytes:
        """Compress content using zlib."""
        return zlib.compress(content, level=6)

    def _decompress_content(self, compressed: bytes) -> bytes:
        """Decompress zlib-compressed content."""
        return zlib.decompress(compressed)

    def _generate_snapshot_id(self) -> str:
        """Generate a unique snapshot ID."""
        existing = list(self.snapshots_dir.glob("SNAP-*"))
        counter = len(existing) + 1
        while (self.snapshots_dir / f"SNAP-{counter:03d}").exists():
            counter += 1
        return f"SNAP-{counter:03d}"

    def create_snapshot(
        self,
        files: list[str | Path],
        trigger: str,
        session_id: str | None = None,
        related_entry_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Create a snapshot of specified files.

        Args:
            files: List of file paths to snapshot
            trigger: What triggered this snapshot (session_start, pre_contribution, manual, etc.)
            session_id: Optional session ID
            related_entry_id: Optional related entry ID (e.g., code contribution being logged)
            metadata: Optional additional metadata

        Returns:
            Snapshot manifest with file hashes and metadata
        """
        snapshot_id = self._generate_snapshot_id()
        snapshot_dir = self.snapshots_dir / snapshot_id
        files_dir = snapshot_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        # Build manifest
        manifest = {
            "snapshot_id": snapshot_id,
            "timestamp": datetime.now().isoformat(),
            "trigger": trigger,
            "session_id": session_id,
            "related_entry_id": related_entry_id,
            "git_state": self._get_git_state(),
            "files": [],
            "metadata": metadata or {},
            "compression": "zlib",
            "schema_version": "1.0",
        }

        for file_path in files:
            file_path = Path(file_path)
            if not file_path.is_absolute():
                file_path = self.trace_dir.parent / file_path

            file_info = {
                "original_path": str(file_path),
                "relative_path": str(file_path.relative_to(self.trace_dir.parent))
                if file_path.is_relative_to(self.trace_dir.parent)
                else str(file_path),
                "hash": self._compute_file_hash(file_path),
                "exists": file_path.exists(),
                "size_bytes": file_path.stat().st_size if file_path.exists() else 0,
                "line_count": self._count_lines(file_path) if file_path.exists() else 0,
                "word_count": self._count_words(file_path) if file_path.exists() else 0,
                "snapshot_filename": None,
            }

            # Store compressed copy if file exists
            if file_path.exists():
                try:
                    with open(file_path, "rb") as f:
                        content = f.read()

                    compressed = self._compress_content(content)
                    snapshot_filename = hashlib.sha256(str(file_path).encode()).hexdigest()[:16] + ".zlib"
                    snapshot_path = files_dir / snapshot_filename

                    with open(snapshot_path, "wb") as f:
                        f.write(compressed)

                    file_info["snapshot_filename"] = snapshot_filename
                    file_info["compressed_size"] = len(compressed)
                    file_info["compression_ratio"] = round(len(compressed) / len(content), 3) if content else 1.0
                except Exception as e:
                    file_info["snapshot_error"] = str(e)

            manifest["files"].append(file_info)

        # Save manifest
        manifest_path = snapshot_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        return manifest

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        """
        Retrieve a snapshot manifest.

        Args:
            snapshot_id: The snapshot ID to retrieve

        Returns:
            Snapshot manifest or None if not found
        """
        snapshot_dir = self.snapshots_dir / snapshot_id
        manifest_path = snapshot_dir / "manifest.json"

        if not manifest_path.exists():
            return None

        with open(manifest_path, encoding="utf-8") as f:
            return json.load(f)

    def get_snapshot_file_content(self, snapshot_id: str, file_path: str) -> bytes | None:
        """
        Retrieve the content of a snapshotted file.

        Args:
            snapshot_id: The snapshot ID
            file_path: The original file path

        Returns:
            Decompressed file content or None if not found
        """
        manifest = self.get_snapshot(snapshot_id)
        if not manifest:
            return None

        for file_info in manifest.get("files", []):
            if file_info.get("original_path") == file_path or file_info.get("relative_path") == file_path:
                snapshot_filename = file_info.get("snapshot_filename")
                if not snapshot_filename:
                    return None

                snapshot_path = self.snapshots_dir / snapshot_id / "files" / snapshot_filename
                if not snapshot_path.exists():
                    return None

                with open(snapshot_path, "rb") as f:
                    compressed = f.read()

                return self._decompress_content(compressed)

        return None

    def list_snapshots(
        self, session_id: str | None = None, trigger: str | None = None, since: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """
        List snapshots matching criteria.

        Args:
            session_id: Filter by session ID
            trigger: Filter by trigger type
            since: Filter by timestamp (ISO format)
            limit: Maximum number of results

        Returns:
            List of snapshot summaries
        """
        snapshots = []

        for snapshot_dir in sorted(self.snapshots_dir.glob("SNAP-*"), reverse=True):
            manifest_path = snapshot_dir / "manifest.json"
            if not manifest_path.exists():
                continue

            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)

            # Apply filters
            if session_id and manifest.get("session_id") != session_id:
                continue
            if trigger and manifest.get("trigger") != trigger:
                continue
            if since and manifest.get("timestamp", "") < since:
                continue

            git_commit = manifest.get("git_state", {}).get("commit_hash") or ""
            snapshots.append(
                {
                    "snapshot_id": manifest["snapshot_id"],
                    "timestamp": manifest["timestamp"],
                    "trigger": manifest["trigger"],
                    "session_id": manifest.get("session_id"),
                    "file_count": len(manifest.get("files", [])),
                    "git_commit": git_commit[:8] if git_commit else "",
                }
            )

            if len(snapshots) >= limit:
                break

        return snapshots

    def cleanup_old_snapshots(
        self, keep_linked: bool = True, max_age_days: int = 30, max_count: int = 100
    ) -> dict[str, Any]:
        """
        Clean up old snapshots based on retention policy.

        Args:
            keep_linked: Keep snapshots linked to entries (via related_entry_id)
            max_age_days: Maximum age in days for unlinked snapshots
            max_count: Maximum number of snapshots to keep

        Returns:
            Cleanup summary
        """
        from datetime import timedelta

        cutoff_date = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        all_snapshots = list(sorted(self.snapshots_dir.glob("SNAP-*"), reverse=True))

        to_delete = []
        kept = []

        for idx, snapshot_dir in enumerate(all_snapshots):
            manifest_path = snapshot_dir / "manifest.json"
            if not manifest_path.exists():
                to_delete.append(snapshot_dir)
                continue

            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)

            # Keep if linked and flag is set
            if keep_linked and manifest.get("related_entry_id"):
                kept.append(snapshot_dir.name)
                continue

            # Delete if too old or over count
            if manifest.get("timestamp", "") < cutoff_date or idx >= max_count:
                to_delete.append(snapshot_dir)
            else:
                kept.append(snapshot_dir.name)

        # Perform deletion
        deleted_count = 0
        for snapshot_dir in to_delete:
            try:
                import shutil

                shutil.rmtree(snapshot_dir)
                deleted_count += 1
            except Exception:
                pass  # Log error but continue

        return {
            "deleted_count": deleted_count,
            "kept_count": len(kept),
            "cutoff_date": cutoff_date,
            "max_count": max_count,
        }


# Module-level convenience functions
def create_snapshot(trace_dir: Path, files: list[str | Path], trigger: str, **kwargs) -> dict[str, Any]:
    """Create a snapshot using a new manager instance."""
    manager = SnapshotManager(trace_dir)
    return manager.create_snapshot(files, trigger, **kwargs)


def get_snapshot(trace_dir: Path, snapshot_id: str) -> dict[str, Any] | None:
    """Get a snapshot using a new manager instance."""
    manager = SnapshotManager(trace_dir)
    return manager.get_snapshot(snapshot_id)
