"""R-F573 — DD case file: canonical entity + version chain + diff.

Per docs/dd_versioning_proposal_2026_05_16.md.

Operator question: "What if someone runs DD about the same company twice
— how should that get recorded? We need DDs about the same company or
person to get updated as a different version, professional and coherent."

Pre-R-F573 the dd_orchestrator persists every run under a fresh
`run_id = dd_<12-hex>` with no back-reference. Re-screening the same
entity produces two unrelated rows in `crucix:dd:report_index` and
operators have to spot the duplication manually.

R-F573 adds:
  1. canonical_entity_id — deterministic key per entity:
       company:{ISO2}:{REGNUM}                if registration_number present
       company:{ISO2}:name_hash               otherwise
       person:{normalized_name}:{ISO2 or '??'}:{dob_year or '????'}
       vessel:imo:{IMO}                       if IMO present
       address:{ISO2}:line_hash               for address lookups
  2. version_number + previous_run_id stored on the index entry
  3. version_diff: when version_number > 1, the orchestrator computes a
     finding-level diff vs the previous run (new findings / resolved
     findings / risk_change) and attaches it to the report.
  4. /api/aria/dd/case/{canonical_entity_id} endpoint (wired in
     aria_service/routes/aria.py) — returns the full version chain.

Storage stays in Redis under existing keys. The three new fields on
each index entry are nullable, so old entries written before R-F573
still load cleanly. Backfill is deferred to R-F575+.

Operator override (split / merge of canonical_entity_id) is deferred to
R-F575 — uncommon enough not to block ship.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.dd_versioning")

# ── Name normalisation for canonical_entity_id ──────────────────────────────
#
# Lossy on purpose. We want
#   "Embraer S.A."         → "embraer"
#   "EMBRAER, S.A."        → "embraer"
#   "BAE Systems plc"      → "bae systems"
#   "Aselsan A.S."         → "aselsan"
#   "Açoteq Engenharia"    → "acoteq engenharia"
# so that re-running DD on the same company through any reasonable
# spelling variant lands on the same canonical_entity_id.
#
# Reuses the same suffix dictionary as the sanctions-classify path so
# two systems agree on what counts as a "discriminating" name part.

_LEGAL_SUFFIXES: set[str] = {
    # English
    "ltd", "limited", "llc", "inc", "incorporated", "corp", "corporation",
    "plc", "lp", "llp", "co", "company",
    # German / Austrian
    "gmbh", "ag", "kg", "kgaa", "mbh", "ohg",
    # French / Belgian / Lux
    "sa", "sas", "sarl", "scs", "scrl", "sprl", "eurl",
    # Italian / Spanish / Portuguese
    "srl", "spa", "sl", "slu", "sau", "lda", "ltda", "sab",
    # Dutch / Belgian
    "bv", "nv", "cv", "vof",
    # Nordic
    "ab", "as", "asa", "oy", "oyj", "aps",
    # Post-Soviet / CIS
    "ooo", "zao", "oao", "pao", "too", "tov",
    # Slavic
    "sro", "spol", "zrt", "kft", "nyrt", "sp", "doo", "dd",
    # Turkish
    "sti",
    # Greek
    "ae", "epe",
    # Chinese / East Asian
    "gs",
    # JSC variants
    "jsc", "ojsc", "cjsc", "pjsc",
}


@fail_wire(module="dd_versioning", gap_type="engine_failure")
def normalize_name(name: str) -> str:
    """Lowercase, strip diacritics + punctuation + legal suffixes.

    Designed so cosmetic variations of the same company collapse to a
    single key:
      "Embraer S.A."     → "embraer"
      "EMBRAER, S.A."    → "embraer"
      "Embraer SA"       → "embraer"
      "BAE Systems plc"  → "bae systems"
      "Açoteq Engenharia LDA" → "acoteq engenharia"

    Single-character tokens are dropped (so "S.A." → ["s", "a"] →
    dropped). Multi-character legal-suffix tokens (sa, ag, plc, ltd,
    …) drop via the _LEGAL_SUFFIXES set. If every remaining token IS
    a suffix the function returns the original suffix-only set so an
    entity literally named "SA" doesn't collapse to empty.
    """
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    n = re.sub(r"[^a-zA-Z0-9]+", " ", n.lower()).strip()
    n = re.sub(r"\s+", " ", n)
    tokens = n.split()
    if not tokens:
        return ""
    # Drop single-char tokens — almost always punctuation artifacts
    # from "S.A." / "U.S." / "P.L.C." style abbreviations.
    tokens = [t for t in tokens if len(t) >= 2]
    if not tokens:
        return ""
    # If every remaining token IS a suffix, the entity name IS just a
    # suffix (rare) — preserve to avoid collapsing to "".
    if all(t in _LEGAL_SUFFIXES for t in tokens):
        return " ".join(tokens)
    return " ".join(t for t in tokens if t not in _LEGAL_SUFFIXES)


def _name_hash(name: str) -> str:
    """12-char SHA1 of the normalised name. Used when a registration
    number / IMO / similar canonical key isn't available."""
    norm = normalize_name(name)
    if not norm:
        return ""
    return hashlib.sha1(norm.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


# R-F2480 — registry-type LABEL prefixes that some sources prepend to the ID (e.g.
# Brazil's `CNPJ 45.218.484/0003-40`, Denmark's `CVR 12345678`). The label is NOT
# part of the identity — the jurisdiction is already encoded in the canonical key
# (`company:BR:…`) — so a source that includes it must not fork a SECOND canonical
# case for the SAME company. Whitelist only (never a bare 2-letter country prefix
# like a UK `SC`/`NI` company number), so a legitimate alpha-ID prefix is preserved.
_REGNUM_LABEL_PREFIXES = (
    "CNPJ", "CPF", "CVR", "NIP", "NIF", "NIT", "RUC", "RFC",
    "UEN", "ABN", "ACN", "TIN", "EIN", "KVK", "SIREN", "SIRET",
)


def _scrub_regnum(regnum: str) -> str:
    """Uppercase + drop punctuation/whitespace from a registration number so
    cosmetic variation doesn't fork the canonical key: the Brazilian-format CNPJ
    `07.689.002/0001-89` → `07689002000189`.

    R-F2480 — ALSO strip a leading registry-type LABEL, so `CNPJ 45.218.484/0003-40`
    and `45.218.484/0003-40` both scrub to `45218484000340` (they were forking two
    duplicate cases for one company on re-run). Only strips when digits remain, so a
    pure-ID is never emptied."""
    s = re.sub(r"[^A-Z0-9]", "", (regnum or "").upper())
    for _lbl in _REGNUM_LABEL_PREFIXES:
        if s.startswith(_lbl):
            _rest = s[len(_lbl):]
            if _rest and any(c.isdigit() for c in _rest):
                return _rest
    return s


# ── Canonical entity ID ─────────────────────────────────────────────────────


@fail_wire(module="dd_versioning", gap_type="engine_failure")
def canonical_entity_id(
    *,
    entity_type: str | None,
    name: str | None,
    jurisdiction_iso2: str | None = None,
    registration_number: str | None = None,
    imo: str | None = None,
    dob: str | None = None,
    nationality_iso2: str | None = None,
) -> str | None:
    """Compute a deterministic canonical ID for an entity.

    Returns None when the input is too thin to canonicalise (e.g. only
    a name, no jurisdiction or entity_type). In that case the caller
    can still persist the report — version chain will be a no-op.

    Format
    ------
        company:{ISO2}:{REGNUM_scrubbed}      preferred for companies
        company:{ISO2}:{name_hash}            fallback for unregistered
        person:{normalized}:{ISO2}:{year}     persons (lossy)
        vessel:imo:{IMO}                      vessels with IMO
        vessel:flag:{name_hash}               vessels without IMO
        address:{ISO2}:{name_hash}            addresses
    """
    et = (entity_type or "").strip().lower()
    iso = (jurisdiction_iso2 or "").strip().upper() or "??"

    if et == "company":
        if registration_number:
            regnum = _scrub_regnum(registration_number)
            if regnum:
                return f"company:{iso}:{regnum}"
        nh = _name_hash(name or "")
        if not nh:
            return None
        return f"company:{iso}:{nh}"

    if et == "person":
        norm = normalize_name(name or "")
        if not norm:
            return None
        n_iso = (nationality_iso2 or iso or "??").upper()
        year = "????"
        if dob:
            m = re.match(r"^(\d{4})", dob.strip())
            if m:
                year = m.group(1)
        return f"person:{norm.replace(' ', '_')}:{n_iso}:{year}"

    if et == "vessel":
        if imo:
            imo_clean = re.sub(r"[^0-9]", "", imo)
            if imo_clean:
                return f"vessel:imo:{imo_clean}"
        nh = _name_hash(name or "")
        if not nh:
            return None
        return f"vessel:flag:{nh}"

    if et == "address":
        nh = _name_hash(name or "")
        if not nh:
            return None
        return f"address:{iso}:{nh}"

    # Unknown type: fall back to name-hash if we have anything.
    if name:
        nh = _name_hash(name)
        if nh:
            return f"unknown:{iso}:{nh}"
    return None


@fail_wire(module="dd_versioning", gap_type="engine_failure")
def canonical_entity_id_from_report(report: Any) -> str | None:
    """Convenience wrapper — pull the relevant fields off an
    ARKDDReport.identity section. Safe to call before
    _persist_report."""
    ident = getattr(report, "identity", None)
    if ident is None:
        return None
    return canonical_entity_id(
        entity_type=getattr(ident, "entity_type", None),
        name=getattr(ident, "entity_name", None),
        jurisdiction_iso2=getattr(ident, "jurisdiction_iso2", None),
        registration_number=getattr(ident, "registration_number", None),
        imo=getattr(ident, "imo_number", None),
        dob=getattr(ident, "date_of_birth", None),
        nationality_iso2=getattr(ident, "nationality_iso2", None),
    )


# ── Version chain resolver ──────────────────────────────────────────────────


@fail_wire(module="dd_versioning", gap_type="engine_failure")
def resolve_version_chain(
    canonical_id: str | None,
    index_entries: list[dict],
    *,
    current_run_id: str | None = None,
) -> tuple[int, str | None]:
    """Given the current canonical_entity_id and the existing report
    index (newest-first list of dicts), return
    (next_version_number, previous_run_id).

    If canonical_id is None → (1, None) — re-runs without a canonical
    key just become orphan v1 entries (existing behaviour preserved).
    """
    if not canonical_id or not index_entries:
        return 1, None
    matching = [
        e for e in index_entries
        if e.get("canonical_entity_id") == canonical_id
        and (not current_run_id or e.get("run_id") != current_run_id)
    ]
    if not matching:
        return 1, None
    matching.sort(key=lambda e: e.get("generated_at") or "", reverse=True)
    last = matching[0]
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="dd_versioning",
        summary="Resolve Version Chain",
        source_id="dd_versioning:R-F996",
    )

    return int(last.get("version_number") or 1) + 1, last.get("run_id")


