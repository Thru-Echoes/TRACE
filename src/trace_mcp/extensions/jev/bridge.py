"""Install and locate the Node bridge used by the Vercel AI Gateway route.

A package launched through ``uvx`` runs from a built copy in uv's cache, so npm
packages installed in a source checkout are not next to the bridge script. The
bridge is therefore copied to a stable directory and its dependencies installed
there. The copy is compared with the packaged files on every use, so a server
upgrade cannot run against an out-of-date bridge unnoticed.

Exports: ``get_bridge_dir``, ``get_installed_bridge``, ``install_bridge``.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_PACKAGED_DIR = Path(__file__).parent
_BRIDGE_SCRIPT = "gateway_bridge.mjs"
_BRIDGE_FILES = (_BRIDGE_SCRIPT, "package.json", "package-lock.json")
_SETUP_HINT = "run `trace-mcp jev setup`"
_MINIMUM_NODE_MAJOR = 22
_NODE_REQUIRED = f"The Jev Gateway route needs Node.js {_MINIMUM_NODE_MAJOR} or newer on the server's PATH"


@functools.lru_cache(maxsize=4)
def _get_node_major(node: str) -> int:
    """Return Node's major version, or 0 if it cannot be determined."""
    try:
        output = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10, check=True).stdout
        return int(output.strip().lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _require_node() -> None:
    """Raise ValueError unless a new enough Node is on this process's PATH."""
    node = shutil.which("node")
    if node is None or _get_node_major(node) < _MINIMUM_NODE_MAJOR:
        raise ValueError(_NODE_REQUIRED)


def get_bridge_dir() -> Path:
    """Return the bridge directory: ``TRACE_JEV_BRIDGE_DIR`` or ``~/.trace/jev-bridge``."""
    configured = os.environ.get("TRACE_JEV_BRIDGE_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".trace" / "jev-bridge"


def get_installed_bridge() -> Path:
    """Return the path of a bridge that is ready to run.

    Returns:
        The installed bridge script.

    Raises:
        ValueError: Node is not on this process's PATH, or the bridge is missing,
            incomplete, or differs from the packaged version.
    """
    _require_node()
    directory = get_bridge_dir()
    if not (directory / "node_modules" / "ai" / "package.json").is_file():
        raise ValueError(f"The Jev Gateway bridge is not installed; {_SETUP_HINT}")
    for name in _BRIDGE_FILES:
        installed = directory / name
        if not installed.is_file() or installed.read_bytes() != (_PACKAGED_DIR / name).read_bytes():
            raise ValueError(f"The Jev Gateway bridge is out of date; {_SETUP_HINT}")
    return directory / _BRIDGE_SCRIPT


def install_bridge() -> Path:
    """Build the bridge in a temporary directory and swap it in on success.

    Returns:
        The installed bridge script.

    Raises:
        RuntimeError: ``npm`` is missing or the install failed; any existing bridge
            is left as it was.
        ValueError: Node is missing or too old.

    Side effects: replaces the bridge directory; runs ``npm ci``, which downloads
    packages from the npm registry. Install scripts are disabled.
    """
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm was not found; install Node.js 22 or newer")
    _require_node()
    directory = get_bridge_dir()
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory.parent, prefix=".jev-bridge-") as scratch:
        staged = Path(scratch) / "new"
        staged.mkdir()
        for name in _BRIDGE_FILES:
            shutil.copyfile(_PACKAGED_DIR / name, staged / name)
        result = subprocess.run(
            [npm, "ci", "--omit=dev", "--ignore-scripts"],
            cwd=staged,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"npm ci failed: {result.stderr.strip()[-2000:]}")
        if directory.exists():
            directory.rename(Path(scratch) / "old")
        staged.rename(directory)
    return get_installed_bridge()
