"""
Document Corrections Store — durable learning signal for document_intelligence.

Every structured extraction produced by document_intelligence.process_document
is persisted here with a stable extraction_id. The team verifies or corrects
specific fields after their own checks, and those corrections feed back into
future extraction prompts as few-shot examples for the same form type.

Storage: JSON-on-disk at $ARIA_DATA_DIR/document_corrections.json (default
'data/document_corrections.json'). Disk over Redis because corrections are
durable training signal that must outlive cache TTLs.

Schema:
{
  "extractions": {
    "<extraction_id>": {
      "id": "doc_xxxxxx",
      "form_code": "HK_NSC1",
      "filename": "...",
      "source": "whatsapp:group:sender",
      "structured_original": { ... },     # what ARIA produced
      "structured_current":  { ... },     # original + applied corrections
      "overview_markdown_original": "...",
      "verifications": [{"by": "...", "at": "ISO"}],
      "corrections":   [{"field": "...", "value": "...", "by": "...", "at": "ISO"}],
      "created_at": "ISO",
      "updated_at": "ISO"
    }
  },
  "by_form": {
    "HK_NSC1": ["doc_xxxxxx", "doc_yyyyyy", ...]   # most recent last
  }
}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.doc_corrections")

_LOCK = asyncio.Lock()

# Storage location resolution — mirrors rag_store._resolve_rag_path:
#   1. ARIA_DATA_DIR env var if set
#   2. /data if the fly.io persistent volume is mounted (production)
#   3. ./data otherwise (local dev — relative to cwd)
# Past incident pattern: chromadb originally fell back to /tmp/aria_rag
# because /data wasn't mounted, wiping data on every redeploy. Don't repeat.
def _resolve_data_dir() -> Path:
    env = os.environ.get("ARIA_DATA_DIR")
    if env:
        return Path(env)
    if Path("/data").exists() and Path("/data").is_dir():
        return Path("/data")
    return Path("data")


_DATA_DIR = _resolve_data_dir()
_STORE_PATH = _DATA_DIR / "document_corrections.json"

# Cap how many extractions we keep per form (for few-shot prompt size) and
# overall (to keep the store from growing unbounded). 5000 entries × ~3KB each
# = ~15MB on disk, acceptable.
_MAX_FEW_SHOT_PER_FORM = 6
_MAX_TOTAL_EXTRACTIONS = 5000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_store() -> dict:
    return {"extractions": {}, "by_form": {}}


def _load_sync() -> dict:
    try:
        if _STORE_PATH.exists():
            return json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("doc_corrections load failed (%s) — starting empty", e)
    return _empty_store()


def _save_sync(store: dict) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _STORE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_STORE_PATH)
    except Exception as e:
        logger.warning("doc_corrections save failed: %s", e)


async def _load() -> dict:
    return await asyncio.to_thread(_load_sync)


async def _save(store: dict) -> None:
    await asyncio.to_thread(_save_sync, store)


def new_extraction_id() -> str:
    """Generate a short, URL-safe, prefix-tagged extraction id."""
    return "doc_" + secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]


# ── Path resolver for nested-field corrections ──────────────────────────────
# Supports dotted paths with numeric segments for list indices, e.g.
#   "company_name_en"
#   "allottees.0.name"
#   "shares.1.amount_paid"

def _set_path(obj: Any, path: str, value: Any) -> bool:
    """Set a nested value by dotted path. Returns True on success."""
    parts = [p for p in path.split(".") if p != ""]
    if not parts:
        return False
    cur = obj
    for p in parts[:-1]:
        if isinstance(cur, list):
            try:
                idx = int(p)
            except ValueError:
                return False
            if idx < 0 or idx >= len(cur):
                return False
            cur = cur[idx]
        elif isinstance(cur, dict):
            if p not in cur or not isinstance(cur[p], (dict, list)):
                # auto-create dict for new branches; skip auto-list creation —
                # ambiguous when intermediate segment is numeric
                cur[p] = {}
            cur = cur[p]
        else:
            return False
    last = parts[-1]
    if isinstance(cur, list):
        try:
            idx = int(last)
        except ValueError:
            return False
        if idx < 0 or idx >= len(cur):
            return False
        cur[idx] = value
    elif isinstance(cur, dict):
        cur[last] = _coerce(value)
    else:
        return False
    return True


def _coerce(v: Any) -> Any:
    """Best-effort coercion of string corrections into numbers/booleans."""
    if not isinstance(v, str):
        return v
    s = v.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.lower() in ("null", "none", ""):
        return None
    # int
    try:
        if "." not in s and s.lstrip("-").isdigit():
            return int(s)
    except Exception:
        pass
    # float
    try:
        return float(s.replace(",", ""))
    except Exception:
        pass
    return s


# ── Public API ──────────────────────────────────────────────────────────────

async def save_extraction(
    *,
    extraction_id: str,
    form_code: str,
    filename: str,
    source: str,
    structured: dict,
    overview_markdown: str,
    user_id: str = "",
) -> None:
    """Persist a freshly produced extraction. Idempotent on extraction_id.

    R-F1826 (audit H7): store the owning user_id so get_extraction can enforce
    ownership (extractions hold uploaded document content — PII/contracts)."""
    async with _LOCK:
        store = await _load()
        now = _now()
        store["extractions"][extraction_id] = {
            "id": extraction_id,
            "user_id": user_id or "",
            "form_code": form_code,
            "filename": filename,
            "source": source,
            "structured_original": structured,
            "structured_current": json.loads(json.dumps(structured)),  # deep copy
            "overview_markdown_original": overview_markdown,
            "verifications": [],
            "corrections": [],
            "created_at": now,
            "updated_at": now,
        }
        bucket = store["by_form"].setdefault(form_code, [])
        if extraction_id in bucket:
            bucket.remove(extraction_id)
        bucket.append(extraction_id)
        # Cap total
        if len(store["extractions"]) > _MAX_TOTAL_EXTRACTIONS:
            sorted_ids = sorted(
                store["extractions"].items(),
                key=lambda kv: kv[1].get("created_at", ""),
            )
            for rid, _ in sorted_ids[: len(store["extractions"]) - _MAX_TOTAL_EXTRACTIONS]:
                store["extractions"].pop(rid, None)
                for arr in store["by_form"].values():
                    if rid in arr:
                        arr.remove(rid)
        await _save(store)


async def get_extraction(extraction_id: str, user_id: Optional[str] = None) -> Optional[dict]:
    """R-F1826 (audit H7): when user_id is passed, return the record only if it
    belongs to that user (or is a legacy/admin record with no owner). Pass None
    for trusted internal/admin paths."""
    store = await _load()
    rec = store["extractions"].get(extraction_id)
    if rec is None:
        return None
    if user_id is not None:
        owner = (rec.get("user_id") or "").strip()
        if owner and owner != user_id:
            return None  # ownership mismatch — treat as not found (no existence leak)
    return rec


async def recent_extractions(limit: int = 20, form_code: Optional[str] = None,
                             user_id: Optional[str] = None) -> list[dict]:
    # R-F1909 (G3, audit M2): when user_id is passed, list ONLY the caller's own
    # records (plus legacy/admin records with no owner) — same ownership rule as
    # get_extraction. Without it this leaked every tenant's filenames/form-types/
    # timestamps + the ids needed to drive the verify/correct IDOR. user_id=None
    # = trusted internal/admin path (no filter).
    store = await _load()
    items = list(store["extractions"].values())
    if form_code:
        items = [e for e in items if e.get("form_code") == form_code]
    if user_id is not None:
        items = [e for e in items
                 if not (e.get("user_id") or "").strip()
                 or (e.get("user_id") or "").strip() == user_id]
    items.sort(key=lambda e: e.get("updated_at", ""), reverse=True)
    return items[:limit]


async def find_unverified_for_entity(
    entity_name: str,
    *,
    max_age_hours: int = 24,
) -> Optional[dict]:
    """Return the most recent unverified extraction whose structured payload
    mentions `entity_name` (case-insensitive substring match).

    Used as a pre-run gate by dd_orchestrate: if the team uploaded a draft
    extraction for this entity but hasn't signed it off with /docverify or
    corrected it with /docfix, block the DD so we don't build a report on
    unconfirmed fields.

    Returns None if there's nothing to gate on (either no match, already
    verified, or has corrections — corrections imply the team engaged with
    the draft so the DD can proceed using the corrected fields).
    """
    if not entity_name or len(entity_name.strip()) < 3:
        return None
    needle = entity_name.strip().lower()
    store = await _load()
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
    candidates: list[dict] = []
    for rec in store["extractions"].values():
        if rec.get("verifications") or rec.get("corrections"):
            continue
        if (rec.get("updated_at") or "") < cutoff:
            continue
        blob = json.dumps(
            rec.get("structured_current") or rec.get("structured_original") or {},
            ensure_ascii=False,
        ).lower()
        if needle in blob or needle in (rec.get("filename") or "").lower():
            candidates.append(rec)
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return candidates[0]


async def verify_extraction(extraction_id: str, by: str,
                            user_id: Optional[str] = None) -> Optional[dict]:
    """Mark the extraction as human-verified. Returns the updated record.

    R-F1909 (G3, audit H7): when user_id is passed, only the owner may verify
    (or a legacy/admin record with no owner). This is an IDOR write — verifying
    flips the find_unverified_for_entity DD pre-run gate, so a cross-tenant
    verify could let a DD proceed on another user's unconfirmed fields."""
    async with _LOCK:
        store = await _load()
        rec = store["extractions"].get(extraction_id)
        if not rec:
            return None
        if user_id is not None:
            owner = (rec.get("user_id") or "").strip()
            if owner and owner != user_id:
                return None  # ownership mismatch — treat as not found
        rec.setdefault("verifications", []).append({"by": by or "unknown", "at": _now()})
        rec["updated_at"] = _now()
        await _save(store)
        return rec


