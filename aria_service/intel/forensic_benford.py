"""forensic_benford — Benford's Law first-digit distribution test
(R-F70, 2026-05-09).

Why this module exists
──────────────────────
Per peer review: 'A company whose reported revenue figures show uniform
leading digits rather than the expected Benford distribution is
statistically anomalous. This is not definitive evidence of fraud but
it is a quantifiable flag that should appear in the DD output.'

In ARIA's active markets — Nigeria CPI 24/100, Angola 33/100, Guinea-
Bissau 20/100 — financial statements are frequently unreliable. A
Benford check is the cheapest forensic-accounting flag we can run on
ingested financials, and it produces a deterministic, paste-into-DD-
report numeric anomaly indicator.

What Benford's Law says
───────────────────────
In naturally occurring data sets, the leading digit (1-9) follows a
logarithmic distribution:

    P(d) = log10(1 + 1/d)   for d = 1..9

    expected:  1: 30.1%,  2: 17.6%,  3: 12.5%,  4:  9.7%,  5:  7.9%,
               6:  6.7%,  7:  5.8%,  8:  5.1%,  9:  4.6%

Numbers that arise from human fabrication (made-up revenue figures,
expense reports padded to round dollars, financial statement balance
sheet lines invented to hit a target) tend to show:
  - Uniform first-digit distribution (~11% each)
  - Excess of 1-3 starting digits (the fabricator's brain prefers
    'reasonable-looking' totals)
  - Suspiciously few 1's relative to expected (when avoiding round
    numbers the fabricator over-corrects)

Applicability caveats (read before citing in a DD report)
─────────────────────────────────────────────────────────
Benford's Law applies cleanly when:
  - The dataset spans multiple orders of magnitude (revenue figures
    from $1k to $10M satisfy this; revenues all between $50k-$80k
    don't)
  - The dataset has 100+ values (fewer = high statistical noise)
  - The values arise from a multiplicative process (revenue,
    invoice amounts, transaction values) — not from a constrained
    process (product weights, percentages, rates)

This module enforces those caveats by returning an `applicable` flag.
A non-applicable dataset (too small, narrow range, constrained source)
gets `applicable: False` with the reason — the operator sees this in
the report instead of a false-anomaly signal.

The chi-squared test
────────────────────
We compare observed vs expected first-digit distribution using a
chi-squared goodness-of-fit:

    χ² = Σ [(observed_i - expected_i)² / expected_i]   for i = 1..9

Degrees of freedom = 8 (9 categories - 1).

Conventional thresholds (alpha = 0.05, 0.01):
    χ² > 15.51  → reject Benford fit at α=0.05  (anomaly_grade='WARN')
    χ² > 20.09  → reject at α=0.01              (anomaly_grade='SEVERE')
    χ² ≤ 15.51  → consistent with Benford       (anomaly_grade='OK')

These are widely-cited forensic-accounting thresholds (Nigrini, ACFE
Fraud Examiners Manual). We don't claim 'fraud' — we claim 'statistical
anomaly worth investigation'.

Public API
──────────
    benford_test(values: Iterable[float | int | str]) -> dict
        Returns {applicable, n, chi_squared, anomaly_grade,
                 expected_pct, observed_pct, narrative}

    benford_narrative(result: dict) -> str
        One-line operator-readable summary.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import math
import re
from typing import Any, Iterable

# Expected Benford first-digit distribution (probabilities)
_BENFORD_EXPECTED: dict[int, float] = {
    d: math.log10(1.0 + 1.0 / d) for d in range(1, 10)
}

# Chi-squared critical values, df=8
_CHI2_CRIT_05 = 15.5073   # α=0.05
_CHI2_CRIT_01 = 20.0902   # α=0.01

# Minimum sample size for the test to be meaningful
_MIN_SAMPLE = 50

# Maximum order-of-magnitude span required (max/min ratio); below this,
# Benford doesn't apply (constrained range)
_MIN_ORDER_SPAN = 10.0  # 1 order of magnitude

_NUMERIC_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _to_positive_float(v: Any) -> float | None:
    """Normalise mixed input (str / int / float) to positive float, or
    None if unusable. Negative values are flipped to absolute (a -£500
    expense and a +£500 expense have the same first-digit distribution).
    Zero is dropped (no leading non-zero digit).

    String forms: '1,234.56', '$1,234.56', '£1,234.56k' all accepted by
    extracting the first numeric run. Currency suffixes (k, M, B) are
    NOT normalised — '500k' is treated as 500 — because the first digit
    is what matters for Benford, not the magnitude.
    """
    if isinstance(v, (int, float)):
        n = abs(float(v))
        return n if n > 0 else None
    if isinstance(v, str):
        m = _NUMERIC_RE.search(v.replace(",", ""))
        if not m:
            return None
        try:
            n = abs(float(m.group(0)))
            return n if n > 0 else None
        except ValueError:
            return None
    return None


def _first_digit(n: float) -> int | None:
    """First non-zero digit of |n|, or None if n <= 0 / NaN."""
    if not (n > 0):
        return None
    # Normalise to [1.0, 10.0) by repeated division
    while n >= 10.0:
        n /= 10.0
    while n < 1.0:
        n *= 10.0
    d = int(n)
    return d if 1 <= d <= 9 else None


def benford_test(values: Iterable[Any]) -> dict[str, Any]:
    """Run a Benford first-digit chi-squared test.

    Returns a dict with:
        applicable      bool — whether the test is meaningful for this dataset
        reason_inapplicable  str | None — why not (small N, narrow range)
        n               int — sample size (after filtering)
        chi_squared     float — test statistic (df=8)
        anomaly_grade   'OK' | 'WARN' | 'SEVERE' | 'INAPPLICABLE'
        expected_pct    dict[1..9] -> float (Benford)
        observed_pct    dict[1..9] -> float
        observed_count  dict[1..9] -> int
        narrative       str (one line)
    """
    cleaned: list[float] = []
    for v in values:
        f = _to_positive_float(v)
        if f is not None:
            cleaned.append(f)

    n = len(cleaned)
    expected_pct = {d: _BENFORD_EXPECTED[d] * 100 for d in range(1, 10)}

    if n < _MIN_SAMPLE:
        return {
            "applicable":          False,
            "reason_inapplicable": f"sample too small: n={n} (need ≥ {_MIN_SAMPLE})",
            "n":                   n,
            "chi_squared":         None,
            "anomaly_grade":       "INAPPLICABLE",
            "expected_pct":        expected_pct,
            "observed_pct":        {d: 0.0 for d in range(1, 10)},
            "observed_count":      {d: 0 for d in range(1, 10)},
            "narrative":           f"Benford INAPPLICABLE — only {n} values (need ≥{_MIN_SAMPLE}).",
        }

    lo, hi = min(cleaned), max(cleaned)
    if lo > 0 and (hi / lo) < _MIN_ORDER_SPAN:
        return {
            "applicable":          False,
            "reason_inapplicable": (
                f"value range too narrow: {lo:.4g}..{hi:.4g} "
                f"(span {hi/lo:.2f}× < required {_MIN_ORDER_SPAN}×)"
            ),
            "n":                   n,
            "chi_squared":         None,
            "anomaly_grade":       "INAPPLICABLE",
            "expected_pct":        expected_pct,
            "observed_pct":        {d: 0.0 for d in range(1, 10)},
            "observed_count":      {d: 0 for d in range(1, 10)},
            "narrative":           (
                f"Benford INAPPLICABLE — values span only {hi/lo:.2f}× "
                f"(need ≥{_MIN_ORDER_SPAN}× for the test to be meaningful)."
            ),
        }

    counts: dict[int, int] = {d: 0 for d in range(1, 10)}
    valid = 0
    for v in cleaned:
        d = _first_digit(v)
        if d is not None:
            counts[d] += 1
            valid += 1

    if valid < _MIN_SAMPLE:
        return {
            "applicable":          False,
            "reason_inapplicable": f"after first-digit extraction: n_valid={valid} (need ≥ {_MIN_SAMPLE})",
            "n":                   valid,
            "chi_squared":         None,
            "anomaly_grade":       "INAPPLICABLE",
            "expected_pct":        expected_pct,
            "observed_pct":        {d: 0.0 for d in range(1, 10)},
            "observed_count":      counts,
            "narrative":           f"Benford INAPPLICABLE — only {valid} valid first digits.",
        }

    observed_pct = {d: (counts[d] / valid) * 100 for d in range(1, 10)}

    chi2 = 0.0
    for d in range(1, 10):
        expected_count = _BENFORD_EXPECTED[d] * valid
        if expected_count > 0:
            chi2 += ((counts[d] - expected_count) ** 2) / expected_count

    if chi2 > _CHI2_CRIT_01:
        grade = "SEVERE"
    elif chi2 > _CHI2_CRIT_05:
        grade = "WARN"
    else:
        grade = "OK"

    narrative = _build_narrative(grade, chi2, valid, observed_pct, expected_pct)

    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="forensic_benford",
        summary="Benford Test",
        source_id="forensic_benford:R-F1001",
    )

    return {
        "applicable":          True,
        "reason_inapplicable": None,
        "n":                   valid,
        "chi_squared":         round(chi2, 3),
        "anomaly_grade":       grade,
        "expected_pct":        {d: round(expected_pct[d], 2) for d in range(1, 10)},
        "observed_pct":        {d: round(observed_pct[d], 2) for d in range(1, 10)},
        "observed_count":      counts,
        "thresholds":          {"warn_alpha_05": _CHI2_CRIT_05, "severe_alpha_01": _CHI2_CRIT_01},
        "narrative":           narrative,
    }


def _build_narrative(
    grade: str,
    chi2: float,
    n: int,
    observed: dict[int, float],
    expected: dict[int, float],
) -> str:
    """One-line operator-readable summary."""
    # Find the digit with the largest deviation
    max_dev_digit = max(range(1, 10), key=lambda d: abs(observed[d] - expected[d]))
    obs = observed[max_dev_digit]
    exp = expected[max_dev_digit]
    direction = "excess" if obs > exp else "deficit"

    if grade == "OK":
        return (
            f"Benford fit OK — n={n}, χ²={chi2:.2f} (consistent with "
            f"naturally-arising data; no fabrication signal)."
        )
    if grade == "WARN":
        return (
            f"Benford WARN — n={n}, χ²={chi2:.2f} (rejects naturalness "
            f"at α=0.05). Largest deviation: digit {max_dev_digit} "
            f"observed {obs:.1f}% vs expected {exp:.1f}% ({direction})."
        )
    return (
        f"Benford SEVERE — n={n}, χ²={chi2:.2f} (rejects at α=0.01). "
        f"Largest deviation: digit {max_dev_digit} observed {obs:.1f}% "
        f"vs expected {exp:.1f}% ({direction}). Worth investigating."
    )


def benford_narrative(result: dict[str, Any]) -> str:
    """Return just the narrative string from a benford_test result."""
    return result.get("narrative", "")


def summary() -> dict[str, Any]:
    """Capability-manifest entry."""
    return {
        "module":          "forensic_benford",
        "method":          "Benford's Law first-digit chi-squared test (df=8)",
        "thresholds":      {"warn_alpha_05": _CHI2_CRIT_05, "severe_alpha_01": _CHI2_CRIT_01},
        "min_sample":      _MIN_SAMPLE,
        "min_order_span":  _MIN_ORDER_SPAN,
        "expected_pct":    {d: round(_BENFORD_EXPECTED[d] * 100, 2) for d in range(1, 10)},
    }

# R-F2119 §21a — wire failure handler for forensic_benford
try:
    wire_failure(module="forensic_benford", detail="module shutdown",
                gap_type="engine_failure", source="forensic_benford:shutdown")
except Exception:
    pass
