"""Memory replication — daily forensic snapshot of ARIA's critical state.

Phase 1 (this module): snapshot every important Redis key to a gzipped
JSON file on the fly.io persistent volume at /data/aria_backups/
YYYY-MM-DD.json.gz. 30-day retention. Restore helper.

What it protects against:
  - Accidental Redis FLUSHDB / wrong-key DEL
  - Redis memory pressure eviction (LRU)
  - fly.io Redis add-on restart that loses in-memory state
  - Operator recovery: "what did mastery look like 5 days ago?"

What it does NOT protect against:
  - Loss of the fly.io machine + volume (same-host backup = same blast
    radius as primary). Phase 2 (future commit) will mirror to S3 or
    Neon Postgres for genuine cross-host durability.

Persists:
  - Snapshot file:   /data/aria_backups/YYYY-MM-DD.json.gz
  - Manifest:        /data/aria_backups/manifest.json
  - Stats (24h):     crucix:learning:memory_backup:stats_24h

Scheduled: MEMORY-BACKUP-DAILY at 04:00 UTC (quiet hour, post-export batch).
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.learning.memory_replication")


_BACKUP_DIR = Path(os.getenv("ARIA_BACKUP_DIR", "/data/aria_backups"))
_MANIFEST_FILE = _BACKUP_DIR / "manifest.json"
_RETENTION_DAYS = int(os.getenv("ARIA_BACKUP_RETENTION_DAYS", "30"))


# ═══════════════════════════════════════════════════════════════════════
# Critical-keys registry — what we snapshot every day
# ═══════════════════════════════════════════════════════════════════════
#
# Ordered by recovery priority. If a partial restore is needed, the
# operator would restore this list top-to-bottom.

_CRITICAL_KEYS: tuple[str, ...] = (
    # Mastery + calibration
    "crucix:aria:student:mastery",
    "crucix:aria:student:regional_mastery",
    "crucix:calibration:review",
    "crucix:calibration:baseline",
    "crucix:calibration:last_correction",
    # DD state
    "crucix:dd:report_index",
    "crucix:aria:quarantined_runs",
    # Claim ledger + mem0
    "crucix:aria:claim_ledger",
    "crucix:aria:mem0:facts",
    # Bright-lines + verification
    "crucix:aria:bright_lines:hits_24h",
    "crucix:learning:verification:recent",
    "crucix:learning:verification:stats_24h",
    # Learning loop state
    "crucix:learning:spider:queue",
    "crucix:learning:spider:visited",
    "crucix:learning:spider:stats_24h",
    "crucix:learning:metacog:journal",
    "crucix:learning:research:attempts",
    "crucix:learning:research:stats_24h",
    "crucix:learning:style:exemplars",
    "crucix:learning:style:stats_24h",
    # Autonomous engine
    "crucix:autonomous:fires_24h",
    # Predictor + audit log (heads, not whole trail)
    "crucix:chat_audit:index",
    # Source validator pending queues
    "crucix:source_validator:pending_count",
    "crucix:constitution:pending_count",
    "crucix:codegen:pending_count",
    "crucix:golden:pending_count",
    "crucix:ground_truth:pending_count",
)


# ═══════════════════════════════════════════════════════════════════════
# Snapshot
# ═══════════════════════════════════════════════════════════════════════

async def run_daily_backup() -> dict[str, Any]:
    """Snapshot every critical key to a gzipped JSON on the volume.
    Returns {file, keys_saved, size_bytes, skipped, duration_s}."""
    t_start = time.monotonic()
    snapshot: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host":       os.uname().nodename if hasattr(os, "uname") else "unknown",
        "retention_days": _RETENTION_DAYS,
        "keys":       {},
    }
    skipped: list[str] = []
    try:
        from ..intel import redis_store as rs
    except Exception as exc:
        logger.warning("redis_store import failed: %s", exc)
        return {"error": f"redis_store unavailable: {exc}"}

    for key in _CRITICAL_KEYS:
        try:
            # Prefer get_json where the payload is JSON-shaped; fall back
            # to raw string get() for scalar counters.
            val = await rs.get_json(key)
            if val is None:
                raw = await rs.get(key)
                if raw is None:
                    skipped.append(key)
                    continue
                snapshot["keys"][key] = {"type": "scalar", "value": raw}
            else:
                snapshot["keys"][key] = {"type": "json", "value": val}
        except Exception as exc:
            skipped.append(f"{key} (err: {str(exc)[:60]})")

    # Ensure backup dir exists
    try:
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("backup dir create failed %s: %s", _BACKUP_DIR, exc)
        return {"error": f"cannot create {_BACKUP_DIR}: {exc}"}

    today = datetime.now(timezone.utc).date().isoformat()
    out_file = _BACKUP_DIR / f"{today}.json.gz"
    payload = json.dumps(snapshot, ensure_ascii=False, default=str).encode("utf-8")
    try:
        with gzip.open(out_file, "wb", compresslevel=6) as gz:
            gz.write(payload)
    except Exception as exc:
        logger.warning("backup file write failed %s: %s", out_file, exc)
        return {"error": f"write failed: {exc}"}

    size_bytes = out_file.stat().st_size

    # Update manifest
    manifest = _load_manifest()
    manifest.setdefault("runs", []).append({
        "at": snapshot["created_at"],
        "file": out_file.name,
        "keys_saved": len(snapshot["keys"]),
        "skipped": len(skipped),
        "size_bytes": size_bytes,
    })
    # Trim manifest to last 90 entries to keep the file small
    manifest["runs"] = manifest["runs"][-90:]
    manifest["last_run_at"] = snapshot["created_at"]
    manifest["last_run_file"] = out_file.name
    _save_manifest(manifest)

    # Purge old backups (retention)
    removed = await _purge_old_backups()

    # 24h stats
    try:
        stats = await rs.get_json("crucix:learning:memory_backup:stats_24h") or {}
        stats["runs_24h"] = stats.get("runs_24h", 0) + 1
        stats["bytes_total_24h"] = stats.get("bytes_total_24h", 0) + size_bytes
        stats["keys_saved_24h"] = stats.get("keys_saved_24h", 0) + len(snapshot["keys"])
        stats["last_run_at"] = snapshot["created_at"]
        await rs.set_json("crucix:learning:memory_backup:stats_24h", stats, ex=86400)
    except Exception:
        pass

    # brain_hook — every successful backup is a durability win
    try:
        from ..intel import brain_hook
        await brain_hook.absorb(
            module="memory_replication",
            summary=f"Daily backup: {len(snapshot['keys'])} keys, "
                    f"{size_bytes} bytes, {removed} old purged",
            success=True,
        )
    except Exception:
        pass

    summary = {
        "file": str(out_file),
        "keys_saved": len(snapshot["keys"]),
        "keys_skipped": skipped[:10],
        "size_bytes": size_bytes,
        "size_kb": round(size_bytes / 1024, 1),
        "purged_old": removed,
        "duration_s": round(time.monotonic() - t_start, 2),
    }
    logger.info("[memory_replication] %s", summary)
    return summary


async def _purge_old_backups() -> int:
    """Remove backup files older than _RETENTION_DAYS. Returns count removed."""
    if not _BACKUP_DIR.exists():
        return 0
    cutoff = time.time() - (_RETENTION_DAYS * 86400)
    removed = 0
    try:
        for p in _BACKUP_DIR.glob("*.json.gz"):
            if p.stat().st_mtime < cutoff:
                try:
                    p.unlink()
                    removed += 1
                except Exception:
                    continue
    except Exception as exc:
        logger.debug("purge failed: %s", exc)
    return removed


# ═══════════════════════════════════════════════════════════════════════
# Restore
# ═══════════════════════════════════════════════════════════════════════

async def restore_from_backup(
    date: str,
    keys: list[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Restore ALL (or a subset of) keys from a named backup file.

    Args:
      date:    "YYYY-MM-DD" — the backup filename
      keys:    None = restore everything in the snapshot;
               otherwise restore only these keys.
      dry_run: True = report what WOULD be restored without writing.
               Default True — operator MUST pass dry_run=False to
               actually overwrite live Redis.

    Returns: {file, would_restore, actually_restored, errors, dry_run}
    """
    out: dict[str, Any] = {
        "file": None,
        "would_restore": 0,
        "actually_restored": 0,
        "errors": [],
        "dry_run": dry_run,
    }
    target = _BACKUP_DIR / f"{date}.json.gz"
    if not target.exists():
        out["errors"].append(f"backup file not found: {target}")
        return out
    out["file"] = str(target)

    try:
        with gzip.open(target, "rb") as gz:
            snapshot = json.loads(gz.read().decode("utf-8"))
    except Exception as exc:
        out["errors"].append(f"snapshot read failed: {exc}")
        return out

    snap_keys = snapshot.get("keys") or {}
    if keys:
        snap_keys = {k: v for k, v in snap_keys.items() if k in keys}
    out["would_restore"] = len(snap_keys)

    if dry_run:
        return out

    try:
        from ..intel import redis_store as rs
    except Exception as exc:
        out["errors"].append(f"redis_store unavailable: {exc}")
        return out

    for key, entry in snap_keys.items():
        try:
            kind = entry.get("type") if isinstance(entry, dict) else None
            value = entry.get("value") if isinstance(entry, dict) else None
            if kind == "scalar":
                await rs.set(key, value)
            else:
                # Default: treat as JSON
                await rs.set_json(key, value)
            out["actually_restored"] += 1
        except Exception as exc:
            out["errors"].append(f"{key}: {str(exc)[:160]}")

    logger.warning(
        "[memory_replication] RESTORE: %d/%d keys from %s (errors: %d)",
        out["actually_restored"], out["would_restore"], target, len(out["errors"]),
    )
    return out


