"""R-F3011 — the FCA adapter must NOT report a real, authorised firm as
"Unauthorised" because the FCA search returned a CLONE-FIRM scam warning first.

Live defect (Schroder DD dd_fc3e2b4e824b): lookup_firm took firm_rows[0] blindly.
FCA ranks clone-firm scam warnings (fraudsters impersonating an authorised firm)
high; the top row was "Schroder Investment Management (Clone of FCA authorised
firm)" with an empty FRN and status "Unauthorised" — so the report flagged the
REAL Schroders as AMBER "Unauthorised" (a false, defamation-class accusation).

Fix: partition clone/scam warnings out, pick the best distinctive-name match among
genuine firms, and when ONLY clone warnings match, return is_authorised=None
(unknown) with a clone-warning flag — never is_authorised=False on the subject.
"""
import asyncio
from unittest.mock import patch

from aria_service.intel import fca_register as fca


class _CM:
    def __init__(self, client):
        self._c = client

    async def __aenter__(self):
        return self._c

    async def __aexit__(self, *a):
        return False


class _Resp:
    def __init__(self, rows):
        self.status_code = 200
        self._rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return {"Data": self._rows}


class _Client:
    def __init__(self, rows):
        self._rows = rows

    async def get(self, url, params=None):
        return _Resp(self._rows)


def _run(name, rows):
    with patch.object(fca, "_creds", return_value=("e@x.com", "k")), \
         patch("httpx.AsyncClient", return_value=_CM(_Client(rows))):
        return asyncio.run(fca.lookup_firm(name))


# ── pure helpers ──────────────────────────────────────────────────────────
def test_rf3011_clone_marker_detected():
    assert fca._is_clone_or_scam_warning(
        {"Name": "Schroder Investment Management (Clone of FCA authorised firm)"})
    assert fca._is_clone_or_scam_warning({"Name": "Acme Capital (CLONE)"})
    assert not fca._is_clone_or_scam_warning({"Name": "Schroder Investment Management Limited"})


def test_rf3011_name_match_ignores_generic_suffixes():
    hi = fca._name_match_score("Schroder Investment Management Limited",
                               "Schroder Investment Management Ltd")
    lo = fca._name_match_score("Schroder Investment Management Limited",
                               "Aviva Investors UK Limited")
    assert hi > 0.5, "distinctive token 'schroder' must drive a strong match"
    assert lo == 0.0, "a same-suffix stranger must not match on 'limited/uk/investment'"


# ── the live defect: ONLY a clone warning matched ──────────────────────────
def test_rf3011_only_clone_rows_never_reports_subject_unauthorised():
    rows = [{"Type": "firm",
             "Name": "Schroder Investment Management (Clone of FCA authorised firm)",
             "Reference Number": "", "Status": "Unauthorised"}]
    res = _run("Schroder Investment Management Limited", rows)
    assert res["matched"] is True
    assert res.get("clone_warning") is True
    assert res.get("is_authorised") is None, \
        "a clone warning must NOT be reported as the subject being unauthorised (was False — the bug)"
    assert res.get("frn") == ""


def test_rf3011_genuine_firm_preferred_over_clone_warning():
    rows = [
        {"Type": "firm",
         "Name": "Schroder Investment Management (Clone of FCA authorised firm)",
         "Reference Number": "", "Status": "Unauthorised"},
        {"Type": "firm", "Name": "Schroder Investment Management Limited",
         "Reference Number": "119348", "Status": "Authorised"},
    ]
    res = _run("Schroder Investment Management Limited", rows)
    assert res["matched"] is True
    assert res["frn"] == "119348", "the genuine authorised firm must win, not the clone"
    assert res["is_authorised"] is True
    assert res.get("clone_warning") is True and res.get("clone_count") == 1, \
        "the clone must still be surfaced as a note, just not as the subject's status"


def test_rf3011_genuine_unauthorised_firm_still_flagged():
    # a REAL (non-clone) firm that is genuinely not authorised must still read False
    rows = [{"Type": "firm", "Name": "Dodgy Capital Limited",
             "Reference Number": "999999", "Status": "No longer authorised"}]
    res = _run("Dodgy Capital Limited", rows)
    assert res["matched"] is True
    assert res["is_authorised"] is False, "a genuine non-authorised firm is still a true finding"
    assert res.get("clone_warning") is False
