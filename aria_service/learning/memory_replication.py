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

# Email shipping (Phase 2a) — cross-host durability via the existing SMTP
# channel. Attachment cap is 20 MB (most providers cap at 25 MB).
_MAX_EMAIL_ATTACHMENT_BYTES = 20 * 1024 * 1024


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
    # R-F463 (2026-05-14): brain singleton keys. Knowledge is sharded
    # (its shards are picked up via _CRITICAL_PATTERNS); these single
    # keys are the index/head pointers without which the shards can't
    # be reassembled. mistake_ledger:log is the append-only event log;
    # head_hash anchors the audit chain.
    "crucix:aria:knowledge",            # legacy single-blob (pre-shard fallback)
    "crucix:aria:knowledge:meta",       # shard count + version
    "crucix:mistake_ledger:log",        # append-only event log
    "crucix:mistake_ledger:head_hash",  # audit-chain anchor
)


# R-F463 (2026-05-14): wildcard-pattern backups for the per-entity brain
# keyspaces. DD-audit P0 #5 flagged that ~85% of the brain was unbacked:
#   - aria:verified_facts:*       — per-fact VERIFIED claims (heart of [[aria_infinite_memory]])
#   - crucix:aria:knowledge:shard:* — sharded knowledge snapshot pages
#   - crucix:aria:conversations:* — per-user conversation indices
#   - crucix:mistake_ledger:by_sig:* / by_id:* — secondary indices off the main log
#
# Each pattern carries a `max_keys` cap so a runaway keyspace can't blow
# the backup file size. When the cap fires we record `truncated_at` in
# the snapshot so the recovery operator knows partial restore.
_CRITICAL_PATTERNS: tuple[tuple[str, int], ...] = (
    ("aria:verified_facts:*",          5000),
    ("crucix:aria:knowledge:shard:*",  1000),
    ("crucix:aria:conversations:*",    2000),
    ("crucix:mistake_ledger:by_sig:*", 5000),
    ("crucix:mistake_ledger:by_id:*",  5000),
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

    # R-F463 (2026-05-14): wildcard-pattern keys. Per DD-audit P0 #5,
    # the brain's per-entity keyspaces (verified_facts, knowledge shards,
    # conversations, mistake-ledger indices) were unbacked. Pull each
    # pattern's keys up to max_keys; record truncation in the snapshot
    # so a recovery operator knows whether partial restore is at play.
    snapshot["truncated_patterns"] = {}
    for pattern, max_keys in _CRITICAL_PATTERNS:
        try:
            # scan_keys' count is per-iteration (NOT a hard cap); fetch up
            # to max_keys + 1 to detect overflow honestly.
            matched = await rs.scan_keys(pattern, count=max_keys + 1)
            if len(matched) > max_keys:
                snapshot["truncated_patterns"][pattern] = {
                    "matched_at_least": len(matched),
                    "kept": max_keys,
                }
                matched = matched[:max_keys]
            for k in matched:
                try:
                    val = await rs.get_json(k)
                    if val is None:
                        raw = await rs.get(k)
                        if raw is None:
                            skipped.append(k)
                            continue
                        snapshot["keys"][k] = {"type": "scalar", "value": raw}
                    else:
                        snapshot["keys"][k] = {"type": "json", "value": val}
                except Exception as exc:
                    skipped.append(f"{k} (err: {str(exc)[:60]})")
        except Exception as exc:
            logger.warning("R-F463 scan_keys(%s) failed: %s", pattern, exc)
            skipped.append(f"{pattern} (scan err: {str(exc)[:60]})")

    # Ensure backup dir exists
    try:
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("backup dir create failed %s: %s", _BACKUP_DIR, exc)
        return {"error": f"cannot create {_BACKUP_DIR}: {exc}"}

    # R-F224 (2026-05-11) — snapshot the disk-resident knowledge files
    # too. Pre-R-F224, /data/aria_knowledge.json, /data/aria_signals.json
    # had ZERO backup despite the F87/F88 disk-first migration. These
    # are the three largest stores (knowledge 1M cap, ledger 1M cap,
    # RAG chromadb ~80K chunks). Without snapshotting them, a fly.io
    # machine loss wipes ALL learned facts even though the daily backup
    # runs. RAG chromadb is a directory of binary files — we list its
    # size + filenames but don't inline it (too big); operator should
    # add a separate `fly ssh sftp` step. Knowledge + Signals JSON are
    # small enough to inline as base64-gzipped blobs.
    snapshot["disk_files"] = {}
    import base64 as _b64
    for disk_label, disk_path in (
        ("knowledge", "/data/aria_knowledge.json"),
        ("signals", "/data/aria_signals.json"),
    ):
        try:
            _p = Path(disk_path)
            if _p.exists() and _p.is_file():
                raw = _p.read_bytes()
                # gzip+base64 so the file inlines into the JSON without
                # binary issues. ~3-4x compression on JSON payloads.
                gz_blob = gzip.compress(raw, compresslevel=6)
                snapshot["disk_files"][disk_label] = {
                    "path": disk_path,
                    "raw_bytes": len(raw),
                    "gz_bytes": len(gz_blob),
                    "gz_b64": _b64.b64encode(gz_blob).decode("ascii"),
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                }
            else:
                snapshot["disk_files"][disk_label] = {
                    "path": disk_path,
                    "missing": True,
                }
        except Exception as exc:
            snapshot["disk_files"][disk_label] = {
                "path": disk_path,
                "error": str(exc)[:200],
            }

    # RAG chromadb — too large to inline; record size + file list only
    try:
        rag_dir = Path("/data/aria_rag")
        if rag_dir.exists() and rag_dir.is_dir():
            files = list(rag_dir.rglob("*"))
            total_bytes = sum(f.stat().st_size for f in files if f.is_file())
            snapshot["disk_files"]["rag_chromadb"] = {
                "path": str(rag_dir),
                "file_count": len([f for f in files if f.is_file()]),
                "total_bytes": total_bytes,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "note": "Directory contents NOT inlined (too large). "
                        "Operator must separately snapshot via fly ssh sftp "
                        "to truly back this up.",
            }
        else:
            snapshot["disk_files"]["rag_chromadb"] = {
                "path": str(rag_dir),
                "missing": True,
            }
    except Exception as exc:
        snapshot["disk_files"]["rag_chromadb"] = {"error": str(exc)[:200]}

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

    # 24h stats — keepttl on subsequent writes (same family fix as f981c0a).
    # Daily backup runs would otherwise reset the 24h window every time
    # and the counter would never decay.
    try:
        existing = await rs.get_json("crucix:learning:memory_backup:stats_24h")
        is_fresh = not isinstance(existing, dict)
        stats = existing if isinstance(existing, dict) else {}
        stats["runs_24h"] = stats.get("runs_24h", 0) + 1
        stats["bytes_total_24h"] = stats.get("bytes_total_24h", 0) + size_bytes
        stats["keys_saved_24h"] = stats.get("keys_saved_24h", 0) + len(snapshot["keys"])
        stats["last_run_at"] = snapshot["created_at"]
        if is_fresh:
            await rs.set_json("crucix:learning:memory_backup:stats_24h", stats, ex=86400)
        else:
            await rs.set_json("crucix:learning:memory_backup:stats_24h", stats, keepttl=True)
    except Exception:
        pass

    # ── Phase 2a: email shipping (cross-host durability) ──
    # After every successful on-disk backup, email the gzipped file to
    # the operator as an off-site copy. Uses the existing ARIA_EMAIL_*
    # SMTP config already set up for the WhatsApp listener, so no new
    # infrastructure. Best-effort — a failed email never fails the backup.
    email_result = await _ship_backup_email(out_file, size_bytes, snapshot["created_at"])
    if email_result.get("sent"):
        logger.info(
            "[memory_replication] emailed backup to %s (%d bytes)",
            email_result.get("to"), size_bytes,
        )

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


async def _ship_backup_email(
    backup_path: Path, size_bytes: int, created_iso: str,
) -> dict[str, Any]:
    """Email the backup file to the operator as an off-host copy.

    Uses the same SMTP env vars the WhatsApp listener uses
    (ARIA_SMTP_HOST, ARIA_SMTP_USER, ARIA_SMTP_PASS, ARIA_SMTP_PORT).
    Destination: ARIA_BACKUP_EMAIL_TO (falls back to ARIA_OPERATOR_EMAIL).

    Best-effort. Returns {sent, skipped_reason, to, bytes}. Never raises.
    """
    result: dict[str, Any] = {
        "sent": False,
        "skipped_reason": "",
        "to": "",
        "bytes": size_bytes,
    }

    # Operator can set ARIA_BACKUP_EMAIL_ENABLED=0 to disable shipping
    if (os.getenv("ARIA_BACKUP_EMAIL_ENABLED", "1") or "1").strip() in ("0", "false", "no"):
        result["skipped_reason"] = "ARIA_BACKUP_EMAIL_ENABLED=0"
        return result

    if size_bytes > _MAX_EMAIL_ATTACHMENT_BYTES:
        result["skipped_reason"] = (
            f"attachment size {size_bytes} exceeds cap "
            f"{_MAX_EMAIL_ATTACHMENT_BYTES}; email delivery would fail"
        )
        return result

    # Collect SMTP config — same env vars as emailReader.mjs
    smtp_host = (os.getenv("ARIA_SMTP_HOST")
                 or os.getenv("ARIA_EMAIL_HOST")
                 or "").strip()
    smtp_user = (os.getenv("ARIA_SMTP_USER")
                 or os.getenv("EMAIL_USER")
                 or os.getenv("ARIA_EMAIL_USER")
                 or "").strip()
    smtp_pass = (os.getenv("ARIA_SMTP_PASS")
                 or os.getenv("EMAIL_PASS")
                 or os.getenv("ARIA_EMAIL_PASS")
                 or "").strip()
    smtp_port = int(os.getenv("ARIA_SMTP_PORT")
                    or os.getenv("EMAIL_PORT")
                    or "465")
    to_addr = (os.getenv("ARIA_BACKUP_EMAIL_TO")
               or os.getenv("ARIA_OPERATOR_EMAIL")
               or smtp_user).strip()

    if not (smtp_host and smtp_user and smtp_pass and to_addr):
        result["skipped_reason"] = (
            "SMTP env not configured — set ARIA_SMTP_HOST / "
            "ARIA_SMTP_USER / ARIA_SMTP_PASS to enable backup email shipping"
        )
        return result

    result["to"] = to_addr

    try:
        import smtplib, ssl
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = smtp_user
        msg["To"] = to_addr
        msg["Subject"] = f"ARIA memory backup — {created_iso[:10]}"
        msg.set_content(
            f"Automated off-host copy of ARIA's state snapshot.\n"
            f"\n"
            f"Created (UTC):   {created_iso}\n"
            f"File:            {backup_path.name}\n"
            f"Size:            {size_bytes:,} bytes ({size_bytes / 1024:.1f} KB)\n"
            f"Retention:       local {_RETENTION_DAYS} days\n"
            f"\n"
            f"This email IS your off-host copy. Keep it — in a fly.io-loss\n"
            f"scenario you restore from this attachment:\n"
            f"  1. gunzip <filename>.json.gz  →  <filename>.json\n"
            f"  2. POST to /api/aria/memory/backup/restore with:\n"
            f"     {{\"date\": \"{created_iso[:10]}\", \"dry_run\": false}}\n"
            f"     (after uploading the .json.gz to /data/aria_backups/)\n"
            f"\n"
            f"— ARIA (automated)"
        )

        with backup_path.open("rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="gzip",
                filename=backup_path.name,
            )

        ctx = ssl.create_default_context()
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=45) as s:
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=45) as s:
                s.starttls(context=ctx)
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)

        result["sent"] = True
    except Exception as exc:
        result["skipped_reason"] = f"send failed: {str(exc)[:200]}"
        logger.warning(
            "[memory_replication] email shipping failed: %s", result["skipped_reason"]
        )
    return result


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

    # Report email-shipping configured-ness so the dashboard + operator
    # can see whether off-host copies are actually going out.
    smtp_ok = bool(
        (os.getenv("ARIA_SMTP_HOST") or os.getenv("ARIA_EMAIL_HOST")) and
        (os.getenv("ARIA_SMTP_USER") or os.getenv("ARIA_EMAIL_USER")) and
        (os.getenv("ARIA_SMTP_PASS") or os.getenv("ARIA_EMAIL_PASS"))
    )
    email_disabled = (os.getenv("ARIA_BACKUP_EMAIL_ENABLED", "1") or "1").strip() in ("0", "false", "no")
    email_destination = (
        os.getenv("ARIA_BACKUP_EMAIL_TO")
        or os.getenv("ARIA_OPERATOR_EMAIL")
        or (os.getenv("ARIA_SMTP_USER") or os.getenv("ARIA_EMAIL_USER"))
        or ""
    )
    email_shipping_status = (
        "DISABLED" if email_disabled
        else ("LIVE" if smtp_ok else "GATED")
    )

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
        # Off-host shipping status (Phase 2a)
        "email_shipping_status": email_shipping_status,
        "email_destination":     email_destination,
    }


def summary() -> dict[str, Any]:
    """Capability-manifest summary."""
    return {
        "critical_keys_tracked": len(_CRITICAL_KEYS),
        "critical_patterns_tracked": len(_CRITICAL_PATTERNS),
        "critical_patterns": [p for p, _ in _CRITICAL_PATTERNS],
        "backup_dir": str(_BACKUP_DIR),
        "retention_days": _RETENTION_DAYS,
        "compression": "gzip level 6",
    }
