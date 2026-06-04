"""Training-data export — accumulates ARIA's own interactions as a
fine-tune-ready JSONL corpus.

Every day, this module scans the last N days of:
  - DD reports (from dd_orchestrator.REPORT_INDEX_KEY) that passed
    verification + were not quarantined
  - chat turns (from chat_audit_log) where the response was tagged
    `grounded` by the verifier
  - writer outputs (from WriterAuditLog) with `degraded=False`
  - adversarial attacks that ARIA passed (attack_run.passed=True)
  - compliance-screen responses where the live check matched ARIA's
    answer

It filters out anything tagged UNCERTAIN / quarantined / fallback /
degraded. The output is the training corpus nobody else has —
Arkmurus-specific DD + compliance + brokering examples, structured
as OpenAI-format instruction-input-output triples.

Output: /data/aria_training/YYYY-MM-DD.jsonl (one file per day)
plus a rolling manifest at /data/aria_training/manifest.json
describing current corpus size + train/val/test splits.

LLM-free. Zero external cost. Runs LEARNING-EXPORT-DAILY at 03:30 UTC.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.learning.training_export")

# R-F1319: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1319
    _ws1319(
        module="learning.training_export",
        summary="Training Export active",
        source_id="learning:training_export:R-F1319",
    )
except Exception:
    pass

# Destination — default to fly.io persistent volume, fall back to /tmp
_EXPORT_DIR = Path(os.getenv("ARIA_TRAINING_EXPORT_DIR", "/data/aria_training"))
_MANIFEST_FILE = _EXPORT_DIR / "manifest.json"

# Quality floor — do NOT include examples below these thresholds
_MIN_WORD_COUNT = 20          # replies shorter than this aren't useful
_MAX_WORD_COUNT = 4000        # cap monster replies
_EXCLUDE_TAGS = (
    "UNCERTAIN", "SPECULATIVE", "CITATION-QUARANTINED",
    "[RECALL — not in document]", "[BRIGHT-LINES TRIGGERED]",
    "fallback", "degraded",
)


# ═══════════════════════════════════════════════════════════════════════
# Collection helpers — each returns list[dict] of {user, assistant, meta}
# ═══════════════════════════════════════════════════════════════════════

# Per-source diagnostics for the most recent run. Populated by each
# collector so run_daily_export can surface "why did this source return
# 0?" to the dashboard instead of silently emitting {"written": 0}.
# Before this, 45 spider ingests + 0 training examples gave no signal
# that (for example) the writer audit path was missing.
_last_collection_diag: dict[str, dict[str, Any]] = {}


def _set_diag(source: str, count: int,
              error: str | None = None,
              reason: str | None = None,
              **extra: Any) -> None:
    entry: dict[str, Any] = {
        "count": count,
        "error": error,
        "reason": reason,
    }
    # Caller-supplied diagnostic fields (e.g. kept_by_tier breakdown for
    # chat_turns). Surfaced via training_export.last_run_diagnostics so
    # the dashboard can show "5 grounded + 12 well_formed" instead of
    # just "17 collected".
    for k, v in extra.items():
        entry[k] = v
    _last_collection_diag[source] = entry
    if error:
        logger.warning("[training_export] %s: %s", source, error)
    elif reason and count == 0:
        logger.warning("[training_export] %s: 0 collected (%s)", source, reason)
    else:
        logger.info("[training_export] %s: %d collected", source, count)


async def _collect_chat_turns(days: int) -> list[dict[str, Any]]:
    """High-grounded chat turns from the audit log."""
    out: list[dict[str, Any]] = []
    try:
        from ..intel import chat_audit_log as cal
        # chat_audit_log stores entries keyed by crucix:chat_audit:{date}:{index}
        recent = await cal.get_stats()
        if not isinstance(recent, dict):
            return out
        # Pull recent entries through the public accessor if it exists
        if hasattr(cal, "get_recent"):
            entries = await cal.get_recent(limit=500)
        else:
            _set_diag("chat_turns", 0, reason="chat_audit_log.get_recent_missing")
            return out
        if not entries:
            # Distinguish "audit log empty" from "all filtered out". If the
            # audit log itself has no entries, upstream /chat/stream is
            # bypassing record_chat — a different bug than a tight filter.
            _set_diag("chat_turns", 0, reason="chat_audit_log_empty_for_window")
            return out
        # 2026-04-26 angle (a): cross-sweep upgrades. The verification
        # accumulator queue carries entries that were originally
        # well_formed/unverified but whose claims have since gained
        # 2+-source corroboration in verified_intel. We import lazily
        # so a stale verification module doesn't block the rest of the
        # collector.
        try:
            from ..intel import verification_accumulator as _va
        except Exception:
            _va = None

        kept_by_tier = {"grounded": 0, "well_formed": 0, "upgraded": 0}
        for e in entries or []:
            if not isinstance(e, dict):
                continue
            user_msg = e.get("user_message") or e.get("message") or ""
            aria_reply = e.get("response") or e.get("reply") or ""
            grounded_rate = e.get("grounded_rate", 0) or 0
            # chat_audit_log.record_chat writes `verification_status`; older
            # code wrote `honesty_verdict`. Accept either (same treatment as
            # style_learner `c0418c5`) so a field rename doesn't silently
            # reject every entry.
            verdict = (
                (e.get("verification_status") or e.get("honesty_verdict") or "").lower()
            )
            # Cross-sweep upgrade check: if the accumulator queue says
            # this entry is now grounded (later sweeps added the missing
            # corroboration), promote the verdict here. The audit log
            # entry itself is HMAC-signed and immutable — the upgrade
            # lives in the sidecar queue.
            upgraded = False
            if _va and verdict in ("well_formed", "unverified"):
                rh = e.get("response_hash") or ""
                if rh:
                    try:
                        queued = await _va.get_status(rh)
                    except Exception:
                        queued = None
                    if queued and queued.get("current_status") == "grounded":
                        verdict = "grounded"
                        # Use the upgraded grounded_rate so meta is honest
                        # about what tipped the entry into the bucket.
                        grounded_rate = queued.get("upgraded_grounded_rate") or grounded_rate
                        upgraded = True
            # 2026-04-26 angle (b): accept two training tiers.
            # - grounded:    claims actually verified (high quality), gated
            #                on grounded_rate >= 0.40 belt-and-braces in case
            #                the engine ever drifts.
            # - well_formed: tier-marker discipline good but corroboration
            #                thin (sweep signals are typically 1-source on
            #                first appearance). Common case in production
            #                — without accepting this the chat training
            #                pipeline starves to 0 examples.
            if verdict == "grounded":
                if grounded_rate < 0.40:
                    continue
                tier = "upgraded" if upgraded else "grounded"
            elif verdict == "well_formed":
                tier = "well_formed"
            else:
                continue
            if not user_msg or not aria_reply:
                continue
            wc = len(aria_reply.split())
            if wc < _MIN_WORD_COUNT or wc > _MAX_WORD_COUNT:
                continue
            if any(tag.lower() in aria_reply.lower() for tag in _EXCLUDE_TAGS):
                continue
            kept_by_tier[tier] += 1
            out.append({
                "user": user_msg[:3000],
                "assistant": aria_reply[:16000],
                "meta": {
                    "source": "chat_audit",
                    "grounded_rate": grounded_rate,
                    "verification_tier": tier,
                    "trace_id": e.get("trace_id"),
                },
            })
        _set_diag(
            "chat_turns",
            len(out),
            reason=None if out else "no_grounded_or_well_formed_chat_turns_in_window",
            kept_by_tier=kept_by_tier,
            upgraded_examples=kept_by_tier.get("upgraded", 0),
        )
    except Exception as exc:
        _set_diag("chat_turns", 0, error=f"{type(exc).__name__}: {exc}")
    return out


async def _collect_dd_reports(days: int) -> list[dict[str, Any]]:
    """DD reports that passed verification + are not quarantined."""
    out: list[dict[str, Any]] = []
    try:
        from ..intel import redis_store as rs
        from ..intel import dd_orchestrator as dd
        from ..intel import run_quarantine
        idx = await rs.get_json(getattr(dd, "REPORT_INDEX_KEY", "crucix:dd:report_index"))
        items = idx if isinstance(idx, list) else (idx.get("items") if isinstance(idx, dict) else [])
        cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
        for it in items:
            if not isinstance(it, dict):
                continue
            generated = it.get("generated_at") or it.get("run_at") or ""
            try:
                ts = datetime.fromisoformat(generated.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            if ts < cutoff:
                continue
            run_id = it.get("run_id") or ""
            # Quarantine filter — tonight's F3 bad runs will be skipped
            if run_id and await run_quarantine.is_quarantined(run_id):
                continue
            # Fetch the full report body for the training example
            body_key = f"crucix:dd:report:{run_id}"
            body = await rs.get_json(body_key)
            if not isinstance(body, dict):
                continue
            entity = (body.get("identity") or {}).get("entity_name") or ""
            if not entity or len(entity) < 3:
                continue
            # Reconstruct a training instance: "run DD on X" → DD report
            rendered = body.get("rendered") or body.get("markdown") or ""
            if not rendered or len(rendered.split()) < _MIN_WORD_COUNT:
                continue
            out.append({
                "user": f"Run a deep DD on {entity}.",
                "assistant": rendered[:16000],
                "meta": {
                    "source": "dd_report",
                    "run_id": run_id,
                    "risk": body.get("risk_classification", ""),
                },
            })
        _set_diag("dd_reports", len(out),
                  reason=None if out else "no_qualifying_dd_reports_in_window")
    except Exception as exc:
        _set_diag("dd_reports", 0, error=f"{type(exc).__name__}: {exc}")
    return out


async def _collect_writer_outputs(days: int) -> list[dict[str, Any]]:
    """Writer outputs with degraded=False — full-fidelity examples."""
    out: list[dict[str, Any]] = []
    try:
        audit_path = Path(os.getenv("ARIA_WRITER_AUDIT_PATH",
                                     "/data/aria_writer_audit.jsonl"))
        if not audit_path.exists():
            _set_diag("writers", 0, reason=f"audit_path_missing:{audit_path}")
            return out
        cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
        for line in audit_path.read_text(encoding="utf-8").splitlines()[-2000:]:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            md = entry.get("metadata") or {}
            if md.get("degraded") is True:
                continue
            try:
                ts = datetime.fromisoformat(
                    entry.get("timestamp", "").replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                ts = 0
            if ts < cutoff:
                continue
            writer_type = md.get("writer", "document")
            reference = md.get("reference", "")
            wc = md.get("word_count", 0)
            if wc < _MIN_WORD_COUNT or wc > _MAX_WORD_COUNT:
                continue
            out.append({
                "user": f"Produce a {writer_type} document (reference {reference}).",
                "assistant": f"[Document produced: {reference}, {wc} words, hash {md.get('output_hash', '')[:10]}]",
                "meta": {
                    "source": "writer_output",
                    "writer_type": writer_type,
                    "word_count": wc,
                },
            })
        _set_diag("writers", len(out),
                  reason=None if out else "no_qualifying_writer_outputs_in_window")
    except Exception as exc:
        _set_diag("writers", 0, error=f"{type(exc).__name__}: {exc}")
    return out


async def _collect_adversarial_passes(days: int) -> list[dict[str, Any]]:
    """Passed adversarial attacks — ARIA's defensive responses as examples.

    `adversarial_challenge.recent_runs` returns aggregate-run records
    (one per full suite execution). Individual attack results are inside
    `run["results"]`, each carrying `{attack_id, passed, responses[], ...}`.
    The attack prompt text isn't persisted in the run record — look it up
    from ATTACK_LIBRARY by attack_id. Before 2026-04-24 this function
    iterated `run` directly and read non-existent `attack_prompt` /
    `response` keys, so every iteration was filtered by the empty-turn
    guard and zero examples ever landed in training.
    """
    out: list[dict[str, Any]] = []
    try:
        from ..intel import adversarial_challenge as ac
        if not hasattr(ac, "recent_runs"):
            _set_diag("adversarial", 0, reason="recent_runs_not_available")
            return out
        runs = await ac.recent_runs(limit=50)
        library_by_id = {a.id: a for a in getattr(ac, "ATTACK_LIBRARY", [])}
        cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
        for run in runs or []:
            if not isinstance(run, dict):
                continue
            # Window filter — adversarial runs are weekly, so a 7-day
            # window should almost always catch the most recent one.
            try:
                run_ts = datetime.fromisoformat(
                    (run.get("run_at") or "").replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                run_ts = 0.0
            if run_ts and run_ts < cutoff:
                continue
            for result in run.get("results") or []:
                if not isinstance(result, dict) or not result.get("passed"):
                    continue
                attack_id = result.get("attack_id") or ""
                attack = library_by_id.get(attack_id)
                # Concatenate all turns in the scenario as the user prompt —
                # for multi-turn drift attacks (C_GRADUAL), the full arc is
                # the training signal, not just the final turn.
                turns = list(attack.turns) if attack and attack.turns else []
                user_turn = "\n\n".join(t for t in turns if t)
                # Pick the final response — that's the defensive reply the
                # model should learn. Earlier turns may be tee-up prose.
                responses = result.get("responses") or []
                aria_reply = (responses[-1] if responses else "") or ""
                if not user_turn or not aria_reply:
                    continue
                wc = len(aria_reply.split())
                if wc < _MIN_WORD_COUNT:
                    continue
                out.append({
                    "user": user_turn[:3000],
                    "assistant": aria_reply[:16000],
                    "meta": {
                        "source": "adversarial_pass",
                        "attack_id": attack_id,
                        "severity": result.get("severity") or "",
                        "category": result.get("category") or "",
                    },
                })
        _set_diag("adversarial", len(out),
                  reason=None if out else "no_passed_attacks_in_window")
    except Exception as exc:
        _set_diag("adversarial", 0, error=f"{type(exc).__name__}: {exc}")
    return out


# ═══════════════════════════════════════════════════════════════════════
# Main export
# ═══════════════════════════════════════════════════════════════════════

async def run_daily_export(days_lookback: int = 7) -> dict[str, Any]:
    """Daily scheduled export. Writes one JSONL file per day.

    Default lookback is 7 days, not 1: the previous 1-day default silently
    missed every DD report more than 24h old, producing 0 training examples
    even when `dd/reports` had 13+ valid reports in the index. 7 days tolerates
    weekend gaps + occasional quiet days without over-ingesting old content
    (de-dupe by hash handles that anyway).

    Returns a summary suitable for mem0 / brain_hook absorption.
    """
    # 2026-04-26 angle (a): refresh cross-sweep upgrades before
    # collecting. The accumulator has been queueing well_formed /
    # unverified entries since each chat turn fired; running reconcile()
    # here re-evaluates each pending claim against the current
    # verified_intel snapshot. Any entry whose claims now have
    # 2+-source corroboration is upgraded to `grounded` in the queue
    # and gets picked up by _collect_chat_turns below as if it had
    # originally graded grounded. Single-pass-per-export is sufficient
    # cadence — daily export means daily reconcile.
    try:
        from ..intel import verification_accumulator as _va
        recon = await _va.reconcile()
        logger.info(
            "[training_export] verification reconcile: scanned=%d upgraded=%d still_pending=%d",
            recon.get("scanned", 0),
            recon.get("upgraded", 0),
            recon.get("still_pending", 0),
        )
    except Exception as exc:
        logger.debug("verification_accumulator.reconcile failed (non-fatal): %s", exc)

    # Collect from every source in parallel-ish (await each)
    chat_turns = await _collect_chat_turns(days_lookback)
    dd_reports = await _collect_dd_reports(days_lookback)
    writers    = await _collect_writer_outputs(days_lookback)
    adversarial = await _collect_adversarial_passes(days_lookback)

    all_examples = chat_turns + dd_reports + writers + adversarial

    # De-dupe by hash of (user + assistant) — prevents repeat-ingesting
    # the same example across consecutive runs
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for ex in all_examples:
        key = hashlib.sha256(
            ((ex.get("user") or "") + "||" + (ex.get("assistant") or "")).encode("utf-8", errors="ignore")
        ).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        unique.append(ex)

    # Ensure export dir exists
    try:
        _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("Could not create export dir %s: %s", _EXPORT_DIR, exc)
        return {"written": 0, "error": str(exc)}

    today = datetime.now(timezone.utc).date().isoformat()
    out_file = _EXPORT_DIR / f"{today}.jsonl"

    # Convert to OpenAI-compatible fine-tune format
    written = 0
    with out_file.open("w", encoding="utf-8") as f:
        for ex in unique:
            record = {
                "messages": [
                    {"role": "user", "content": ex["user"]},
                    {"role": "assistant", "content": ex["assistant"]},
                ],
                "metadata": ex.get("meta", {}),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    # Update manifest with running totals + train/val/test split plan
    manifest = _load_manifest()
    manifest["last_export_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["last_export_count"] = written
    manifest["total_examples"] = manifest.get("total_examples", 0) + written
    manifest["by_source"] = manifest.get("by_source", {})
    for ex in unique:
        src = (ex.get("meta") or {}).get("source", "unknown")
        manifest["by_source"][src] = manifest["by_source"].get(src, 0) + 1
    manifest["files"] = manifest.get("files", {})
    manifest["files"][today] = written
    _save_manifest(manifest)

    # Feed brain_hook so mastery on "learning" topic goes up
    try:
        from ..intel import brain_hook
        await brain_hook.absorb(
            module="training_export",
            summary=f"Exported {written} training examples (total: {manifest['total_examples']})",
            success=True,
        )
    except Exception:
        pass

    logger.info(
        "[learning] exported %d training examples to %s (total corpus: %d)",
        written, out_file, manifest["total_examples"],
    )
    # Persist per-source diagnostics on the manifest so the dashboard
    # can surface "why did this source return 0?" without rerunning.
    manifest["last_run_diagnostics"] = _last_collection_diag.copy()
    _save_manifest(manifest)
    return {
        "written": written,
        "by_source": {k: v for k, v in manifest["by_source"].items()},
        "total_examples": manifest["total_examples"],
        "file": str(out_file),
        "diagnostics": _last_collection_diag.copy(),
    }


# ═══════════════════════════════════════════════════════════════════════
# Manifest + helpers
# ═══════════════════════════════════════════════════════════════════════

def _load_manifest() -> dict[str, Any]:
    try:
        if _MANIFEST_FILE.exists():
            return json.loads(_MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("manifest load failed: %s", exc)
    return {"created_at": datetime.now(timezone.utc).isoformat()}


def _save_manifest(manifest: dict[str, Any]) -> None:
    try:
        _MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MANIFEST_FILE.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("manifest save failed: %s", exc)


def get_manifest() -> dict[str, Any]:
    """Public API — read-only manifest for dashboard + API endpoint."""
    return _load_manifest()


def summary() -> dict[str, Any]:
    """Capability-manifest summary."""
    m = _load_manifest()
    return {
        "export_dir": str(_EXPORT_DIR),
        "total_examples": m.get("total_examples", 0),
        "by_source": m.get("by_source", {}),
        "last_export": m.get("last_export_utc", ""),
        "last_export_count": m.get("last_export_count", 0),
        "last_run_diagnostics": m.get("last_run_diagnostics", {}),
        "daily_files": len(m.get("files", {})),
    }
