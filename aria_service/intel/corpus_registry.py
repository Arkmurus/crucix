"""ARIA Corpus Registry Loader.

Reads aria_service/intel/corpus_registry.yaml and exposes a Python interface
for querying the source list. The YAML is the single source of truth for:

  - Tier A bulk ingest (cli/ingest_tier_a.py)
  - Tier C scheduled scrapers (cli/ingest_tier_c.py — TODO)
  - Tier E entity-screening auto-trigger (researcher.py — TODO)
  - The /corpus stats command on WhatsApp
  - The Tier D analytic_principles addendum (which sources principles came from)

Why a registry instead of hard-coding URLs in each ingest script
═════════════════════════════════════════════════════════════════
The master list has 140+ URLs across 6 tiers. Hard-coding them in each
consumer would create drift the moment any URL changes (and they will).
A single YAML file edited once propagates to every consumer.

Public API
══════════
    load_registry()                            -> dict
    get_sources(tier=None, source_class=None,  -> list[Source]
                region=None, cplp_only=False,
                ingest_strategy=None)
    get_source_by_url(url)                     -> Source | None
    tier_summary()                             -> dict[str, int]
    by_source_class()                          -> dict[str, list[Source]]

Source dataclass shape mirrors the YAML entry exactly.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.corpus_registry")

# Path to the YAML registry. Lives next to this module so deployment is
# automatic — bundling this Python module also bundles the YAML.
_REGISTRY_PATH = Path(__file__).parent / "corpus_registry.yaml"

# Valid tier values — used for validation when loading. Anything outside
# this set is logged but not rejected (forward-compatible).
VALID_TIERS = {"A", "A+", "B", "B+", "C", "C+", "D", "E", "F", "unknown"}


@dataclass
class Source:
    """One entry from the corpus registry."""
    url: str
    tier: str
    source_class: str
    category: str = ""
    region: str = "global"
    ingest_strategy: str = "html_crawl"
    frequency: str = "once"
    cplp_relevant: bool = False
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Source":
        return cls(
            url=d.get("url", ""),
            tier=d.get("tier", ""),
            source_class=d.get("source_class", ""),
            category=d.get("category", ""),
            region=d.get("region", "global"),
            ingest_strategy=d.get("ingest_strategy", "html_crawl"),
            frequency=d.get("frequency", "once"),
            cplp_relevant=bool(d.get("cplp_relevant", False)),
            notes=d.get("notes", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "tier": self.tier,
            "source_class": self.source_class,
            "category": self.category,
            "region": self.region,
            "ingest_strategy": self.ingest_strategy,
            "frequency": self.frequency,
            "cplp_relevant": self.cplp_relevant,
            "notes": self.notes,
        }


@lru_cache(maxsize=1)
def load_registry() -> list[Source]:
    """Load the registry from disk. Cached after first call.

    Returns the full source list as a list of Source objects. Empty list
    on any failure (PyYAML missing, file missing, parse error) — callers
    should handle the empty case gracefully so a broken registry doesn't
    take down the chat path.
    """
    if not _REGISTRY_PATH.exists():
        logger.warning("corpus_registry.yaml not found at %s", _REGISTRY_PATH)
        return []
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed — corpus registry unavailable")
        return []
    try:
        with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.warning("Failed to parse corpus_registry.yaml: %s", e)
        return []

    if not isinstance(data, dict) or "sources" not in data:
        logger.warning("corpus_registry.yaml has unexpected shape — missing 'sources' key")
        return []

    raw_sources = data["sources"]
    if not isinstance(raw_sources, list):
        logger.warning("corpus_registry.yaml 'sources' is not a list")
        return []

    out: list[Source] = []
    for i, entry in enumerate(raw_sources):
        if not isinstance(entry, dict):
            logger.debug("Skipping non-dict entry at index %d", i)
            continue
        if not entry.get("url") or not entry.get("tier"):
            logger.debug("Skipping entry with missing url/tier at index %d", i)
            continue
        tier = entry.get("tier", "")
        if tier not in VALID_TIERS:
            logger.debug("Entry at index %d has unknown tier %r", i, tier)
        out.append(Source.from_dict(entry))

    logger.info("Loaded %d corpus sources from %s", len(out), _REGISTRY_PATH.name)
    return out


def get_sources(
    *,
    tier: Optional[str] = None,
    source_class: Optional[str] = None,
    region: Optional[str] = None,
    cplp_only: bool = False,
    ingest_strategy: Optional[str] = None,
    category: Optional[str] = None,
) -> list[Source]:
    """Return sources matching the given filters.

    All filters are AND-combined. None means "any". Tier is matched
    case-sensitively to support B vs B+ disambiguation.
    """
    sources = load_registry()
    out: list[Source] = []
    for s in sources:
        if tier is not None and s.tier != tier:
            continue
        if source_class is not None and s.source_class != source_class:
            continue
        if region is not None and s.region != region:
            continue
        if cplp_only and not s.cplp_relevant:
            continue
        if ingest_strategy is not None and s.ingest_strategy != ingest_strategy:
            continue
        if category is not None and s.category != category:
            continue
        out.append(s)
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="corpus_registry",
        summary="Get Sources",
        source_id="corpus_registry:R-F996",
    )

    return out


def get_source_by_url(url: str) -> Optional[Source]:
    """Return the Source matching this URL exactly, or None."""
    if not url:
        return None
    for s in load_registry():
        if s.url == url:
            return s
    return None


def tier_summary() -> dict[str, int]:
    """Return {tier: count} for the registry. Used by /corpus stats."""
    out: dict[str, int] = {}
    for s in load_registry():
        out[s.tier] = out.get(s.tier, 0) + 1
    return out


def by_source_class() -> dict[str, list[Source]]:
    """Return {source_class: [Source, ...]} grouping. Used by ingest planners
    that want to batch sources from the same publisher together."""
    out: dict[str, list[Source]] = {}
    for s in load_registry():
        out.setdefault(s.source_class, []).append(s)
    return out


def stats() -> dict[str, Any]:
    """Return a summary dict suitable for the /corpus command."""
    sources = load_registry()
    return {
        "total_sources": len(sources),
        "by_tier": tier_summary(),
        "by_strategy": {
            strat: sum(1 for s in sources if s.ingest_strategy == strat)
            for strat in {s.ingest_strategy for s in sources}
        },
        "cplp_relevant": sum(1 for s in sources if s.cplp_relevant),
        "registry_path": str(_REGISTRY_PATH),
    }

# R-F2119 §21a — wire failure handler for corpus_registry
try:
    wire_failure(module="corpus_registry", detail="module shutdown",
                gap_type="engine_failure", source="corpus_registry:shutdown")
except Exception:
    pass
