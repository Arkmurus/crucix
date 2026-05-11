"""R-F292 / R-F293 — DD link-tree capability tests.

Pinned 2026-05-11 after the live modirumgespi.com DD ran the crawler off
the seed domain into an unrelated academic paper, and tagged page numbers
as years (1001, 1031, 1892).

These are CAPABILITY tests, not internal-logic tests: they exercise
`_score_link` and `_extract_facts_rules` against realistic synthetic
inputs and assert the user-visible symptom is gone.
"""
from __future__ import annotations

from aria_service.intel import link_investigator as li


# ───────────────────────── R-F293 — year-range filter ─────────────────────────

def test_rf293_year_extractor_rejects_pre_1800_four_digit_tokens():
    """The previous regex matched any 4-digit number starting with 1 or 2,
    so 1001 / 1031 / 1892 / 1500 all came back tagged as `year`. The fix
    restricts the range to 1800-2049."""
    sample = (
        "Annual Report 2024. Founded in 1985. "
        "Article 1001 of the corporate code. "
        "Section 1031 covers exchanges. "
        "Page 1892 of the appendix. "
        "Total: 5023 employees by 2025."
    )
    facts = li._extract_facts_rules(sample, "https://example.com/about")
    year_values = {f.value for f in facts if f.kind == "year"}
    # Wanted: 2024, 1985, 2025. Forbidden: 1001, 1031, 1892, 5023.
    # 1892 was a real production false-positive (page number tagged as year)
    # on modirumgespi.com 2026-05-11.
    assert "2024" in year_values
    assert "1985" in year_values
    assert "2025" in year_values
    assert "1001" not in year_values, (
        f"R-F293 regression: '1001' wrongly extracted as year. Got {year_values}"
    )
    assert "1031" not in year_values
    assert "1892" not in year_values, (
        f"R-F293 regression: '1892' (production false-positive) "
        f"still extracted. Got {year_values}"
    )
    assert "5023" not in year_values


def test_rf293_year_extractor_accepts_modern_century_boundary():
    """Bare-year regex: 1900 and 2049 inclusive; 1899 and 2050 excluded.
    Pre-1900 founding years go through the explicit `founded in YYYY`
    regex, not the bare-year regex (see next test)."""
    sample = "Reference year 1900. Cohort year 2049. Compare with 1899 and 2050."
    facts = li._extract_facts_rules(sample, "https://example.com/")
    year_values = {f.value for f in facts if f.kind == "year"}
    assert "1900" in year_values
    assert "2049" in year_values
    assert "1899" not in year_values
    assert "2050" not in year_values


def test_rf293_founding_year_phrase_also_range_restricted():
    """Same fix applied to the `founded in YYYY` / `since YYYY` patterns."""
    sample = "Founded in 1502 by anonymous traders. Active since 1923."
    facts = li._extract_facts_rules(sample, "https://example.com/")
    founding_values = {f.value for f in facts if f.kind == "founding_year"}
    assert "1923" in founding_values
    assert "1502" not in founding_values, (
        f"R-F293 founding_year regression. Got {founding_values}"
    )


# ───────────────────────── R-F292 — same-host bias ─────────────────────────

def _score(url, anchor, base):
    return li._score_link(url=url, anchor_text=anchor, base_url=base,
                          query_context="defence DD")


def test_rf292_same_host_beats_random_external():
    """The capability test for the modirumgespi.com failure: a same-host
    /products page MUST outscore an external academic-paper link with
    keyword bait."""
    base = "https://modirumgespi.com/en"
    same_host = _score(
        "https://modirumgespi.com/en/products",
        anchor="Our Products",
        base=base,
    )
    external_bait = _score(
        "https://www.macrothink.org/journal/bms/article-on-rat-trading.pdf",
        anchor="Read our research publication",
        base=base,
    )
    assert same_host.score > external_bait.score, (
        f"R-F292 capability regression: external academic link "
        f"({external_bait.score}, reasons={external_bait.reasons}) "
        f"outscored same-host page "
        f"({same_host.score}, reasons={same_host.reasons}). "
        f"The modirumgespi.com 'rat trading' incident is reproducible."
    )
    assert "same_host(+3)" in same_host.reasons


def test_rf292_authoritative_external_still_wins():
    """The fix must NOT block legitimate authoritative external lookups
    (Companies House, OFAC, EU registries). They get +3 to +5 from the
    auth-domain bonus and should still beat the same-host baseline."""
    base = "https://modirumgespi.com/en"
    same_host = _score(
        "https://modirumgespi.com/en/random",
        anchor="Random Page",
        base=base,
    )
    auth_external = _score(
        "https://find-and-update.company-information.service.gov.uk/company/12345678",
        anchor="UK Companies House Filing",
        base=base,
    )
    assert auth_external.score >= same_host.score, (
        f"R-F292 broke authoritative-external follow: "
        f"CH score={auth_external.score}, same-host={same_host.score}"
    )


def test_rf292_off_host_non_authoritative_is_penalised():
    """Random off-domain link with NO authoritative bonus and NO strong
    keyword should be net-negative — the crawler should not follow it."""
    base = "https://modirumgespi.com/en"
    junk = _score(
        "https://random-blog.example.com/news/some-post",
        anchor="Read more",
        base=base,
    )
    assert junk.score <= 0, (
        f"R-F292 off-host penalty missing: junk link scored {junk.score}, "
        f"reasons={junk.reasons}"
    )
    assert any("off_host_non_authoritative" in r for r in junk.reasons), (
        f"R-F292 reason tag missing: {junk.reasons}"
    )


# ───────────────────────── Combined: modirumgespi.com fixture ─────────────────

def test_rf292_rf293_modirumgespi_combined_capability():
    """End-to-end capability test reproducing the 2026-05-11 live failure
    mode: scoring + fact extraction on a realistic synthetic page that
    contains the exact symptoms (off-host academic link, page-number
    4-digit tokens). Both fixes must be active for this to pass."""
    base = "https://modirumgespi.com/en"

    # 1. Score 4 candidate links the way the live page would offer them.
    candidates = [
        _score("https://modirumgespi.com/en/company", "Company", base),
        _score("https://modirumgespi.com/en/products", "Products", base),
        _score("https://www.macrothink.org/journal/bms/rat-trading.pdf",
               "Open access research publication", base),
        _score("https://modirumgespi.com/en/news", "News", base),
    ]
    scored = sorted(candidates, key=lambda c: -c.score)
    top_3_hosts = {li.urlparse(c.url).netloc for c in scored[:3]}
    assert "modirumgespi.com" in top_3_hosts, (
        f"R-F292 capability regression: top-3 links don't include "
        f"same-host page. Got: {[(c.url, c.score) for c in scored]}"
    )
    assert all(c.url != "https://www.macrothink.org/journal/bms/rat-trading.pdf"
               or c.score < scored[0].score
               for c in scored[:1]), (
        "R-F292 capability regression: academic paper is the #1 link"
    )

    # 2. Fact extraction on a page that mentions page numbers in 1000s.
    page_text = (
        "Modirum | GESPI. Founded by Elias Silvola. "
        "Reference: doc 1001-A, section 1031.2. "
        "Annual Report 2024. Investor Update 2025."
    )
    facts = li._extract_facts_rules(page_text, base)
    years = {f.value for f in facts if f.kind == "year"}
    assert years == {"2024", "2025"}, (
        f"R-F293 capability regression: expected only {{'2024','2025'}}, "
        f"got {years}"
    )
