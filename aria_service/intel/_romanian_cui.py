"""Romanian CUI (Cod Unic de Înregistrare) analyzer.

The CUI is issued sequentially by ONRC (Oficiul Național al Registrului
Comerțului), so a higher number implies a more recent registration.
That sequential property lets us derive an approximate incorporation
year from the CUI alone — critical for counter-checking "founded in
YYYY" claims on company websites.

**This module is PURELY approximate.** ONRC issues CUIs at a variable
rate (~450k-600k new entities per year in the 2020s), and numbers can
be re-issued or reserved. Any date returned carries a ±6-month
uncertainty window and MUST be verified against the ONRC portal
(https://portal.onrc.ro) before relying on it operationally.

ARIA uses this module in the DD orchestrator identity layer:
  - If the counterparty provides a CUI (directly or via website),
    surface the approximate incorporation date
  - Cross-check against any claimed founding year on the company's
    website / RFQ / marketing
  - Emit a RED finding when the gap exceeds 2 years (website claim of
    "founded in 1994" on a CUI that places incorporation in 2024 is
    an unambiguous misrepresentation)

ANCHOR POINTS
─────────────
Calibrated from public ONRC data + observed defence-BD cases:

  CUI    9,000,000  ≈ 1997
  CUI   14,000,000  ≈ 2003
  CUI   18,000,000  ≈ 2006
  CUI   22,000,000  ≈ 2008
  CUI   26,000,000  ≈ 2010
  CUI   30,000,000  ≈ 2012
  CUI   33,000,000  ≈ 2014
  CUI   36,000,000  ≈ 2016
  CUI   39,000,000  ≈ 2018
  CUI   41,500,000  ≈ 2019
  CUI   43,500,000  ≈ 2020
  CUI   45,500,000  ≈ 2021
  CUI   47,500,000  ≈ 2022
  CUI   49,500,000  ≈ 2023
  CUI   51,000,000  ≈ 2024-06
  CUI   51,800,000  ≈ 2024-11
  CUI   52,000,000  ≈ 2025-01

  → CUI 51,884,521 (Serban Industries SRL) ≈ 2024-12 / 2025-01

Anchors should be refreshed annually from ONRC bulletins as the rate
changes. The interpolation between anchors is linear.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional


# (CUI, approximate_date) anchor pairs, strictly increasing on CUI.
_CUI_ANCHORS: list[tuple[int, date]] = [
    ( 9_000_000, date(1997,  6, 1)),
    (14_000_000, date(2003,  6, 1)),
    (18_000_000, date(2006,  6, 1)),
    (22_000_000, date(2008,  6, 1)),
    (26_000_000, date(2010,  6, 1)),
    (30_000_000, date(2012,  6, 1)),
    (33_000_000, date(2014,  6, 1)),
    (36_000_000, date(2016,  6, 1)),
    (39_000_000, date(2018,  6, 1)),
    (41_500_000, date(2019,  6, 1)),
    (43_500_000, date(2020,  6, 1)),
    (45_500_000, date(2021,  6, 1)),
    (47_500_000, date(2022,  6, 1)),
    (49_500_000, date(2023,  6, 1)),
    (51_000_000, date(2024,  6, 1)),
    (51_800_000, date(2024, 11, 1)),
    (52_000_000, date(2025,  1, 1)),
    (52_500_000, date(2025,  6, 1)),
    (53_000_000, date(2026,  1, 1)),
]

# Uncertainty window (months) on either side of the interpolated date
_UNCERTAINTY_MONTHS = 4


@dataclass
class CUIAnalysis:
    cui: int
    raw: str
    estimated_incorporation: Optional[date]
    uncertainty_months: int
    age_months_now: Optional[int]
    anchor_before: Optional[tuple[int, str]]
    anchor_after: Optional[tuple[int, str]]
    out_of_range: bool
    notes: str

    def as_dict(self) -> dict:
        return {
            "cui": self.cui,
            "raw": self.raw,
            "estimated_incorporation": self.estimated_incorporation.isoformat() if self.estimated_incorporation else None,
            "uncertainty_months": self.uncertainty_months,
            "age_months_now": self.age_months_now,
            "anchor_before": self.anchor_before,
            "anchor_after": self.anchor_after,
            "out_of_range": self.out_of_range,
            "notes": self.notes,
        }

    def disclaimer(self) -> str:
        return (
            f"Romanian CUI {self.cui} estimates incorporation ≈ "
            f"{self.estimated_incorporation.isoformat() if self.estimated_incorporation else 'unknown'} "
            f"(±{self.uncertainty_months} months). ARIA interpolation — "
            f"verify against ONRC portal (https://portal.onrc.ro) before relying on this date."
        )


def parse_cui(text: str | int) -> Optional[int]:
    """Parse a CUI from free text. Accepts integers, 'CUI 1234',
    'RO12345678' (VAT form), etc. Returns the integer CUI or None."""
    if isinstance(text, int):
        return text if text > 0 else None
    if not text:
        return None
    s = str(text).strip().upper()
    # Strip common prefixes
    for prefix in ("CUI", "CIF", "RO", "J40/", "ROMANIA"):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    # Extract first numeric sequence
    m = re.search(r"(\d{5,10})", s)
    if not m:
        return None
    try:
        val = int(m.group(1))
    except ValueError:
        return None
    if val < 1_000 or val > 99_999_999:
        return None
    return val


def _linear_interp(cui: int, lo: tuple[int, date], hi: tuple[int, date]) -> date:
    """Linear interpolation between two anchors in (CUI, date) space."""
    lo_cui, lo_date = lo
    hi_cui, hi_date = hi
    if hi_cui == lo_cui:
        return lo_date
    ratio = (cui - lo_cui) / (hi_cui - lo_cui)
    lo_ord = lo_date.toordinal()
    hi_ord = hi_date.toordinal()
    interp_ord = int(round(lo_ord + ratio * (hi_ord - lo_ord)))
    return date.fromordinal(max(1, interp_ord))


def analyse_cui(cui: int | str) -> Optional[CUIAnalysis]:
    """Return a CUIAnalysis for a Romanian CUI, or None if unparseable."""
    parsed = parse_cui(cui)
    if parsed is None:
        return None

    raw = str(cui)

    # Find bracketing anchors
    before: Optional[tuple[int, date]] = None
    after: Optional[tuple[int, date]] = None
    for i, anchor in enumerate(_CUI_ANCHORS):
        if anchor[0] <= parsed:
            before = anchor
        if anchor[0] >= parsed and after is None:
            after = anchor
        if after:
            break

    notes = ""
    estimated: Optional[date] = None
    out_of_range = False

    if before and after and before != after:
        estimated = _linear_interp(parsed, before, after)
        notes = f"linear interpolation between CUI {before[0]:,} and {after[0]:,}"
    elif before and not after:
        # Above highest anchor — extrapolate conservatively
        estimated = before[1]
        out_of_range = True
        notes = (
            f"CUI {parsed:,} is above the highest anchor ({_CUI_ANCHORS[-1][0]:,}) — "
            f"incorporation is more recent than the anchor date. "
            f"Refresh anchors from current ONRC bulletins."
        )
    elif after and not before:
        estimated = after[1]
        out_of_range = True
        notes = (
            f"CUI {parsed:,} is below the lowest anchor ({_CUI_ANCHORS[0][0]:,}) — "
            f"incorporation is older than the anchor date. Probably 1990s."
        )
    elif before == after and before is not None:
        estimated = before[1]
        notes = f"CUI exactly matches anchor {before[0]:,}"

    age_months_now: Optional[int] = None
    if estimated:
        today = date.today()
        age_days = (today - estimated).days
        age_months_now = max(0, int(round(age_days / 30.5)))

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="_romanian_cui",
        summary="Analyse Cui",
        source_id="_romanian_cui:R-F996",
    )

    return CUIAnalysis(
        cui=parsed,
        raw=raw,
        estimated_incorporation=estimated,
        uncertainty_months=_UNCERTAINTY_MONTHS,
        age_months_now=age_months_now,
        anchor_before=(before[0], before[1].isoformat()) if before else None,
        anchor_after=(after[0], after[1].isoformat()) if after else None,
        out_of_range=out_of_range,
        notes=notes,
    )


def compare_claimed_founding(cui: int | str, claimed_year: int) -> dict:
    """Compare a CUI-derived incorporation date against a claimed founding
    year (e.g. from a company website or marketing material).

    Returns:
      {
        "cui": int,
        "claimed_year": int,
        "estimated_incorporation_year": int,
        "gap_years": int,
        "verdict": "consistent" | "suspicious" | "misrepresentation" | "unknown",
        "severity": "info" | "amber" | "red" | "hard_stop",
        "detail": str,
      }

    Verdict rules:
      - |gap| <= 1 year          → consistent, info
      - claimed_year newer by >1 → suspicious, amber (claim is LATER
                                    than registry implies — odd but not
                                    necessarily fraudulent; may be a
                                    re-registration or brand reboot)
      - claimed_year older by 2-5 years → amber
      - claimed_year older by >5 years  → red (misrepresentation)
      - claimed_year older by >10 years → hard_stop
        (e.g. claiming "founded 1994" on a 2025 CUI = 30-year lie)
    """
    analysis = analyse_cui(cui)
    if analysis is None or analysis.estimated_incorporation is None:
        return {
            "cui": cui,
            "claimed_year": claimed_year,
            "verdict": "unknown",
            "severity": "info",
            "detail": "CUI could not be analysed — verify directly against ONRC.",
        }

    est_year = analysis.estimated_incorporation.year
    gap = est_year - claimed_year  # positive = claim is older than registry
    severity = "info"
    verdict = "consistent"
    detail = ""

    if abs(gap) <= 1:
        verdict = "consistent"
        severity = "info"
        detail = (
            f"Claimed founding year {claimed_year} is consistent with "
            f"CUI-derived estimate ({est_year} ±{analysis.uncertainty_months}mo)."
        )
    elif gap < 0:
        # Claim is NEWER than registry says — unusual but legitimate
        # if the company was re-registered or rebranded.
        verdict = "suspicious"
        severity = "amber"
        detail = (
            f"Claimed founding year {claimed_year} is {-gap} year(s) AFTER "
            f"CUI-derived incorporation estimate ({est_year}). This may reflect "
            f"a legitimate rebrand or re-registration; verify registry history."
        )
    elif gap >= 10:
        verdict = "misrepresentation"
        severity = "hard_stop"
        detail = (
            f"CRITICAL: Claimed founding year {claimed_year} is {gap} years BEFORE "
            f"the CUI-derived incorporation estimate ({est_year}). Romanian CUIs "
            f"are issued sequentially by ONRC; a {gap}-year gap is not a "
            f"rounding error. This is a verifiable misrepresentation of company "
            f"age on a material fact. Treat as fraudulent misrepresentation under "
            f"UK / EU contract law and refuse onboarding pending written "
            f"reconciliation."
        )
    elif gap >= 5:
        verdict = "misrepresentation"
        severity = "red"
        detail = (
            f"Claimed founding year {claimed_year} is {gap} years BEFORE the "
            f"CUI-derived estimate ({est_year}). This is a material discrepancy "
            f"requiring formal explanation before any commercial engagement."
        )
    else:  # 2-4 year gap
        verdict = "suspicious"
        severity = "amber"
        detail = (
            f"Claimed founding year {claimed_year} is {gap} years before the "
            f"CUI-derived estimate ({est_year}). Within normal uncertainty but "
            f"worth verifying against ONRC's incorporation date."
        )

    return {
        "cui": analysis.cui,
        "claimed_year": claimed_year,
        "estimated_incorporation_year": est_year,
        "estimated_incorporation_date": analysis.estimated_incorporation.isoformat(),
        "gap_years": gap,
        "uncertainty_months": analysis.uncertainty_months,
        "verdict": verdict,
        "severity": severity,
        "detail": detail,
    }

# R-F2119 §21a — wire failure handler for _romanian_cui
try:
    wire_failure(module="_romanian_cui", detail="module shutdown",
                gap_type="engine_failure", source="_romanian_cui:shutdown")
except Exception:
    pass
