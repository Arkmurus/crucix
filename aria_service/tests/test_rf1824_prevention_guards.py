"""R-F1824 — Phase-4 prevention guards (make the authz vuln classes un-reintroducible).

check_no_token_default (audit H2): flags a hardcoded 'aria-internal' auth-token fallback.
check_ssrf_fetch_boundary (audit C2): flags an intel module fetching a user-controlled
URL variable without the url_safety SSRF boundary (dynamic-URL only — constant API
calls are not flagged).

Tests drive the REAL guards + a regression that the actually-fixed repo files pass.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
from pre_commit_checks import check_no_token_default, check_ssrf_fetch_boundary  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]


def _mk(tmp_path, rel, body):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ── H2 guard ──────────────────────────────────────────────────────────────────
def test_token_default_flagged(tmp_path):
    f = _mk(tmp_path, "lib/x.mjs", "const T = process.env.ARIA_INTERNAL_TOKEN || 'aria-internal';\n")
    assert len(check_no_token_default([f])) == 1


def test_token_default_clean_passes(tmp_path):
    f = _mk(tmp_path, "lib/x.mjs", "const T = process.env.ARIA_INTERNAL_TOKEN || '';\n")
    assert check_no_token_default([f]) == []


def test_token_default_real_files_clean():
    files = [REPO / "services/wa-listener/aria_wa_listener.mjs", REPO / "lib/aria/proactive.mjs",
             REPO / "lib/whatsapp/ariaWhatsApp.mjs", REPO / "services/aria_zoom_service.py"]
    assert check_no_token_default(files) == []


# ── C2 guard ──────────────────────────────────────────────────────────────────
def test_ssrf_dynamic_fetch_flagged(tmp_path):
    f = _mk(tmp_path, "aria_service/intel/badfetch.py",
            "import httpx\nasync def go(url):\n    async with httpx.AsyncClient() as c:\n        return await c.get(url)\n")
    issues = check_ssrf_fetch_boundary([f])
    assert len(issues) == 1 and "SSRF" in issues[0]


def test_ssrf_constant_fetch_not_flagged(tmp_path):
    f = _mk(tmp_path, "aria_service/intel/apifetch.py",
            'import httpx\nasync def go():\n    async with httpx.AsyncClient() as c:\n        return await c.get("https://api.example.com/v1")\n')
    assert check_ssrf_fetch_boundary([f]) == []


def test_ssrf_with_guard_passes(tmp_path):
    f = _mk(tmp_path, "aria_service/intel/goodfetch.py",
            "import httpx\nfrom . import url_safety\nasync def go(url):\n    async with httpx.AsyncClient() as c:\n        return await url_safety.safe_get(c, url)\n")
    assert check_ssrf_fetch_boundary([f]) == []


def test_ssrf_real_fixed_files_clean():
    files = [REPO / "aria_service/intel/document_reader.py", REPO / "aria_service/intel/link_investigator.py"]
    assert check_ssrf_fetch_boundary(files) == []
