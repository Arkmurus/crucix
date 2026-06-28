"""economic_substance — distinct from ghost detection (R-F77, 2026-05-09).

Why this module exists
──────────────────────
Per peer review: 'Ghost score identifies entities that may be entirely
synthetic. Economic substance testing addresses a different and more
sophisticated problem: entities that are real but whose commercial
substance is disproportionate to their claims. A registered company
with 2 employees and a nominal paid-up share capital claiming £50M
annual turnover and the capability to supply 10,000 artillery rounds
is not a ghost — it is a front.'

Compliance basis
────────────────
- OECD BEPS Action 5 substantial activity requirements
- EU Anti-Tax Avoidance Directive (ATAD) Article 6 (general anti-abuse)
- FATF guidance on shell companies (typology report, 2023)
- UK Companies Act 2006 'true and fair view' obligation on accounts

The substance criteria below are the operationalised version of those
frameworks for defence-DD use cases.

Scoring approach
────────────────
Each criterion produces a score in [0.0, 1.0]:
  1.0 = strongly substantive (claims are well-supported)
  0.0 = strongly insubstantive (claims dwarf observable substance)

The composite substance_score is a weighted average. Below a threshold
(default 0.40) we flag 'INSUBSTANTIVE' — the operator should treat
the counterparty's capability claims with severe skepticism.

Note this is *not* fraud detection. An insubstantive score means the
*claims* don't match the *observable substance*. The counterparty may
be:
  - A legitimate trader using a holding company as the contracting
    vehicle (substance lives in a sister entity)
  - A genuine front for a sanctioned principal
  - A nominee for an undisclosed beneficial owner
  - Or just a company with bad bookkeeping

The operator decides which. ARIA flags the disproportion.

Public API
──────────
    score_substance(profile: dict) -> dict
        Required profile fields (all optional; absent ones drop their
        criterion's weight from the total):
          - employees:              int  (headcount, audited if possible)
          - paid_up_capital_usd:    float  (declared paid-up share capital)
          - claimed_revenue_usd:    float  (annual revenue or trailing-12)
          - claimed_contract_size_usd: float (largest single deal claimed)
          - incorporation_date:     'YYYY-MM-DD' (date of incorporation)
          - claimed_history_years:  int  (years of operations claimed)
          - registered_address:     str  (full address — for virtual-
                                          office heuristic)
          - directors_count:        int  (board size — single-director
                                          shells score worse)
          - virtual_office_flag:    bool  (set externally if known)

    substance_narrative(result) -> str
        One-line operator-readable summary.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger("aria.economic_substance")

# ── Criterion weights — must sum to 1.0 across all criteria evaluated.
# When a criterion has no data, its weight is redistributed pro-rata
# across the remaining criteria so the composite score stays in [0,1].
_W_HEADCOUNT_TO_REVENUE   = 0.25  # employees vs claimed_revenue
_W_CAPITAL_TO_CONTRACT    = 0.20  # paid_up_capital vs largest claimed contract
_W_INCORPORATION_AGE      = 0.15  # incorporation_date vs claimed_history
_W_DIRECTOR_SUBSTANCE     = 0.10  # directors_count
_W_ADDRESS_SUBSTANCE      = 0.15  # virtual-office heuristic on address
_W_REVENUE_TO_CAPITAL     = 0.15  # revenue / paid-up capital ratio sanity

_INSUBSTANTIVE_THRESHOLD  = 0.40  # composite below this → flag

# Industry-typical revenue per employee (USD/year). Defence trading is
# capital-light vs say semiconductor manufacturing, so the benchmark
# is high. Revenue/employee >> $1M often signals broker pass-through
# (legitimate) or fictitious revenue (illegitimate). The threshold
# here is intentionally permissive — a company claiming $5M/employee
# is a yellow flag, not a red one.
_REVENUE_PER_EMPLOYEE_HIGH = 1_500_000.0  # > this = "high productivity claim"
_REVENUE_PER_EMPLOYEE_VERY_HIGH = 5_000_000.0  # > this = "implausible w/o explanation"

# Paid-up capital vs largest claimed contract — a company claiming a
# single deal larger than 100× its capital base is operating with thin
# substance for that deal size.
_CAPITAL_TO_CONTRACT_OK_RATIO = 0.10   # capital ≥ 10% of largest contract = OK
_CAPITAL_TO_CONTRACT_BAD_RATIO = 0.01  # capital < 1% of largest contract = bad

# Virtual-office address heuristic patterns. These match the strings a
# registered-agent virtual-office service typically uses.
_VIRTUAL_OFFICE_TOKENS = [
    "regus", "wework", "iwg", "spaces", "servcorp",
    "registered agent", "registered office", "agent for service",
    "c/o ", "care of ", "suite 100", "suite 1000",  # generic shared-suite numbers
    "po box", "p.o. box", "private bag",
    "mailbox", "mail forwarding",
    "virtual office", "secretarial services",
    # Common shell-jurisdiction registered-agent service names
    "harneys", "trident trust", "intertrust", "vistra",
    "ocra", "tmf group", "appleby",
]


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return None


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _score_headcount_to_revenue(employees: int | None, revenue: float | None) -> dict[str, Any]:
    """Score the headcount-to-revenue ratio. Defence trading is capital-
    light, so this is intentionally lenient on the high side."""
    if employees is None or revenue is None or employees <= 0:
        return {"score": None, "reason": "missing data"}
    ratio = revenue / employees
    if ratio >= _REVENUE_PER_EMPLOYEE_VERY_HIGH:
        return {
            "score":   0.10,
            "reason": (
                f"revenue/employee = ${ratio:,.0f} (≥ ${_REVENUE_PER_EMPLOYEE_VERY_HIGH:,.0f}; "
                f"implausible without disclosed pass-through structure)"
            ),
            "ratio":  ratio,
        }
    if ratio >= _REVENUE_PER_EMPLOYEE_HIGH:
        return {
            "score":   0.45,
            "reason": (
                f"revenue/employee = ${ratio:,.0f} (high; defence-broker "
                f"pass-through possible — verify pass-through structure)"
            ),
            "ratio":  ratio,
        }
    return {
        "score":   1.00,
        "reason": f"revenue/employee = ${ratio:,.0f} (within typical range)",
        "ratio":  ratio,
    }


def _score_capital_to_contract(capital: float | None, contract: float | None) -> dict[str, Any]:
    """Score paid-up capital vs largest claimed contract size."""
    if capital is None or contract is None or contract <= 0:
        return {"score": None, "reason": "missing data"}
    if capital <= 0:
        return {
            "score":   0.05,
            "reason": "paid-up capital ≤ 0 with non-zero claimed contract",
        }
    ratio = capital / contract
    if ratio >= _CAPITAL_TO_CONTRACT_OK_RATIO:
        return {
            "score":   1.00,
            "reason": f"capital ${capital:,.0f} = {ratio*100:.1f}% of largest contract ${contract:,.0f} (≥10%; substantive)",
            "ratio":   ratio,
        }
    if ratio >= _CAPITAL_TO_CONTRACT_BAD_RATIO:
        return {
            "score":   0.40,
            "reason": f"capital ${capital:,.0f} = {ratio*100:.2f}% of largest contract ${contract:,.0f} (1-10%; thin)",
            "ratio":   ratio,
        }
    return {
        "score":   0.10,
        "reason": f"capital ${capital:,.0f} = {ratio*100:.3f}% of largest contract ${contract:,.0f} (< 1%; insubstantive)",
        "ratio":   ratio,
    }


def _score_incorporation_age(
    incorporation_date: str | None,
    claimed_history_years: int | None,
) -> dict[str, Any]:
    """Compare incorporation date to claimed years of operating history."""
    if not incorporation_date or claimed_history_years is None:
        return {"score": None, "reason": "missing data"}
    try:
        inc = datetime.fromisoformat(incorporation_date).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return {"score": None, "reason": f"unparseable incorporation_date: {incorporation_date!r}"}
    now = datetime.now(timezone.utc)
    actual_years = (now - inc).days / 365.25
    if actual_years <= 0:
        return {"score": 0.05, "reason": "incorporation date in the future"}
    if claimed_history_years <= actual_years + 0.5:
        return {
            "score":   1.00,
            "reason": f"actual age {actual_years:.1f}y ≥ claimed {claimed_history_years}y",
            "actual_years": round(actual_years, 1),
        }
    delta = claimed_history_years - actual_years
    if delta <= 2:
        return {
            "score":   0.50,
            "reason": f"claimed {claimed_history_years}y but only {actual_years:.1f}y old (1-2y overstatement; possibly counting predecessor)",
            "actual_years": round(actual_years, 1),
        }
    return {
        "score":   0.10,
        "reason": f"claimed {claimed_history_years}y but only {actual_years:.1f}y old ({delta:.1f}y overstatement)",
        "actual_years": round(actual_years, 1),
    }


def _score_director_substance(directors_count: int | None) -> dict[str, Any]:
    """Single-director companies are common in shell structures."""
    if directors_count is None:
        return {"score": None, "reason": "missing data"}
    if directors_count >= 3:
        return {"score": 1.00, "reason": f"{directors_count} directors (proper board)"}
    if directors_count == 2:
        return {"score": 0.70, "reason": "2 directors (minimal but functional board)"}
    if directors_count == 1:
        return {"score": 0.30, "reason": "1 director (single-director risk; common in nominee shells)"}
    return {"score": 0.05, "reason": "0 directors (registered without governance)"}


def _score_address_substance(
    address: str | None,
    virtual_office_flag: bool | None,
) -> dict[str, Any]:
    """Detect virtual-office / mail-forwarding addresses."""
    if virtual_office_flag is True:
        return {"score": 0.20, "reason": "virtual_office_flag set externally"}
    if not address:
        return {"score": None, "reason": "missing data"}
    addr_lower = address.lower()
    matched_tokens = [t for t in _VIRTUAL_OFFICE_TOKENS if t in addr_lower]
    if matched_tokens:
        return {
            "score":   0.30,
            "reason": f"virtual-office tokens matched in address: {matched_tokens[:3]}",
            "matched_tokens": matched_tokens,
        }
    return {
        "score":   1.00,
        "reason": "address does not match known virtual-office patterns",
    }


def _score_revenue_to_capital(revenue: float | None, capital: float | None) -> dict[str, Any]:
    """Sanity check: a company claiming $50M revenue on $1k paid-up
    capital is implausible for asset-heavy operations. For pure
    brokerage this is normal — that's why this is the lightest weight."""
    if revenue is None or capital is None or capital <= 0:
        return {"score": None, "reason": "missing data"}
    ratio = revenue / capital
    if ratio <= 100:
        return {"score": 1.00, "reason": f"revenue/capital = {ratio:.1f}× (normal)"}
    if ratio <= 1000:
        return {"score": 0.70, "reason": f"revenue/capital = {ratio:.1f}× (high; broker-pass-through possible)"}
    if ratio <= 10000:
        return {"score": 0.40, "reason": f"revenue/capital = {ratio:.1f}× (very high; verify operating model)"}
    return {"score": 0.10, "reason": f"revenue/capital = {ratio:.1f}× (extreme; minimal substance for claimed turnover)"}