async def correct_extraction(
    extraction_id: str, field: str, value: Any, by: str,
    user_id: Optional[str] = None,
) -> Optional[dict]:
    """Apply a field correction to the structured record. Returns updated record.

    Field syntax: dotted path with numeric segments for list indices, e.g.
    'company_name_en', 'allottees.0.name', 'shares.1.amount_paid'.
    Values are auto-coerced (numbers, booleans, null).

    R-F1909 (G3, audit H7): when user_id is passed, only the owner may correct
    (or a legacy/admin record with no owner) — without it any token holder could
    overwrite arbitrary structured fields on another tenant's document.
    """
    async with _LOCK:
        store = await _load()
        rec = store["extractions"].get(extraction_id)
        if not rec:
            return None
        if user_id is not None:
            owner = (rec.get("user_id") or "").strip()
            if owner and owner != user_id:
                return None  # ownership mismatch — treat as not found
        ok = _set_path(rec.setdefault("structured_current", {}), field, value)
        if not ok:
            return {"_error": f"could not set field '{field}' — bad path"}
        rec.setdefault("corrections", []).append({
            "field": field, "value": value, "by": by or "unknown", "at": _now(),
        })
        rec["updated_at"] = _now()
        await _save(store)
        return rec


# ── Few-shot prompt injection ───────────────────────────────────────────────

