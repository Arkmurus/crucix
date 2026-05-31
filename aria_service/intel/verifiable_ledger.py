"""
ARK-VLS: Verifiable Ledger System for ARIA (R-F1182)
Blockchain-inspired immutable intelligence records with cryptographic
chain-of-custody for DD reports.

Design
------
Every DD report gets a cryptographic proof (hash + ECDSA signature + chain
link to the previous version of the same canonical entity). The proof is
stored separately from the report body so ARKDDReport schema is unchanged.

Storage
-------
  crucix:dd:vls:{run_id}  →  VLSProof (dict with hash, signature, chain)
  crucix:dd:vls:chain:{canonical_entity_id}  →  ordered list of run_ids

Key management
--------------
ECDSA SECP256K1 key pair at /data/aria_vls_key.pem (persistent volume).
Generated on first boot if absent. Public key exported at /data/aria_vls_key.pub.

Integration
-----------
Called fire-and-forget from dd_orchestrator._persist_report after the
report is stored. Never blocks the DD pipeline. Both success and failure
are wired to the brain via engine_wiring.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

logger = logging.getLogger("aria.verifiable_ledger")

# ── Brain wiring (R-F994) ──────────────────────────────────────────────────
from .engine_wiring import wire_success, wire_failure

# ── Redis key namespace ─────────────────────────────────────────────────────
_VLS_KEY_PREFIX = "crucix:dd:vls"
_VLS_CHAIN_PREFIX = "crucix:dd:vls:chain"

# ── Key paths ───────────────────────────────────────────────────────────────
_DATA_DIR = Path(os.environ.get("ARIA_DATA_DIR", "/data"))
_PRIVATE_KEY_PATH = _DATA_DIR / "aria_vls_key.pem"
_PUBLIC_KEY_PATH = _DATA_DIR / "aria_vls_key.pub"


# ============================================================================
# KEY MANAGEMENT
# ============================================================================

def _load_or_generate_key() -> ec.EllipticCurvePrivateKey:
    """Load existing ECDSA key or generate a new SECP256K1 pair.

    The key is stored on the persistent /data volume so it survives
    restarts. If the key file doesn't exist, a new pair is generated
    and both private and public keys are written to disk.
    """
    if _PRIVATE_KEY_PATH.exists():
        try:
            with open(_PRIVATE_KEY_PATH, "rb") as f:
                return ec.EllipticCurvePrivateKey.from_pem(f.read())
        except Exception as e:
            logger.warning("VLS: failed to load key, generating new: %s", e)

    private_key = ec.generate_private_key(ec.SECP256K1())
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    with open(_PRIVATE_KEY_PATH, "wb") as f:
        f.write(private_key.private_bytes(
            Encoding.PEM,
            PrivateFormat.PKCS8,
            NoEncryption(),
        ))
    with open(_PUBLIC_KEY_PATH, "wb") as f:
        f.write(private_key.public_key().public_bytes(
            Encoding.PEM,
            PublicFormat.SubjectPublicKeyInfo,
        ))

    logger.info("VLS: generated new ECDSA SECP256K1 key pair at %s", _PRIVATE_KEY_PATH)
    return private_key


# Lazy-loaded key — initialised on first use so boot doesn't fail if
# cryptography isn't installed (though it should be).
_private_key: Optional[ec.EllipticCurvePrivateKey] = None


def _get_key() -> ec.EllipticCurvePrivateKey:
    global _private_key
    if _private_key is None:
        _private_key = _load_or_generate_key()
    return _private_key


def get_public_key_pem() -> str:
    """Return the public key in PEM format for export endpoints."""
    key = _get_key().public_key()
    return key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()


# ============================================================================
# CRYPTOGRAPHIC PRIMITIVES
# ============================================================================

def _compute_hash(data: dict) -> str:
    """SHA-256 hash of a JSON-serialised dict (sorted keys for stability)."""
    raw = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _sign_hash(data_hash: str) -> str:
    """ECDSA-SHA256 signature, base64-encoded."""
    key = _get_key()
    signature = key.sign(data_hash.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(signature).decode("ascii")


def verify_signature(data: dict, signature_b64: str) -> bool:
    """Verify an ECDSA-SHA256 signature against data.

    Returns True if the signature is valid, False otherwise.
    Uses the module's own public key. For third-party verification,
    the public key can be exported via get_public_key_pem().
    """
    try:
        data_hash = _compute_hash(data)
        signature_bytes = base64.b64decode(signature_b64)
        key = _get_key().public_key()
        key.verify(signature_bytes, data_hash.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


# ============================================================================
# VLS PROOF — stored per report
# ============================================================================

def _build_proof(
    report_body: dict,
    previous_proof: Optional[dict],
) -> dict:
    """Build a cryptographic proof for a DD report.

    Args:
        report_body: The full ARKDDReport.as_dict() output.
        previous_proof: The VLSProof of the previous version of the same
            canonical entity, or None if this is the first version.

    Returns:
        dict with keys: hash, previous_hash, signature, timestamp, version
    """
    report_hash = _compute_hash(report_body)
    previous_hash = (previous_proof or {}).get("hash", "0" * 64)
    signature = _sign_hash(report_hash)

    return {
        "hash": report_hash,
        "previous_hash": previous_hash,
        "signature": signature,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": (previous_proof or {}).get("version", 0) + 1,
    }


# ============================================================================
# PUBLIC API
# ============================================================================

async def record_report(report: Any) -> dict:
    """Cryptographically seal a DD report and store the proof.

    Called fire-and-forget from _persist_report. Never raises — all
    errors are caught and wired to the brain as failures.

    Args:
        report: ARKDDReport instance (must have run_id, as_dict(),
            canonical_entity_id, version_number).

    Returns:
        dict with "status": "ok" | "skipped" and "proof" if stored.
    """
    try:
        run_id = getattr(report, "run_id", None)
        if not run_id:
            wire_failure("verifiable_ledger", "record_report: no run_id", source="verifiable_ledger")
            return {"status": "skipped", "reason": "no run_id"}

        canonical_id = getattr(report, "canonical_entity_id", None)
        report_body = report.as_dict() if hasattr(report, "as_dict") else {}

        if not report_body:
            wire_failure("verifiable_ledger", f"record_report: empty body for {run_id}", source="verifiable_ledger")
            return {"status": "skipped", "reason": "empty body"}

        from . import redis_store as rs

        # Resolve the previous proof for chain linking
        previous_proof = None
        if canonical_id:
            try:
                chain_key = f"{_VLS_CHAIN_PREFIX}:{canonical_id}"
                chain = await rs.get_json(chain_key) or []
                if chain:
                    last_run_id = chain[0]  # newest first
                    prev_proof_key = f"{_VLS_KEY_PREFIX}:{last_run_id}"
                    previous_proof = await rs.get_json(prev_proof_key)
            except Exception as e:
                logger.debug("VLS: chain resolve failed (non-fatal): %s", e)

        # Build and store the proof
        proof = _build_proof(report_body, previous_proof)
        proof_key = f"{_VLS_KEY_PREFIX}:{run_id}"
        await rs.set_json(proof_key, proof)

        # Update the chain index
        if canonical_id:
            try:
                chain_key = f"{_VLS_CHAIN_PREFIX}:{canonical_id}"
                chain = await rs.get_json(chain_key) or []
                chain.insert(0, run_id)
                chain = chain[:100]  # cap at 100 versions
                await rs.set_json(chain_key, chain)
            except Exception as e:
                logger.debug("VLS: chain update failed (non-fatal): %s", e)

        wire_success(
            module="verifiable_ledger",
            summary=f"VLS proof stored for DD {run_id} v{proof['version']}",
            entity_name=getattr(report, "canonical_entity_id", "") or run_id,
            source_id=run_id,
        )

        return {"status": "ok", "proof": proof}

    except Exception as e:
        logger.warning("VLS: record_report failed: %s", e)
        wire_failure("verifiable_ledger", f"record_report: {e}", source="verifiable_ledger")
        return {"status": "error", "reason": str(e)}


async def get_proof(run_id: str) -> Optional[dict]:
    """Retrieve the VLS proof for a given DD run_id.

    Returns None if no proof exists (pre-VLS reports, or the proof
    was never stored).
    """
    try:
        from . import redis_store as rs
        return await rs.get_json(f"{_VLS_KEY_PREFIX}:{run_id}")
    except Exception as e:
        logger.debug("VLS: get_proof failed for %s: %s", run_id, e)
        return None


async def verify_chain(canonical_entity_id: str) -> dict:
    """Verify the entire VLS chain for a canonical entity.

    Checks:
      1. Every proof's hash matches the stored report body
      2. Every proof's signature is valid
      3. The chain of previous_hash links is unbroken

    Args:
        canonical_entity_id: The canonical entity ID to verify.

    Returns:
        dict with verified (bool), total_versions, and per-version results.
    """
    try:
        from . import redis_store as rs

        chain_key = f"{_VLS_CHAIN_PREFIX}:{canonical_entity_id}"
        chain = await rs.get_json(chain_key) or []
        if not chain:
            return {
                "verified": False,
                "error": "No VLS chain found for this entity",
                "canonical_entity_id": canonical_entity_id,
            }

        results = []
        previous_hash = "0" * 64
        all_valid = True

        # Chain is stored newest-first; verify oldest-first so
        # previous_hash chains correctly.
        for run_id in reversed(chain):
            proof = await rs.get_json(f"{_VLS_KEY_PREFIX}:{run_id}")
            if not proof:
                results.append({
                    "run_id": run_id,
                    "verified": False,
                    "reason": "Proof not found",
                })
                all_valid = False
                continue

            # Check chain link
            chain_ok = proof.get("previous_hash") == previous_hash
            if not chain_ok:
                results.append({
                    "run_id": run_id,
                    "verified": False,
                    "reason": "Chain broken: previous_hash mismatch",
                })
                all_valid = False
                continue

            # Check signature (verify against the hash stored in the proof)
            # We reconstruct the hash from the report body to verify integrity
            report_key = f"crucix:dd:report:{run_id}"
            report_body = await rs.get_json(report_key)
            if report_body:
                computed_hash = _compute_hash(report_body)
                hash_ok = computed_hash == proof.get("hash")
                sig_ok = verify_signature(report_body, proof.get("signature", ""))
                version_ok = hash_ok and sig_ok
                if not version_ok:
                    results.append({
                        "run_id": run_id,
                        "verified": False,
                        "reason": "Hash or signature mismatch",
                        "hash_valid": hash_ok,
                        "signature_valid": sig_ok,
                    })
                    all_valid = False
                    continue
            else:
                # Report body expired or missing — can only verify chain link
                results.append({
                    "run_id": run_id,
                    "verified": False,
                    "reason": "Report body not found (cannot verify hash/signature)",
                    "chain_link_valid": chain_ok,
                })
                all_valid = False
                continue

            results.append({
                "run_id": run_id,
                "verified": True,
                "version": proof.get("version"),
                "timestamp": proof.get("timestamp"),
            })
            previous_hash = proof.get("hash")

        return {
            "verified": all_valid,
            "canonical_entity_id": canonical_entity_id,
            "total_versions": len(chain),
            "verified_count": sum(1 for r in results if r.get("verified")),
            "results": results,
        }

    except Exception as e:
        logger.warning("VLS: verify_chain failed: %s", e)
        wire_failure("verifiable_ledger", f"verify_chain: {e}", source="verifiable_ledger")
        return {
            "verified": False,
            "error": str(e),
            "canonical_entity_id": canonical_entity_id,
        }


async def verify_single(run_id: str) -> dict:
    """Verify a single DD report's VLS proof.

    Checks the hash and signature of the report body against the
    stored proof. Does NOT check chain linking (use verify_chain
    for that).

    Returns:
        dict with verified (bool), run_id, and details.
    """
    try:
        from . import redis_store as rs

        proof = await rs.get_json(f"{_VLS_KEY_PREFIX}:{run_id}")
        if not proof:
            return {
                "verified": False,
                "run_id": run_id,
                "reason": "No VLS proof found for this report",
            }

        report_key = f"crucix:dd:report:{run_id}"
        report_body = await rs.get_json(report_key)
        if not report_body:
            return {
                "verified": False,
                "run_id": run_id,
                "reason": "Report body not found (may have expired)",
                "proof_exists": True,
            }

        computed_hash = _compute_hash(report_body)
        hash_valid = computed_hash == proof.get("hash")
        sig_valid = verify_signature(report_body, proof.get("signature", ""))

        return {
            "verified": hash_valid and sig_valid,
            "run_id": run_id,
            "hash_valid": hash_valid,
            "signature_valid": sig_valid,
            "version": proof.get("version"),
            "timestamp": proof.get("timestamp"),
            "previous_hash": proof.get("previous_hash"),
        }

    except Exception as e:
        logger.warning("VLS: verify_single failed: %s", e)
        return {
            "verified": False,
            "run_id": run_id,
            "error": str(e),
        }


async def get_chain(canonical_entity_id: str) -> list[dict]:
    """Return the ordered list of VLS proofs for a canonical entity.

    Returns empty list if no chain exists.
    """
    try:
        from . import redis_store as rs
        chain_key = f"{_VLS_CHAIN_PREFIX}:{canonical_entity_id}"
        chain = await rs.get_json(chain_key) or []
        results = []
        for run_id in chain:
            proof = await rs.get_json(f"{_VLS_KEY_PREFIX}:{run_id}")
            if proof:
                proof["run_id"] = run_id
                results.append(proof)
        return results
    except Exception as e:
        logger.debug("VLS: get_chain failed: %s", e)
        return []
