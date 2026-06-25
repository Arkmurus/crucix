"""R-F1929 — WhatsApp connection UX is consolidated to ONE manager.

The model card had a primitive, duplicated copy of the connection workflow:
prompt() for the name, then innerHTML-swap + location.reload() (which blew away
the whole page on connect). The dedicated wa-connections.html already has the good
UX (styled input, account cards, QR modal, live polling, no reload). This guard
keeps the model card READ-ONLY (status + a link into the manager) so the primitive
patterns can't creep back and the logic stays in one place.
"""
from __future__ import annotations

import pathlib

PUBLIC = pathlib.Path(__file__).resolve().parents[2] / "public"
MODEL_CARD = (PUBLIC / "model-card.html").read_text(encoding="utf-8", errors="ignore")


def test_model_card_has_no_primitive_wa_flow():
    assert "prompt(" not in MODEL_CARD, "model-card must not use prompt() for WA connections"
    assert "location.reload()" not in MODEL_CARD, "model-card must not full-page-reload on WA connect"
    assert 'data-action="createAccount"' not in MODEL_CARD, "the inline create widget must be gone"


def test_model_card_links_to_the_dedicated_manager():
    assert "/wa-connections.html" in MODEL_CARD, \
        "model-card must link to the dedicated WhatsApp connection manager"


def test_dedicated_manager_still_present_and_polished():
    wa = (PUBLIC / "wa-connections.html").read_text(encoding="utf-8", errors="ignore")
    # the good patterns must remain: modal QR, in-place refresh (not reload), styled input
    assert "qrModal" in wa and "showQrRaw" in wa, "QR modal flow must remain"
    assert "await refresh()" in wa, "in-place refresh (no page reload) must remain"
    assert "location.reload()" not in wa, "the manager must not full-page-reload"
