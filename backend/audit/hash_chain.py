"""
Tamper-proof audit hash chain for OVERWATCH/BULWARK.

Every system event (ROE evaluation, engagement decision, BDA result, operator
override) is appended as an AuditEntry whose SHA-256 hash is computed over its
content plus the previous entry's hash. Any post-hoc modification to any entry
breaks the chain and is detected by verify(). The chain can be exported to and
loaded from a JSON Lines file for archival and cross-system replay.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("overwatch.audit.hash_chain")


@dataclass
class AuditEntry:
    sequence: int
    timestamp: float
    event_type: str      # "roe_evaluation", "engagement", "bda", "decision"
    data: dict
    previous_hash: str
    entry_hash: str


def _compute_hash(
    sequence: int,
    timestamp: float,
    event_type: str,
    data: dict,
    previous_hash: str,
) -> str:
    """Compute SHA-256 over canonical fields.

    json.dumps with sort_keys=True guarantees deterministic serialization
    regardless of dict insertion order across Python versions.
    """
    payload = (
        str(sequence)
        + str(timestamp)
        + event_type
        + json.dumps(data, sort_keys=True, default=str)
        + previous_hash
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditHashChain:
    """Tamper-proof audit log using SHA-256 hash chain.

    append() is the only write path. Every call computes a new hash that
    commits the full prior chain state into the new entry, making undetected
    insertion, deletion, or modification computationally infeasible.
    """

    _GENESIS_HASH: str = "0" * 64

    def __init__(self) -> None:
        self._entries: List[AuditEntry] = []
        self._last_hash: str = self._GENESIS_HASH

    def append(self, event_type: str, data: dict, timestamp: float) -> AuditEntry:
        """Add an entry to the chain.

        entry_hash = SHA256(sequence + timestamp + event_type + json(data) + previous_hash)
        """
        sequence = len(self._entries)
        entry_hash = _compute_hash(sequence, timestamp, event_type, data, self._last_hash)
        entry = AuditEntry(
            sequence=sequence,
            timestamp=timestamp,
            event_type=event_type,
            data=dict(data),
            previous_hash=self._last_hash,
            entry_hash=entry_hash,
        )
        self._entries.append(entry)
        self._last_hash = entry_hash
        logger.debug(
            "AuditHashChain append seq=%d type=%s hash=%s",
            sequence, event_type, entry_hash[:16],
        )
        return entry

    def verify(self) -> Tuple[bool, Optional[int]]:
        """Verify the entire chain.

        Recomputes each entry's hash from its fields and checks:
        1. Recomputed hash matches stored entry_hash.
        2. entry.previous_hash matches the prior entry's entry_hash.

        Returns (True, None) on success.
        Returns (False, index) where index is the first broken entry.
        """
        expected_previous = self._GENESIS_HASH
        for entry in self._entries:
            if entry.previous_hash != expected_previous:
                logger.warning(
                    "Hash chain broken: seq=%d previous_hash mismatch", entry.sequence
                )
                return False, entry.sequence

            recomputed = _compute_hash(
                entry.sequence,
                entry.timestamp,
                entry.event_type,
                entry.data,
                entry.previous_hash,
            )
            if recomputed != entry.entry_hash:
                logger.warning(
                    "Hash chain broken: seq=%d entry_hash mismatch", entry.sequence
                )
                return False, entry.sequence

            expected_previous = entry.entry_hash

        return True, None

    def export(self, path: Path) -> None:
        """Export chain as JSON Lines (one entry per line)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for entry in self._entries:
                fh.write(json.dumps(asdict(entry), sort_keys=True, default=str))
                fh.write("\n")
        logger.info("AuditHashChain exported %d entries to %s", len(self._entries), path)

    @classmethod
    def load(cls, path: Path) -> "AuditHashChain":
        """Load a chain from disk and verify it before returning.

        Raises ValueError if the chain is tampered or the file is malformed.
        """
        chain = cls()
        with path.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Malformed JSON at line {line_no} in {path}"
                    ) from exc
                entry = AuditEntry(
                    sequence=obj["sequence"],
                    timestamp=obj["timestamp"],
                    event_type=obj["event_type"],
                    data=obj["data"],
                    previous_hash=obj["previous_hash"],
                    entry_hash=obj["entry_hash"],
                )
                chain._entries.append(entry)

        if chain._entries:
            chain._last_hash = chain._entries[-1].entry_hash

        valid, broken_at = chain.verify()
        if not valid:
            raise ValueError(
                f"Hash chain integrity check failed at entry index {broken_at} in {path}"
            )

        logger.info("AuditHashChain loaded and verified %d entries from %s", len(chain._entries), path)
        return chain

    @property
    def length(self) -> int:
        return len(self._entries)

    @property
    def last_hash(self) -> str:
        return self._last_hash
