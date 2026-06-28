"""ach_explainability — structured Analysis of Competing Hypotheses
output for DD conclusions (R-F71, 2026-05-09).

Why this module exists
──────────────────────
Per peer review: 'When ARIA concludes RED, two audiences need to
understand why: the Arkmurus operator making the business decision,
and potentially a regulator or lawyer reviewing the decision six
months later. The current text narrative explains the reasoning but
does not structure it in a way that is auditable or contestable.'

What's already in place (verified pre-build)
────────────────────────────────────────────
ARIA already has ACH framing throughout:
  - intel/analytic_principles.py — Heuer's ACH doctrine
  - intel/osint_knowledge.py — full ACH discussion
  - intel/v3_prompts.py — ACH PROTOCOL embedded in prompt chain
  - metacognitive/engine.py — reasoning quality scored against ACH
  - metacognitive/domains.py — ACH listed as competency

What's missing: a structured machine-readable EXPLAINABILITY OUTPUT
that complements the narrative. This module fills that gap.

The structured shape
────────────────────
{
  "conclusion":     str,           # the headline verdict (e.g. RED / AMBER)
  "confidence":     float,         # 0..1 calibrated confidence
  "hypotheses": [
    {
      "label":      "H1: counterparty is legitimate",
      "prior":      0.30,
      "posterior":  0.05,
      "supports":   [signal_id, ...],
      "contradicts": [signal_id, ...],
      "rationale":  str
    },
    ...
  ],
  "signals": [
    {
      "id":        "S1",
      "claim":     "OFAC SDN listing — Wagner Group, 2017",
      "source":    {url, retrieved_at, source_type, reliability},
      "weight":    0.85,
      "applies_to": ["H1: contradicts", "H2: supports"]
    },
    ...
  ],
  "considered_and_rejected": [
    {
      "alternative": "Mistaken identity (different Wagner)",
      "reason":      "Address + phonetic match + relationships disambiguate",
    },
    ...
  ],
  "narrative": str   # the human-readable version
}

This is the format that makes ARIA's DD conclusions:
  - Auditable      — every signal has source + weight + applies_to
  - Contestable    — alternatives are explicit, not implicit
  - Reproducible   — same signals + same weights = same conclusion
  - Diff-able      — when a signal updates (sanctions delisting), the
                     downstream conclusion can be re-derived

Public API
──────────
    build_ach_output(conclusion, confidence, hypotheses, signals,
                      rejected, narrative) -> dict
        Validate + assemble into the canonical output shape.

    extract_signals_from_dd(dd_data) -> list[signal_dict]
        Helper: converts a typical DD orchestrator output into the
        signal shape this module expects. Best-effort — not all DD
        layers emit signals in a uniform format yet.

    summary() -> dict
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.ach_explainability")


def build_ach_output(
    *,
    conclusion: str,
    confidence: float,
    hypotheses: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    rejected: list[dict[str, Any]] | None = None,
    narrative: str = "",
    subject: str = "",
) -> dict[str, Any]:
    """Validate + assemble the canonical ACH output dict.

    Rejects malformed input (raises ValueError) so downstream consumers
    can rely on the shape. The structured field names are a contract
    other modules can build against.
    """
    if not isinstance(conclusion, str) or not conclusion:
        raise ValueError("conclusion must be a non-empty string")
    confidence = float(confidence)
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be in [0,1], got {confidence}")
    if not isinstance(hypotheses, list) or not hypotheses:
        raise ValueError("hypotheses must be a non-empty list")

    # Validate each hypothesis has required fields
    cleaned_hyp: list[dict[str, Any]] = []
    for i, h in enumerate(hypotheses):
        if not isinstance(h, dict):
            raise ValueError(f"hypothesis {i} must be a dict")
        if "label" not in h:
            raise ValueError(f"hypothesis {i} missing 'label'")
        cleaned_hyp.append({
            "label":      h["label"],
            "prior":      float(h.get("prior", 1.0 / len(hypotheses))),
            "posterior":  float(h.get("posterior", h.get("prior", 0.0))),
            "supports":   list(h.get("supports", [])),
            "contradicts": list(h.get("contradicts", [])),
            "rationale":  str(h.get("rationale", "")),
        })

    # Posteriors should sum to ~1.0 (normalised across hypotheses)
    posterior_sum = sum(h["posterior"] for h in cleaned_hyp)
    if posterior_sum > 0:
        for h in cleaned_hyp:
            h["posterior"] = round(h["posterior"] / posterior_sum, 4)

    # Validate signals
    cleaned_sig: list[dict[str, Any]] = []
    for i, s in enumerate(signals or []):
        if not isinstance(s, dict):
            continue
        cleaned_sig.append({
            "id":         s.get("id", f"S{i+1}"),
            "claim":      str(s.get("claim", "")),
            "source":     s.get("source", {}),
            "weight":     float(s.get("weight", 0.5)),
            "applies_to": list(s.get("applies_to", [])),
        })

    cleaned_rejected: list[dict[str, Any]] = []
    for r in rejected or []:
        if isinstance(r, dict) and "alternative" in r:
            cleaned_rejected.append({
                "alternative": str(r["alternative"]),
                "reason":      str(r.get("reason", "")),
            })

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="ach_explainability",
        summary="Build Ach Output",
        source_id="ach_explainability:R-F996",
    )

    return {
        "schema_version": "ach.v1",
        "subject":        subject,
        "conclusion":     conclusion,
        "confidence":     round(confidence, 3),
        "hypotheses":     cleaned_hyp,
        "signals":        cleaned_sig,
        "considered_and_rejected": cleaned_rejected,
        "narrative":      narrative,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
    }


def extract_signals_from_dd(dd_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Helper to convert a typical DD orchestrator output into the
    signal shape this module expects.

    Best-effort: not all DD layers emit uniform signals yet. We pull
    sanctions matches, FATF typology matches, economic-substance
    flags, RCA inherited risks, TBML anomalies. As more layers add
    structured output, this function extends.
    """
    signals: list[dict[str, Any]] = []
    next_id = 1

    # Sanctions divergence (R-F68)
    if dd_data.get("sanctions_divergence"):
        sd = dd_data["sanctions_divergence"]
        for pm in (sd.get("per_match") or []):
            for li in (pm.get("listed_in") or []):
                signals.append({
                    "id":      f"S{next_id}",
                    "claim":   f"Listed on {li.get('list')} ({li.get('jurisdiction')})",
                    "source":  {
                        "url":          pm.get("url"),
                        "source_type":  "sanctions_list",
                        "reliability":  0.95,
                    },
                    "weight":  0.90,
                    "applies_to": ["H_RED:supports"],
                })
                next_id += 1

    # FATF typology matches (R-F72)
    if dd_data.get("fatf_matches"):
        for m in (dd_data["fatf_matches"].get("matches") or []):
            signals.append({
                "id":      f"S{next_id}",
                "claim":   f"FATF typology: {m.get('id')} (score {m.get('score')})",
                "source":  {
                    "source_type":  "fatf_typology",
                    "reliability":  0.85,
                    "doc_ref":      m.get("source"),
                },
                "weight":  m.get("score", 0.5),
                "applies_to": ["H_RED:supports"],
            })
            next_id += 1

    # Economic substance (R-F77)
    if dd_data.get("substance"):
        s = dd_data["substance"]
        if s.get("grade") == "INSUBSTANTIVE":
            signals.append({
                "id":      f"S{next_id}",
                "claim":   f"Economic substance: INSUBSTANTIVE (score {s.get('substance_score')})",
                "source":  {"source_type": "computed", "reliability": 0.85},
                "weight":  0.80,
                "applies_to": ["H_RED:supports"],
            })
            next_id += 1

    # RCA inherited risks (R-F76)
    if dd_data.get("rca"):
        for ih in (dd_data["rca"].get("inherited_risks") or []):
            signals.append({
                "id":      f"S{next_id}",
                "claim":   ih.get("narrative", ""),
                "source":  {"source_type": "rca_inherited", "reliability": 0.75},
                "weight":  ih.get("weight", 0.5),
                "applies_to": ["H_RED:supports"],
            })
            next_id += 1

    # TBML anomaly (R-F73)
    if dd_data.get("tbml") and dd_data["tbml"].get("grade") in ("FLAG", "SEVERE", "BLATANT"):
        t = dd_data["tbml"]
        signals.append({
            "id":      f"S{next_id}",
            "claim":   f"TBML {t['grade']}: {t.get('narrative')}",
            "source":  {"source_type": "tbml_benchmark", "reliability": 0.80},
            "weight":  {"FLAG": 0.50, "SEVERE": 0.75, "BLATANT": 0.95}.get(t["grade"], 0.50),
            "applies_to": ["H_RED:supports"],
        })
        next_id += 1

    return signals


def summary() -> dict[str, Any]:
    return {
        "module":    "ach_explainability",
        "purpose":   "structured machine-readable ACH output for DD conclusions",
        "schema":    "ach.v1",
        "consumers": ["dd_orchestrator", "report writer", "audit log"],
    }

# R-F2119 §21a — wire failure handler for ach_explainability
try:
    wire_failure(module="ach_explainability", detail="module shutdown",
                gap_type="engine_failure", source="ach_explainability:shutdown")
except Exception:
    pass
