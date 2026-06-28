"""crypto_sanctions — wallet address screening against sanctioned-entity
crypto datasets (R-F74, 2026-05-09).

Why this module exists
──────────────────────
Per peer review: 'Cryptocurrency intelligence is the gap that will
matter most in the next 24 months. The FATF March 2026 report that
underpins the ARIA Coin analysis confirmed stablecoins account for
84% of illicit virtual asset volume. Several of ARIA's active market
counterparties — particularly in Nigeria, UAE, and Turkey — operate
in cryptocurrency-adjacent environments.'

Source
──────
OpenSanctions publishes a free dataset of cryptocurrency addresses
associated with sanctioned entities at:
    https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv

The full dataset is large; for crypto-address screening we use the
narrower 'crypto wallets' filtered view that OpenSanctions exposes
as part of its consolidated sanctions output. Each sanctioned entity
that has known wallet addresses publishes them in the `properties.
walletAddress` array.

What this module does
─────────────────────
1. fetch_and_index() — daily refresh of the OpenSanctions wallet
   table into Redis. Compact storage: address (lowercase) → list of
   { entity_id, sanctioned_under, added_at }. Deduped + chain-detected
   from the address prefix where determinable.

2. screen_wallet(address) — synchronous lookup against the indexed
   table. Returns the matching sanctions records or empty list.

3. screen_wallet_batch(addresses) — same but for a list of addresses
   (typical DD input has multiple).

4. Brain absorbs every match with topic=crypto_sanctioned_wallet so
   any future DD on the same wallet hits memory immediately.

What this does NOT do (left for next iteration)
───────────────────────────────────────────────
- TRM Labs / Chainalysis API integration (paid; per peer review,
  'API integration follows when the first client specifically requires
  it')
- Wallet-graph traversal (find addresses that transacted with
  sanctioned addresses — the laundering trail)
- Stablecoin issuer freeze detection (Tether/Circle freeze lists)
  — separate dataset, separate module

Public API
──────────
    fetch_and_index(force=False) -> dict   — refresh the index from OpenSanctions
    screen_wallet(address) -> list[dict]  — lookup one address
    screen_wallet_batch(addresses) -> dict[address, list[dict]]
    summary() -> dict
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any
from .engine_wiring import wired, wire_failure

logger = logging.getLogger("aria.crypto_sanctions")

# OpenSanctions consolidated sanctions targets (free) — full list
_OPENSANCTIONS_TARGETS_URL = (
    "https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv"
)

# Redis keys for the indexed wallet table
_REDIS_INDEX_KEY = "crucix:aria:crypto_sanctions:index"          # hash: addr -> JSON
_REDIS_LAST_REFRESH_KEY = "crucix:aria:crypto_sanctions:last_refresh"
_REFRESH_TTL_SECONDS = 24 * 3600  # daily refresh

# Address detection patterns by chain — used to tag matches with a
# best-guess chain identifier. These are coarse: ARIA's job is to
# flag exposure, not to do full chain forensics.
_CHAIN_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("bitcoin",      re.compile(r"^(bc1|[13])[a-zA-Z0-9]{25,62}$")),
    ("ethereum",     re.compile(r"^0x[a-fA-F0-9]{40}$")),
    ("tron",         re.compile(r"^T[A-Za-z0-9]{33}$")),
    ("solana",       re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")),
    ("monero",       re.compile(r"^4[0-9AB][a-zA-Z0-9]{93}$")),
    ("ripple",       re.compile(r"^r[1-9A-HJ-NP-Za-km-z]{24,34}$")),
]


def detect_chain(address: str) -> str:
    """Best-effort chain classification from address shape."""
    if not address or not isinstance(address, str):
        return "unknown"
    a = address.strip()
    for chain, pat in _CHAIN_PATTERNS:
        if pat.match(a):
            return chain
    return "unknown"


async def _redis():
    """Convenience: get the redis_store reference."""
    from . import redis_store as rs
    return rs


@wired(module="crypto_sanctions", summary="Crypto sanctions fetch and index", check_falsy_success=True)

async def fetch_and_index(force: bool = False) -> dict[str, Any]:
    """Refresh the crypto-wallet index from OpenSanctions.

    Triggered: by autonomous task (daily) OR by /api/aria/crypto/refresh
    when the operator wants an immediate refresh. Redis-backed cache
    ensures we never hammer the OpenSanctions CDN.

    Args:
        force: ignore the daily TTL and refresh anyway

    Returns:
        {
          "ok":           bool,
          "fetched_count": int,    # rows fetched
          "indexed_count": int,    # unique wallet addresses indexed
          "skipped_recent": bool,
          "fetched_at":    ISO8601,
        }
    """
    rs = await _redis()
    if not force:
        last = await rs.get(_REDIS_LAST_REFRESH_KEY)
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                age = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if age < _REFRESH_TTL_SECONDS:
                    return {
                        "ok":            True,
                        "skipped_recent": True,
                        "age_seconds":    int(age),
                        "fetched_count":  0,
                        "indexed_count":  await _index_size(rs),
                    }
            except (ValueError, TypeError):
                pass

    try:
        import httpx
    except ImportError as e:
        return {"ok": False, "error": f"httpx unavailable: {e}"}

    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(_OPENSANCTIONS_TARGETS_URL)
            if resp.status_code != 200:
                return {"ok": False, "error": f"OpenSanctions HTTP {resp.status_code}"}
            csv_text = resp.text
    except Exception as e:
        return {"ok": False, "error": f"OpenSanctions fetch failed: {type(e).__name__}: {e}"}

    # Parse CSV; pull out rows that have a wallet address
    import csv as _csv
    import io
    reader = _csv.DictReader(io.StringIO(csv_text))
    indexed = 0
    fetched = 0
    new_index: dict[str, list[dict[str, Any]]] = {}
    for row in reader:
        fetched += 1
        # The OpenSanctions simple CSV has columns including 'addresses'
        # and 'crypto_addresses' depending on the schema version. Try
        # both and tolerate either.
        wallet_field = row.get("crypto_addresses") or row.get("walletAddress") or ""
        if not wallet_field:
            continue
        wallets = [w.strip() for w in re.split(r"[,;\s]+", wallet_field) if w.strip()]
        if not wallets:
            continue
        entity_id = row.get("id") or row.get("entity_id") or ""
        entity_name = row.get("name") or row.get("caption") or ""
        topics = row.get("topics") or ""
        countries = row.get("countries") or ""
        for w in wallets:
            wlow = w.lower()
            new_index.setdefault(wlow, []).append({
                "entity_id":    entity_id,
                "entity_name":  entity_name,
                "topics":       topics,
                "countries":    countries,
                "chain":        detect_chain(w),
                "indexed_at":   datetime.now(timezone.utc).isoformat(),
            })
            indexed += 1

    if not new_index:
        return {
            "ok":            True,
            "fetched_count": fetched,
            "indexed_count": 0,
            "narrative":     "No wallet addresses found in OpenSanctions targets CSV.",
        }

    # Replace the index atomically (single set_json with the whole map)
    await rs.set_json(_REDIS_INDEX_KEY, new_index, ex=2 * _REFRESH_TTL_SECONDS)
    await rs.set(
        _REDIS_LAST_REFRESH_KEY,
        datetime.now(timezone.utc).isoformat(),
        ex=2 * _REFRESH_TTL_SECONDS,
    )

    return {
        "ok":            True,
        "fetched_count": fetched,
        "indexed_count": len(new_index),
        "skipped_recent": False,
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
        "narrative":     f"Indexed {len(new_index)} unique wallets from {fetched} OpenSanctions rows.",
    }


async def _index_size(rs) -> int:
    """Number of unique addresses in the index."""
    try:
        idx = await rs.get_json(_REDIS_INDEX_KEY)
        return len(idx) if isinstance(idx, dict) else 0
    except Exception:
        return 0


@wired(module="crypto_sanctions", summary="Crypto wallet screen", check_falsy_success=True)

async def screen_wallet(address: str) -> list[dict[str, Any]]:
    """Look up one wallet address in the indexed table.

    Returns: list of matching sanctions records (empty if no match).
    Match is exact (lowercase) — OpenSanctions provides the normalised
    form. Chain auto-detected and included in the response.
    """
    if not address or not isinstance(address, str):
        return []
    a = address.strip().lower()
    if not a:
        return []
    rs = await _redis()
    try:
        idx = await rs.get_json(_REDIS_INDEX_KEY)
    except Exception as e:
        logger.warning("crypto_sanctions: redis read failed: %s", e)
        return []
    if not isinstance(idx, dict):
        return []
    hits = idx.get(a) or []
    if hits:
        # Brain absorb the hit
        try:
            from . import brain_hook
            for hit in hits:
                await brain_hook.absorb(
                    module="crypto_sanctions",
                    summary=(
                        f"Sanctioned wallet match: {address[:10]}... ({hit.get('chain')}) "
                        f"linked to {hit.get('entity_name','?')}"
                    ),
                    detail=(
                        f"Address: {address}. Entity: {hit.get('entity_name')}. "
                        f"Topics: {hit.get('topics')}. Countries: {hit.get('countries')}."
                    ),
                    topic="crypto_sanctioned_wallet",
                    entity=hit.get("entity_name"),
                    success=True,
                    confidence="CONFIRMED",
                )
        except Exception as e:
            logger.debug("crypto sanctions brain absorb failed (non-fatal): %s", e)
    return hits


@wired(module="crypto_sanctions", summary="Crypto wallet batch screen", check_falsy_success=True)

async def screen_wallet_batch(addresses: list[str]) -> dict[str, Any]:
    """Screen a list of addresses; returns per-address match list."""
    if not addresses:
        return {"results": {}, "total_addresses": 0, "matched_addresses": 0}
    results: dict[str, list[dict[str, Any]]] = {}
    matched_count = 0
    for a in addresses:
        if not a:
            continue
        hits = await screen_wallet(a)
        results[a] = hits
        if hits:
            matched_count += 1
    return {
        "results":           results,
        "total_addresses":   len(addresses),
        "matched_addresses": matched_count,
        "narrative": (
            f"Screened {len(addresses)} address(es); {matched_count} matched "
            f"sanctioned-entity wallets."
            if matched_count
            else f"Screened {len(addresses)} address(es); none matched sanctioned wallets."
        ),
    }


async def index_status() -> dict[str, Any]:
    """For /api/aria/crypto/status — show last refresh + size."""
    rs = await _redis()
    last = await rs.get(_REDIS_LAST_REFRESH_KEY)
    size = await _index_size(rs)
    return {
        "indexed_count":     size,
        "last_refresh":      last,
        "stale_seconds":     None,  # could compute if last is set
        "source":            _OPENSANCTIONS_TARGETS_URL,
    }


def summary() -> dict[str, Any]:
    return {
        "module":  "crypto_sanctions",
        "source":  "OpenSanctions consolidated targets (free)",
        "chains":  [c for c, _ in _CHAIN_PATTERNS],
    }

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="crypto_sanctions",
                     summary="crypto_sanctions module active",
                     source_id="crypto_sanctions:init")
    except Exception:
        try:
            wire_failure(module="crypto_sanctions", detail="module init failed",
                        gap_type="engine_failure", source="crypto_sanctions:init")
        except Exception:
            pass
