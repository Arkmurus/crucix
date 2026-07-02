"""R-F2336 — the VLS signing key must be STABLE across reloads (reboots).

Before the fix, `_load_or_generate_key` called `ec.EllipticCurvePrivateKey.from_pem`
(a method that does not exist in `cryptography`), so every load raised AttributeError,
the except swallowed it, and a NEW key was generated and written on every boot — breaking
signature verification for every previously-sealed DD report (hash stayed valid, only the
signature failed). This is the exact "Hash valid: true / Signature valid: false" symptom.
"""
import pytest

from aria_service.intel import verifiable_ledger as vl


def test_key_is_stable_across_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(vl, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(vl, "_PRIVATE_KEY_PATH", tmp_path / "vls_key.pem")
    monkeypatch.setattr(vl, "_PUBLIC_KEY_PATH", tmp_path / "vls_key.pub")
    monkeypatch.setattr(vl, "_private_key", None)

    # First use: generates + persists the key and signs a payload.
    data = {"run_id": "dd_test", "risk": "RED", "body": "x"}
    sig = vl._sign_hash(vl._compute_hash(data))
    assert (tmp_path / "vls_key.pem").exists()
    pub1 = vl.get_public_key_pem()
    assert vl.verify_signature(data, sig) is True

    # Simulate a reboot: drop the in-memory key so the next use RELOADS from disk.
    monkeypatch.setattr(vl, "_private_key", None)
    pub2 = vl.get_public_key_pem()

    # The reloaded key MUST be identical (the bug regenerated a different one every boot).
    assert pub1 == pub2, "VLS key changed across reload — R-F2336 regression"
    # And a signature made BEFORE the reboot must STILL verify (the user-visible symptom).
    assert vl.verify_signature(data, sig) is True


@pytest.mark.asyncio
async def test_verify_single_reports_content_intact_not_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(vl, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(vl, "_PRIVATE_KEY_PATH", tmp_path / "k.pem")
    monkeypatch.setattr(vl, "_PUBLIC_KEY_PATH", tmp_path / "k.pub")
    monkeypatch.setattr(vl, "_private_key", None)

    body = {"run_id": "dd_x", "risk_classification": "AMBER"}
    good_hash = vl._compute_hash(body)
    # Proof carries the correct hash but a signature that will NOT verify (rotated key).
    proof = {"hash": good_hash, "signature": "AAAA", "version": 1}

    import aria_service.intel.redis_store as rs

    async def fake_get_json(key):
        if key.startswith("crucix:dd:vls:"):
            return proof
        if key.startswith("crucix:dd:report:"):
            return body
        return None

    monkeypatch.setattr(rs, "get_json", fake_get_json)
    r = await vl.verify_single("dd_x")
    assert r["hash_valid"] is True
    assert r["signature_valid"] is False
    assert r["content_intact"] is True          # body is provably intact
    assert r["verified"] is False               # but not fully verified
    assert "not evidence of tampering" in r["reason"].lower()