# ── Version diff ────────────────────────────────────────────────────────────


_FINDING_KEY_FIELDS = ("title", "severity", "source")


def _finding_signature(f: dict | Any) -> str:
    """Stable key for de-duplication across runs. Uses title + severity
    + source. Two findings are 'the same finding' iff they share all
    three. Body / detail text can drift across runs (timestamps, etc.)
    so we don't hash that."""
    if hasattr(f, "as_dict"):
        d = f.as_dict()
    elif isinstance(f, dict):
        d = f
    else:
        return ""
    return "|".join(str(d.get(k) or "") for k in _FINDING_KEY_FIELDS)


@fail_wire(module="dd_versioning", gap_type="engine_failure")
def compute_version_diff(
    *,
    previous_report: dict | None,
    current_report: dict,
) -> dict:
    """Return a version_diff dict comparing current_report (just
    produced) to previous_report (loaded from Redis under the previous
    run_id).

    The diff is finding-level, not free-text. Operators want to see:
      - what's NEW (added since last run)
      - what's GONE (resolved since last run)
      - whether the headline risk changed
    A timestamp-anchored summary string is included for chat / WhatsApp.
    """
    if not previous_report:
        return {
            "summary": "First version of this case file — no prior run to diff against.",
            "previous_run_id": None,
            "previous_generated": None,
            "previous_risk": None,
            "current_risk": current_report.get("risk_classification"),
            "risk_changed": False,
            "new_findings": [],
            "resolved_findings": [],
            "unchanged_count": 0,
        }

    def _collect_findings(report: dict) -> list[dict]:
        out: list[dict] = []
        for section_key in (
            "identity", "network", "verification", "compliance",
            "digital", "commercial_coherence", "synthesis",
        ):
            sec = report.get(section_key) or {}
            for f in sec.get("findings") or []:
                if isinstance(f, dict):
                    out.append({**f, "_section": section_key})
        return out

    prev_findings = _collect_findings(previous_report)
    curr_findings = _collect_findings(current_report)
    prev_sigs = {_finding_signature(f): f for f in prev_findings}
    curr_sigs = {_finding_signature(f): f for f in curr_findings}

    new_keys = curr_sigs.keys() - prev_sigs.keys()
    gone_keys = prev_sigs.keys() - curr_sigs.keys()
    unchanged_keys = curr_sigs.keys() & prev_sigs.keys()

    new_findings = [
        {
            "title": curr_sigs[k].get("title"),
            "severity": curr_sigs[k].get("severity"),
            "source": curr_sigs[k].get("source"),
            "section": curr_sigs[k].get("_section"),
        }
        for k in sorted(new_keys)
    ][:50]
    resolved_findings = [
        {
            "title": prev_sigs[k].get("title"),
            "severity": prev_sigs[k].get("severity"),
            "source": prev_sigs[k].get("source"),
            "section": prev_sigs[k].get("_section"),
        }
        for k in sorted(gone_keys)
    ][:50]

    prev_risk = previous_report.get("risk_classification")
    curr_risk = current_report.get("risk_classification")
    risk_changed = prev_risk != curr_risk

    parts: list[str] = []
    if risk_changed:
        parts.append(f"Risk: {prev_risk} → {curr_risk}.")
    else:
        parts.append(f"Risk unchanged ({curr_risk}).")
    if new_findings:
        parts.append(f"{len(new_findings)} new finding(s).")
    if resolved_findings:
        parts.append(f"{len(resolved_findings)} finding(s) cleared.")
    if not new_findings and not resolved_findings:
        parts.append("No finding-level changes since last run.")

    return {
        "summary": " ".join(parts),
        "previous_run_id": previous_report.get("run_id"),
        "previous_generated": previous_report.get("generated_at"),
        "previous_risk": prev_risk,
        "current_risk": curr_risk,
        "risk_changed": risk_changed,
        "new_findings": new_findings,
        "resolved_findings": resolved_findings,
        "unchanged_count": len(unchanged_keys),
        "diff_generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Case-file fetcher ───────────────────────────────────────────────────────


@fail_wire(module="dd_versioning", gap_type="engine_failure")
def filter_index_by_canonical_id(
    canonical_id: str,
    index_entries: list[dict],
) -> list[dict]:
    """Return all index entries belonging to the same canonical
    entity, sorted newest-first. Pure function — Redis I/O lives in
    the caller (dd_orchestrator endpoint)."""
    if not canonical_id or not index_entries:
        return []
    matching = [
        e for e in index_entries
        if e.get("canonical_entity_id") == canonical_id
    ]
    matching.sort(key=lambda e: e.get("generated_at") or "", reverse=True)
    return matching

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
