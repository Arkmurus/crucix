"""R-F1927 — WhatsApp multi-account links must survive a listener restart.

The bug the operator hit ("No WhatsApp accounts connected" + can't keep a link):
`_accounts` was an in-memory Map with NO boot-restore, while Baileys creds DO
persist per-account on the volume. Every deploy/restart wiped the Map, so the UI
forgot every linked device even though its creds were on disk — and several
aria-wa redeploys in one day made it look permanently broken.

Fix: persist account metadata (id/name/ownerUserId) and on boot re-instantiate
each socket from its saved creds (reconnects without a new QR if still linked).

Source-pinned (the listener mjs starts a server + Baileys on import, so it can't be
imported under pytest — same approach as the R-F1909/R-F1918 WA guards).
"""
from __future__ import annotations

import pathlib

WA = (pathlib.Path(__file__).resolve().parents[2]
      / "services" / "wa-listener" / "aria_wa_listener.mjs").read_text(encoding="utf-8", errors="ignore")


def test_account_metadata_is_persisted_and_restored():
    assert "function _persistAccounts()" in WA
    assert "async function _loadAccounts()" in WA
    assert "_ACCOUNTS_META_FILE" in WA
    # restored at boot, alongside the other R-F964/R-F1918 restores
    assert "_loadAccounts()" in WA and "_loadAccounts().catch" in WA


def test_persist_called_on_create_and_delete():
    # at least 2 call sites: account create + delete (keep the file in sync)
    assert WA.count("_persistAccounts();") >= 2


def test_restore_reuses_saved_creds_and_preserves_ownership():
    # restore re-instantiates the socket from saved creds via _createAccount...
    assert "await _createAccount(m.id, m.name || m.id, m.ownerUserId" in WA
    # ...only if the creds dir still exists (don't resurrect deleted accounts)
    assert "fs.existsSync(_accountPath(m.id))" in WA
    # ...and the R-F1909 (G3) per-user ownership is carried through the restart
    assert "ownerUserId: a.ownerUserId" in WA
