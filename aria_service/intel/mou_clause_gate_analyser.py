"""R-F642 — MOU clause-gate an

alyser.

Why this exists
───────────────
The Arkmurus-ADSM MOU V2.0 has a critical clause — 4.5(e):
    "Arkmurus is satisfied, acting reasonably, that the proposed
     Transaction may proceed in accordance with applicable compliance,
     export control, sanctions, end user and licensing requirements."

This is a DISCRETIONARY SATISFACTION GATE — it puts the licensing-
satisfaction decision entirely on Arkmurus's side, and is the
actual mechanism that protects Arkmurus regardless of Cl 6.8's
14-day-after-signature licence-supply timing.

This module:
  - Scans MOU/contract text for known gate clauses
  - Identifies discretionary-satisfaction language patterns
    ("reasonably satisfied", "in its sole discretion",
    "to its satisfaction", "acting reasonably")
  - For each gate found, lists WHAT EVIDENCE the gate requires
    (compliance, sanctions, export control, EUC, etc.)
  - Returns a structured analysis the operator + orchestrator use to
    pre-compute the "what we need before satisfying 4.5(e)" list

This is NOT legal advice. It's a structural-pattern detector."""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import re
from dataclasses import dataclass


# ─────────────────────────────────────────────────────────────────────
# Pattern definitions
# ─────────────────────────────────────────────────────────────────────


# Discretionary-satisfaction language — the linguistic marker of a
# "Party X gets to decide whether they're happy" gate.
_SATISFACTION_LANGUAGE = (
    r"acting reasonably",
    r"reasonably satisfied",
    r"reasonable satisfaction",
    r"to (?:its|their) satisfaction",
    r"in (?:its|their) sole discretion",
    r"in (?:its|their) reasonable discretion",
    r"at (?:its|their) discretion",
    r"if (?:satisfied|content)",
    r"subject to (?:its|their) (?:approval|consent)",
    r"to (?:its|their) reasonable satisfaction",
)


# Evidence-domain keywords — what kind of evidence each gate touches.
_EVIDENCE_DOMAINS = {
    "sanctions": (
        r"sanctions?", r"OFSI", r"OFAC", r"SDN", r"sanctioned",
        r"embargo", r"trade embargo",
    ),
    "export_control": (
        r"export control", r"export[- ]?licen[cs]e", r"SIEL", r"OIEL",
        r"SITCL", r"DSP-?5", r"DDTC", r"ECJU", r"ECO 2008",
        r"USML", r"EAR", r"ITAR", r"trade controls?",
        r"brokering licen[cs]e",
    ),
    "end_user": (
        r"end[- ]?user", r"end[- ]?use", r"EUC", r"end[- ]?user "
        r"certificate", r"end[- ]?user undertaking",
    ),
    "anti_bribery": (
        r"bribery", r"FCPA", r"Bribery Act", r"corrupt practices",
        r"improper payments?", r"kickbacks?",
    ),
    "aml": (
        r"money launder", r"AML", r"CFT", r"Proceeds of Crime",
        r"SAR", r"suspicious activity",
    ),
    "licensing_general": (
        r"licen[cs](?:e|es|ing)", r"registrations?", r"authorisations?",
        r"approvals?", r"GAMI", r"BAFA", r"BAAINBw", r"CIEEMG",
    ),
}


# ─────────────────────────────────────────────────────────────────────
# Results
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClauseGate:
    clause_label: str           # e.g. "Clause 4.5(e)" or "Section 6.3"
    satisfaction_language: str  # the literal triggering phrase
    raw_text: str               # the clause sentence (truncated)
    evidence_domains: tuple[str, ...]  # which domains the gate touches
    party_required_to_be_satisfied: str  # best-effort: who must be satisfied
    notes: str = ""


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


