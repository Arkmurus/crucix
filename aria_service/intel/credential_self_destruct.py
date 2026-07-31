"""R-F1137 — Credential self-destruct mechanism.

Every credential has a TTL (default 1 hour). After TTL, the credential is
automatically rotated or invalidated. If a security event is detected
(anomaly, malware, injection), ALL credentials can be immediately invalidated
via the panic button.

Graduated response (never self-DoS on false positive):
1. TTL expiry — graceful re-fetch (do NOT hard-fail a running task)
2. Soft rotation — re-fetch on next use if TTL expired
3. Panic wipe — ALL credentials invalidated (HIGH confidence event ONLY)

The panic button is wired to quarantine_network.destructive_quarantine —
it only fires on operator-approved destructive quarantine.

Usage:
    from aria_service.intel.credential_self_destruct import (
        store_credential_with_ttl,
        get_credential_with_ttl,
        invalidate_all_credentials,
        get_credential_status,
    )

    # Store with 1 hour TTL:
    await store_credential_with_ttl("api_key_openai", "sk-...", ttl_s=3600)

    # Get (auto-refreshes if expired):
    cred = await get_credential_with_ttl("api_key_openai")
    if cred is None:
        # Credential expired and couldn't be refreshed
        await re_register()
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("aria.credential_self_destruct")

# Redis keys
_CRED_VAULT_KEY = "crucix:security:credential_vault"
_CRED_STATUS_KEY = "crucix:security:credential_status"
_PANIC_KEY = "crucix:security:credential_panic"

# Default TTL: 1 hour
DEFAULT_TTL_S = 3600

# Grace period: after TTL expiry, credential is still usable for this long
# to allow graceful re-fetch without hard-failing running tasks
GRACE_PERIOD_S = 300  # 5 minutes


async def _get_vault() -> dict[str, Any]:
    """Get the credential vault from Redis."""
    try:
        from . import redis_store as rs
        return await rs.get_json(_CRED_VAULT_KEY) or {}
    except Exception:
        return {}


async def _set_vault(state: dict[str, Any]) -> None:
    """Set the credential vault in Redis."""
    try:
        from . import redis_store as rs
        await rs.set_json(_CRED_VAULT_KEY, state, ex=86400 * 7)
    except Exception as e:
        logger.warning("[credential_self_destruct] Redis store failed: %s", e)


async def store_credential_with_ttl(
    credential_id: str,
    credential_value: str,
    ttl_s: int = DEFAULT_TTL_S,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Store a credential with a TTL.

    After TTL expires, the credential is automatically invalidated.
    A grace period allows running tasks to complete before hard failure.

    Args:
        credential_id: Unique identifier for this credential.
        credential_value: The credential value (API key, password, etc.).
        ttl_s: Time-to-live in seconds (default 1 hour).
        metadata: Optional metadata (source, purpose, etc.).

    Returns:
        Dict with credential status.
    """
    now = time.time()
    entry = {
        "credential_id": credential_id,
        "stored_at": now,
        "expires_at": now + ttl_s,
        "grace_period_until": now + ttl_s + GRACE_PERIOD_S,
        "ttl_s": ttl_s,
        "is_valid": True,
        "invalidated_at": None,
        "invalidated_reason": None,
        "access_count": 0,
        "metadata": metadata or {},
    }

    vault = await _get_vault()
    vault[credential_id] = entry
    await _set_vault(vault)

    # Store the actual value separately (not in the vault metadata)
    try:
        from . import redis_store as rs
        value_key = f"{_CRED_VAULT_KEY}:value:{credential_id}"
        await rs.set_json(value_key, {
            "credential_id": credential_id,
            "value": credential_value,
        }, ex=ttl_s + GRACE_PERIOD_S + 3600)  # Extra buffer
    except Exception as e:
        # R-F3567 — this logged a warning and still returned an entry with
        # is_valid=True. The vault metadata says the credential is stored while
        # its VALUE never landed, so the caller believes it holds a working
        # credential and only finds out at first use. A split write is exactly
        # the failure the brain has to know about.
        logger.warning("[credential_self_destruct] Value store failed: %s", e)
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="credential_self_destruct",
                detail=(
                    f"credential '{credential_id}' registered in the vault but its "
                    f"VALUE write FAILED ({type(e).__name__}) — the entry reads valid "
                    f"and the secret is not retrievable"
                ),
                gap_type="engine_failure",
                source="credential_self_destruct:store_credential_with_ttl",
            )
        except Exception:
            logger.debug("[credential_self_destruct] brain wiring failed", exc_info=True)
    else:
        # §21a SUCCESS branch — provisioning a credential with a self-destruct
        # TTL is a security-relevant, low-volume event, unlike the per-read
        # accessor below which must stay unsignalled.
        try:
            from .engine_wiring import wire_success
            wire_success(
                module="credential_self_destruct",
                summary=f"credential '{credential_id}' stored with a {ttl_s}s TTL",
                detail=f"grace_period_s={GRACE_PERIOD_S}",
                confidence="CONFIRMED",
                source_id=f"credential_self_destruct:{credential_id}",
            )
        except Exception:
            logger.debug("[credential_self_destruct] brain wiring failed", exc_info=True)

    logger.info(
        "[credential_self_destruct] Stored credential '%s' with TTL %ds",
        credential_id, ttl_s,
    )

    return entry


