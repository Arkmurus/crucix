"""ARIA Source Validator — quality-signal harness before a source enters the atlas.

Extracted from the `ARIA_Source_Discovery.zip` proposal (2026-04-15). The
shipped stack already owns gap analysis (web_atlas.surface_gaps), citation
walk (source_scout._scout_citation), tier classification
(verified_intel.SourceTierClassifier), and reliability EMA
(web_atlas.record_ingest). What was missing — and what this module adds —
is a CONTENT-QUALITY gate that runs BEFORE a candidate URL reaches
web_atlas.add_source(), so that fresh-domain blog-spam cannot sneak
into the atlas just because the hostname happens to sit under a gov TLD
or because it was cited by a Tier 2 source once.

Ten quality signals (weights in parentheses):
  - HTTPS (0.5)                       — basic credibility
  - Domain age ≥2 years (1.0)         — via WHOIS (optional dep); falls
                                        back to `not verified — treat as
                                        new` when whois is missing
  - RSS/API availability (1.5)        — machine-readable updates
  - Update consistency (1.0)          — feedparser-driven; optional dep
  - Bylined journalism (2.0)          — CRITICAL; named authors vs anon
  - Institutional backing (1.5)       — about-page signals or gov TLD
  - Language quality (1.0)            — caps ratio, professional vocab,
                                        blog-host filter
  - Cross-correlation with VERIFIED   — covers same entities as verified
    facts (2.0)                         facts; only VERIFIED is used so
                                        validation is not circular
  - Tier proposal (pure function, rule-based)
  - Coverage-domain inference (pure function)

Integration points (this module is called by):
  - source_scout._scout_citation  — gate before adding citees
  - source_scout._scout_targeted  — gate before adding gap-filler hits
  - /source_validator/validate    — manual validation endpoint
  - DAILY-CORE-DEVELOP            — weekly registry health report

Autonomy boundary (matches aria_autonomy_doctrine.md):
  - Running validation               = auto-allowed
  - Proposing a candidate            = auto-allowed
  - Adding a Tier 1a/1b gov source   = auto-allowed (strict schema)
  - Adding a Tier 2/3/4 source       = DRAFT → pending queue → human approve
  - Suspending a degrading source    = auto-allowed (doctrine: "never
                                       silently trust a failing source")
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from . import redis_store as rs
# C-29 — imported BY NAME, not reached through `rs`. The module-level `rs` is
# monkeypatched by tests and could be any object; `rs.StoreReadError` would then
# raise AttributeError inside the very except clause meant to handle a read
# failure, turning a declared-unreadable store back into a crash.
from .redis_store import StoreReadError

logger = logging.getLogger("aria.intel.source_validator")


# ══════════════════════════════════════════════════════════════════════════
# COVERAGE DOMAIN CATALOGUE
# ══════════════════════════════════════════════════════════════════════════
# The 23 named coverage domains with minimum source requirements and
# priority. Enriches web_atlas.surface_gaps with domain-level semantics
# instead of the crude source-count bucketing.

COVERAGE_DOMAINS: dict[str, dict[str, Any]] = {
    # CPLP Africa — Arkmurus core moat
    "angola_procurement":       {"min_sources": 3, "priority": "CRITICAL"},
    "angola_politics":          {"min_sources": 2, "priority": "HIGH"},
    "mozambique_defence":       {"min_sources": 2, "priority": "HIGH"},
    "guinea_bissau":            {"min_sources": 1, "priority": "MEDIUM"},
    "cape_verde":               {"min_sources": 1, "priority": "LOW"},
    # Wider Africa
    "nigeria_defence":          {"min_sources": 3, "priority": "HIGH"},
    "kenya_security":           {"min_sources": 2, "priority": "HIGH"},
    "ghana_procurement":        {"min_sources": 2, "priority": "HIGH"},
    "south_africa_defence":     {"min_sources": 3, "priority": "HIGH"},
    "sahel_security":           {"min_sources": 2, "priority": "HIGH"},
    "drc_conflict":             {"min_sources": 2, "priority": "HIGH"},
    # Middle East & Gulf
    "saudi_defence":            {"min_sources": 3, "priority": "HIGH"},
    "uae_procurement":          {"min_sources": 3, "priority": "HIGH"},
    "gulf_geopolitics":         {"min_sources": 2, "priority": "HIGH"},
    # Latin America
    "brazil_defence":           {"min_sources": 3, "priority": "HIGH"},
    "peru_procurement":         {"min_sources": 2, "priority": "HIGH"},
    "colombia_security":        {"min_sources": 2, "priority": "HIGH"},
    "latam_geopolitics":        {"min_sources": 2, "priority": "MEDIUM"},
    # Europe & Turkey
    "turkey_defence":           {"min_sources": 3, "priority": "HIGH"},
    "poland_rearmament":        {"min_sources": 2, "priority": "HIGH"},
    "nato_procurement":         {"min_sources": 2, "priority": "HIGH"},
    # Compliance & sanctions
    "ofac_sanctions":           {"min_sources": 1, "priority": "CRITICAL"},
    "hmt_sanctions":            {"min_sources": 1, "priority": "CRITICAL"},
    "eu_sanctions":             {"min_sources": 1, "priority": "CRITICAL"},
    "fatf_updates":             {"min_sources": 1, "priority": "HIGH"},
    "export_control_updates":   {"min_sources": 2, "priority": "HIGH"},
    # Defence industry
    "global_arms_transfers":    {"min_sources": 3, "priority": "HIGH"},
    "oem_news":                 {"min_sources": 3, "priority": "MEDIUM"},
    "tender_portals":           {"min_sources": 5, "priority": "CRITICAL"},
    "conflict_data":            {"min_sources": 2, "priority": "HIGH"},
}


class ValidationStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    AUTO_APPROVED = "AUTO_APPROVED"  # Tier 1a/1b gov — strict-schema gate only
    AUTO_REJECTED = "AUTO_REJECTED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"


# ══════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class QualitySignal:
    check_name: str
    passed: bool
    evidence: str
    weight: float = 1.0


@dataclass
class SourceCandidate:
    candidate_id: str
    url: str
    domain: str
    discovered_via: str                # "citation" | "tld_probe" | "targeted" | "manual"
    gap_it_fills: str                  # coverage_domain tag
    coverage_domains: list[str]
    validation_status: ValidationStatus = ValidationStatus.PENDING
    quality_signals: list[QualitySignal] = field(default_factory=list)
    overall_quality_score: float = 0.0
    tier_proposed: str = "4"
    tier_rationale: str = ""
    discovered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    validation_notes: str = ""

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "url": self.url,
            "domain": self.domain,
            "discovered_via": self.discovered_via,
            "gap_it_fills": self.gap_it_fills,
            "coverage_domains": self.coverage_domains,
            "validation_status": self.validation_status.value,
            "overall_quality_score": self.overall_quality_score,
            "tier_proposed": self.tier_proposed,
            "tier_rationale": self.tier_rationale,
            "discovered_at": self.discovered_at,
            "validation_notes": self.validation_notes,
            "quality_signals": [
                {"check": s.check_name, "passed": s.passed,
                 "evidence": s.evidence, "weight": s.weight}
                for s in self.quality_signals
            ],
        }

    def to_approval_message(self) -> str:
        signals_passed = sum(1 for s in self.quality_signals if s.passed)
        signals_total = len(self.quality_signals)
        tier_name = {
            "1a": "Official primary (single source sufficient)",
            "1b": "Official secondary",
            "2":  "High-quality journalism",
            "3":  "Specialist defence press",
            "4":  "General regional news",
            "5":  "Unreliable",
        }.get(self.tier_proposed, "Unknown")
        lines = [
            "📡 ARIA — NEW SOURCE CANDIDATE FOR REVIEW",
            "",
            f"URL:           {self.url}",
            f"Fills gap:     {self.gap_it_fills}",
            f"Proposed tier: {self.tier_proposed} — {tier_name}",
            f"Quality score: {self.overall_quality_score:.0%} "
            f"({signals_passed}/{signals_total} checks passed)",
            "",
            "QUALITY SIGNALS:",
        ]
        for sig in self.quality_signals:
            status = "✓" if sig.passed else "✗"
            lines.append(f"  {status} {sig.check_name}: {sig.evidence[:100]}")
        if self.tier_rationale:
            lines += ["", f"TIER RATIONALE: {self.tier_rationale}"]
        if self.validation_notes:
            lines += ["", f"NOTES: {self.validation_notes}"]
        lines += [
            "",
            f"→ Approve: POST /api/aria/source_validator/approve {{candidate_id: '{self.candidate_id}'}}",
            f"→ Reject:  POST /api/aria/source_validator/reject  {{candidate_id: '{self.candidate_id}', reason: '...'}}",
        ]
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# VALIDATOR
# ══════════════════════════════════════════════════════════════════════════

_USER_AGENT = "Mozilla/5.0 (compatible; ARIASourceValidator/3.1; +https://arkmurus.com)"
_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,pt;q=0.7",
}

_GOV_TLDS = (".gov", ".gov.", ".mil", ".mil.", ".gouv", ".gob", ".go.")
_TIER_1A_REGISTRY_DOMAINS = {
    "companies-house.service.gov.uk",
    "find-and-update.company-information.service.gov.uk",
    "orsr.sk", "krs.ms.gov.pl", "receita.economia.gov.br", "anaf.ro",
    "ofac.treasury.gov", "treasury.gov", "eeas.europa.eu", "un.org",
    "sanctionslist.ofsi.hmtreasury.gov.uk",
}

_LOW_VALUE_MEMORY_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "medium.com",
    "pinterest.com",
    "quora.com",
    "reddit.com",
    "tiktok.com",
    "tumblr.com",
    "wikipedia.org",
    "wordpress.com",
    "x.com",
    "youtube.com",
}


def _domain_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


async def memory_ingest_allowed(
    url: str,
    credibility_tier: int | None = None,
) -> tuple[bool, str]:
    """Return whether a generic web result may enter intelligence memory.

    Tier-1/2 search results can enter directly. Low-value/general web domains
    must have been approved by source_validator first; otherwise they are useful
    as transient search context only, not permanent intelligence memory.
    """
    try:
        host = (urlparse(url).hostname or "").lower().lstrip("www.")
    except Exception:
        return False, "invalid_url"
    if not host:
        return False, "missing_domain"
    try:
        tier_value = int(credibility_tier) if credibility_tier is not None else None
    except (TypeError, ValueError):
        tier_value = None
    if tier_value is not None and tier_value <= 2:
        return True, "trusted_credibility_tier"
    if any(_domain_matches(host, d) for d in _TIER_1A_REGISTRY_DOMAINS):
        return True, "tier_1a_registry_domain"
    low_value = any(_domain_matches(host, d) for d in _LOW_VALUE_MEMORY_DOMAINS)
    if not low_value:
        return True, "not_low_value_domain"
    try:
        history = await rs.get_json(_K_HISTORY) or []
    except Exception:
        history = []
    for cand in history:
        cand_domain = str(cand.get("domain") or "").lower().lstrip("www.")
        status = str(cand.get("validation_status") or "")
        if _domain_matches(host, cand_domain) and status in {
            ValidationStatus.APPROVED.value,
            ValidationStatus.AUTO_APPROVED.value,
        }:
            return True, "source_validator_approved"
    return False, "low_value_domain_unapproved"


def _candidate_id(url: str) -> str:
    return hashlib.md5(
        f"{url}{datetime.now(timezone.utc).isoformat()}".encode()
    , usedforsecurity=False).hexdigest()[:12]


async def _fetch(url: str, timeout: float = 10.0) -> tuple[int, str]:
    """Async GET using httpx — returns (status, text). Silent on failure."""
    try:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:  # no-breaker: source validation is best-effort
            r = await client.get(url, headers=_HEADERS)
            return r.status_code, r.text
    except Exception as e:
        logger.debug("fetch %s failed: %s", url, e)
        return 0, ""


async def _head(url: str, timeout: float = 5.0) -> int:
    try:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:  # no-breaker: source validation is best-effort
            r = await client.head(url, headers=_HEADERS)
            return r.status_code
    except Exception:
        return 0


# ── Quality check primitives (all sync, pure or httpx-based) ────────────

def _check_https(url: str) -> QualitySignal:
    passed = url.startswith("https://")
    return QualitySignal(
        "HTTPS", passed,
        "Secure HTTPS" if passed else "HTTP only — not secure",
        weight=0.5,
    )


def _check_domain_age(domain: str) -> QualitySignal:
    """Attempt WHOIS. whois is an optional dep — gracefully degrade."""
    try:
        import whois  # type: ignore
        w = whois.whois(domain)
        created = w.creation_date
        if isinstance(created, list):
            created = created[0] if created else None
        if created:
            try:
                created = created.replace(tzinfo=None)
            except Exception:
                pass
            age_days = (datetime.now() - created).days
            age_years = age_days / 365.0
            passed = age_years >= 2.0
            return QualitySignal(
                "Domain Age", passed,
                f"{age_years:.1f} years old "
                f"({'established' if passed else 'new — less credible'})",
                weight=1.0,
            )
    except Exception as e:
        logger.debug("whois lookup for %s failed: %s", domain, e)
    return QualitySignal(
        "Domain Age", False,
        "Could not verify domain age (whois unavailable) — treat as new",
        weight=1.0,
    )


async def _check_rss_availability(domain: str) -> QualitySignal:
    rss_candidates = [
        f"https://{domain}/feed",
        f"https://{domain}/rss",
        f"https://{domain}/feed.xml",
        f"https://{domain}/rss.xml",
        f"https://{domain}/atom.xml",
        f"https://{domain}/news/rss",
    ]
    for rss_url in rss_candidates:
        if await _head(rss_url, timeout=5.0) == 200:
            return QualitySignal(
                "RSS/API Available", True,
                f"RSS feed found: {rss_url}", weight=1.5,
            )
    return QualitySignal(
        "RSS/API Available", False,
        "No RSS feed found — scraping required (higher maintenance)",
        weight=1.5,
    )


async def _check_bylined_journalism(url: str) -> QualitySignal:
    status, content = await _fetch(url, timeout=10.0)
    if status != 200 or not content:
        return QualitySignal(
            "Bylined Journalism", False,
            f"Could not fetch page (HTTP {status})", weight=2.0,
        )
    lc = content.lower()
    signals = [
        bool(re.search(r'author.*?[A-Z][a-z]+ [A-Z][a-z]+', content)),
        "byline" in lc,
        '"author"' in lc,
        "correspondent" in lc,
        "editor" in lc,
        "journalist" in lc,
        bool(re.search(r"by [A-Z][a-z]+ [A-Z][a-z]+", content)),
    ]
    count = sum(signals)
    passed = count >= 2
    return QualitySignal(
        "Bylined Journalism", passed,
        f"{count}/7 authorship signals. "
        f"{'Named authors found.' if passed else 'Anonymous content — lower quality.'}",
        weight=2.0,
    )


async def _check_institutional_backing(domain: str) -> QualitySignal:
    # Government/official TLDs get a free pass.
    dl = domain.lower()
    if any(tld in dl for tld in _GOV_TLDS):
        return QualitySignal(
            "Institutional Backing", True,
            f"Government/official domain: {domain}", weight=1.5,
        )
    for about_url in (
        f"https://{domain}/about",
        f"https://{domain}/about-us",
        f"https://{domain}/who-we-are",
    ):
        status, content = await _fetch(about_url, timeout=8.0)
        if status == 200 and content:
            lc = content.lower()
            signals = [
                "founded" in lc, "incorporated" in lc, "registered" in lc,
                "mission" in lc, "editorial" in lc, "staff" in lc,
                "headquarters" in lc,
                bool(re.search(r"\d{4}", content)),
            ]
            count = sum(signals)
            passed = count >= 3
            return QualitySignal(
                "Institutional Backing", passed,
                f"About page at {about_url}. {count}/8 institutional signals. "
                f"{'Credible.' if passed else 'Limited evidence.'}",
                weight=1.5,
            )
    return QualitySignal(
        "Institutional Backing", False,
        "No about page found and not a gov/mil TLD",
        weight=1.5,
    )


async def _check_language_quality(url: str) -> QualitySignal:
    status, content = await _fetch(url, timeout=10.0)
    if status != 200 or not content:
        return QualitySignal(
            "Language Quality", False,
            f"Could not fetch page (HTTP {status})", weight=1.0,
        )
    # Strip HTML crudely — we only need rough text density signals.
    text = re.sub(r"<[^>]+>", " ", content)
    caps_ratio = sum(1 for c in text if c.isupper()) / max(len(text), 1)
    professional_terms = sum(
        text.lower().count(t)
        for t in ("according to", "confirmed", "stated", "reported",
                  "announced", "released", "published", "ministry",
                  "official", "spokesperson")
    )
    is_blog = any(p in url.lower() for p in (
        "blogspot", "wordpress.com", "medium.com", "substack.com",
        "/blog/", "personal",
    ))
    signals = [caps_ratio < 0.15, professional_terms >= 5, not is_blog]
    passed = sum(signals) >= 2
    return QualitySignal(
        "Language Quality", passed,
        f"{sum(signals)}/3 signals — caps={caps_ratio:.0%}, "
        f"prof_terms={professional_terms}, blog={is_blog}",
        weight=1.0,
    )


async def _cross_correlate_with_verified(
    domain: str, gap_domain: str, web_search_fn: Optional[Callable],
) -> Optional[QualitySignal]:
    """Covers same entities as verified facts? Only VERIFIED is used —
    no circular validation against legacy records."""
    try:
        keys = await rs.scan_keys("aria:verified_facts:*", count=50)
        entities: list[str] = []
        for k in keys[:10]:
            data = await rs.get_json(k)
            if not data:
                continue
            if data.get("verification_status") != "VERIFIED":
                continue
            ent = data.get("entity_name")
            if ent and ent not in entities:
                entities.append(ent)
            if len(entities) >= 3:
                break
        if not entities:
            return QualitySignal(
                "Cross-Correlation", True,
                "No verified facts available yet — passes by default",
                weight=0.5,
            )
        if not web_search_fn:
            return None
        matches = 0
        for entity in entities[:3]:
            try:
                results = await web_search_fn(
                    f'"{entity}" site:{domain}', max_results=2,
                )
                if results:
                    matches += 1
            except Exception:
                continue
        coverage = matches / max(len(entities[:3]), 1)
        passed = coverage >= 0.5
        return QualitySignal(
            "Cross-Correlation", passed,
            f"Covers {matches}/{len(entities[:3])} verified entities "
            f"({coverage:.0%})",
            weight=2.0,
        )
    except Exception as e:
        logger.debug("cross-correlation failed: %s", e)
        return None


def _propose_tier(
    url: str,
    domain: str,
    signals: list[QualitySignal],
    overall_score: float,
) -> tuple[str, str]:
    dl = domain.lower()
    # Official registries
    if dl in _TIER_1A_REGISTRY_DOMAINS:
        return "1a", "Official registry / sanctions database"
    # Sanctions-list paths
    if any(s in url for s in ("ofac", "/sdn", "sanctions", "un.org/sc")):
        return "1a", "Official sanctions database"
    # Gov/mil domain
    if any(tld in dl for tld in _GOV_TLDS):
        return "1b", "Official government domain"
    # Quality signals
    byline = next((s for s in signals if s.check_name == "Bylined Journalism"), None)
    inst = next((s for s in signals if s.check_name == "Institutional Backing"), None)
    if byline and byline.passed and inst and inst.passed and overall_score >= 0.70:
        return "2", "Named bylined journalism with institutional backing"
    if overall_score >= 0.55:
        return "3", "Specialist/regional — passes most quality checks"
    return "4", "General source — limited quality evidence"


def _infer_coverage_domains(
    url: str, domain: str, gap_domain: str,
) -> list[str]:
    tags = {gap_domain} if gap_domain else set()
    url_lower = url.lower()
    domain_lower = domain.lower()
    domain_keywords = {
        "angola_procurement": ("angola", "luanda", "orga"),
        "angola_politics":    ("angola", "mpla", "unita"),
        "mozambique_defence": ("mozambique", "maputo", "fadm"),
        "nigeria_defence":    ("nigeria", "abuja", "nigerian"),
        "kenya_security":     ("kenya", "nairobi"),
        "ghana_procurement":  ("ghana", "accra"),
        "south_africa_defence": ("south africa", "denel", "pretoria"),
        "brazil_defence":     ("brazil", "brasil", "defesa"),
        "turkey_defence":     ("turkey", "turkish", "ankara", "savunma"),
        "saudi_defence":      ("saudi", "riyadh"),
        "uae_procurement":    ("uae", "emirates", "abu dhabi"),
        "sahel_security":     ("sahel", "mali", "burkina", "niger"),
        "nato_procurement":   ("nato",),
    }
    for dtag, kws in domain_keywords.items():
        if dtag in tags:
            continue
        if any(k in url_lower or k in domain_lower for k in kws):
            tags.add(dtag)
    return sorted(tags)


# ══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════

async def validate(
    url: str,
    gap_domain: str = "",
    discovered_via: str = "manual",
    web_search_fn: Optional[Callable] = None,
) -> SourceCandidate:
    """Run the full validation protocol on a candidate URL. Returns a
    SourceCandidate with tier proposal, signals, and status. The caller
    is responsible for pushing the candidate to the pending queue or
    auto-approving if Tier 1a/1b."""
    try:
        domain = urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        from .engine_wiring import wire_failure as _wf
        _wf(
            module="source_validator",
            detail=f"urlparse failed for url: {url[:200]}",
            gap_type="engine_failure",
            source="source_validator:validate",
        )
        domain = url.lower()

    cand = SourceCandidate(
        candidate_id=_candidate_id(url),
        url=url, domain=domain,
        discovered_via=discovered_via,
        gap_it_fills=gap_domain,
        coverage_domains=_infer_coverage_domains(url, domain, gap_domain),
    )
    signals: list[QualitySignal] = [
        _check_https(url),
        _check_domain_age(domain),
    ]
    # Async checks
    signals.append(await _check_rss_availability(domain))
    signals.append(await _check_bylined_journalism(url))
    signals.append(await _check_institutional_backing(domain))
    signals.append(await _check_language_quality(url))
    cc = await _cross_correlate_with_verified(domain, gap_domain, web_search_fn)
    if cc:
        signals.append(cc)
    cand.quality_signals = signals

    total_w = sum(s.weight for s in signals) or 1.0
    passed_w = sum(s.weight for s in signals if s.passed)
    cand.overall_quality_score = round(passed_w / total_w, 3)

    cand.tier_proposed, cand.tier_rationale = _propose_tier(
        url, domain, signals, cand.overall_quality_score,
    )

    critical_failed = [s for s in signals if not s.passed and s.weight >= 2.0]
    # Auto-reject on very low score OR if any critical (w≥2.0) check failed AND
    # the source is not a Tier 1a gov registry (those pass the critical gates
    # inherently — they're not expected to have bylined journalism).
    is_gov = any(tld in domain for tld in _GOV_TLDS) or domain in _TIER_1A_REGISTRY_DOMAINS
    if cand.overall_quality_score < 0.40:
        cand.validation_status = ValidationStatus.AUTO_REJECTED
        cand.validation_notes = (
            f"Auto-rejected: score {cand.overall_quality_score:.0%} < 40%"
        )
    elif critical_failed and not is_gov:
        cand.validation_status = ValidationStatus.AUTO_REJECTED
        cand.validation_notes = (
            "Auto-rejected: critical checks failed: "
            + ", ".join(s.check_name for s in critical_failed)
        )
    elif cand.tier_proposed in ("1a", "1b") and is_gov:
        # Gov TLDs with passing score auto-approve per doctrine's
        # "new registry/sanctions adapter additions" auto-allowed clause.
        cand.validation_status = ValidationStatus.AUTO_APPROVED
        cand.validation_notes = (
            "Auto-approved: Tier 1a/1b gov domain within schema gate"
        )
    elif cand.tier_proposed == "2" and cand.overall_quality_score >= 0.70:
        # Tier 2 sources (quality journalism, think tanks, ISW/UCDP/SIPRI class)
        # auto-approve if they pass 70% of the 10-signal validator.
        # Past incident 2026-04-16: ISW and UCDP were found by source_scout
        # but sat in PENDING indefinitely because only gov TLDs auto-approved.
        cand.validation_status = ValidationStatus.AUTO_APPROVED
        cand.validation_notes = (
            f"Auto-approved: Tier 2 source with quality score "
            f"{cand.overall_quality_score:.0%} (≥70% threshold)"
        )
    else:
        cand.validation_status = ValidationStatus.PENDING

    logger.info(
        "validated %s: tier=%s score=%.0f%% status=%s",
        domain, cand.tier_proposed, cand.overall_quality_score * 100,
        cand.validation_status.value,
    )
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="source_validator",
        summary="Validate",
        source_id="source_validator:R-F1001",
    )

    return cand


# ── Pending-approvals queue ──────────────────────────────────────────────

_K_PENDING = "aria:source_validator:pending"
_K_HISTORY = "aria:source_validator:history"


async def queue_candidate(cand: SourceCandidate) -> dict:
    """Push a PENDING candidate to the approval queue. AUTO_APPROVED
    candidates skip the queue and go straight to web_atlas.add_source
    via the caller (source_scout)."""
    pending = await rs.get_json(_K_PENDING) or []
    pending.insert(0, cand.to_dict())
    if len(pending) > 200:
        pending = pending[:200]
    await rs.set_json(_K_PENDING, pending, ex=30 * 86400)

    # Audit — source_atlas_add is the closest existing action for
    # "candidate proposed".
    try:
        from . import audit_log as _al
        if "source_atlas_add" in _al.RECORDED_ACTIONS:
            await _al.record(
                "source_atlas_add",
                actor="aria_source_validator",
                inputs={"url": cand.url, "tier_proposed": cand.tier_proposed,
                        "gap": cand.gap_it_fills},
                outputs={"candidate_id": cand.candidate_id,
                         "status": cand.validation_status.value,
                         "score": cand.overall_quality_score},
                decision="queued_for_review",
                notes=cand.validation_notes[:300],
            )
    except Exception:
        pass

    return {"queued": True, "candidate_id": cand.candidate_id,
            "queue_depth": len(pending)}


async def list_candidates(status: Optional[str] = None, limit: int = 50) -> list[dict]:
    pending = await rs.get_json(_K_PENDING) or []
    if status:
        pending = [p for p in pending if p.get("validation_status") == status]
    return pending[:limit]


async def approve_candidate(candidate_id: str, approved_by: str = "human") -> dict:
    pending = await rs.get_json(_K_PENDING) or []
    cand = next((p for p in pending if p.get("candidate_id") == candidate_id), None)
    if not cand:
        return {"ok": False, "error": "candidate not found"}
    cand["validation_status"] = ValidationStatus.APPROVED.value
    cand["reviewed_by"] = approved_by
    cand["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    # Remove from pending, archive to history
    pending = [p for p in pending if p.get("candidate_id") != candidate_id]
    await rs.set_json(_K_PENDING, pending, ex=30 * 86400)
    history = await rs.get_json(_K_HISTORY) or []
    history.insert(0, cand)
    await rs.set_json(_K_HISTORY, history[:500], ex=365 * 86400)
    # Register with Web Atlas
    try:
        from . import web_atlas
        await web_atlas.add_source(
            url=cand["url"],
            tier=cand.get("tier_proposed", "4"),
            topic_tags=cand.get("coverage_domains", []),
            region="global",
            added_by=f"approval:{approved_by}",
        )
    except Exception as e:
        return {"ok": False, "error": f"atlas add failed: {e}"}
    # Audit
    try:
        from . import audit_log as _al
        if "source_atlas_promote" in _al.RECORDED_ACTIONS:
            await _al.record(
                "source_atlas_promote",
                actor="aria_source_validator",
                inputs={"candidate_id": candidate_id,
                        "approved_by": approved_by},
                outputs={"url": cand["url"], "tier": cand.get("tier_proposed")},
                decision="approved",
            )
    except Exception:
        pass
    # Brain signal — an approved candidate is a curation win
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="source_validator",
            summary=f"Candidate approved: {cand.get('url','')} (Tier {cand.get('tier_proposed','?')})",
            detail=f"Score {cand.get('overall_quality_score',0):.2f}, "
                   f"gap {cand.get('gap_it_fills','')}, by {approved_by}",
            success=True,
        )
    except Exception:
        pass
    return {"ok": True, "candidate_id": candidate_id,
            "approved_by": approved_by, "url": cand["url"]}


async def reject_candidate(
    candidate_id: str, reason: str = "", rejected_by: str = "human",
) -> dict:
    pending = await rs.get_json(_K_PENDING) or []
    cand = next((p for p in pending if p.get("candidate_id") == candidate_id), None)
    if not cand:
        return {"ok": False, "error": "candidate not found"}
    cand["validation_status"] = ValidationStatus.REJECTED.value
    cand["reviewed_by"] = rejected_by
    cand["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    cand["rejection_reason"] = reason
    pending = [p for p in pending if p.get("candidate_id") != candidate_id]
    await rs.set_json(_K_PENDING, pending, ex=30 * 86400)
    history = await rs.get_json(_K_HISTORY) or []
    history.insert(0, cand)
    await rs.set_json(_K_HISTORY, history[:500], ex=365 * 86400)
    try:
        from . import audit_log as _al
        if "source_atlas_demote" in _al.RECORDED_ACTIONS:
            await _al.record(
                "source_atlas_demote",
                actor="aria_source_validator",
                inputs={"candidate_id": candidate_id,
                        "rejected_by": rejected_by},
                outputs={"reason": reason[:200]},
                decision="rejected",
            )
    except Exception:
        pass
    # Brain signal + mistake-ledger entry — a rejected candidate is data
    # about what NOT to add; predictor can warn on similar future candidates
    try:
        from . import brain_hook as _bh, mistake_ledger as _ml
        await _bh.absorb(
            module="source_validator",
            summary=f"Candidate rejected: {cand.get('url','')}",
            detail=f"Reason: {reason[:200]}",
            success=False,
            gap_type="source_validator_rejected",
            gap_detail=reason[:200],
        )
        await _ml.record(
            category="source_validator_rejected",
            task_type="source_validator",
            domain=cand.get("gap_it_fills", "unknown"),
            what=f"Source {cand.get('url','')} rejected",
            why=reason[:300],
            fix="Predictor should flag similar domains before future scouts propose them",
            what_class="source_quality_fail",
            severity="LOW",
            source_ref=candidate_id,
        )
    except Exception:
        pass
    return {"ok": True, "candidate_id": candidate_id,
            "rejected_by": rejected_by, "reason": reason}


# ══════════════════════════════════════════════════════════════════════════
# COVERAGE-AWARE GAP ANALYSIS (enrichment over web_atlas.surface_gaps)
# ══════════════════════════════════════════════════════════════════════════

async def coverage_gaps_by_domain() -> list[dict]:
    """For each of the 23 COVERAGE_DOMAINS, compute deficit = min_sources
    minus the count of approved atlas entries tagged with this domain.
    Returns a list sorted by priority (CRITICAL → HIGH → MEDIUM → LOW)
    then by deficit size. This complements web_atlas.surface_gaps with
    domain-level semantics."""
    families_idx = await rs.get_json("aria:atlas:index:families") or []
    # Count per-domain coverage
    per_domain: dict[str, int] = {d: 0 for d in COVERAGE_DOMAINS}
    for fam in families_idx:
        rec = await rs.get_json(f"aria:atlas:source:{fam}")
        if not rec:
            continue
        topics = rec.get("topics") or []
        for t in topics:
            if t in per_domain:
                per_domain[t] += 1
    gaps = []
    priority_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    for domain, req in COVERAGE_DOMAINS.items():
        current = per_domain.get(domain, 0)
        min_needed = req["min_sources"]
        if current < min_needed:
            gaps.append({
                "domain": domain,
                "priority": req["priority"],
                "current_sources": current,
                "required_sources": min_needed,
                "deficit": min_needed - current,
            })
    gaps.sort(key=lambda g: (
        priority_rank.get(g["priority"], 9),
        -g["deficit"],
    ))
    return gaps


async def coverage_report() -> str:
    """Human-readable coverage summary for the daily briefing."""
    gaps = await coverage_gaps_by_domain()
    if not gaps:
        return "✓ No significant coverage gaps — all 23 tracked domains meet minimum source requirements."
    lines = [
        "ARIA INTELLIGENCE COVERAGE GAP ANALYSIS",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Gaps identified: {len(gaps)}",
        "─" * 55,
    ]
    marker = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "⚪"}
    for g in gaps[:15]:
        lines.append(
            f"\n{marker.get(g['priority'], '⚪')} "
            f"{g['domain'].replace('_', ' ').upper()} [{g['priority']}]"
        )
        lines.append(
            f"   Sources: {g['current_sources']}/{g['required_sources']} "
            f"(deficit: {g['deficit']})"
        )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# REGISTRY HEALTH REPORT
# ══════════════════════════════════════════════════════════════════════════

_K_RELIABILITY_PREFIX = "aria:atlas:reliability:"

# R-F3908 — the gap type is REGISTERED in `capability_gaps.VALID_GAP_TYPES`.
# `record_gap` silently drops an unregistered type, so emitting one looks exactly
# like wiring while delivering nothing; a test pins the registration.
_BLINDNESS_GAP_TYPE = "source_registry_unreadable"


async def _report_registry_blindness(surface: str, err: Exception) -> None:
    """R-F3908 (§21a/§21d) — tell the brain the source registry went BLIND.

    Not "a source is failing" — the INSTRUMENT is unreadable. Those demand opposite
    responses (investigate the source vs. investigate the store), which is why this
    carries its own gap type rather than reusing `source_uptime_degraded`.

    Best-effort and never raises: this runs on a path that is already degraded, and
    an exception here would convert a reported blindness into an unreported crash.
    """
    try:
        from . import brain_hook as _bh

        await _bh.absorb(
            module="source_validator",
            summary=f"Source registry unreadable — {surface} is blind",
            detail=f"{type(err).__name__}: {err}",
            success=False,
            gap_type=_BLINDNESS_GAP_TYPE,
            gap_detail=(
                f"{surface} could not read aria:atlas:index:families. Registry "
                "reliability reports and auto-suspend are both blind until the "
                "state store recovers."
            ),
            source_id="source_validator:R-F3908",
            confidence="CONFIRMED",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[R-F3908] blindness signal failed to reach the brain: %s", exc)

# C-29 — one scan covers the whole atlas. 200 families x 12 DD layers is ~2.4k
# keys today; the cap is set an order of magnitude clear of that so truncation is
# a genuine anomaly rather than routine, and truncation is REPORTED (below)
# instead of silently shrinking the measured set.
_RELIABILITY_SCAN_CAP = 20000


async def _observed_reliability_keys(
    known_families: list | None = None,
) -> tuple[dict[str, list[str]], bool]:
    """C-29 — ``(family -> [reliability keys that EXIST], scan_complete)``.

    The consumer must read WHAT WAS WRITTEN, not what the catalogue guessed would
    be written. See this module's C-29 test for the full defect; the short form is
    that `record_ingest(url, layer_name)` keys on the DD LAYER name while the old
    reader enumerated the family's seeded catalogue TAGS, and those two vocabularies
    share exactly one token out of twelve.

    ONE scan for the whole prefix, not one per family: `state_store.scan_keys` is a
    GLOB range scan on the key primary index, and R-F703 records a live event-loop
    wedge caused by running it per-call on a hot path. 194 scans per report would
    walk straight back into that.

    Second return value is load-bearing. `scan_keys` returns `[]` on store failure
    exactly as it does on an empty keyspace, so a caller that trusted the list alone
    would report a wedged store as "nobody has ever measured anything" — C-29
    relocated rather than cured.
    """
    keys = await rs.scan_keys(f"{_K_RELIABILITY_PREFIX}*", count=_RELIABILITY_SCAN_CAP)
    complete = len(keys) < _RELIABILITY_SCAN_CAP
    known = {f for f in (known_families or ()) if isinstance(f, str)}
    by_family: dict[str, list[str]] = {}

    for key in keys:
        rest = key[len(_K_RELIABILITY_PREFIX):]
        # `aria:atlas:reliability:{family}:{topic}`. Split at the LAST colon, not
        # the first: `web_atlas._source_family` returns `urlparse().netloc`, which
        # KEEPS THE PORT — so `https://example.com:8080/x` yields the family
        # `example.com:8080`, and an IPv6 literal yields `[::1]:9`. Splitting at the
        # first colon would file that key under `example.com` and leave the real
        # family with zero observations: C-29 reproduced for every ported source.
        family, sep, _topic = rest.rpartition(":")
        if not sep or not family:
            continue
        # `_normalise_topic` does not strip colons, so a topic could in principle
        # carry one and defeat the rpartition above. The family index is the
        # authority when it can answer: prefer the longest KNOWN family that this
        # key actually belongs to.
        if known and family not in known:
            best = None
            for candidate in known:
                if rest.startswith(candidate + ":") and (best is None or len(candidate) > len(best)):
                    best = candidate
            if best is not None:
                family = best
        by_family.setdefault(family, []).append(key)
    return by_family, complete


async def _measured_reliability(
    fam: str,
    topics: list,
    observed_keys: list[str] | None = None,
) -> tuple[float | None, int]:
    """R-F3254 — ``(overall, sample_count)``; `overall` is None when NOTHING was measured.

    This used to read `sum(scores) / len(scores) if scores else 0.5`, inline and
    duplicated in both callers. The 0.5 is the web_atlas PRIOR, not an observation,
    and returning it made "never measured" indistinguishable from "measured, and
    mediocre" — which is how a source nobody has ever sampled ended up reported as
    FAILING (0.40 <= 0.5 < 0.60) and, at a caller-supplied threshold of 0.6,
    auto-SUSPENDED with the fabricated reason "Overall reliability 0.50 below 0.60".

    Same reading error as `INITIAL_MASTERY = 0.5` on the Phase A gate-#2 heatmap:
    a starved cell is not a weak cell. Absent is not false — so say None.

    C-29 — `observed_keys` is the family's ACTUAL reliability keys, from
    `_observed_reliability_keys()`. When supplied it is authoritative and complete
    for that family: every key in it exists, so there is nothing to probe for and
    nothing to guess. That is both the correctness fix and a large read reduction —
    the old path issued up to 10 `get_json` misses per family (~1,940 reads across a
    194-family atlas, of which 21 hit).

    `observed_keys=None` keeps the legacy per-topic probe so the function stays
    correct for any caller that has not built the index — but note the legacy path
    can only ever see topics the family was TAGGED with, which is the defect itself.
    """
    if observed_keys is not None:
        # Bounded: these are reads on the event loop and the caller's scan cap is
        # global, so one pathological family could otherwise dominate it. Twelve DD
        # layers is the real ceiling today, so 50 never truncates in practice — and
        # an EMA average is already well determined long before that.
        keys: list[str] = list(observed_keys)[:50]
    else:
        keys = [f"{_K_RELIABILITY_PREFIX}{fam}:{t}" for t in topics[:10]]

    scores: list[float] = []
    for key in keys:
        rel = await rs.get_json(key)
        if rel:
            scores.append(float(rel.get("score", 0.5)))
    if not scores:
        return None, 0
    return sum(scores) / len(scores), len(scores)


async def registry_health_report() -> dict:
    """Weekly registry health summary — top performers, degraded,
    suspended — fed into the 05:45 team briefing and WEEKLY-CORE-META.
    Uses web_atlas reliability scores (EMA) to compute health_tier.

    R-F3254 — reports a fifth bucket, `unmeasured`: sources with zero reliability
    samples. They are NOT healthy and NOT failing, and they carry
    `overall_health: None` rather than a score nobody measured.

    The five buckets partition every family that HAS a record, so the five
    `*_count` values sum to `total_sources` minus any family listed in the index
    whose `aria:atlas:source:*` record is missing from the store (those are
    skipped below, as they were before this change).
    """
    # C-29 — STRICT read. `get_json` returns None on a store failure (the R-F1
    # None-on-error contract), which becomes `families_idx = []` and renders as
    # "the atlas has 0 sources" — a fabricated measurement of an unreadable store,
    # indistinguishable from a genuinely empty atlas. Declare it instead.
    try:
        families_idx = await rs.get_json_strict("aria:atlas:index:families") or []
    except StoreReadError as e:
        logger.warning("[C-29] registry_health_report: store unreadable: %s", e)
        # R-F3908 (§21a) — a log line is DARK. C-29 exists because an instrument
        # that cannot see reads as a clean instrument; a blindness detector that
        # tells nobody reproduces that defect one level up. Failure branch → brain.
        await _report_registry_blindness("registry_health_report", e)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "store_readable": False,
            "error": f"store unreadable: {e}",
            # Counts are None, never 0: nothing was measured because nothing could
            # be READ, and a 0 here would read as a fact about the atlas.
            "total_sources": None,
            "healthy_count": None, "degraded_count": None, "failing_count": None,
            "dead_count": None, "unmeasured_count": None,
            "top_performers": [], "degraded": [], "failing": [], "dead": [],
            "unmeasured": [],
        }

    observed_by_family, scan_complete = await _observed_reliability_keys(families_idx)

    healthy: list[dict] = []
    degraded: list[dict] = []
    failing: list[dict] = []
    dead: list[dict] = []
    unmeasured: list[dict] = []

    for fam in families_idx:
        rec = await rs.get_json(f"aria:atlas:source:{fam}")
        if not rec:
            continue
        # Average reliability across topics as a health proxy
        topics = rec.get("topics") or []
        observed = observed_by_family.get(fam)
        # C-29 — on a TRUNCATED scan a family with no observed keys may simply not
        # have been reached, so fall back to the legacy tag probe rather than
        # asserting "unmeasured" from an incomplete read.
        if observed is None and not scan_complete:
            overall, samples = await _measured_reliability(fam, topics)
        else:
            overall, samples = await _measured_reliability(fam, topics, observed or [])
        bucket_rec = {
            "family": fam,
            "tier": rec.get("tier"),
            "overall_health": round(overall, 3) if overall is not None else None,
            "samples": samples,
            "topics": len(topics),
            "last_ok": rec.get("last_ok"),
        }
        if overall is None:
            unmeasured.append(bucket_rec)
        elif overall >= 0.80:
            healthy.append(bucket_rec)
        elif overall >= 0.60:
            degraded.append(bucket_rec)
        elif overall >= 0.40:
            failing.append(bucket_rec)
        else:
            dead.append(bucket_rec)

    healthy.sort(key=lambda r: r["overall_health"], reverse=True)
    degraded.sort(key=lambda r: r["overall_health"])
    failing.sort(key=lambda r: r["overall_health"])
    dead.sort(key=lambda r: r["overall_health"])
    unmeasured.sort(key=lambda r: r["family"])      # no score to sort on
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # C-29 — both flags are honesty state, not decoration. `store_readable`
        # False means the counts below are unknowable, not zero; `scan_complete`
        # False means some observations may be missing from the measured set.
        "store_readable": True,
        "scan_complete": scan_complete,
        "total_sources": len(families_idx),
        "healthy_count": len(healthy),
        "degraded_count": len(degraded),
        "failing_count": len(failing),
        "dead_count": len(dead),
        "unmeasured_count": len(unmeasured),
        "top_performers": healthy[:10],
        "degraded": degraded[:10],
        "failing": failing[:10],
        "dead": dead[:10],
        "unmeasured": unmeasured[:10],
    }


async def suspend_failing_sources(threshold: float = 0.40) -> dict:
    """Auto-suspend sources whose overall reliability drops below
    `threshold`. Auto-allowed per doctrine: 'never silently trust a
    failing source'. Suspension is reversible — a later ingest that
    raises the score back above 0.60 can un-suspend."""
    # C-29 — this consumer was blind in exactly the same way as
    # registry_health_report: `_measured_reliability` returned None for every
    # family, so `overall is None` short-circuited below and auto-suspend could
    # NEVER fire, whatever the threshold. Reading written keys makes it live.
    # A store failure must not be able to suspend anything, so read strictly and
    # abort rather than proceeding over an empty family list.
    try:
        families_idx = await rs.get_json_strict("aria:atlas:index:families") or []
    except StoreReadError as e:
        logger.warning("[C-29] suspend_failing_sources: store unreadable: %s", e)
        # R-F3908 (§21a) — see registry_health_report. This branch matters MORE:
        # a blind auto-suspend silently stops enforcing "never silently trust a
        # failing source", and nothing downstream would notice it had stopped.
        await _report_registry_blindness("suspend_failing_sources", e)
        # Shape MUST match the success return below: `suspended` is a COUNT and
        # `families` is the list. Returning a list as `suspended` would make a
        # caller's `result["suspended"] > 0` raise TypeError on the failure path —
        # a crash reachable only when the store is already unhealthy.
        return {"ok": False, "store_readable": False, "suspended": 0,
                "families": [], "error": f"store unreadable: {e}"}

    observed_by_family, scan_complete = await _observed_reliability_keys(families_idx)
    suspended: list[str] = []
    for fam in families_idx:
        rec = await rs.get_json(f"aria:atlas:source:{fam}")
        if not rec:
            continue
        topics = rec.get("topics") or []
        observed = observed_by_family.get(fam)
        if observed is None and not scan_complete:
            overall, _samples = await _measured_reliability(fam, topics)
        else:
            overall, _samples = await _measured_reliability(fam, topics, observed or [])
        if overall is None:
            # R-F3254 — YOU CANNOT DEMOTE WHAT YOU NEVER MEASURED. This branch
            # used to receive the 0.5 prior, which survives the default 0.40
            # threshold but NOT a caller-supplied one: `POST /api/aria/
            # source_validator/suspend_failing` takes any threshold, so 0.6
            # suspended every never-sampled family and wrote the fabricated
            # reason "Overall reliability 0.50 below 0.60 threshold" into the
            # record, plus a mistake-ledger entry blaming the source.
            continue
        if overall < threshold and rec.get("status") != "SUSPENDED":
            rec["status"] = "SUSPENDED"
            rec["degradation_reason"] = (
                f"Overall reliability {overall:.2f} below {threshold:.2f} threshold"
            )
            rec["suspended_at"] = datetime.now(timezone.utc).isoformat()
            await rs.set_json(f"aria:atlas:source:{fam}", rec, ex=365 * 86400)
            suspended.append(fam)
            try:
                from . import audit_log as _al
                if "source_atlas_demote" in _al.RECORDED_ACTIONS:
                    await _al.record(
                        "source_atlas_demote",
                        actor="aria_source_validator",
                        inputs={"family": fam, "overall": overall},
                        outputs={"status": "SUSPENDED"},
                        decision="suspended",
                        notes=rec["degradation_reason"],
                    )
            except Exception:
                pass
            # Brain signal + mistake-ledger so predictor knows which
            # families are failing — future scouts should deprioritise
            # candidates from suspended source families.
            try:
                from . import brain_hook as _bh, mistake_ledger as _ml
                await _bh.absorb(
                    module="source_validator",
                    summary=f"Source auto-suspended: {fam}",
                    detail=rec["degradation_reason"],
                    success=False,
                    gap_type="source_auto_suspended",
                    gap_detail=rec["degradation_reason"],
                )
                await _ml.record(
                    category="source_auto_suspended",
                    task_type="web_atlas",
                    domain=fam,
                    what=f"Source {fam} auto-suspended",
                    why=rec["degradation_reason"],
                    fix="Investigate reliability drop; consider atlas removal",
                    what_class="source_degradation",
                    severity="MEDIUM",
                )
            except Exception:
                pass
    return {"suspended": len(suspended), "families": suspended}

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
