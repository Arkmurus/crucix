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
    # ── HARD STOP ── ICC warrants / Interpol Red Notices (explicit routing)
    # 2026-04-12: was implicit via "wanted" topic. Now explicit so the DD
    # report can show "ICC warrant" vs generic "wanted" in the finding text.
    "icc":              "hard_stop",
    "icc.wanted":       "hard_stop",
    "interpol":         "red",
    "interpol.red":     "hard_stop",
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

# Common corporate suffixes / legal-form indicators stripped from
# entity names before token comparison. Fuzzy screening often latches
# onto these short tokens (SRL, LTD, LLC, INC) because they're
# high-frequency across the database — a shared "LTD" token is not
# evidence that two companies are related. Source: most national
# company-law vocabularies.
_CORP_SUFFIXES: set[str] = {
    # Romanian / Italian
    "srl", "spa", "snc", "sas",
    # German / Austrian / Swiss
    "gmbh", "ag", "kg", "kgaa", "mbh", "ohg",
    # UK / Commonwealth
    "ltd", "limited", "plc", "llp", "lp", "pty",
    # US
    "llc", "inc", "corp", "corporation", "company", "co", "lllp",
    # French / Belgian / Lux
    "sa", "sarl", "sas", "scs", "scrl", "sprl", "eurl",
    # Spanish / Portuguese / LatAm
    "sl", "slu", "sau", "lda", "ltda", "sab", "cv",
    # Dutch / Belgian
    "bv", "nv", "cv", "vof",
    # Nordic
    "ab", "as", "oy", "oyj", "aps",
    # Post-Soviet / CIS
    "ooo", "zao", "oao", "pao", "too", "tov", "doo", "dd",
    # Slavic
    "sro", "as", "zrt", "kft", "nyrt", "sp",
    # Chinese / East Asian
    "gs",
    # Misc / descriptive
    "holdings", "holding", "group", "grupo", "gruppo", "group",
    "international", "intl", "global", "worldwide", "industries",
    "industry", "trading", "trade", "services", "service", "solutions",
    "systems", "technologies", "technology", "tech", "enterprise",
    "enterprises", "partners", "partnership", "associates",
    # Slovenian / Croatian / Serbian
    "d.o.o", "doo", "d.d", "d.n.o", "k.d",
    # Greek
    "ae", "epe",
    # Non-alphanumeric stripped variants
    "jsc", "ojsc", "cjsc", "pjsc",
}

# Generic English words that have no entity-identification value.
_STOPWORDS: set[str] = {
    "the", "and", "of", "for", "in", "on", "at", "to", "by",
    "a", "an", "with", "or", "new", "old",
}


def _tokenize_entity_name(name: str) -> set[str]:
    """Split a company / person name into meaningful lowercase tokens.

    Drops corporate suffixes, stopwords, and tokens shorter than 3
    characters (which are almost always acronyms / particles with no
    discriminating power). Normalises diacritics and non-alphanumeric
    punctuation to whitespace so "São Tomé" and "Cote d'Ivoire" split
    cleanly.
    """
    if not name:
        return set()
    import re as _re
    import unicodedata as _ud
    # Normalise accents: "Moçambique" → "Mocambique"
    normalised = _ud.normalize("NFKD", name)
    normalised = "".join(c for c in normalised if not _ud.combining(c))
    # Non-alphanumeric → whitespace
    cleaned = _re.sub(r"[^a-zA-Z0-9]+", " ", normalised).lower()
    tokens = cleaned.split()
    return {
        t for t in tokens
        if len(t) >= 3
        and t not in _CORP_SUFFIXES
        and t not in _STOPWORDS
        and not t.isdigit()
    }


def _name_overlap(query: str, candidate: str) -> int:
    """Number of meaningful tokens shared between query and candidate."""
    q_tokens = _tokenize_entity_name(query)
    c_tokens = _tokenize_entity_name(candidate)
    return len(q_tokens & c_tokens)


