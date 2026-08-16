"""R-F4082 (C-130) — ARIA could render a page but not INSPECT it.

She already fetches (`crawler/fetcher`), renders JS (`intel/headless` driving
Lightpanda over CDP — verified installed and `is_available()` True on
aria-intel), and extracts text (trafilatura). What she had no way to answer is
the question a security or due-diligence analyst actually asks:

  * what security headers does this site set — CSP, HSTS, X-Frame-Options?
  * is it serving mixed content, or loading scripts from third parties?
  * which THIRD-PARTY domains does the page contact?
  * what does the console say — errors, leaked stack traces, tokens?
  * where did it finally land after redirects?

Text extraction cannot answer any of those, because the answers are in headers,
network activity and the console — not the prose.

WHY THIS IS A SEARCH-TIER CONCERN TOO. §27b measured that identifying ourselves
is what unblocks legitimate sources: `python-requests/2.0` got HTTP 403 from the
Wikipedia API and a descriptive UA got 200, same IP, same second. The inspector
therefore sends a descriptive User-Agent by default.

WHAT THIS DELIBERATELY IS NOT. Read-only navigation and observation. No
stealth/anti-bot evasion, no CAPTCHA handling, no form submission, no login. §27
is explicit that evading anti-bot controls to take data a provider is refusing
us is untenable for a due-diligence product — the same reasoning that stopped us
scraping TrustOnline and using Find Case Law unlicensed. Tests below pin that
boundary so a later "just add stealth" cannot pass quietly.
"""
from __future__ import annotations

import pathlib

import pytest

from aria_service.intel import page_inspect as pi

_RAW = pathlib.Path(pi.__file__).read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """Source with docstrings and whole-line comments removed.

    The module's own docstring explains what it deliberately does NOT do
    (stealth, CAPTCHA handling), so a raw substring scan flags the prose that
    documents the boundary. That is the same blunt-grep flaw the old R-F1319
    tests had — assert against CODE, not commentary.
    """
    import ast

    tree = ast.parse(src)
    doc_spans = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                doc_spans.add(d)
    out = src
    for d in doc_spans:
        out = out.replace(d, "")
    return chr(10).join(
        ln for ln in out.splitlines() if not ln.lstrip().startswith("#")
    )


_SRC = _code_only(_RAW)


# ── shape and honesty ─────────────────────────────────────────────────────

def test_security_header_set_is_the_analyst_set():
    """The headers a reviewer actually asks about, not an arbitrary subset."""
    names = {h.lower() for h in pi.SECURITY_HEADERS}
    for required in ("content-security-policy", "strict-transport-security",
                     "x-frame-options", "x-content-type-options",
                     "referrer-policy"):
        assert required in names, f"{required} missing from SECURITY_HEADERS"


def test_missing_header_is_reported_as_absent_not_omitted():
    """An absent security header is the FINDING — it must not vanish from the report."""
    report = pi._build_header_report({"content-security-policy": "default-src 'self'"})
    assert report["content-security-policy"]["present"] is True
    assert report["strict-transport-security"]["present"] is False, (
        "a header that is not set must appear with present=False — dropping it "
        "makes 'no HSTS' indistinguishable from 'not checked' (§1)"
    )


def test_third_party_classification_uses_registrable_domain():
    """A subdomain of the site is FIRST party; a different site is third party."""
    assert pi._is_third_party("https://cdn.example.com/a.js", "example.com") is False
    assert pi._is_third_party("https://example.com/a.js", "example.com") is False
    assert pi._is_third_party("https://tracker.evil.net/a.js", "example.com") is True


def test_unavailable_browser_yields_unknown_not_clean(monkeypatch):
    """No browser must NOT read as 'no findings'.

    The §1 collapse this repo has paid for three times: "could not measure"
    rendered as "measured and found nothing". On the security surface that would
    be a false all-clear.
    """
    monkeypatch.setattr(pi, "_browser_available", lambda: False)
    out = pi.inspect_page_unavailable_result("https://example.com")
    assert out["ok"] is False
    assert out["available"] is False
    assert out["security_headers"] is None, "must be None (unknown), never {}"
    assert out["console_errors"] is None
    assert "error" in out


# ── the ethics boundary, pinned in source ─────────────────────────────────

def test_it_identifies_itself():
    """§27b — a descriptive UA is what unblocks legitimate sources."""
    assert "AriaIntelligence" in pi.USER_AGENT, (
        f"the inspector must identify itself, not impersonate a browser: "
        f"{pi.USER_AGENT!r}"
    )


def test_no_evasion_machinery():
    """§27 — never evade anti-bot controls to take data a provider is refusing.

    Pinned on the source because the failure mode is a future well-meaning
    addition ('just add stealth so it works on site X'), not a runtime state.
    """
    banned = ("playwright_stealth", "undetected", "captcha", "2captcha",
              "anticaptcha", "solve_recaptcha")
    found = [b for b in banned if b in _SRC.lower()]
    assert not found, (
        f"evasion machinery {found} — §27: taking data a provider is refusing "
        f"us is untenable for a due-diligence product"
    )


def test_read_only_no_interaction():
    """Observation only: no clicking, typing or form submission."""
    banned = ("page.click(", "page.fill(", "page.type(", "page.set_input_files(")
    found = [b for b in banned if b in _SRC]
    assert not found, f"interaction primitives present: {found}"


# ── §21a wiring ───────────────────────────────────────────────────────────

def test_both_branches_reach_the_brain():
    assert "wire_success" in _SRC and "wire_failure" in _SRC, (
        "§21a: a new capability ships wired on BOTH branches or it ships dark"
    )


@pytest.mark.asyncio
async def test_inspect_page_returns_unknown_when_browser_missing(monkeypatch):
    """End-to-end shape without launching a browser."""
    monkeypatch.setattr(pi, "_browser_available", lambda: False)
    out = await pi.inspect_page("https://example.com")
    assert out["ok"] is False and out["available"] is False
    assert out["security_headers"] is None


def test_availability_does_not_depend_on_lightpanda():
    """R-F4082 — this module drives CHROMIUM, not Lightpanda.

    `intel/headless.is_available()` checks for the Lightpanda binary it drives
    over CDP for cheap DOM rendering. Gating page inspection on that would make
    the capability refuse to run on a box that HAS chromium — a feature coupled
    to an unrelated binary, which is the "gate on the wrong thing" shape §1
    keeps recording.
    """
    import inspect as _inspect

    src = _code_only(_inspect.getsource(pi._browser_available))
    assert "headless" not in src, (
        "page inspection is gated on the Lightpanda availability check again — "
        "it launches chromium, so that gate is about the wrong binary"
    )
    assert "chromium" in src.lower(), (
        "availability must actually look for the browser this module launches"
    )
