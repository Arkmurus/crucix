"""ARIA Capability Manifest — auto-derived inventory of what ARIA can do.

ARIA cannot assess her own strengths, or diff herself against peers, without
a machine-readable description of her own surface area. This module derives
that description from the codebase itself — never hand-maintained, so it
cannot drift silently.

Five capability axes, all derived from authoritative sources in the repo:

  1. modules          — every aria_service/intel/*.py file (the working set
                        of intelligence capabilities ARIA has)
  2. jurisdictions    — registry_adapters._SUPPORTED_JURISDICTIONS (the
                        countries where she can pull a company registry)
  3. autonomous_tasks — entries in aria_service/autonomous/tasks.yaml (what
                        she runs unprompted, and which are currently enabled)
  4. corpus_tiers     — tier counts from corpus_registry.yaml (how deep her
                        knowledge substrate goes per tier)
  5. sanctions_lists  — set of sanctions sources wired in. Derived by
                        introspecting the sanctions module for SOURCES.

The manifest is signed by timestamp + content hash. `snapshot()` persists
it to Redis. `diff_vs_last()` compares the latest snapshot against the prior
one — any capability that *disappeared* is a regression signal and is emitted
to self_metrics as coverage=0.0 on the capability_manifest axis, so it
shows up in the nightly self_assess rollup.

Public API
──────────
  derive() -> dict                      # fresh manifest from the code
  snapshot() -> dict                    # derive + persist + diff + emit
  latest() -> dict | None               # last persisted snapshot
  diff(old, new) -> dict                # pure diff between two manifests
  history(limit=10) -> list[dict]       # snapshot history
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.capability_manifest")

_KEY_HISTORY = "crucix:capability_manifest:history"
_KEY_LATEST = "crucix:capability_manifest:latest"

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INTEL_DIR = _REPO_ROOT / "aria_service" / "intel"
_TASKS_YAML = _REPO_ROOT / "aria_service" / "autonomous" / "tasks.yaml"
_CORPUS_YAML = _INTEL_DIR / "corpus_registry.yaml"


def _list_intel_modules() -> list[str]:
    """Every .py under aria_service/intel/ excluding dunder + private helpers.
    Filename (minus extension) = capability key."""
    if not _INTEL_DIR.exists():
        return []
    out: list[str] = []
    for p in sorted(_INTEL_DIR.glob("*.py")):
        name = p.stem
        if name.startswith("__") or name.startswith("_"):
            continue
        out.append(name)
    return out


def _list_jurisdictions() -> list[str]:
    try:
        from . import registry_adapters
        return sorted(registry_adapters._SUPPORTED_JURISDICTIONS)
    except Exception as e:
        logger.debug("capability_manifest: jurisdictions derive failed: %s", e)
        return []


def _list_autonomous_tasks() -> dict[str, Any]:
    """Parse tasks.yaml with PyYAML if available, else regex fallback.
    Returns: {total, enabled, ids: [...], enabled_ids: [...]}"""
    if not _TASKS_YAML.exists():
        return {"total": 0, "enabled": 0, "ids": [], "enabled_ids": []}
    text = _TASKS_YAML.read_text(encoding="utf-8", errors="ignore")
    try:
        import yaml
        doc = yaml.safe_load(text) or {}
        tasks = doc.get("tasks", []) if isinstance(doc, dict) else []
        ids = [str(t.get("id", "")).strip() for t in tasks if t.get("id")]
        enabled_ids = [str(t.get("id", "")).strip() for t in tasks if t.get("enabled")]
        return {
            "total": len(ids), "enabled": len(enabled_ids),
            "ids": sorted(ids), "enabled_ids": sorted(enabled_ids),
        }
    except Exception:
        # Regex fallback — counts "- id:" lines; enabled count is best-effort.
        ids = re.findall(r"^\s*-\s*id:\s*([A-Za-z0-9_\-]+)", text, re.MULTILINE)
        enabled_blocks = re.findall(r"enabled:\s*true", text, re.IGNORECASE)
        return {
            "total": len(ids), "enabled": len(enabled_blocks),
            "ids": sorted(set(ids)), "enabled_ids": [],
        }


def _list_corpus_tiers() -> dict[str, int]:
    """Count entries per tier from corpus_registry.yaml. Regex-based so we
    don't require pyyaml — the manifest stays derivable in any environment."""
    if not _CORPUS_YAML.exists():
        return {}
    text = _CORPUS_YAML.read_text(encoding="utf-8", errors="ignore")
    tiers = re.findall(r"^\s*tier:\s*([A-Z][+]?)", text, re.MULTILINE)
    counts: dict[str, int] = {}
    for t in tiers:
        counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items()))