def classify_match(match: dict, query_name: str = "") -> str:
    """Classify one match into 'info' | 'amber' | 'red' | 'hard_stop'.

    If `query_name` is provided, the function additionally enforces a
    token-overlap check: the match must share at least one meaningful
    token (after stripping corporate suffixes + stopwords + <3-char
    tokens) with the queried name. Matches that fail this check are
    demoted to 'info' regardless of topic or score — they are fuzzy-
    Levenshtein noise, not real hits. This prevents "Serban Industries
    SRL" from being treated as an FBI-wanted person on the basis of a
    0.83 Levenshtein score on short tokens.
    """
    if not isinstance(match, dict):
        return "info"
    score = float(match.get("score") or 0.0)
    topics = match.get("topics") or []
    severity = "info"
    for t in topics:
        sev = _TOPIC_SEVERITY.get(t, "info")
        if SEVERITY_RANK[sev] > SEVERITY_RANK[severity]:
            severity = sev
    # Dataset-level ICC/Interpol detection — some matches have the dataset
    # name ("icc", "interpol_red_notices") but no explicit topic tag.
    # 2026-04-12: explicit routing so DD reports show "ICC warrant" clearly.
    datasets = match.get("lists") or match.get("datasets") or []
    ds_lower = " ".join(str(d).lower() for d in datasets)
    if "icc" in ds_lower and SEVERITY_RANK["hard_stop"] > SEVERITY_RANK[severity]:
        severity = "hard_stop"
    elif "interpol" in ds_lower and "red" in ds_lower and SEVERITY_RANK["hard_stop"] > SEVERITY_RANK[severity]:
        severity = "hard_stop"
    elif "interpol" in ds_lower and SEVERITY_RANK["red"] > SEVERITY_RANK[severity]:
        severity = "red"
    # Score floor — if escalation-worthy but fuzzy, demote to info
    if SEVERITY_RANK[severity] >= 1 and score < SCORE_FLOOR_FOR_ESCALATION:
        severity = "info"
    # Token-overlap check — if query is provided and the match doesn't
    # share any meaningful token with it, demote. Skipped when no query
    # is provided (backward-compat for callers that haven't been updated).
    if query_name and SEVERITY_RANK[severity] >= 1:
        candidate_name = match.get("name") or match.get("caption") or ""
        if _name_overlap(query_name, candidate_name) == 0:
            severity = "info"
    return severity


def classify_matches(matches: list[dict], query_name: str = "") -> dict:
    """Classify a set of sanctions matches.

    If `query_name` is provided, applies a token-overlap filter so
    fuzzy-match noise (matches whose caption shares zero meaningful
    tokens with the query) is demoted to 'info' before severity
    aggregation. This fixes the Serban Industries SRL false-positive
    pattern where Levenshtein-fuzzy matches on short strings produced
    score-0.8+ hits on wildly unrelated entities.

    Returns:
        {
          "worst_severity":  "info" | "amber" | "red" | "hard_stop" | "none",
          "summary":         "BAE Systems plc (score 1.00, topics: corp.state,corp.public)",
          "per_match":       [{name, score, topics, datasets, severity, token_overlap}, ...],
          "total_matches":   int,
          "noise_filtered":  int    # count of matches demoted by token-overlap check
        }
    """
    if not matches:
        return {
            "worst_severity": "none",
            "summary": "no matches",
            "per_match": [],
            "total_matches": 0,
            "noise_filtered": 0,
        }

    per_match: list[dict] = []
    worst = "info"
    worst_rank = -1
    noise_filtered = 0
    for m in matches:
        if not isinstance(m, dict):
            continue
        # Compute topic-based severity first (ignoring query)
        topic_severity = classify_match(m, query_name="")
        # Then apply query-aware severity which may demote due to token overlap
        final_severity = classify_match(m, query_name=query_name) if query_name else topic_severity
        candidate_name = m.get("name") or m.get("caption") or ""
        overlap = _name_overlap(query_name, candidate_name) if query_name else -1
        was_demoted = (
            query_name
            and SEVERITY_RANK[topic_severity] >= 1
            and SEVERITY_RANK[final_severity] < SEVERITY_RANK[topic_severity]
        )
        if was_demoted:
            noise_filtered += 1
        per_match.append({
            "name":     candidate_name,
            "score":    float(m.get("score") or 0.0),
            "topics":   m.get("topics") or [],
            "datasets": m.get("lists") or m.get("datasets") or [],
            "severity": final_severity,
            "token_overlap": overlap,
            "noise_filtered": was_demoted,
        })
        if SEVERITY_RANK[final_severity] > worst_rank:
            worst = final_severity
            worst_rank = SEVERITY_RANK[final_severity]

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
        if noise_filtered > 0:
            summary = (
                f"{len(per_match)} fuzzy matches — {noise_filtered} filtered as name-overlap noise "
                f"(matched entities share no meaningful token with the query). No real hits."
            )
        else:
            summary = f"{len(per_match)} matches — all below action threshold"

    return {
        "worst_severity": worst,
        "summary": summary,
        "per_match": per_match,
        "total_matches": len(per_match),
        "noise_filtered": noise_filtered,
    }
