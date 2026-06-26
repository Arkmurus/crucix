"""Guardian trusted-circle vault (R-F1979).

The "circle" is the small set of contacts the user EXPLICITLY enrolled — the only
recipients Guardian may share location with, alert, or act toward without a
per-message confirmation. Contact identifiers (phone/JID) are SENSITIVE PII and
are encrypted at rest (Fernet). Consent is explicit and revocable. This is the
access-control list the Action Gateway enforces for high-risk actions.
"""
from __future__ import annotations

import base64
import logging
import os
import time

from ..intel import redis_store as rs

logger = logging.getLogger("aria.guardian.circle")

_CIRCLE_KEY = "crucix:guardian:circle:{user}"
_MAX_CIRCLE = 25

_FERNET_CACHE: tuple | None = None


def _fernet():
    """(cipher, encrypted) — Guardian vault key, falling back to the shared
    credential vault key, else plaintext-with-warning (dev only)."""
    global _FERNET_CACHE
    if _FERNET_CACHE is not None:
        return _FERNET_CACHE
    raw_key = (os.getenv("ARIA_GUARDIAN_VAULT_KEY")
               or os.getenv("ARIA_CREDENTIAL_VAULT_KEY") or "").strip()
    if not raw_key:
        logger.warning("[guardian.circle] no vault key (ARIA_GUARDIAN_VAULT_KEY) — "
                       "circle contacts stored in PLAINTEXT. Set a key for encryption at rest.")
        _FERNET_CACHE = (None, False)
        return _FERNET_CACHE
    try:
        from cryptography.fernet import Fernet
        if len(raw_key) == 44 and raw_key.endswith("="):
            cipher = Fernet(raw_key)
        else:
            b = raw_key.encode("utf-8")[:32].ljust(32, b"\0")
            cipher = Fernet(base64.urlsafe_b64encode(b))
        _FERNET_CACHE = (cipher, True)
    except Exception as e:
        logger.warning("[guardian.circle] Fernet init failed: %s — PLAINTEXT", e)
        _FERNET_CACHE = (None, False)
    return _FERNET_CACHE


def _enc(plain: str) -> str:
    cipher, on = _fernet()
    if not on or not plain:
        return plain
    try:
        return "enc:" + cipher.encrypt(plain.encode("utf-8")).decode("ascii")
    except Exception:
        return plain


def _dec(value: str) -> str:
    if not value or not value.startswith("enc:"):
        return value
    cipher, on = _fernet()
    if not on:
        return value
    try:
        return cipher.decrypt(value[4:].encode("ascii")).decode("utf-8")
    except Exception:
        return value


def _norm_jid(jid: str) -> str:
    """Normalise a phone/JID for comparison (digits + the @s.whatsapp.net suffix)."""
    j = (jid or "").strip()
    if "@" in j:
        return j
    digits = "".join(ch for ch in j if ch.isdigit())
    return f"{digits}@s.whatsapp.net" if digits else ""


async def add_contact(user: str, name: str, jid: str, relationship: str = "") -> dict:
    """Enrol a contact into the user's trusted circle (explicit consent).
    Returns {ok, count} or {ok:False, error}."""
    if not user or not name or not _norm_jid(jid):
        return {"ok": False, "error": "user, name and a valid phone/jid are required"}
    circle = await _load(user)
    njid = _norm_jid(jid)
    # de-dup by normalised jid
    circle = [c for c in circle if _norm_jid(_dec(c.get("jid", ""))) != njid]
    if len(circle) >= _MAX_CIRCLE:
        return {"ok": False, "error": f"circle is full ({_MAX_CIRCLE})"}
    circle.append({
        "name": name.strip()[:80],
        "jid": _enc(njid),
        "relationship": (relationship or "").strip()[:40],
        "added_at": time.time(),
    })
    await _save(user, circle)
    return {"ok": True, "count": len(circle)}


async def remove_contact(user: str, name_or_jid: str) -> dict:
    circle = await _load(user)
    key = (name_or_jid or "").strip().lower()
    njid = _norm_jid(name_or_jid)
    before = len(circle)
    circle = [c for c in circle
              if c.get("name", "").lower() != key
              and _norm_jid(_dec(c.get("jid", ""))) != njid]
    await _save(user, circle)
    return {"ok": True, "removed": before - len(circle), "count": len(circle)}


async def list_circle(user: str) -> list[dict]:
    """Decrypted view of the circle (jid revealed for use by the gateway)."""
    out = []
    for c in await _load(user):
        out.append({
            "name": c.get("name", ""),
            "jid": _dec(c.get("jid", "")),
            "relationship": c.get("relationship", ""),
            "added_at": c.get("added_at"),
        })
    return out


async def is_in_circle(user: str, jid: str) -> bool:
    nj = _norm_jid(jid)
    if not nj:
        return False
    return any(_norm_jid(c["jid"]) == nj for c in await list_circle(user))


async def resolve(user: str, name_or_jid: str) -> str | None:
    """Resolve a circle contact NAME (or jid) to its JID. Only circle members
    resolve — this is what keeps Guardian from messaging arbitrary numbers."""
    key = (name_or_jid or "").strip().lower()
    nj = _norm_jid(name_or_jid)
    for c in await list_circle(user):
        if c["name"].lower() == key or (nj and _norm_jid(c["jid"]) == nj):
            return c["jid"]
    return None


async def _load(user: str) -> list[dict]:
    try:
        return (await rs.get_json(_CIRCLE_KEY.format(user=user))) or []
    except Exception:
        return []


async def _save(user: str, circle: list[dict]) -> None:
    try:
        await rs.set_json(_CIRCLE_KEY.format(user=user), circle[:_MAX_CIRCLE])
    except Exception as e:
        logger.warning("[guardian.circle] save failed for %s: %s", user, e)
