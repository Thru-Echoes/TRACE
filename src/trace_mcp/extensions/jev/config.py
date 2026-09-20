"""Configuration for the optional, explicitly enabled Jev advisory extension."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from trace_mcp import project_identity as pident

_GLOBAL_ENV = Path.home() / ".trace" / ".env"


def _parse_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if value[:1] in ('"', "'"):
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end >= 0 else value[1:]
        else:
            value = value.split("#", 1)[0].rstrip()
        if value:
            values[key.strip()] = value
    return values


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class JevConfig:
    enabled: bool = False
    api_key: str | None = field(default=None, repr=False)
    model: str = "jev-latest"
    local_only: bool = False


def load_config() -> JevConfig:
    """Load config once at extension registration; a key alone enables nothing."""
    project = _parse_dotenv(Path.cwd() / ".env")
    global_ = _parse_dotenv(_GLOBAL_ENV)
    merged = {**global_, **project}
    for key in ("TYPESAFE_AI_API_KEY", "TYPESAFE_API_KEY", "TRACE_JEV_ENABLED", "TRACE_JEV_MODEL"):
        value = os.environ.get(key)
        if value and value.strip():
            merged[key] = value
    local_only = any(_truthy(source.get("TRACE_LOCAL_ONLY")) for source in (global_, project, dict(os.environ)))
    api_key = merged.get("TYPESAFE_AI_API_KEY") or merged.get("TYPESAFE_API_KEY")
    return JevConfig(
        enabled=_truthy(merged.get("TRACE_JEV_ENABLED")) and not local_only,
        api_key=api_key,
        model=merged.get("TRACE_JEV_MODEL", "jev-latest"),
        local_only=local_only,
    )


def effective_config(config: JevConfig, project: str) -> JevConfig:
    """Apply the registry's per-project no-egress ratchet, failing closed if unreadable."""
    registry = pident.get_registry_cached()
    if registry is None:
        return config
    entry = registry.projects.get(pident.key_for_label(project))
    if entry is not None and entry.config.local_only and not config.local_only:
        return replace(config, enabled=False, local_only=True)
    return config
