"""R-F1929 — WhatsApp connection UX is consolidated to ONE manager.

The model card had a primitive, duplicated copy of the connection workflow:
prompt() for the name, then innerHTML-swap + location.reload() (which blew away
the whole page on connect). The dedicated wa-connections.html already has the good
UX (styled input, account cards, QR modal, live polling, no reload). R-F3405
removed even the read-only status from the public model card: connection inventory
is operational information and belongs only on the authenticated manager.
"""
from __future__ import annotations

import pathlib

PUBLIC = pathlib.Path(__file__).resolve().parents[2] / "public"
MODEL_CARD = (PUBLIC / "model-card.html").read_text(encoding="utf-8", errors="ignore")


def test_model_card_has_no_primitive_wa_flow():
    assert "prompt(" not in MODEL_CARD, "model-card must not use prompt() for WA connections"
    assert "location.reload()" not in MODEL_CARD, "model-card must not full-page-reload on WA connect"
    assert 'data-action="createAccount"' not in MODEL_CARD, "the inline create widget must be gone"


def test_public_model_card_exposes_no_wa_inventory_or_manager_link():
    assert "/api/wa-listener/accounts" not in MODEL_CARD
    assert 'id="wa-accounts"' not in MODEL_CARD
    assert "/wa-connections.html" not in MODEL_CARD


def test_dedicated_manager_still_present_and_polished():
    wa = (PUBLIC / "wa-connections.html").read_text(encoding="utf-8", errors="ignore")
    # the good patterns must remain: modal QR, in-place refresh (not reload), styled input
    assert "qrModal" in wa and "showQrRaw" in wa, "QR modal flow must remain"
    assert "await refresh()" in wa, "in-place refresh (no page reload) must remain"
    assert "location.reload()" not in wa, "the manager must not full-page-reload"
