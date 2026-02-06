"""
TRACE V&V Cryptographic Integrity Chain

Tamper-evident audit trail using hash chaining.
Each entry gets a hash that includes the previous hash,
creating an immutable chain.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


class IntegrityChain:
    """Manages the cryptographic integrity chain for TRACE entries."""

    GENESIS_HASH = "sha256:0000000000000000000000000000000000000000000000000000000000000000"

    def __init__(self, trace_dir: Path):
        """
        Initialize the integrity chain manager.

        Args:
            trace_dir: Path to the .trace directory
        """
        self.trace_dir = Path(trace_dir)
        self.chain_file = self.trace_dir / "chain.json"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._load_chain()

    def _load_chain(self) -> None:
        """Load the chain from file or create new."""
        if self.chain_file.exists():
            with open(self.chain_file, encoding="utf-8") as f:
                self.chain = json.load(f)
        else:
            self.chain = {
                "version": "1.0",
                "created": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "entries": [],
                "current_position": 0,
                "genesis_hash": self.GENESIS_HASH,
            }

    def _save_chain(self) -> None:
        """Save the chain to file."""
        self.chain["last_updated"] = datetime.now().isoformat()
        with open(self.chain_file, "w", encoding="utf-8") as f:
            json.dump(self.chain, f, indent=2)

    def _compute_hash(self, data: str) -> str:
        """Compute SHA-256 hash of data."""
        return f"sha256:{hashlib.sha256(data.encode('utf-8')).hexdigest()}"

    def _serialize_entry(self, entry: dict[str, Any]) -> str:
        """Serialize an entry for hashing (deterministic JSON)."""
        # Remove integrity field if present (we're computing it)
        entry_copy = {k: v for k, v in entry.items() if k != "integrity"}
        return json.dumps(entry_copy, sort_keys=True, ensure_ascii=False)

    def compute_entry_hash(self, entry: dict[str, Any], previous_hash: str) -> str:
        """
        Compute the hash for an entry including previous hash.

        Args:
            entry: The entry data
            previous_hash: Hash of the previous entry in chain

        Returns:
            Hash string
        """
        serialized = self._serialize_entry(entry)
        combined = f"{previous_hash}|{serialized}"
        return self._compute_hash(combined)

    def add_entry(self, entry_id: str, entry_type: str, entry_data: dict[str, Any]) -> dict[str, Any]:
        """
        Add an entry to the integrity chain.

        Args:
            entry_id: Unique ID of the entry
            entry_type: Type of entry (code_contribution, suggestion, etc.)
            entry_data: The full entry data

        Returns:
            Integrity metadata to attach to the entry
        """
        # Get previous hash
        if self.chain["entries"]:
            previous_hash = self.chain["entries"][-1]["entry_hash"]
        else:
            previous_hash = self.GENESIS_HASH

        # Compute entry hash
        entry_hash = self.compute_entry_hash(entry_data, previous_hash)

        # Create chain entry
        chain_entry = {
            "position": self.chain["current_position"],
            "entry_id": entry_id,
            "entry_type": entry_type,
            "timestamp": datetime.now().isoformat(),
            "previous_hash": previous_hash,
            "entry_hash": entry_hash,
        }

        # Add to chain
        self.chain["entries"].append(chain_entry)
        self.chain["current_position"] += 1
        self._save_chain()

        # Return integrity metadata for the entry
        return {"entry_hash": entry_hash, "previous_hash": previous_hash, "chain_position": chain_entry["position"]}

    def verify_entry(self, entry_data: dict[str, Any], entry_id: str | None = None) -> dict[str, Any]:
        """
        Verify an entry's integrity.

        Args:
            entry_data: The entry data to verify
            entry_id: Optional entry ID to look up in chain

        Returns:
            Verification result
        """
        integrity = entry_data.get("integrity", {})

        if not integrity:
            return {"verified": False, "error": "No integrity metadata in entry", "entry_id": entry_id}

        stored_hash = integrity.get("entry_hash")
        previous_hash = integrity.get("previous_hash")
        chain_position = integrity.get("chain_position")

        if not all([stored_hash, previous_hash, chain_position is not None]):
            return {"verified": False, "error": "Incomplete integrity metadata", "entry_id": entry_id}

        # Recompute the hash
        computed_hash = self.compute_entry_hash(entry_data, previous_hash)

        # Check if hashes match
        hash_matches = computed_hash == stored_hash

        # Check chain position
        chain_entry = None
        for ce in self.chain["entries"]:
            if ce["position"] == chain_position:
                chain_entry = ce
                break

        position_valid = chain_entry is not None
        chain_hash_matches = chain_entry["entry_hash"] == stored_hash if chain_entry else False

        return {
            "verified": hash_matches and chain_hash_matches,
            "entry_id": entry_id,
            "stored_hash": stored_hash,
            "computed_hash": computed_hash,
            "hash_matches": hash_matches,
            "chain_position": chain_position,
            "position_valid": position_valid,
            "chain_hash_matches": chain_hash_matches,
            "timestamp": datetime.now().isoformat(),
        }

    def verify_chain(self, trace: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Verify the entire integrity chain.

        Args:
            trace: Optional TRACE data to cross-verify entries

        Returns:
            Chain verification result
        """
        if not self.chain["entries"]:
            return {"verified": True, "chain_length": 0, "message": "Empty chain"}

        errors = []
        warnings = []
        verified_count = 0

        # Verify chain linkage
        previous_hash = self.GENESIS_HASH

        for i, entry in enumerate(self.chain["entries"]):
            # Check position
            if entry["position"] != i:
                errors.append({"position": i, "error": f"Position mismatch: expected {i}, got {entry['position']}"})

            # Check previous hash link
            if entry["previous_hash"] != previous_hash:
                errors.append(
                    {
                        "position": i,
                        "entry_id": entry.get("entry_id"),
                        "error": f"Chain break: previous_hash mismatch at position {i}",
                    }
                )

            # If trace data provided, verify entry hash
            if trace:
                entry_id = entry.get("entry_id")
                entry_type = entry.get("entry_type")

                # Map entry types to trace collections
                type_map = {
                    "code_contribution": "code_contributions",
                    "ai_suggestion": "ai_suggestions",
                    "decision": "decisions",
                    "learning": "learnings",
                    "gotcha": "gotchas",
                    "idea": "ideas",
                    "error": "errors",
                    "intervention": "interventions",
                }

                collection = type_map.get(entry_type, f"{entry_type}s")
                trace_entry = None

                for te in trace.get(collection, []):
                    if te.get("id") == entry_id:
                        trace_entry = te
                        break

                if trace_entry:
                    verification = self.verify_entry(trace_entry, entry_id)
                    if verification["verified"]:
                        verified_count += 1
                    else:
                        errors.append({"position": i, "entry_id": entry_id, "error": "Entry hash verification failed"})
                else:
                    warnings.append({"position": i, "entry_id": entry_id, "warning": "Entry not found in trace data"})

            previous_hash = entry["entry_hash"]

        return {
            "verified": len(errors) == 0,
            "chain_length": len(self.chain["entries"]),
            "verified_entries": verified_count if trace else None,
            "genesis_hash": self.chain["genesis_hash"],
            "latest_hash": self.chain["entries"][-1]["entry_hash"] if self.chain["entries"] else None,
            "errors": errors,
            "warnings": warnings,
            "timestamp": datetime.now().isoformat(),
        }

    def get_chain_summary(self) -> dict[str, Any]:
        """Get a summary of the chain state."""
        entry_types = {}
        for entry in self.chain["entries"]:
            entry_type = entry.get("entry_type", "unknown")
            entry_types[entry_type] = entry_types.get(entry_type, 0) + 1

        return {
            "chain_length": len(self.chain["entries"]),
            "current_position": self.chain["current_position"],
            "genesis_hash": self.chain["genesis_hash"],
            "latest_hash": self.chain["entries"][-1]["entry_hash"] if self.chain["entries"] else None,
            "created": self.chain["created"],
            "last_updated": self.chain["last_updated"],
            "entry_types": entry_types,
        }

    def find_entry_by_id(self, entry_id: str) -> dict[str, Any] | None:
        """Find a chain entry by entry ID."""
        for entry in self.chain["entries"]:
            if entry.get("entry_id") == entry_id:
                return entry
        return None

    def get_entries_after(self, position: int) -> list[dict[str, Any]]:
        """Get all chain entries after a given position."""
        return [e for e in self.chain["entries"] if e["position"] > position]

    def rebuild_chain(self, trace: dict[str, Any]) -> dict[str, Any]:
        """
        Rebuild the integrity chain from trace data.

        WARNING: This will replace the existing chain.

        Args:
            trace: The TRACE data

        Returns:
            Rebuild summary
        """
        old_chain_length = len(self.chain["entries"])

        # Reset chain
        self.chain = {
            "version": "1.0",
            "created": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "entries": [],
            "current_position": 0,
            "genesis_hash": self.GENESIS_HASH,
        }

        # Collect all entries with timestamps
        all_entries = []

        collections = [
            ("code_contributions", "code_contribution"),
            ("ai_suggestions", "ai_suggestion"),
            ("decisions", "decision"),
            ("learnings", "learning"),
            ("gotchas", "gotcha"),
            ("ideas", "idea"),
            ("errors", "error"),
            ("interventions", "intervention"),
        ]

        for collection_name, entry_type in collections:
            for entry in trace.get(collection_name, []):
                if entry.get("id"):
                    all_entries.append(
                        {"id": entry["id"], "type": entry_type, "timestamp": entry.get("timestamp", ""), "data": entry}
                    )

        # Sort by timestamp
        all_entries.sort(key=lambda x: x["timestamp"])

        # Add entries to chain
        added_count = 0
        for entry_info in all_entries:
            integrity = self.add_entry(entry_info["id"], entry_info["type"], entry_info["data"])

            # Update entry with integrity metadata
            entry_info["data"]["integrity"] = integrity
            added_count += 1

        self._save_chain()

        return {
            "old_chain_length": old_chain_length,
            "new_chain_length": len(self.chain["entries"]),
            "entries_added": added_count,
            "timestamp": datetime.now().isoformat(),
        }


# Module-level convenience functions
def verify_chain_integrity(trace_dir: Path, trace: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify chain integrity using a new manager instance."""
    chain = IntegrityChain(trace_dir)
    return chain.verify_chain(trace)


def compute_entry_hash(trace_dir: Path, entry: dict[str, Any], previous_hash: str) -> str:
    """Compute entry hash using a new manager instance."""
    chain = IntegrityChain(trace_dir)
    return chain.compute_entry_hash(entry, previous_hash)