def _list_sanctions_sources() -> list[str]:
    """Introspect sanctions module for declared sources. Best-effort — if
    the module shape changes, this returns [] and the axis just shows 0."""
    try:
        from . import sanctions  # type: ignore
        for attr in ("SOURCES", "LISTS", "SANCTIONS_SOURCES"):
            v = getattr(sanctions, attr, None)
            if isinstance(v, (list, tuple, set)):
                return sorted([str(x) for x in v])
            if isinstance(v, dict):
                return sorted(v.keys())
    except Exception as e:
        logger.debug("capability_manifest: sanctions derive failed: %s", e)
    return []


def _hash_manifest(m: dict) -> str:
    """Stable hash over the capability content, ignoring generated_at."""
    stable = {k: v for k, v in m.items() if k not in ("generated_at", "content_hash")}
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def derive() -> dict:
    """Build a fresh manifest from the current codebase. Pure function —
    never writes anywhere. Safe to call for dry-run / diagnostic."""
    modules = _list_intel_modules()
    jurisdictions = _list_jurisdictions()
    tasks = _list_autonomous_tasks()
    tiers = _list_corpus_tiers()
    sanctions_sources = _list_sanctions_sources()

    manifest: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "modules": {"count": len(modules), "items": modules},
        "jurisdictions": {"count": len(jurisdictions), "items": jurisdictions},
        "autonomous_tasks": tasks,
        "corpus_tiers": tiers,
        "sanctions_sources": {"count": len(sanctions_sources), "items": sanctions_sources},
    }
    manifest["content_hash"] = _hash_manifest(manifest)
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="capability_manifest",
        summary="Derive",
        source_id="capability_manifest:R-F1001",
    )

    return manifest


def diff(old: dict | None, new: dict) -> dict:
    """Report what's been added / removed between two manifests.

    Removals are the important half — they're regressions. Additions are
    growth. Returns {added, removed, changed_counts} per axis.
    """
    if not old:
        return {"baseline": True, "added": {}, "removed": {}, "changed_counts": {}}

    def _set_diff(axis: str, field: str = "items") -> dict:
        a = set(old.get(axis, {}).get(field, []) or [])
        b = set(new.get(axis, {}).get(field, []) or [])
        return {"added": sorted(b - a), "removed": sorted(a - b)}

    added: dict[str, list] = {}
    removed: dict[str, list] = {}
    for axis in ("modules", "jurisdictions", "sanctions_sources"):
        d = _set_diff(axis)
        if d["added"]:
            added[axis] = d["added"]
        if d["removed"]:
            removed[axis] = d["removed"]

    # Autonomous tasks — compare ids
    old_ids = set(old.get("autonomous_tasks", {}).get("ids", []) or [])
    new_ids = set(new.get("autonomous_tasks", {}).get("ids", []) or [])
    if new_ids - old_ids:
        added["autonomous_tasks"] = sorted(new_ids - old_ids)
    if old_ids - new_ids:
        removed["autonomous_tasks"] = sorted(old_ids - new_ids)

    # Corpus tiers — count drift per tier
    tier_drift: dict[str, int] = {}
    old_tiers = old.get("corpus_tiers", {}) or {}
    new_tiers = new.get("corpus_tiers", {}) or {}
    for t in set(old_tiers) | set(new_tiers):
        delta = int(new_tiers.get(t, 0)) - int(old_tiers.get(t, 0))
        if delta:
            tier_drift[t] = delta

    return {
        "baseline": False,
        "added": added,
        "removed": removed,
        "corpus_tier_drift": tier_drift,
        "content_hash_changed": old.get("content_hash") != new.get("content_hash"),
    }