async def few_shot_examples(form_code: str, max_n: int = _MAX_FEW_SHOT_PER_FORM) -> list[dict]:
    """Return the most recent verified-or-corrected extractions for a form
    type, suitable for inclusion in the next extractor prompt.

    Selection:
      1. Extractions with at least one human correction OR verification
      2. Newest first
      3. Capped at max_n

    Each example is reduced to {filename, structured_current} to keep the
    prompt budget reasonable.
    """
    store = await _load()
    ids = store["by_form"].get(form_code, [])
    candidates: list[dict] = []
    for eid in reversed(ids):  # newest last in array → reverse for newest first
        rec = store["extractions"].get(eid)
        if not rec:
            continue
        if not (rec.get("verifications") or rec.get("corrections")):
            continue
        candidates.append({
            "filename": rec.get("filename", "?"),
            "structured": rec.get("structured_current") or rec.get("structured_original"),
        })
        if len(candidates) >= max_n:
            break
    return candidates


def render_few_shot_block(examples: list[dict]) -> str:
    """Format few-shot examples for inclusion in the extractor system prompt."""
    if not examples:
        return ""
    parts = ["", "# HUMAN-VERIFIED EXAMPLES — match this exact field shape and naming convention:"]
    for i, ex in enumerate(examples, 1):
        parts.append(f"## Example {i} — {ex['filename']}")
        parts.append("```json")
        parts.append(json.dumps(ex["structured"], ensure_ascii=False, indent=2))
        parts.append("```")
    parts.append("")
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="document_corrections",
        summary="Render Few Shot Block",
        source_id="document_corrections:R-F996",
    )

    return "\n".join(parts)


# ── Status helpers used by overview renderer ────────────────────────────────

def status_marker(rec: dict) -> str:
    """Build the [DRAFT|VERIFIED|CORRECTED] header line for an extraction."""
    if not rec:
        return ""
    verifications = rec.get("verifications") or []
    corrections = rec.get("corrections") or []
    if verifications:
        v = verifications[-1]
        return f"✅ *VERIFIED* by {v.get('by')} at {v.get('at')}"
    if corrections:
        return f"✏️ *CORRECTED* — {len(corrections)} field(s) updated post-extraction"
    return "📝 *DRAFT* — awaiting human verification"

# R-F2119 §21a — wire failure handler for document_corrections
try:
    wire_failure(module="document_corrections", detail="module shutdown",
                gap_type="engine_failure", source="document_corrections:shutdown")
except Exception:
    pass
