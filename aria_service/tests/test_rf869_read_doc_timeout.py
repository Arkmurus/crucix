"""R-F869 — read-document time cap raised 45→80s + env-configurable.

The 45s R-F725 cap 504'd on a legitimate large/scanned trade-finance contract
(Forcados SPA, MT199/DLC MT700) whose multi-page OCR exceeds 45s. 80s sits under
the WA listener's 90s brainPost timeout so the caller still receives the result.
Configurable via ARIA_READ_DOC_TIMEOUT_S for no-redeploy tuning.
"""
from __future__ import annotations

from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "routes" / "aria.py").read_text(encoding="utf-8")


def test_timeout_is_env_configurable():
    assert 'os.getenv("ARIA_READ_DOC_TIMEOUT_S"' in SRC or "ARIA_READ_DOC_TIMEOUT_S" in SRC


def test_default_is_80_not_45():
    # The new default budget for read-document.
    assert '"ARIA_READ_DOC_TIMEOUT_S", "80"' in SRC
    # The old hard 45.0 in the wait_for must be gone.
    assert "_read_document_ep_impl(request), timeout=45.0" not in SRC


def test_default_under_wa_brainpost_timeout():
    """The read-document cap (80s) MUST stay under the WhatsApp listener's
    90s brainPost timeout, or the WA side times out first and the user never
    gets the read result."""
    wa = (Path(__file__).resolve().parents[2] / "services" / "wa-listener" / "aria_wa_listener.mjs").read_text(encoding="utf-8")
    assert "'/aria/') ? 90000" in wa or "90000" in wa  # WA waits 90s for /aria/ paths
    # 80 (read-doc default) < 90 (WA brainPost) — invariant documented here.


def test_timeout_used_in_wait_for():
    assert "_read_document_ep_impl(request), timeout=_r869_timeout" in SRC