async def get_credential_with_ttl(
    credential_id: str,
    refresh_callback=None,
) -> Optional[str]:
    """Get a credential value, with TTL check and graceful refresh.

    If the credential's TTL has expired but it's within the grace period,
    the credential is still returned but a refresh is triggered.
    If past the grace period, None is returned (caller must re-register).

    Args:
        credential_id: Unique identifier for the credential.
        refresh_callback: Optional async function to call for refresh.
            Called as refresh_callback(credential_id) when TTL is near expiry.

    Returns:
        The credential value, or None if expired and not refreshable.
    """
    # Check panic status first
    try:
        from . import redis_store as rs
        panicked = await rs.get_json(_PANIC_KEY)
        if panicked and panicked.get("panicked"):
            logger.warning(
                "[credential_self_destruct] PANIC ACTIVE — all credentials invalidated "
                "at %s (reason: %s)",
                panicked.get("panicked_at"), panicked.get("reason"),
            )
            return None
    except Exception:
        pass

    vault = await _get_vault()
    entry = vault.get(credential_id)

    if not entry:
        logger.warning(
            "[credential_self_destruct] Credential '%s' not found",
            credential_id,
        )
        return None

    now = time.time()

    # Check if credential was explicitly invalidated
    if not entry.get("is_valid", True):
        logger.info(
            "[credential_self_destruct] Credential '%s' was invalidated: %s",
            credential_id, entry.get("invalidated_reason"),
        )
        return None

    # Check TTL
    if now > entry.get("grace_period_until", 0):
        # Past grace period — hard fail
        logger.warning(
            "[credential_self_destruct] Credential '%s' expired "
            "(TTL %ds, grace period ended)",
            credential_id, entry.get("ttl_s", DEFAULT_TTL_S),
        )
        return None

    if now > entry.get("expires_at", 0):
        # TTL expired but within grace period — try refresh
        logger.info(
            "[credential_self_destruct] Credential '%s' TTL expired, "
            "within grace period — attempting refresh",
            credential_id,
        )
        if refresh_callback:
            try:
                await refresh_callback(credential_id)
            except Exception as e:
                logger.warning(
                    "[credential_self_destruct] Refresh failed for '%s': %s",
                    credential_id, e,
                )

    # Update access count
    entry["access_count"] = entry.get("access_count", 0) + 1
    vault[credential_id] = entry
    await _set_vault(vault)

    # Get the actual value
    try:
        from . import redis_store as rs
        value_key = f"{_CRED_VAULT_KEY}:value:{credential_id}"
        value_entry = await rs.get_json(value_key)
        if value_entry:
            return value_entry.get("value")
    except Exception:
        pass

    return None


async def invalidate_credential(
    credential_id: str,
    reason: str = "Manual invalidation",
) -> dict[str, Any]:
    """Invalidate a single credential.

    The credential is marked as invalid and will not be returned by
    get_credential_with_ttl. The value is also deleted from Redis.

    Args:
        credential_id: The credential to invalidate.
        reason: Why the credential is being invalidated.

    Returns:
        Dict with invalidation status.
    """
    vault = await _get_vault()
    entry = vault.get(credential_id)

    if entry:
        entry["is_valid"] = False
        entry["invalidated_at"] = time.time()
        entry["invalidated_reason"] = reason
        vault[credential_id] = entry
        await _set_vault(vault)

    # Delete the value
    try:
        from . import redis_store as rs
        value_key = f"{_CRED_VAULT_KEY}:value:{credential_id}"
        await rs.set_json(value_key, None, ex=1)  # Expire immediately
    except Exception:
        pass

    logger.warning(
        "[credential_self_destruct] Invalidated credential '%s': %s",
        credential_id, reason,
    )

    return {
        "credential_id": credential_id,
        "invalidated": True,
        "reason": reason,
        "invalidated_at": datetime.now(timezone.utc).isoformat(),
    }


