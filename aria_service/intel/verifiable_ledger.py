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
    load_pem_private_key,
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
    """Load the existing ECDSA key, or generate a new SECP256K1 pair on FIRST use only.

    The key is stored on the persistent /data volume so it survives restarts.

    R-F2336 (bug fix): the previous loader called ``ec.EllipticCurvePrivateKey.from_pem`` —
    a method that DOES NOT EXIST in `cryptography` — so loading an existing key ALWAYS
    raised AttributeError, the except swallowed it, and a brand-new key was generated and
    written over the file on EVERY boot. That silently rotated the signing key on every
    restart, so every report sealed under a prior boot's key failed signature verification
    (the hash stayed valid — the report body was intact — only the signature could not be
    checked against the current key). Loading via serialization.load_pem_private_key makes
    the key STABLE across restarts. A valid key file present on disk is NEVER overwritten;
    regeneration happens only when no file exists, or (loudly, wired to the brain) when an
    existing file is genuinely unreadable.
    """
    if _PRIVATE_KEY_PATH.exists():
        try:
            with open(_PRIVATE_KEY_PATH, "rb") as f:
                key = load_pem_private_key(f.read(), password=None)
            if isinstance(key, ec.EllipticCurvePrivateKey):
                return key
            raise TypeError(f"stored VLS key is not an EC private key: {type(key).__name__}")
        except Exception as e:
            # Do NOT silently rotate — surface loudly. With the correct API this only fires
            # on a genuinely corrupt/truncated file (rare), where prior proofs are already
            # unverifiable; regenerating keeps NEW reports sealable rather than breaking VLS.
            logger.error(
                "VLS: existing key at %s failed to load (%s) — regenerating (LOUD). Reports "
                "sealed under the previous key can no longer be signature-verified.",
                _PRIVATE_KEY_PATH, e,
            )
            try:
                wire_failure("verifiable_ledger", f"VLS key load failed, regenerating: {e}",
                             gap_type="engine_failure", source="verifiable_ledger:key")
            except Exception:
                pass

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

async def record_report(
    report: Any = None,
    *,
    run_id: str = "",
    report_body: dict | None = None,
    canonical_entity_id: str | None = None,
    version_number: int = 1,
) -> dict:
    """Cryptographically seal a DD report and store the proof.

    R-F2005: accepts EITHER a legacy ARKDDReport instance OR explicit
    keyword arguments (run_id, report_body, canonical_entity_id,
    version_number). The keyword path is preferred because it receives
    the EXACT dict that was stored in Redis (including `rendered` and
    `version_diff`), so the proof hash matches the stored body.

    Called fire-and-forget from _persist_report. Never raises — all
    errors are caught and wired to the brain as failures.

    Args:
        report: Legacy ARKDDReport instance (deprecated).
        run_id: DD report run ID.
        report_body: The EXACT dict stored in Redis (must include
            rendered, version_diff, etc. for hash consistency).
        canonical_entity_id: Canonical entity ID for chain linking.
        version_number: Version number of this report.

    Returns:
        dict with "status": "ok" | "skipped" and "proof" if stored.
    """
    try:
        # Support both legacy (ARKDDReport) and new (explicit kwargs) paths
        if report is not None:
            # Legacy path — kept for backward compatibility
            _run_id = getattr(report, "run_id", None)
            _canonical = getattr(report, "canonical_entity_id", None)
            _body = report.as_dict() if hasattr(report, "as_dict") else {}
            _version = getattr(report, "version_number", 1)
        else:
            _run_id = run_id
            _canonical = canonical_entity_id
            _body = report_body or {}
            _version = version_number

        if not _run_id:
            wire_failure("verifiable_ledger", "record_report: no run_id", source="verifiable_ledger")
            return {"status": "skipped", "reason": "no run_id"}

        if not _body:
            wire_failure("verifiable_ledger", f"record_report: empty body for {_run_id}", source="verifiable_ledger")
            return {"status": "skipped", "reason": "empty body"}

        from . import redis_store as rs

        # Resolve the previous proof for chain linking
        previous_proof = None
        if _canonical:
            try:
                chain_key = f"{_VLS_CHAIN_PREFIX}:{_canonical}"
                chain = await rs.get_json(chain_key) or []
                if chain:
                    last_run_id = chain[0]  # newest first
                    prev_proof_key = f"{_VLS_KEY_PREFIX}:{last_run_id}"
                    previous_proof = await rs.get_json(prev_proof_key)
            except Exception as e:
                logger.debug("VLS: chain resolve failed (non-fatal): %s", e)

        # Build and store the proof
        proof = _build_proof(_body, previous_proof)
        proof_key = f"{_VLS_KEY_PREFIX}:{_run_id}"
        await rs.set_json(proof_key, proof)

        # Update the chain index
        if _canonical:
            try:
                chain_key = f"{_VLS_CHAIN_PREFIX}:{_canonical}"
                chain = await rs.get_json(chain_key) or []
                chain.insert(0, _run_id)
                chain = chain[:100]  # cap at 100 versions
                await rs.set_json(chain_key, chain)
            except Exception as e:
                logger.debug("VLS: chain update failed (non-fatal): %s", e)

        wire_success(
            module="verifiable_ledger",
            summary=f"VLS proof stored for DD {_run_id} v{proof['version']}",
            entity_name=_canonical or _run_id,
            source_id=_run_id,
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
            # R-F2065: extract risk_classification from the report body for
            # the frontend risk pill. The field is stored as an uppercase
            # string (RED, AMBER_DARK, AMBER_LIGHT, GREEN, HARD_STOP, etc.)
            # and may be absent on pre-R-F130 reports.
            _risk = (report_body or {}).get("risk_classification", "")

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
                        "risk_classification": _risk,
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
                    "risk_classification": _risk,
                })
                all_valid = False
                continue

            results.append({
                "run_id": run_id,
                "verified": True,
                "version": proof.get("version"),
                "timestamp": proof.get("timestamp"),
                "risk_classification": _risk,
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

        # R-F2065: include risk_classification for frontend display
        _risk = report_body.get("risk_classification", "")

        # R-F2336: hash mismatch = the body CHANGED since sealing (real tampering).
        # hash-OK but signature-invalid = the body is INTACT but was signed under a
        # superseded key (pre-fix key-rotation artifact) — NOT tampering. Report these
        # distinctly so the UI never cries "tampered" when the content is provably intact.
        if hash_valid and not sig_valid:
            _reason = ("Report content is INTACT (hash matches the sealed value) but its "
                       "signature was produced under a superseded signing key and cannot be "
                       "re-verified — a key-rotation artifact (R-F2336), NOT evidence of tampering.")
        elif not hash_valid:
            _reason = ("Report content hash does NOT match the sealed value — the stored "
                       "report may have been altered since it was sealed.")
        else:
            _reason = "Report is intact and its signature verifies against the VLS key."
        return {
            "verified": hash_valid and sig_valid,
            "content_intact": hash_valid,
            "run_id": run_id,
            "hash_valid": hash_valid,
            "signature_valid": sig_valid,
            "reason": _reason,
            "version": proof.get("version"),
            "timestamp": proof.get("timestamp"),
            "previous_hash": proof.get("previous_hash"),
            "risk_classification": _risk,
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