def detect_gates(contract_text: str) -> list[ClauseGate]:
    """Scan contract text + return all discretionary-satisfaction gates.

    Each match is a ClauseGate with the triggering language + evidence
    domains it touches + the clause label (best-effort).
    """
    if not contract_text or not isinstance(contract_text, str):
        return []
    gates: list[ClauseGate] = []
    text_lc = contract_text.lower()

    # Find all satisfaction-language matches with positional context
    for pattern in _SATISFACTION_LANGUAGE:
        for m in re.finditer(pattern, text_lc):
            # Extract sentence context (~400 chars window around match)
            start = max(0, m.start() - 200)
            end = min(len(contract_text), m.end() + 200)
            window = contract_text[start:end]

            # Best-effort clause label — search backwards for a numbered
            # clause reference like "4.5(e)" or "6.3" or "10.2"
            clause_label = _extract_clause_label(contract_text, m.start())

            # Identify evidence domains in the window
            domains: list[str] = []
            for domain, keywords in _EVIDENCE_DOMAINS.items():
                for kw in keywords:
                    if re.search(kw, window, re.IGNORECASE):
                        if domain not in domains:
                            domains.append(domain)
                        break

            # Best-effort: which party must be satisfied? Look for
            # capitalized noun phrases near the satisfaction language.
            party = _extract_satisfied_party(window, m.start() - start)

            gates.append(ClauseGate(
                clause_label=clause_label,
                satisfaction_language=m.group(0),
                raw_text=window.strip(),
                evidence_domains=tuple(domains),
                party_required_to_be_satisfied=party,
            ))

    # Dedup gates that share clause label + language (same sentence
    # may match multiple patterns)
    seen: set[tuple[str, str]] = set()
    unique: list[ClauseGate] = []
    for g in gates:
        key = (g.clause_label, g.satisfaction_language)
        if key not in seen:
            seen.add(key)
            unique.append(g)
    return unique


def _extract_clause_label(text: str, position: int) -> str:
    """Best-effort: find the nearest clause-number marker before `position`.

    Looks for patterns like "4.5(e)", "Clause 4.5", "Section 6.3", etc.
    within the preceding 1000 chars.
    """
    window_start = max(0, position - 1000)
    window = text[window_start:position]
    patterns = (
        r"(\d+\.\d+\([a-z]\))",         # 4.5(e)
        r"((?:Clause|Section)\s+\d+\.\d+)",
        r"((?:Clause|Section)\s+\d+)",
        r"(\d+\.\d+\.\d+)",
        r"(\d+\.\d+)",
    )
    best = ""
    best_pos = -1
    for p in patterns:
        for m in re.finditer(p, window):
            if m.start() > best_pos:
                best_pos = m.start()
                best = m.group(1)
    return best or "(clause label unknown)"


def _extract_satisfied_party(window: str, relative_pos: int) -> str:
    """Best-effort: identify the party that must be satisfied.

    Looks for capitalized Party-name tokens (e.g. "Arkmurus", "ADSM",
    "the Buyer", "the Seller") in the preceding ~100 chars.
    """
    look_back = window[max(0, relative_pos - 100):relative_pos]
    # Match Capitalized words (Party names) within the look-back
    candidates = re.findall(
        r"\b([A-Z][a-zA-Z]{2,30}(?:\s+[A-Z][a-zA-Z]{2,30})?)\b",
        look_back,
    )
    if candidates:
        # Last one before the gate language is most likely the subject
        return candidates[-1]
    return "(party unknown — manual review required)"


def render_finding(contract_text: str) -> list[dict]:
    """Return Finding-shaped dicts for each gate detected."""
    gates = detect_gates(contract_text)
    out: list[dict] = []
    for g in gates:
        severity = "info"  # gates are informational by themselves
        if not g.evidence_domains:
            severity = "amber"  # gate found but no evidence-domain anchor
        out.append({
            "severity": severity,
            "title": (
                f"Discretionary-satisfaction gate: {g.clause_label} — "
                f"'{g.satisfaction_language}'"
            ),
            "detail": (
                f"Gate clause requires {g.party_required_to_be_satisfied} "
                f"to be satisfied. Evidence domains touched: "
                f"{', '.join(g.evidence_domains) if g.evidence_domains else '(none — manual review)'}. "
                f"Raw text excerpt: \"{g.raw_text[:300]}...\""
            ),
            "source": "mou_clause_gate_analyser.detect_gates (R-F642)",
            "confidence": "PROBABLE",
            "clause_label": g.clause_label,
            "evidence_domains": list(g.evidence_domains),
        })
    return out


def list_known_satisfaction_patterns() -> list[str]:
    """Operator dashboard helper."""

    # R-F996 — wire to brain
    wire_success(
        module="mou_clause_gate_analyser",
        summary="MOU clause analysis",
        source_id="mou_clause_gate_analyser:R-F996",
    )
    return list(_SATISFACTION_LANGUAGE)

# R-F2119 §21a — wire failure handler for mou_clause_gate_analyser
try:
    wire_failure(module="mou_clause_gate_analyser", detail="module shutdown",
                gap_type="engine_failure", source="mou_clause_gate_analyser:shutdown")
except Exception:
    pass