def score_substance(profile: dict[str, Any]) -> dict[str, Any]:
    """Compute the economic substance score from a profile dict.

    Returns:
        {
          "substance_score":    float in [0, 1],
          "grade":              "SUBSTANTIVE" | "MARGINAL" | "INSUBSTANTIVE",
          "criteria":           dict of per-criterion {score, reason, ...},
          "applied_weights":    dict of effective weights after redistribution,
          "narrative":          str (one line),
        }
    """
    employees   = _safe_int(profile.get("employees"))
    capital     = _safe_float(profile.get("paid_up_capital_usd"))
    revenue     = _safe_float(profile.get("claimed_revenue_usd"))
    contract    = _safe_float(profile.get("claimed_contract_size_usd"))
    inc_date    = profile.get("incorporation_date")
    history_yrs = _safe_int(profile.get("claimed_history_years"))
    directors   = _safe_int(profile.get("directors_count"))
    address     = profile.get("registered_address")
    vo_flag     = profile.get("virtual_office_flag")

    # Score each criterion
    criteria = {
        "headcount_to_revenue":  _score_headcount_to_revenue(employees, revenue),
        "capital_to_contract":   _score_capital_to_contract(capital, contract),
        "incorporation_age":     _score_incorporation_age(inc_date, history_yrs),
        "director_substance":    _score_director_substance(directors),
        "address_substance":     _score_address_substance(address, vo_flag),
        "revenue_to_capital":    _score_revenue_to_capital(revenue, capital),
    }
    weights = {
        "headcount_to_revenue":  _W_HEADCOUNT_TO_REVENUE,
        "capital_to_contract":   _W_CAPITAL_TO_CONTRACT,
        "incorporation_age":     _W_INCORPORATION_AGE,
        "director_substance":    _W_DIRECTOR_SUBSTANCE,
        "address_substance":     _W_ADDRESS_SUBSTANCE,
        "revenue_to_capital":    _W_REVENUE_TO_CAPITAL,
    }

    # Redistribute weight from criteria with no data
    available_total = sum(weights[k] for k, v in criteria.items() if v.get("score") is not None)
    if available_total == 0:
        return {
            "substance_score": None,
            "grade":           "INDETERMINATE",
            "criteria":        criteria,
            "applied_weights": {},
            "narrative":       "No substance data available — cannot compute score.",
        }
    applied_weights = {
        k: round(weights[k] / available_total, 4)
        for k, v in criteria.items() if v.get("score") is not None
    }
    composite = sum(
        v["score"] * applied_weights[k]
        for k, v in criteria.items() if v.get("score") is not None
    )
    composite = round(composite, 3)

    if composite >= 0.65:
        grade = "SUBSTANTIVE"
    elif composite >= _INSUBSTANTIVE_THRESHOLD:
        grade = "MARGINAL"
    else:
        grade = "INSUBSTANTIVE"

    # Narrative — surface the worst 2 criteria
    flagged = [
        (k, v) for k, v in criteria.items()
        if v.get("score") is not None and v["score"] < 0.45
    ]
    flagged.sort(key=lambda kv: kv[1]["score"])
    flag_summary = "; ".join(f"{k}: {v['reason']}" for k, v in flagged[:2])

    if grade == "SUBSTANTIVE":
        narrative = f"SUBSTANTIVE (score {composite}). No material substance flags."
    elif grade == "MARGINAL":
        narrative = (
            f"MARGINAL substance (score {composite}). "
            f"{('Flags: ' + flag_summary + '.') if flag_summary else ''}".strip()
        )
    else:
        narrative = (
            f"INSUBSTANTIVE (score {composite}). "
            f"Claimed substance is disproportionate to observable indicators. "
            f"{('Worst flags: ' + flag_summary + '.') if flag_summary else ''}".strip()
        )

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="economic_substance",
        summary="Score Substance",
        source_id="economic_substance:R-F996",
    )

    return {
        "substance_score":  composite,
        "grade":            grade,
        "criteria":         criteria,
        "applied_weights":  applied_weights,
        "narrative":        narrative,
        "threshold_insubstantive": _INSUBSTANTIVE_THRESHOLD,
    }


def substance_narrative(result: dict[str, Any]) -> str:
    return result.get("narrative", "")


def summary() -> dict[str, Any]:
    return {
        "module":            "economic_substance",
        "compliance_basis":  "OECD BEPS Action 5 + EU ATAD Art 6 + FATF shell-co typology",
        "criteria":          [
            "headcount_to_revenue", "capital_to_contract", "incorporation_age",
            "director_substance", "address_substance", "revenue_to_capital",
        ],
        "insubstantive_threshold": _INSUBSTANTIVE_THRESHOLD,
    }
