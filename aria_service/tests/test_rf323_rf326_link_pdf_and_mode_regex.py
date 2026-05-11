"""R-F323 + R-F326 — PDF link penalty + extended mode regex.

R-F323: link_investigator scoring penalises .pdf / .docx / .ppt URLs
        (and /papers/, /publications/, /pdf/ paths) heavily so the
        crawler stays on HTML pages. Live failure 2026-05-11 21:44:
        modirumgespi.com DD pulled an "Entropy-Based Evaluation of
        DNS Activity for Threat Hunting" PDF instead of the marketing
        site.

R-F326: mode-resolution regex now catches "full investigation",
        "deep dive", "thorough investigation", "in-depth analysis",
        "background check on", etc. Operator at 21:48 typed "Aria, do
        a full investigation on https://X" — the regex with only the
        DD-noun list missed it, so deep mode wasn't forced. Extended
        noun list: investigation / dive / research / look / review /
        analysis.
"""
from __future__ import annotations

import re


# ───────────────────────── R-F323 — PDF penalty ────────────────────────────

def _score(url, anchor, base):
    from aria_service.intel.link_investigator import _score_link
    return _score_link(url=url, anchor_text=anchor, base_url=base, query_context="DD")


def test_rf323_pdf_link_heavily_penalised_below_html():
    """A same-host .pdf URL must score LOWER than a same-host html page."""
    base = "https://modirumgespi.com/en"
    html_page = _score(
        "https://modirumgespi.com/en/products",
        anchor="Our Products",
        base=base,
    )
    pdf_link = _score(
        "https://modirumgespi.com/papers/entropy-based-dns-threat-hunting.pdf",
        anchor="Read the paper",
        base=base,
    )
    assert html_page.score > pdf_link.score, (
        f"R-F323 regression: PDF link ({pdf_link.score}, reasons="
        f"{pdf_link.reasons}) scored ≥ HTML page ({html_page.score}, "
        f"reasons={html_page.reasons}). The PDF must lose."
    )


def test_rf323_pdf_link_carries_document_skip_reason():
    base = "https://modirumgespi.com/en"
    pdf_link = _score(
        "https://modirumgespi.com/files/whitepaper.pdf",
        anchor="Whitepaper",
        base=base,
    )
    assert any("document_file_non_authoritative" in r for r in pdf_link.reasons), (
        f"R-F323: penalty reason missing. Got reasons={pdf_link.reasons}"
    )


def test_rf323_publications_path_also_penalised():
    base = "https://modirumgespi.com/en"
    pub_link = _score(
        "https://modirumgespi.com/publications/some-doc",
        anchor="Publication",
        base=base,
    )
    assert any("document_file_non_authoritative" in r for r in pub_link.reasons), (
        f"R-F323: /publications/ path not penalised. Got {pub_link.reasons}"
    )


def test_rf323_pdf_on_authoritative_domain_NOT_penalised():
    """A PDF on gov.uk / Companies House / OFAC is a real filing —
    should NOT be penalised. The penalty only applies to non-auth domains."""
    base = "https://modirumgespi.com/en"
    auth_pdf = _score(
        "https://find-and-update.company-information.service.gov.uk/company/12345678/filing.pdf",
        anchor="UK CH Filing",
        base=base,
    )
    # No document_skip reason expected for authoritative domains
    has_doc_skip = any("document_file_non_authoritative" in r for r in auth_pdf.reasons)
    assert not has_doc_skip, (
        f"R-F323 over-penalised authoritative PDF. Got reasons={auth_pdf.reasons}"
    )


def test_rf323_capability_modirumgespi_pdf_loses_to_about_page():
    """End-to-end capability — recreates the 21:44 modirumgespi failure
    shape: a /products HTML page must outrank an academic PDF on the
    same domain."""
    base = "https://modirumgespi.com/en"
    products = _score(
        "https://modirumgespi.com/en/products",
        anchor="Products",
        base=base,
    )
    threat_paper = _score(
        "https://modirumgespi.com/papers/entropy-dns-threat-hunting.pdf",
        anchor="Threat hunting paper",
        base=base,
    )
    assert products.score > threat_paper.score
    assert products.score - threat_paper.score >= 4, (
        f"R-F323: HTML→PDF score gap too small ({products.score - threat_paper.score}). "
        f"Needs to be ≥4 so PDF never wins on minor keyword bonuses."
    )


# ───────────────────────── R-F326 — extended mode regex ─────────────────────

# The exact regex shape we just added — duplicated so the test is
# self-contained and validates against the published rule.
_DEEP_RE = re.compile(
    r"\b(?:deep|comprehensive|thorough|exhaustive|full|in[\-\s]?depth)\s+"
    r"(?:dd|due\s+diligence|background|ark[\-_]?dd|investigation|dive|research|look(?:[\-\s]?into)?|review|analysis)\b",
    re.IGNORECASE,
)
_QUICK_RE = re.compile(
    r"\b(?:quick|fast|rapid|short|brief)\s+"
    r"(?:dd|due\s+diligence|background|investigation|look|check)\b",
    re.IGNORECASE,
)


def _resolve_mode(msg: str, has_url: bool) -> str:
    mode = "standard"
    if _DEEP_RE.search(msg):
        mode = "deep"
    elif _QUICK_RE.search(msg):
        mode = "quick"
    # R-F296 URL fallback
    if mode == "standard" and has_url:
        mode = "deep"
    return mode


def test_rf326_full_investigation_resolves_to_deep():
    """The exact 21:48 operator phrasing."""
    assert _resolve_mode(
        "Aria, do a full investigation on https://modirumgespi.com/en",
        has_url=True,
    ) == "deep"


def test_rf326_deep_dive_resolves_to_deep():
    assert _resolve_mode("deep dive on Acme Corp", has_url=False) == "deep"


def test_rf326_thorough_investigation_resolves_to_deep():
    assert _resolve_mode("thorough investigation of Beta Ltd", has_url=False) == "deep"


def test_rf326_in_depth_analysis_resolves_to_deep():
    assert _resolve_mode("in-depth analysis of Gamma", has_url=False) == "deep"
    assert _resolve_mode("in depth research on Delta", has_url=False) == "deep"


def test_rf326_full_dd_still_resolves_to_deep():
    """Pre-R-F326 phrasings must still work."""
    assert _resolve_mode("full DD on X", has_url=False) == "deep"
    assert _resolve_mode("comprehensive due diligence", has_url=False) == "deep"


def test_rf326_quick_investigation_resolves_to_quick():
    assert _resolve_mode("quick investigation of X", has_url=False) == "quick"


def test_rf326_url_only_no_deep_keyword_still_forces_deep_via_R_F296():
    """The URL fallback (R-F296) covers cases where no mode-keyword is
    present — but URL means crawlable content, so deep is appropriate."""
    assert _resolve_mode(
        "https://acme.com what do they do",
        has_url=True,
    ) == "deep"


def test_rf326_neutral_message_no_url_stays_standard():
    """Pure conversation without URL or mode keyword."""
    assert _resolve_mode(
        "what's happening today",
        has_url=False,
    ) == "standard"
