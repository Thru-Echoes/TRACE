"""
TRACE Protocol Environment Capture

Captures execution environment for reproducibility.
"""

import hashlib
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


class EnvironmentCapture:
    """Captures and manages execution environment information."""

    def __init__(self, project_dir: Path | None = None):
        """
        Initialize environment capture.

        Args:
            project_dir: Project directory for git state and dependencies
        """
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()

    def capture(
        self,
        agent_name: str,
        agent_framework: str = "mcp",
        agent_parameters: dict[str, Any] | None = None,
        mcp_spec_version: str = "2025-06-18",
        server_version: str = "3.0.0",
    ) -> dict[str, Any]:
        """
        Capture current execution environment.

        Args:
            agent_name: Name/identifier of the AI agent/model
            agent_framework: Framework being used (mcp, langchain, etc.)
            agent_parameters: Model parameters (temperature, etc.)
            mcp_spec_version: MCP specification version
            server_version: TRACE server version

        Returns:
            Environment dictionary
        """
        return {
            "captured_at": datetime.now().isoformat(),
            "platform": self._capture_platform(),
            "runtime": self._capture_runtime(),
            "agent": {
                "framework": agent_framework,
                "name": agent_name,
                "parameters": agent_parameters or {},
            },
            "mcp": {
                "spec_version": mcp_spec_version,
                "server_version": server_version,
            },
            "dependencies_hash": self._compute_dependencies_hash(),
            "git_state": self._capture_git_state(),
        }

    def _capture_platform(self) -> dict[str, str]:
        """Capture platform information."""
        return {
            "os": platform.system().lower(),
            "arch": platform.machine(),
            "version": platform.release(),
        }

    def _capture_runtime(self) -> dict[str, str]:
        """Capture runtime information."""
        return {
            "language": "python",
            "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        }

    def _compute_dependencies_hash(self) -> str | None:
        """Compute hash of dependency manifest."""
        # Try common dependency files
        dep_files = [
            "requirements.txt",
            "pyproject.toml",
            "package.json",
            "Cargo.toml",
            "go.mod",
        ]

        for dep_file in dep_files:
            dep_path = self.project_dir / dep_file
            if dep_path.exists():
                try:
                    content = dep_path.read_bytes()
                    return f"sha256:{hashlib.sha256(content).hexdigest()}"
                except Exception:
                    pass

        return None

    def _capture_git_state(self) -> dict[str, Any] | None:
        """Capture git repository state."""
        try:
            # Check if git repo
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True,
                text=True,
                cwd=self.project_dir,
            )
            if result.returncode != 0:
                return None

            # Get current commit
            commit_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=self.project_dir,
            )
            commit = commit_result.stdout.strip() if commit_result.returncode == 0 else None

            # Get current branch
            branch_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                cwd=self.project_dir,
            )
            branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None

            # Check if dirty
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                cwd=self.project_dir,
            )
            dirty = bool(status_result.stdout.strip()) if status_result.returncode == 0 else None

            return {
                "commit": commit,
                "branch": branch,
                "dirty": dirty,
            }
        except Exception:
            return None

    def get_environment_summary(self, env: dict[str, Any]) -> str:
        """Generate a human-readable summary of an environment."""
        platform_info = env.get("platform", {})
        runtime = env.get("runtime", {})
        agent = env.get("agent", {})
        git = env.get("git_state", {})

        lines = [
            f"Platform: {platform_info.get('os', 'unknown')}/{platform_info.get('arch', 'unknown')}",
            f"Runtime: {runtime.get('language', 'unknown')} {runtime.get('version', '')}",
            f"Agent: {agent.get('name', 'unknown')} ({agent.get('framework', 'unknown')})",
        ]

        if git:
            lines.append(f"Git: {git.get('branch', 'unknown')}@{git.get('commit', 'unknown')[:8]}")
            if git.get("dirty"):
                lines[-1] += " (dirty)"

        return "\n".join(lines)


def capture_environment(
    project_dir: Path | None = None,
    agent_name: str = "unknown",
    agent_framework: str = "mcp",
    agent_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Convenience function to capture environment.

    Args:
        project_dir: Project directory
        agent_name: Name of the AI agent
        agent_framework: Agent framework
        agent_parameters: Model parameters

    Returns:
        Environment dictionary
    """
    capturer = EnvironmentCapture(project_dir)
    return capturer.capture(
        agent_name=agent_name,
        agent_framework=agent_framework,
        agent_parameters=agent_parameters,
    )


def generate_environment_id(existing_environments: list[dict]) -> str:
    """Generate unique environment ID."""
    existing_ids = {env.get("id", "") for env in existing_environments}
    counter = 1
    while True:
        env_id = f"ENV{counter:03d}"
        if env_id not in existing_ids:
            return env_id
        counter += 1