async def latest() -> dict | None:
    from . import redis_store as rs
    return await rs.get_json(_KEY_LATEST)


async def weekly_diff(days: int = 7) -> dict:
    """Return a capability diff between the latest manifest and the one
    ~`days` days old. Used by core_develop's weekly review; pre-2026-04-20
    this function didn't exist and the `hasattr()` gate silently skipped
    the capability-change panel of the meta-review.

    Picks the history entry whose `generated_at` is closest to `days` days
    ago (falls back to the oldest entry if history is shallow). Returns
    the same shape as `diff(old, new)` plus `compared_gap_days`."""
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    new = await latest()
    if not new:
        return {"baseline": True, "added": {}, "removed": {},
                "corpus_tier_drift": {}, "content_hash_changed": False,
                "compared_gap_days": None}
    # Read ~2×days of history so there's something to pick from
    hist = await history(limit=max(days * 2, 20))
    if not hist:
        d = diff(None, new)
        d["compared_gap_days"] = None
        return d
    target_ts = _dt.now(_tz.utc) - _td(days=days)
    # Pick the entry whose generated_at is closest to target (prefer older)
    def _parse(ts: str) -> _dt | None:
        if not ts:
            return None
        try:
            return _dt.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            return None
    candidates = [(h, _parse(h.get("generated_at", ""))) for h in hist]
    candidates = [(h, t) for h, t in candidates if t is not None]
    if not candidates:
        old = hist[-1]
    else:
        # Nearest to target; if all newer than target, take the oldest
        candidates.sort(key=lambda ht: abs((ht[1] - target_ts).total_seconds()))
        old = candidates[0][0]
    d = diff(old, new)
    old_ts = _parse(old.get("generated_at", ""))
    new_ts = _parse(new.get("generated_at", ""))
    if old_ts and new_ts:
        d["compared_gap_days"] = round((new_ts - old_ts).total_seconds() / 86400.0, 1)
    else:
        d["compared_gap_days"] = None
    return d


async def history(limit: int = 10) -> list[dict]:
    from . import redis_store as rs
    raw = await rs.lrange(_KEY_HISTORY, 0, max(0, limit - 1))
    out: list[dict] = []
    for r in raw:
        try:
            out.append(json.loads(r) if isinstance(r, str) else r)
        except Exception:
            continue
    return out


async def snapshot() -> dict:
    """Derive → persist → diff → emit. Returns the new manifest with a
    `diff_vs_prior` field so callers (briefing, self_assess) can render
    the drift without re-querying.

    Removed capabilities emit a self_metrics coverage=0.0 signal per
    axis/item so the self_assess rollup treats them as regressions.
    """
    from . import redis_store as rs

    old = await latest()
    new = derive()
    d = diff(old, new)
    new["diff_vs_prior"] = d

    try:
        await rs.set_json(_KEY_LATEST, new)
        await rs.lpush(_KEY_HISTORY, json.dumps(new, default=str))
        try:
            await rs.ltrim(_KEY_HISTORY, 0, 51)  # keep ~1 year of weekly snapshots
        except Exception:
            pass
    except Exception as e:
        logger.warning("capability_manifest: persist failed: %s", e)

    # Emit regression signals for anything that disappeared. Additions are
    # good but not metrics — they show up in the briefing directly.
    if d.get("removed"):
        try:
            from . import self_metrics
            for axis, items in d["removed"].items():
                for item in items:
                    await self_metrics.emit(
                        "coverage",
                        f"manifest_{axis}",
                        "capability_lost",
                        0.0,
                        context={"item": item},
                        source_module="capability_manifest",
                    )
            logger.warning(
                "capability_manifest: %d capabilities removed since last snapshot",
                sum(len(v) for v in d["removed"].values()),
            )
        except Exception as e:
            logger.debug("capability_manifest: self_metrics emit failed: %s", e)

    return new

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
