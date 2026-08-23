"""R-F3155 — per-case encryption so erasure is effective (UK GDPR Art. 17).

── The problem this solves ───────────────────────────────────────────────
`intel/dd_evidence_store.py` is append-only by construction and exposes no
delete. That is correct for what it is: the tamper-evident evidence spine DD
relies on. But a vetting case holds identity documents and criminal-offence
data about a named individual, and Art. 17 gives that individual a right to
erasure — a right that "our store is append-only" does not answer.

R-F3148 was honest about the residue (`erasure_complete: false`). Being honest
about a compliance failure is better than hiding it, but it is not compliance.

── The resolution ────────────────────────────────────────────────────────
Crypto-shredding. Document bytes are encrypted with a per-case key BEFORE they
reach the evidence store; only ciphertext is ever persisted there. The key
lives in the vetting store, which we own and can delete.

  * erasure = destroy the key. The ciphertext remains, and is thereafter
    indistinguishable from random data to anyone without the key.
  * the evidence spine keeps its integrity property — nothing is mutated or
    removed from it, so its tamper-evidence is intact.
  * the audit stub survives: which case, when, which document type, what
    hash — enough to prove the file existed and was disposed of on schedule,
    with no personal content behind it.

This is the recognised approach for immutable stores, and it is why the two
requirements (tamper-evident retention, and effective erasure) stop being in
conflict.

── Choices worth stating ─────────────────────────────────────────────────
AES-256-GCM: authenticated, so a tampered ciphertext fails to decrypt rather
than yielding plausible garbage. A fresh 12-byte nonce per document, stored as
a prefix — never reused, because GCM nonce reuse under one key is catastrophic.

`cryptography` is now DECLARED in requirements.txt. It was already installed
transitively (verifiable_ledger.py imports it at module scope), but an erasure
mechanism must not rest on a dependency nobody declared: a transitive package
can vanish in a routine upgrade, and this one failing closed would silently
stop uploads while failing open would store plaintext.
"""

from __future__ import annotations

import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32          # AES-256
NONCE_BYTES = 12        # GCM standard


class KeyDestroyed(LookupError):
    """The case key has been destroyed — the content is irrecoverable.

    This is the SUCCESS state of an erasure, not a fault. Callers must render
    it as "erased", never as "temporarily unavailable".
    """


class DecryptionFailed(ValueError):
    """Ciphertext did not authenticate under the supplied key."""


def new_case_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Return nonce || ciphertext||tag."""
    if len(key) != KEY_BYTES:
        raise ValueError(f"case key must be {KEY_BYTES} bytes")
    nonce = secrets.token_bytes(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, None)


def decrypt(blob: bytes, key: bytes) -> bytes:
    if key is None:
        raise KeyDestroyed("case key has been destroyed")
    if len(blob) <= NONCE_BYTES:
        raise DecryptionFailed("ciphertext is too short to contain a nonce")
    nonce, body = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    try:
        return AESGCM(key).decrypt(nonce, body, None)
    except InvalidTag as exc:
        raise DecryptionFailed(
            "ciphertext failed authentication — wrong key or tampered data"
        ) from exc


def encryption_enabled() -> bool:
    """Crypto-shredding is ON by default and must be switched OFF explicitly.

    A deployment that disables it keeps the R-F3148 residue and cannot satisfy
    an Art. 17 request for documents. The default is therefore the compliant
    one; the escape hatch exists for migrating an existing plaintext store, not
    as a routine setting.
    """
    raw = (os.getenv("ARIA_VETTING_ENCRYPT_DOCUMENTS", "1") or "").strip().lower()
    on = raw not in {"0", "false", "no", "off"}
    if not on:
        _report_encryption_disabled()
    return on


_disabled_reported = False


def _report_encryption_disabled() -> None:
    """R-F4255 (C-222) — a compliance control that is OFF must SAY SO, now.

    THE DEFECT: with this switch off, `routes/vetting.py` and
    `routes/vetting_portal.py` both take the `if encryption_enabled():` branch
    to False and write **plaintext identity and criminal-offence data into the
    append-only evidence store** — which exposes no delete. Nothing logged it,
    nothing recorded a gap, nothing surfaced it.

    The consequence only became visible at ERASURE time, via
    `retention._PLAINTEXT_RESIDUE_NOTE` — i.e. at the exact moment the subject
    has exercised an Art. 17 right and the data is already durably unerasable.
    A control whose failure is discovered only when you are legally obliged to
    have not failed is not a control.

    REPORTED HERE, at the ONE decision point, rather than at the two upload
    sites. Curating call sites is whack-a-mole — R-F3946 records the same
    reasoning for the Brave DD gate, where the ninth route silently re-opened
    it. A third caller of `encryption_enabled()` inherits this automatically.

    ONCE PER PROCESS. This is a CONFIG state, not a per-document event: a gap
    per upload would be the flood shape that has twice filled a 500-slot ledger,
    and §18 records the same once-per-process choice for
    `sanctions_coverage_degraded`. Restarting re-reports it, which is right —
    the state is still true.

    WARNING, not ERROR: it is a deliberate operator setting, and R-F4248 records
    that an ERROR for an operator condition resets the Phase A gate-#3 streak.
    Never raises — a compliance control must not be broken by its own reporting.
    """
    global _disabled_reported
    if _disabled_reported:
        return
    _disabled_reported = True
    try:
        import logging
        logging.getLogger("aria.vetting.crypto").warning(
            "[R-F4255] ARIA_VETTING_ENCRYPT_DOCUMENTS is OFF — vetting documents "
            "are being stored as PLAINTEXT in the append-only evidence store. "
            "UK GDPR Art. 17 erasure CANNOT be completed for anything uploaded "
            "while this holds (crypto-shredding needs a key to destroy)."
        )
        from ..intel.engine_wiring import wire_failure as _wf
        _wf(
            module="vetting_crypto",
            detail=(
                "ARIA_VETTING_ENCRYPT_DOCUMENTS is OFF. Identity and "
                "criminal-offence documents are stored UNENCRYPTED in an "
                "append-only store that exposes no delete, so destroying the "
                "case key cannot erase them and an Art. 17 request will report "
                "residue instead of completion. The compliant default is ON; "
                "the switch exists only for migrating an existing plaintext "
                "store. OPERATOR ACTION: unset it, then purge the documents "
                "uploaded while it was off."
            )[:600],
            gap_type="data_protection_violation",
            source="vetting_crypto:encryption_disabled",
        )
    except Exception:      # pragma: no cover — reporting never breaks the control
        pass


def _reset_disabled_report_for_test() -> None:
    """Test hook — the once-per-process latch is module state."""
    global _disabled_reported
    _disabled_reported = False
