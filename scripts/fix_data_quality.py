#!/usr/bin/env python3
"""Fix TRACE session data quality issues.

1. Normalize project naming (When-Algorithms-Meet-Artists variants)
2. Fix status mismatch: sessions with ended timestamp but status='active'

Usage:
    python scripts/fix_data_quality.py          # Dry run
    python scripts/fix_data_quality.py --apply   # Apply fixes
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SESSIONS_DIR = Path.home() / ".trace" / "sessions"

# Canonical project name mappings
NAME_FIXES: dict[str, str] = {
    "When Algorithms Meet Artists": "When-Algorithms-Meet-Artists",
    "when-algorithms-meet-artists": "When-Algorithms-Meet-Artists",
    "when_algorithms_meet_artists": "When-Algorithms-Meet-Artists",
}


def fix_session(path: Path, apply: bool = False) -> list[str]:
    """Check and optionally fix a session file. Returns list of fixes applied."""
    fixes: list[str] = []

    with open(path) as f:
        data = json.load(f)

    modified = False

    # Fix 1: Project name normalization
    project = data.get("metadata", {}).get("project", "")
    if project in NAME_FIXES:
        canonical = NAME_FIXES[project]
        fixes.append(f"  PROJECT NAME: '{project}' → '{canonical}'")
        if apply:
            data["metadata"]["project"] = canonical
            modified = True

    # Fix 2: Status mismatch (ended but still 'active')
    status = data.get("status", "")
    ended = data.get("ended")
    if ended is not None and status == "active":
        fixes.append(f"  STATUS MISMATCH: ended={ended} but status='active' → 'completed'")
        if apply:
            data["status"] = "completed"
            modified = True

    if modified and apply:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    return fixes


def main() -> None:
    apply = "--apply" in sys.argv

    if not SESSIONS_DIR.exists():
        print(f"No sessions directory: {SESSIONS_DIR}")
        return

    total_fixes = 0
    files_fixed = 0

    for path in sorted(SESSIONS_DIR.glob("trace_*.json")):
        try:
            fixes = fix_session(path, apply=apply)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"SKIP {path.name}: {e}")
            continue

        if fixes:
            files_fixed += 1
            total_fixes += len(fixes)
            action = "FIXED" if apply else "WOULD FIX"
            print(f"{action} {path.name}:")
            for fix in fixes:
                print(fix)

    print(f"\n{'Applied' if apply else 'Would apply'} {total_fixes} fixes across {files_fixed} files.")
    if not apply and total_fixes > 0:
        print("Run with --apply to apply fixes.")


if __name__ == "__main__":
    main()