async def invalidate_all_credentials(
    reason: str = "Security event — all credentials invalidated",
) -> dict[str, Any]:
    """Panic button — invalidate ALL credentials immediately.

    This is the highest-severity response. Sets a panic flag that causes
    ALL get_credential_with_ttl calls to return None immediately.

    This should ONLY be called from quarantine_network.destructive_quarantine
    with operator approval.

    Args:
        reason: Why all credentials are being invalidated.

    Returns:
        Dict with invalidation status.
    """
    vault = await _get_vault()
    now = time.time()
    count = 0

    for credential_id in list(vault.keys()):
        vault[credential_id]["is_valid"] = False
        vault[credential_id]["invalidated_at"] = now
        vault[credential_id]["invalidated_reason"] = reason
        count += 1

    await _set_vault(vault)

    # Set panic flag
    try:
        from . import redis_store as rs
        await rs.set_json(_PANIC_KEY, {
            "panicked": True,
            "panicked_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "credentials_invalidated": count,
        }, ex=86400 * 7)  # 7 day panic — requires operator to clear
    except Exception:
        pass

    # Delete all credential values
    try:
        from . import redis_store as rs
        for credential_id in list(vault.keys()):
            value_key = f"{_CRED_VAULT_KEY}:value:{credential_id}"
            await rs.set_json(value_key, None, ex=1)
    except Exception:
        pass

    logger.critical(
        "[credential_self_destruct] PANIC: All %d credentials invalidated — %s",
        count, reason,
    )

    # Wire to brain — CRITICAL alert
    try:
        from .engine_wiring import wire_failure
        wire_failure(
            module="credential_self_destruct",
            detail=f"PANIC: All {count} credentials invalidated — {reason}",
            gap_type="security_threat",
            source="credential_self_destruct:panic",
        )
    except Exception:
        logger.debug("[credential_self_destruct] brain wiring failed", exc_info=True)

    return {
        "panicked": True,
        "credentials_invalidated": count,
        "reason": reason,
        "panicked_at": datetime.now(timezone.utc).isoformat(),
    }


async def clear_panic() -> dict[str, Any]:
    """Clear the panic flag — allows credentials to be re-established."""
    try:
        from . import redis_store as rs
        await rs.set_json(_PANIC_KEY, None, ex=1)
    except Exception:
        pass

    logger.warning("[credential_self_destruct] Panic cleared — credentials can be re-established")

    return {
        "panicked": False,
        "cleared_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_credential_status(
    credential_id: Optional[str] = None,
) -> dict[str, Any]:
    """Get credential status.

    Args:
        credential_id: If provided, get status for this credential only.

    Returns:
        Dict with credential status.
    """
    vault = await _get_vault()

    try:
        from . import redis_store as rs
        panicked = await rs.get_json(_PANIC_KEY)
    except Exception:
        panicked = None

    if credential_id:
        entry = vault.get(credential_id, {})
        return {
            "credential_id": credential_id,
            "is_valid": entry.get("is_valid", False),
            "expires_at": entry.get("expires_at"),
            "grace_period_until": entry.get("grace_period_until"),
            "access_count": entry.get("access_count", 0),
            "panicked": panicked.get("panicked", False) if panicked else False,
        }

    return {
        "total_credentials": len(vault),
        "valid_credentials": sum(
            1 for e in vault.values() if e.get("is_valid")
        ),
        "invalidated_credentials": sum(
            1 for e in vault.values() if not e.get("is_valid")
        ),
        "panicked": panicked.get("panicked", False) if panicked else False,
        "panic_reason": panicked.get("reason") if panicked else None,
        "credentials": {
            k: {
                "is_valid": v.get("is_valid"),
                "expires_at": v.get("expires_at"),
                "access_count": v.get("access_count", 0),
            }
            for k, v in vault.items()
        },
    }
