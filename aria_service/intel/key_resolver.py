"""R-F1711 — generic env-first-then-vault API-key accessor.

This is the "add the API key into ARIA's system as a secret/variable" step of
autonomous portal onboarding, done NATIVELY (CLAUDE.md §6 — files + LLM, no Fly
secret-set, no FLY_API_TOKEN, no redeploy/restart):

  * A key ARIA OBTAINS autonomously (or one the operator pastes) is persisted to
    the encrypted /data credential vault via `store_obtained_key`.
  * Every consumer resolves its key via `resolve_key(env_vars, portal_id)`:
    environment variable FIRST (so existing fly secrets keep working), then the
    vault. So a freshly-obtained key goes LIVE on the very next call — no
    process restart, no deploy, no operator.

This generalises the proven `sanctions._resolve_opensanctions_key` (R-F1162),
which is the only provider that already did env-then-vault. The remaining
provider call-sites (captcha_solver, web_search, conflict_tracker, …) read their
keys once at import; converting them to `resolve_key` is what makes an obtained
key actually activate — see the R-F1711 onboarding plan.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("aria.key_resolver")


async def resolve_key(
    env_vars: list[str] | tuple[str, ...],
    portal_id: Optional[str] = None,
) -> str:
    """Resolve an API key: first matching env var, else the vault credential.

    Args:
        env_vars: env var names to try IN ORDER (env wins — fly secrets/ops
            overrides stay authoritative).
        portal_id: the credential-vault key to fall back to (the portal/provider
            id under which an obtained key was stored).

    Returns the key string, or "" if none is configured anywhere.
    """
    for ev in env_vars or ():
        v = (os.environ.get(ev) or "").strip()
        if v:
            return v
    if portal_id:
        try:
            from .portal_registry import get_credential
            cred = await get_credential(portal_id)
            if cred:
                key = (cred.get("api_key") or cred.get("token") or "").strip()
                if key:
                    return key
        except Exception as e:  # never let a vault hiccup break the caller
            logger.debug("resolve_key vault lookup failed for %s: %s", portal_id, e)
    return ""


async def store_obtained_key(
    portal_id: str,
    api_key: str,
    *,
    note: str = "autonomously obtained",
) -> bool:
    """Persist an obtained API key to the encrypted vault so resolve_key picks
    it up on the next call (the autonomous 'inject the secret' step).

    Returns True on success. Never raises — a storage failure is reported, not
    propagated, so the onboarding caller can record the outcome honestly.
    """
    api_key = (api_key or "").strip()
    if not portal_id or not api_key:
        logger.warning(
            "store_obtained_key: refusing empty key/portal_id (portal_id=%r, key_len=%d)",
            portal_id, len(api_key),
        )
        return False
    try:
        from .portal_registry import store_credential
        await store_credential(portal_id, {"api_key": api_key, "note": note})
        logger.info(
            "[key_resolver] R-F1711 stored obtained API key for %s (now live via "
            "resolve_key — no restart/operator needed)", portal_id,
        )
        return True
    except Exception as e:
        logger.warning("store_obtained_key failed for %s: %s", portal_id, e)
        return False