# ═══════════════════════════════════════════════════════════════════════
# Listing + manifest
# ═══════════════════════════════════════════════════════════════════════

async def list_backups() -> list[dict[str, Any]]:
    """Return all backup files sorted newest-first with size + date."""
    out: list[dict[str, Any]] = []
    if not _BACKUP_DIR.exists():
        return out
    try:
        for p in sorted(_BACKUP_DIR.glob("*.json.gz"), reverse=True):
            try:
                stat = p.stat()
                out.append({
                    "filename": p.name,
                    "date": p.stem.replace(".json", ""),
                    "size_bytes": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
            except Exception:
                continue
    except Exception as exc:
        logger.debug("list_backups failed: %s", exc)
    return out


def _load_manifest() -> dict[str, Any]:
    try:
        if _MANIFEST_FILE.exists():
            return json.loads(_MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("manifest load failed: %s", exc)
    return {}


def _save_manifest(m: dict[str, Any]) -> None:
    try:
        _MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MANIFEST_FILE.write_text(
            json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger.debug("manifest save failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════
# Stats + summary
# ═══════════════════════════════════════════════════════════════════════

async def get_stats() -> dict[str, Any]:
    try:
        from ..intel import redis_store as rs
        stats = await rs.get_json("crucix:learning:memory_backup:stats_24h") or {}
    except Exception:
        stats = {}
    backups = await list_backups()
    total_bytes = sum(b.get("size_bytes", 0) for b in backups)
    return {
        "runs_24h": stats.get("runs_24h", 0),
        "keys_saved_24h": stats.get("keys_saved_24h", 0),
        "bytes_24h": stats.get("bytes_total_24h", 0),
        "last_run_at": stats.get("last_run_at", ""),
        "total_backups_on_disk": len(backups),
        "total_bytes_on_disk": total_bytes,
        "total_kb_on_disk": round(total_bytes / 1024, 1),
        "oldest_backup": backups[-1]["date"] if backups else "",
        "newest_backup": backups[0]["date"] if backups else "",
        "retention_days": _RETENTION_DAYS,
        "backup_dir": str(_BACKUP_DIR),
    }


def summary() -> dict[str, Any]:
    """Capability-manifest summary."""
    return {
        "critical_keys_tracked": len(_CRITICAL_KEYS),
        "backup_dir": str(_BACKUP_DIR),
        "retention_days": _RETENTION_DAYS,
        "compression": "gzip level 6",
    }
