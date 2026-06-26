"""Guardian tamper-evident audit chain (R-F1979).

Every action ARIA takes AS the user is appended to a per-user hash chain, so the
user can always answer "what did ARIA do as me?" and any tampering is detectable
(each entry commits to the previous entry's hash). Mirrors the DD report
hash-chain discipline. Storage is Redis (best-effort, never blocks an action),
but a failure to audit is itself recorded so the gap is visible (§21a wiring).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from ..intel import redis_store as rs

logger = logging.getLogger("aria.guardian.audit")

_AUDIT_KEY = "crucix:guardian:audit:{user}"
_AUDIT_HEAD = "crucix:guardian:audit_head:{user}"
_MAX_AUDIT = 5000


def _entry_hash(prev_hash: str, payload: dict) -> str:
    body = prev_hash + json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


async def record(user: str, action: str, detail: dict | None = None,
                 *, outcome: str = "") -> dict:
    """Append a tamper-evident audit entry for a Guardian action. Returns the
    written entry (with its hash + the prev hash it chained from)."""
    if not user:
        user = "__anon__"
    payload = {
        "ts": time.time(),
        "action": action,
        "outcome": outcome,
        "detail": detail or {},
    }
    try:
        prev = (await rs.get(_AUDIT_HEAD.format(user=user))) or "GENESIS"
        h = _entry_hash(prev, payload)
        entry = {**payload, "prev": prev, "hash": h}
        await rs.lpush(_AUDIT_KEY.format(user=user), json.dumps(entry, default=str))
        await rs.ltrim(_AUDIT_KEY.format(user=user), 0, _MAX_AUDIT - 1)
        await rs.set(_AUDIT_HEAD.format(user=user), h)
        return entry
    except Exception as e:  # audit failure must never block the action — but record the gap
        logger.warning("[guardian.audit] record failed for %s/%s: %s", user, action, e)
        try:
            from ..intel.capability_gaps import record_gap
            await record_gap(gap_type="guardian_audit_failure",
                             detail=f"audit write failed: {action}: {e}", source="guardian.audit")
        except Exception:
            pass
        return {"action": action, "hash": "", "error": str(e)}


async def history(user: str, limit: int = 50) -> list[dict]:
    """The user's recent Guardian actions (most recent first)."""
    if not user:
        return []
    try:
        raw = await rs.lrange(_AUDIT_KEY.format(user=user), 0, max(0, limit - 1))
    except Exception:
        return []
    out: list[dict] = []
    for r in raw or []:
        try:
            out.append(json.loads(r))
        except Exception:
            continue
    return out


async def verify_chain(user: str) -> dict:
    """Re-walk the chain oldest→newest and confirm each entry commits to the
    previous hash. Returns {ok, length, broken_at}."""
    entries = list(reversed(await history(user, limit=_MAX_AUDIT)))
    prev = "GENESIS"
    for i, e in enumerate(entries):
        payload = {k: e.get(k) for k in ("ts", "action", "outcome", "detail")}
        expected = _entry_hash(prev, payload)
        if e.get("prev") != prev or e.get("hash") != expected:
            return {"ok": False, "length": len(entries), "broken_at": i}
        prev = e.get("hash")
    return {"ok": True, "length": len(entries), "broken_at": None}
