"""R-F1821 — H6: identity allow-lists (Telegram webhook + WhatsApp sender).

Authorization review H6 (HIGH):
 - Telegram: _allowedUsers was enforced only in the getUpdates polling loop; the
   production webhook path (server.mjs -> _handleMessage) bypassed it, so any sender
   who found the bot could run /ask,/sweep,/risk.
 - WhatsApp: senderJid was never used for authorization — any group member could
   drive compliance commands with the internal token.

Fix: the allow-list check now lives inside telegram _handleMessage (both paths gated);
the WA listener gates compliance commands by WA_ALLOWED_SENDERS (opt-in).

Source-assertion test (both live in self-starting Node modules, not unit-importable;
verifies the security WIRING — same convention as test_proof_footer_rf403 / R-F1817).
"""
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_telegram_handlemessage_enforces_allowlist():
    s = (REPO / "lib/alerts/telegram.mjs").read_text(encoding="utf-8")
    i = s.find("async _handleMessage(msg)")
    assert i > 0, "_handleMessage not found"
    body = s[i:i + 1100]
    # the allow-list gate must run at the top of _handleMessage (covers the webhook path)
    assert "this._allowedUsers" in body, "_handleMessage does not consult the allow-list"
    assert "if (!_isGroup && !_allowed)" in body, "_handleMessage missing the allow-list gate"


def test_wa_handlecommand_enforces_sender_allowlist():
    s = (REPO / "services/wa-listener/aria_wa_listener.mjs").read_text(encoding="utf-8")
    assert "WA_ALLOWED_SENDERS" in s, "WA sender allow-list env not present"
    assert "function _waSenderAllowed" in s, "WA _waSenderAllowed helper missing"
    i = s.find("async function handleCommand(")
    assert i > 0, "handleCommand not found"
    body = s[i:i + 500]
    assert "_waSenderAllowed(senderJid)" in body, "handleCommand does not gate by sender allow-list"
