"""R-F1987 — design partner conversation tracker for Phase A Gate #7.

Gate #7 requires >=4 design-partner conversations. This module provides
a lightweight JSON store + admin endpoint so the operator can log
conversations and the gate check can verify progress automatically.

Usage:
    tracker = DesignPartnerTracker()
    tracker.add(name="Acme Corp", contact="john@acme.com", notes="Initial call")
    tracker.list_all()  # returns all entries
    tracker.count()  # returns total
    tracker.gate_pass()  # True if >= 4
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from .engine_wiring import wire_success, wire_failure

logger = logging.getLogger("aria.design_partner_tracker")

_DATA_DIR = os.environ.get("ARIA_DATA_DIR", "/data")
_TRACKER_PATH = os.path.join(_DATA_DIR, "design_partners.json")


@dataclass
class DesignPartnerEntry:
    """A single design-partner conversation record."""

    name: str
    contact: str
    notes: str = ""
    status: str = "contacted"  # applied | contacted | engaged | onboarded | declined
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # R-F2673: how the record entered the store — "operator" (manually logged)
    # or "public_application" (self-service via partners.html). Public
    # applications land as status="applied" and never count toward the gate
    # until an operator qualifies them. Optional so legacy rows load unchanged.
    source: str = "operator"
    company: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# R-F2673 — statuses that COUNT toward Gate #7. Only operator-set statuses
# qualify: an operator moving a record to contacted/engaged/onboarded is
# vouching that a real design-partner conversation happened. "applied" (a
# public self-service application) and "declined" are EXCLUDED, so the public
# application funnel (partners.html) can never move a gate CLAUDE.md §1 defines
# as operator-owned. Whitelist ⇒ any unknown status fails closed (never counts).
_QUALIFIED_STATUSES = frozenset({"contacted", "engaged", "onboarded"})


class DesignPartnerTracker:
    """Persistent store for design-partner conversations."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _TRACKER_PATH
        self._entries: list[DesignPartnerEntry] = []
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._entries = [DesignPartnerEntry(**e) for e in raw]
            self._loaded = True
        except Exception as e:
            logger.warning("[design partner tracker] load failed: %s", e)
            self._loaded = True

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump([e.to_dict() for e in self._entries], f, indent=2)
        except Exception as e:
            logger.error("[design partner tracker] save failed: %s", e)
            # §21a — a failed write must reach the brain, not die in the log.
            wire_failure(module="design_partner_tracker",
                         detail=f"save failed ({self._path}): {e}",
                         source="design_partner_tracker._save")

    def add(
        self,
        name: str,
        contact: str,
        notes: str = "",
        status: str = "contacted",
        source: str = "operator",
        company: str = "",
    ) -> DesignPartnerEntry:
        """Add a new design-partner conversation (or a public application)."""
        self._load()
        entry = DesignPartnerEntry(
            name=name,
            contact=contact,
            notes=notes,
            status=status,
            source=source,
            company=company,
        )
        self._entries.append(entry)
        self._save()
        logger.info("[design partner tracker] added: %s (%s)", name, contact)
        # §21a — success branch reaches the brain (gate-#7 progress is a signal).
        wire_success(module="design_partner_tracker",
                     summary=f"design partner logged ({len(self._entries)} total): {name}",
                     entity_name=name)
        return entry

    def update(
        self,
        index: int,
        *,
        notes: str | None = None,
        status: str | None = None,
    ) -> DesignPartnerEntry | None:
        """Update an existing entry by index."""
        self._load()
        if index < 0 or index >= len(self._entries):
            return None
        entry = self._entries[index]
        if notes is not None:
            entry.notes = notes
        if status is not None:
            entry.status = status
        entry.updated_at = time.time()
        self._save()
        return entry

    def list_all(self) -> list[dict[str, Any]]:
        """Return all entries as dicts. (Named list_all to avoid shadowing the
        builtin list — R-F1990 review.)"""
        self._load()
        return [e.to_dict() for e in self._entries]

    def count(self) -> int:
        """Return total number of entries (all statuses, incl. applications)."""
        self._load()
        return len(self._entries)

    def qualified_count(self) -> int:
        """R-F2673 — number of entries in an operator-vouched (qualified) status.
        This is the ONLY number that moves Gate #7. Public applications
        (status='applied') and declined records are excluded."""
        self._load()
        return sum(1 for e in self._entries if e.status in _QUALIFIED_STATUSES)

    def gate_pass(self) -> bool:
        """Gate #7 passes at >= 4 QUALIFIED design-partner conversations.
        R-F2673: counts qualified only (contacted/engaged/onboarded) — a public
        applicant cannot close an operator-owned gate (CLAUDE.md §1). Previously
        this counted every entry, which the public funnel would have inflated."""
        return self.qualified_count() >= 4

    def stats(self) -> dict[str, Any]:
        """Return summary stats for the gate check.
        R-F2673: ``qualified`` (not ``total``) is the gate-relevant number;
        ``gate_pass`` is derived from it. ``total`` and ``by_status`` are kept
        for the admin UI (applications are visible but non-counting)."""
        self._load()
        by_status: dict[str, int] = {}
        for e in self._entries:
            by_status[e.status] = by_status.get(e.status, 0) + 1
        qualified = self.qualified_count()
        return {
            "total": self.count(),
            "qualified": qualified,
            "by_status": by_status,
            "gate_pass": qualified >= 4,
            "gate_target": 4,
            "qualified_statuses": sorted(_QUALIFIED_STATUSES),
        }


# Module-level singleton for import convenience
_tracker: DesignPartnerTracker | None = None


def get_tracker() -> DesignPartnerTracker:
    """Get or create the singleton tracker."""
    global _tracker
    if _tracker is None:
        _tracker = DesignPartnerTracker()
    return _tracker
