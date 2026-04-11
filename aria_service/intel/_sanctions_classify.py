"""Shared sanctions-match classifier used by dd_orchestrator and
network_walker.

A score-only classifier is wrong for defence DD: legitimate primes
(BAE Systems, Thales, Lockheed Martin, Rolls-Royce) routinely hit
OpenSanctions at score 1.00 against transparency / state-ownership
data (topics: corp.state, corp.public, gov.soe), which is NOT a
refusal ground. The CORRECT discipline is to look at the OpenSanctions
topic labels on each match and classify by the meaning of the list,
not by the string-match confidence.

Topic taxonomy: https://www.opensanctions.org/reference/#topics

Usage:
    from ._sanctions_classify import classify_matches
    result = classify_matches(matches)  # matches = list[dict] from sanctions.py
    # result = {"worst_severity": "amber", "summary": "...", "per_match": [...]}
"""
from __future__ import annotations


# Topic → severity. Anything NOT in this map defaults to "info".
_TOPIC_SEVERITY: dict[str, str] = {
    # ── HARD STOP ── active sanction / asset freeze / export prohibition
    "sanction":         "hard_stop",
    "sanction.linked":  "hard_stop",
    "sanction.counter": "hard_stop",
    "asset.frozen":     "hard_stop",
    "frozen":           "hard_stop",
    "export.control":   "hard_stop",
    "export.risk":      "hard_stop",
    # ── RED ── crime / debarment / regulatory action — human review required
    "debarment":        "red",
    "crime":            "red",
    "crime.fin":        "red",
    "crime.fraud":      "red",
    "crime.theft":      "red",
    "crime.war":        "red",
    "crime.terror":     "red",
    "crime.traffick":   "red",
    "crime.cyber":      "red",
    "crime.env":        "red",
    "crime.boss":       "red",
    "crime.org":        "red",
    "wanted":           "red",
    "reg.action":       "red",
    # ── AMBER ── PEP / adverse media / disqualified director — enhanced DD
    "role.pep":         "amber",
    "role.pol":         "amber",
    "role.rca":         "amber",
    "role.judge":       "amber",
    "corp.disqual":     "amber",
    "reg.warn":         "amber",
    # ── INFO ── transparency / state-ownership / regulated entity — not a risk
    "corp.public":      "info",
    "corp.state":       "info",
    "corp.offshore":    "info",
    "role.civil":       "info",
    "role.diplo":       "info",
    "role.acting":      "info",
    "fin":              "info",
    "fin.bank":         "info",
    "fin.fund":         "info",
    "fin.adivsor":      "info",
    "gov":              "info",
    "gov.national":     "info",
    "gov.state":        "info",
    "gov.muni":         "info",
    "gov.soe":          "info",
    "gov.igo":          "info",
    "mil":              "info",
    "poi":              "info",
}

SEVERITY_RANK = {"info": 0, "amber": 1, "red": 2, "hard_stop": 3}

# Any match scoring below this floor is demoted to info regardless of
# topic. Below 0.75 is fuzzy noise — wrong-entity-same-name collisions.
SCORE_FLOOR_FOR_ESCALATION = 0.75


def classify_match(match: dict) -> str:
    """Classify one match into 'info' | 'amber' | 'red' | 'hard_stop'."""
    if not isinstance(match, dict):
        return "info"
    score = float(match.get("score") or 0.0)
    topics = match.get("topics") or []
    severity = "info"
    for t in topics:
        sev = _TOPIC_SEVERITY.get(t, "info")
        if SEVERITY_RANK[sev] > SEVERITY_RANK[severity]:
            severity = sev
    # Score floor — if escalation-worthy but fuzzy, demote to info
    if SEVERITY_RANK[severity] >= 1 and score < SCORE_FLOOR_FOR_ESCALATION:
        severity = "info"
    return severity


def classify_matches(matches: list[dict]) -> dict:
    """Classify a set of sanctions matches.

    Returns:
        {
          "worst_severity":  "info" | "amber" | "red" | "hard_stop" | "none",
          "summary":         "BAE Systems plc (score 1.00, topics: corp.state,corp.public)",
          "per_match":       [{name, score, topics, datasets, severity}, ...],
          "total_matches":   int
        }
    """
    if not matches:
        return {"worst_severity": "none", "summary": "no matches", "per_match": [], "total_matches": 0}

    per_match: list[dict] = []
    worst = "info"
    worst_rank = -1
    for m in matches:
        if not isinstance(m, dict):
            continue
        match_severity = classify_match(m)
        per_match.append({
            "name":     m.get("name"),
            "score":    float(m.get("score") or 0.0),
            "topics":   m.get("topics") or [],
            "datasets": m.get("lists") or m.get("datasets") or [],
            "severity": match_severity,
        })
        if SEVERITY_RANK[match_severity] > worst_rank:
            worst = match_severity
            worst_rank = SEVERITY_RANK[match_severity]

    # Compact human summary — show up to 3 worst-class matches
    worst_matches = [pm for pm in per_match if pm["severity"] == worst]
    parts: list[str] = []
    for pm in worst_matches[:3]:
        topics_str = ",".join(pm["topics"][:3]) or "untagged"
        lists_str = ",".join(pm["datasets"][:2]) if pm["datasets"] else ""
        parts.append(
            f"{pm['name']} (score {pm['score']:.2f}, topics: {topics_str}"
            f"{', lists: ' + lists_str if lists_str else ''})"
        )
    summary = "; ".join(parts)
    if len(worst_matches) > 3:
        summary += f" [+{len(worst_matches) - 3} more]"
    if not summary:
        summary = f"{len(per_match)} matches — all below action threshold"

    return {
        "worst_severity": worst,
        "summary": summary,
        "per_match": per_match,
        "total_matches": len(per_match),
    }
