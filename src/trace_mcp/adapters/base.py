"""Adapter interface — the contract every host integration must satisfy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Disposition = Literal["installed", "updated", "skipped"]


@dataclass(frozen=True)
class InstallResult:
    """Record of a single file's installation outcome."""

    path: Path
    disposition: Disposition
    note: str = ""


class Adapter(ABC):
    """Install TRACE protocol enforcement into one host environment.

    Subclasses must set ``name`` and implement ``detect``, ``install``, and
    ``validate``. Adapters are pure installers — they do not run at MCP
    server time and must not be imported by ``server.py``.
    """

    name: str = ""

    @abstractmethod
    def detect(self, directory: Path) -> bool:
        """Return True when this adapter's host is in use for *directory*."""

    @abstractmethod
    def install(self, directory: Path, *, dry_run: bool = False) -> list[InstallResult]:
        """Install hooks, config, and CLAUDE.md block into *directory*.

        Must be idempotent: re-running on an already-installed directory
        should produce ``skipped`` dispositions, not duplicate entries.

        When ``dry_run`` is True, compute the actions without writing files.
        """

    @abstractmethod
    def validate(self, directory: Path) -> list[str]:
        """Return a list of human-readable validation errors.

        Empty list means the adapter is correctly installed.
        """


__all__ = ["Adapter", "Disposition", "InstallResult"]
