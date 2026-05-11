# =============================================================================
# ARIA — Link Investigator
# aria_service/intel/link_investigator.py
#
# Recursive URL-following investigation engine. Takes a seed URL (from
# a web search result or a user-supplied link), walks the hyperlink
# graph to configurable depth, extracts facts from every visited page,
# and fuses the results into a single structured finding.
#
#   SEED URL
#     │
#     ├── extract facts (names, dates, registrations, addresses, amounts)
#     ├── harvest links
#     ├── score each link for intelligence relevance
#     │      +3  filings / accounts / financials / annual-report
#     │      +3  director / officer / ubo / beneficial-owner
#     │      +2  subsidiary / parent / affiliate / group / holdings
#     │      +2  authoritative gov / registry domain
#     │      +1  about / profile / history / contact
#     │      +1  pagination (page=2, ?offset=…, next)
#     │      +0  same-domain baseline
#     │      -5  social / login / CDN asset / mailto / javascript
#     │      skip  already visited in this tree
#     │
#     └── follow top-N → recurse to depth-1
#
#   SECONDARY URLs (depth 1)
#     └── same treatment → recurse to depth-2
#
#   TERTIARY URLs (depth 2, stop)
#
#   INTELLIGENCE FUSION
#     └── merge all facts across the tree, dedupe by normalised claim,
#         triangulate by source count, return structured LinkTreeResult
#
# COST / BUDGET SAFETY:
#   - Wall-time budget (DEFAULT 90s) halts recursion when exceeded
#   - USD budget (DEFAULT $0.15) halts LLM extraction when exceeded
#     (rule-based extraction continues regardless since it's free)
#   - Max page count (DEFAULT 40) caps total fetches per tree
#   - Per-page LLM extraction is OPTIONAL — pass llm=None to run
#     rule-based regex extraction only
#
# PERSISTENCE:
#   - Every completed tree is stored in Redis under
#     crucix:link_tree:{tree_id} with a 48-hour TTL so callers can
#     retrieve the full structured result via
#     GET /api/aria/research/link-investigate/{tree_id}
#
# USAGE:
#   from .link_investigator import investigate_link_tree
#   tree = await investigate_link_tree(
#       seed_url="https://s3rban.com/about",
#       query_context="Serban Industries SRL defence broker",
#       max_depth=2,
#       llm=llm,
#   )
#   tree.as_dict()  → structured JSON for API / storage
#   tree.render_markdown()  → human-readable summary
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

logger = logging.getLogger("ARIA.LinkInvestigator")


# =============================================================================
# CONFIG
# =============================================================================

DEFAULT_MAX_DEPTH = 2          # user spec says max 3; 2 = seed + children + grandchildren
DEFAULT_MAX_LINKS_PER_PAGE = 5
DEFAULT_MAX_PAGES = 40
DEFAULT_WALL_BUDGET_S = 90
DEFAULT_COST_BUDGET_USD = 0.15
DEFAULT_PAGE_TIMEOUT_S = 20

_TREE_REDIS_KEY = "crucix:link_tree:{tree_id}"
_TREE_REDIS_TTL = 48 * 3600


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class ExtractedFact:
    """A single fact pulled from a visited page."""
    kind: str                      # name | address | date | registration | email | phone | amount | url | role | other
    value: str
    context: str = ""              # surrounding text / attribution
    source_url: str = ""
    extractor: str = "rule"        # "rule" | "llm"
    confidence: str = "ASSESSED"   # CONFIRMED | PROBABLE | ASSESSED | UNCERTAIN


@dataclass
class LinkCandidate:
    """A hyperlink harvested from a page with its relevance score."""
    url: str
    anchor_text: str = ""
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    kind: str = "other"            # filings | director | subsidiary | authoritative | about | pagination | other


@dataclass
class PageNode:
    """One page visited in the investigation tree."""
    url: str
    depth: int
    parent_url: Optional[str] = None
    status: str = "pending"        # pending | ok | error | skipped | budget_exceeded
    status_detail: str = ""
    title: str = ""
    text_length: int = 0
    fetch_duration_ms: int = 0
    facts: list[ExtractedFact] = field(default_factory=list)
    harvested_links: int = 0
    followed_children: list[str] = field(default_factory=list)
    llm_extractor_used: bool = False
    llm_cost_usd: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FusedFact:
    """A deduplicated fact with triangulation count."""
    kind: str
    value: str
    source_urls: list[str] = field(default_factory=list)
    triangulation: int = 0
    first_context: str = ""
    confidence: str = "ASSESSED"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class LinkTreeResult:
    """Complete output of one investigation."""
    tree_id: str = field(default_factory=lambda: f"lt_{uuid.uuid4().hex[:12]}")
    seed_url: str = ""
    query_context: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: int = 0
    total_cost_usd: float = 0.0
    max_depth_requested: int = DEFAULT_MAX_DEPTH
    max_depth_reached: int = 0

    pages: list[PageNode] = field(default_factory=list)
    fused_facts: list[FusedFact] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    single_source_facts: list[FusedFact] = field(default_factory=list)

    pages_fetched: int = 0
    pages_failed: int = 0
    pages_skipped: int = 0
    budget_exceeded: bool = False
    budget_exceeded_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "tree_id": self.tree_id,
            "seed_url": self.seed_url,
            "query_context": self.query_context,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "total_cost_usd": self.total_cost_usd,
            "max_depth_requested": self.max_depth_requested,
            "max_depth_reached": self.max_depth_reached,
            "pages": [p.as_dict() for p in self.pages],
            "fused_facts": [f.as_dict() for f in self.fused_facts],
            "key_findings": self.key_findings,
            "single_source_facts": [f.as_dict() for f in self.single_source_facts],
            "pages_fetched": self.pages_fetched,
            "pages_failed": self.pages_failed,
            "pages_skipped": self.pages_skipped,
            "budget_exceeded": self.budget_exceeded,
            "budget_exceeded_reason": self.budget_exceeded_reason,
        }

    def render_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"*🕸 LINK INVESTIGATION — {self.seed_url}*")
        lines.append("")
        lines.append(f"Query: {self.query_context}")
        lines.append(
            f"Pages: {self.pages_fetched} fetched / {self.pages_failed} failed / "
            f"{self.pages_skipped} skipped · Max depth reached: {self.max_depth_reached}"
        )
        lines.append(f"Duration: {self.duration_ms}ms · Cost: ${self.total_cost_usd:.4f}")
        if self.budget_exceeded:
            lines.append(f"⚠ Budget exceeded: {self.budget_exceeded_reason}")
        lines.append("")

        if self.key_findings:
            lines.append("*Key findings (triangulated across ≥2 sources):*")
            for kf in self.key_findings[:15]:
                lines.append(f"  • {kf}")
            lines.append("")

        if self.fused_facts:
            lines.append("*Strongly-supported facts:*")
            for ff in self.fused_facts[:20]:
                lines.append(
                    f"  • [{ff.kind}] {ff.value} "
                    f"— {ff.triangulation} source(s)"
                )
            lines.append("")

        if self.single_source_facts:
            lines.append("*Single-source facts (verify before citing):*")
            for ff in self.single_source_facts[:10]:
                lines.append(f"  • [{ff.kind}] {ff.value}")
            lines.append("")

        lines.append("*Visited pages:*")
        for p in self.pages:
            icon = {"ok": "✓", "error": "✗", "skipped": "○", "budget_exceeded": "⏱"}.get(p.status, "?")
            lines.append(f"  {icon} d{p.depth} {p.url[:90]} — {len(p.facts)} fact(s)")
        lines.append("")
        lines.append(f"_tree_id: {self.tree_id}_")
        return "\n".join(lines)


# =============================================================================
# URL NORMALISATION + DEDUP
# =============================================================================

def _normalise_url(url: str) -> str:
    """Normalise a URL for dedup purposes. Strips fragment, common
    tracking params, trailing slash, and lowercases the host."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return url.strip().lower()
    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    path = parsed.path or "/"
    # Drop trailing slash on non-root paths
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    # Drop utm_ / ref / fbclid tracking params
    kept: list[str] = []
    if parsed.query:
        for pair in parsed.query.split("&"):
            key = pair.split("=", 1)[0].lower()
            if key.startswith(("utm_", "ref", "fbclid", "gclid", "_ga", "mc_")):
                continue
            kept.append(pair)
    query = "&".join(kept)
    return urlunparse((scheme, netloc, path, "", query, ""))


# =============================================================================
# LINK WORTHINESS SCORER
# =============================================================================

# URL-keyword → (points, kind)
_URL_KEYWORD_SCORES: list[tuple[str, int, str]] = [
    # High-value filings / accounts / registry data
    ("annual-report", 4, "filings"),
    ("annualreport",  4, "filings"),
    ("filings",       4, "filings"),
    ("accounts",      4, "filings"),
    ("financials",    4, "filings"),
    ("statutory",     4, "filings"),
    ("statement",     3, "filings"),
    ("10-k",          4, "filings"),
    ("10-q",          3, "filings"),
    ("psc",           4, "director"),        # PSC register (UK)
    ("officer",       4, "director"),
    ("director",      4, "director"),
    ("board",         3, "director"),
    ("ubo",           4, "director"),
    ("beneficial",    4, "director"),
    ("ownership",     3, "director"),
    ("controller",    3, "director"),
    # Corporate structure
    ("subsidiary",    3, "subsidiary"),
    ("parent",        2, "subsidiary"),
    ("affiliate",     2, "subsidiary"),
    ("group",         1, "subsidiary"),
    ("holdings",      2, "subsidiary"),
    ("related",       2, "subsidiary"),
    # About / profile / history pages (candidates for founding year etc)
    ("about",         2, "about"),
    ("profile",       2, "about"),
    ("history",       3, "about"),
    ("founded",       3, "about"),
    ("leadership",    3, "director"),
    ("management",    2, "director"),
    ("team",          1, "about"),
    ("contact",       1, "about"),
    # Pagination / next
    ("page=",         2, "pagination"),
    ("offset=",       2, "pagination"),
    ("next",          1, "pagination"),
    # Press / sanctions / compliance sources
    ("sanction",      3, "authoritative"),
    ("ofac",          4, "authoritative"),
    ("ofsi",          4, "authoritative"),
    ("sdn",           3, "authoritative"),
    ("entity-list",   4, "authoritative"),
]

# High-authority domain patterns → bonus points
_AUTH_DOMAINS: list[tuple[str, int]] = [
    (".gov.uk",                     4),
    (".gov",                        3),
    ("companieshouse.gov.uk",       5),
    ("find-and-update.company-information.service.gov.uk", 5),
    ("sec.gov",                     5),
    ("edgar.sec.gov",               5),
    ("europa.eu",                   4),
    ("eur-lex.europa.eu",           5),
    ("treasury.gov",                5),
    ("home.treasury.gov",           5),
    ("sanctionsmap.eu",             5),
    ("opensanctions.org",           5),
    ("icij.org",                    4),
    ("opencorporates.com",          4),
    ("offshoreleaks.icij.org",      5),
    ("gleif.org",                   4),
    ("onrc.ro",                     5),  # Romanian registry
    ("portal.onrc.ro",              5),
    ("mersis.gtb.gov.tr",           5),  # Turkey
    ("ssm.com.my",                  5),  # Malaysia
    ("bizfile.gov.sg",              5),  # Singapore
    ("mca.gov.in",                  5),  # India
    ("egrul.nalog.ru",              5),  # Russia
    ("handelsregister.de",          5),  # Germany
    ("pappers.fr",                  5),  # France
    ("registroimprese.it",          5),  # Italy
    ("kbopub.economie.fgov.be",     5),  # Belgium
    ("ariregister.rik.ee",          5),  # Estonia
    ("cipc.co.za",                  5),  # South Africa
    ("rues.org.co",                 5),  # Colombia
    ("gsxt.gov.cn",                 5),  # China
    ("datacvr.virk.dk",             5),  # Denmark
    ("brreg.no",                    5),  # Norway
    ("sudreg.pravosudje.hr",        5),  # Croatia
    ("ajpes.si",                    5),  # Slovenia
    ("fatf-gafi.org",               4),
    ("transparency.org",            3),
    ("sipri.org",                   3),
    ("iiss.org",                    3),
    ("rusi.org",                    3),
    ("archive.org",                 3),
    ("web.archive.org",             4),
    ("wayback",                     3),
]

# URL-keyword → skip-entirely patterns (assets, social, auth)
_SKIP_PATTERNS = [
    "/login", "/signin", "/signup", "/register", "/password",
    "/cart", "/checkout", "/basket",
    "/favicon", ".ico", ".css", ".js", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".woff", ".woff2", ".ttf", ".eot",
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "linkedin.com/share", "pinterest.com", "youtube.com/embed",
    "tiktok.com", "whatsapp.com/send", "telegram.me",
    "/share?", "mailto:", "tel:", "javascript:",
    "/cdn-cgi/", "/static/", "/assets/",
]


def _score_link(
    url: str,
    anchor_text: str,
    base_url: str,
    query_context: str,
) -> LinkCandidate:
    """Apply the rule-based link worthiness scorer to a single URL.

    Returns a LinkCandidate with the score + reasons list + kind.
    Skipped URLs return score=-1 and kind='skip'.
    """
    candidate = LinkCandidate(url=url, anchor_text=anchor_text)
    if not url:
        candidate.score = -1
        candidate.reasons.append("empty url")
        candidate.kind = "skip"
        return candidate

    lower = url.lower()

    # Hard skip patterns
    for skip in _SKIP_PATTERNS:
        if skip in lower:
            candidate.score = -5
            candidate.reasons.append(f"skip:{skip}")
            candidate.kind = "skip"
            return candidate

    # Parse netloc
    try:
        netloc = urlparse(url).netloc.lower()
        base_netloc = urlparse(base_url).netloc.lower() if base_url else ""
    except Exception:
        netloc = ""
        base_netloc = ""

    # Authoritative domain bonus (longest match wins)
    best_auth_bonus = 0
    for pattern, pts in _AUTH_DOMAINS:
        if pattern in netloc:
            if pts > best_auth_bonus:
                best_auth_bonus = pts
                candidate.kind = "authoritative"
    if best_auth_bonus > 0:
        candidate.score += best_auth_bonus
        candidate.reasons.append(f"authoritative_domain(+{best_auth_bonus})")

    # URL keyword scores
    best_kw_bonus = 0
    for kw, pts, kind in _URL_KEYWORD_SCORES:
        if kw in lower:
            candidate.score += pts
            candidate.reasons.append(f"keyword:{kw}(+{pts})")
            if pts > best_kw_bonus:
                best_kw_bonus = pts
                if candidate.kind in ("other", ""):
                    candidate.kind = kind

    # Anchor text bonus — if the anchor text itself contains a relevance term
    if anchor_text:
        at_lower = anchor_text.lower()
        for kw, pts, _ in _URL_KEYWORD_SCORES:
            if kw in at_lower:
                candidate.score += 1  # anchor text bonus is fixed +1
                candidate.reasons.append(f"anchor_kw:{kw}(+1)")
                break

    # Query-context relevance — if the query names a term and it appears
    # in the anchor text OR URL path, add a bonus
    if query_context:
        q_tokens = [t for t in re.findall(r"\w{4,}", query_context.lower()) if len(t) >= 4]
        for tok in q_tokens:
            if tok in lower or (anchor_text and tok in anchor_text.lower()):
                candidate.score += 2
                candidate.reasons.append(f"query_match:{tok}(+2)")
                break

    # R-F292: same-host bias. Before this fix the same-domain baseline was
    # +0, so any external link with a keyword match (+1/+2) outscored same-
    # host pages. On the modirumgespi.com live DD 2026-05-11 this caused the
    # crawler to drift to an unrelated academic "Rat Trading" paper instead
    # of the actual /company /products /news pages. Same-host now gets a
    # +3 baseline — authoritative external (gov / registry / sanctions) at
    # +3..+5 still wins, but random keyword-bearing off-domain links no
    # longer outrank the seed site's own pages.
    if netloc == base_netloc:
        candidate.score += 3
        candidate.reasons.append("same_host(+3)")
    elif best_auth_bonus == 0:
        # Off-domain AND non-authoritative — penalise so the crawler stays
        # on-mission unless the link is to a real registry / OFAC / etc.
        candidate.score -= 2
        candidate.reasons.append("off_host_non_authoritative(-2)")

    return candidate


# =============================================================================
# HTML HELPERS — regex-based to avoid bs4 dep
# =============================================================================

_ANCHOR_RE = re.compile(
    r'<a\s[^>]*?href=["\']([^"\']+)["\'][^>]*?>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _harvest_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Parse <a href=...> tags from an HTML string. Returns (url, anchor_text) pairs."""
    if not html:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _ANCHOR_RE.finditer(html):
        href = m.group(1).strip()
        if not href or href.startswith("#"):
            continue
        # Absolute URL
        try:
            abs_url = urljoin(base_url, href)
        except Exception:
            continue
        # Anchor text — strip inner tags
        anchor_raw = m.group(2) or ""
        anchor = _TAG_STRIP_RE.sub("", anchor_raw).strip()[:200]
        norm = _normalise_url(abs_url)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append((abs_url, anchor))
    return out


def _extract_title(html: str) -> str:
    if not html:
        return ""
    m = _TITLE_RE.search(html)
    if not m:
        return ""
    return _TAG_STRIP_RE.sub("", m.group(1)).strip()[:200]


# =============================================================================
# RULE-BASED FACT EXTRACTOR (always runs, zero-cost)
# =============================================================================

_RULE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Email
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "email"),
    # UK Companies House number
    (re.compile(r"\b(?:company\s+(?:no\.?|number)\s*:?\s*)([0-9]{7,8})\b", re.IGNORECASE), "registration"),
    # Romanian CUI
    (re.compile(r"\b(?:CUI|CIF)\s*:?\s*([0-9]{5,10})\b", re.IGNORECASE), "registration"),
    # Generic registration / VAT / GIIN patterns
    (re.compile(r"\b(?:VAT|TIN|EIN)\s*:?\s*([A-Z0-9]{5,20})\b", re.IGNORECASE), "registration"),
    # Date (YYYY-MM-DD + common formats)
    (re.compile(r"\b(19[0-9]{2}|20[0-9]{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12][0-9]|3[01])\b"), "date"),
    # R-F293: year regex previously matched ANY 4-digit starting with 1 or 2,
    # which captured 1001 / 1031 / 1892 etc. as years from page numbers /
    # version strings on modirumgespi.com (live DD 2026-05-11). Restrict to
    # 1900-2049 — modern-active-entity range. Pre-1900 founding years are
    # vanishingly rare for live commercial counterparties and the explicit
    # "founded in YYYY" phrase regex below catches the legitimate edge
    # cases without polluting the generic year extractor.
    (re.compile(r"\b(19[0-9]{2}|20[0-4][0-9])\b"), "year"),
    # UK / EU / US phone number — anchored to tel:/phone:/Tel./etc. labels
    # to avoid false positives on JS asset IDs, tracking tokens, etc.
    (re.compile(
        r"(?:tel[:\.\s]|phone[:\.\s]|call\s+(?:us\s+)?(?:on\s+|at\s+)?|\+(?=\d{1,3}[\s\-]))"
        r"(\+?\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4})",
        re.IGNORECASE,
    ), "phone"),
    # Amount (£, $, €) with optional M / B suffix
    (re.compile(r"[£$€]\s?[0-9,]+(?:\.[0-9]+)?\s?(?:million|billion|M|B|thousand|k)?", re.IGNORECASE), "amount"),
    # "founded in YYYY" / "established YYYY" — R-F293 tightened range
    (re.compile(r"\b(?:founded|established|incorporated)\s+(?:in\s+)?(1[89][0-9]{2}|20[0-4][0-9])\b", re.IGNORECASE), "founding_year"),
    (re.compile(r"\bsince\s+(1[89][0-9]{2}|20[0-4][0-9])\b", re.IGNORECASE), "founding_year"),
    (re.compile(r"\b([0-9]{1,3})\s+years?\s+of\s+experience\b", re.IGNORECASE), "years_experience_claim"),
]


def _extract_facts_rules(text: str, source_url: str) -> list[ExtractedFact]:
    """Extract structured facts from plain text using regex rules.
    Deduplicates by (kind, normalised value)."""
    if not text:
        return []
    text_sample = text[:60000]  # cap to avoid pathological regex costs
    seen: set[tuple[str, str]] = set()
    facts: list[ExtractedFact] = []
    for pat, kind in _RULE_PATTERNS:
        for m in pat.finditer(text_sample):
            raw = m.group(0) if not m.groups() else m.group(1) if m.group(1) else m.group(0)
            value = (raw or "").strip()
            if not value or len(value) > 200:
                continue
            norm = value.lower().strip()
            key = (kind, norm)
            if key in seen:
                continue
            seen.add(key)
            # Grab surrounding context ~60 chars before and after
            start = max(0, m.start() - 60)
            end = min(len(text_sample), m.end() + 60)
            ctx = text_sample[start:end].replace("\n", " ").strip()[:200]
            facts.append(ExtractedFact(
                kind=kind,
                value=value,
                context=ctx,
                source_url=source_url,
                extractor="rule",
                confidence="ASSESSED",
            ))
            if len(facts) >= 100:
                break
        if len(facts) >= 100:
            break
    return facts


# =============================================================================
# LLM FACT EXTRACTOR (optional, cost-budgeted)
# =============================================================================

_LLM_EXTRACT_SYSTEM = """You are an intelligence-extraction engine.

Given the text of a web page and a query context, extract every structured
fact that could matter for a defence/compliance due-diligence investigation.

Return STRICT JSON with this shape (no prose, no markdown):

{
  "facts": [
    {"kind": "name|role|address|registration|date|email|phone|amount|url|claim|other",
     "value": "the extracted value",
     "context": "short context sentence that supports the fact"}
  ],
  "suggested_links": [
    {"reason": "why this link is worth following",
     "url": "absolute url as seen on the page",
     "kind": "filings|director|subsidiary|source|pagination|other"}
  ],
  "summary": "one-sentence summary of what this page tells us"
}

Rules:
- Only extract facts that are explicitly present in the text. Do NOT infer.
- Every value must be verbatim or near-verbatim from the text.
- Attribute speakers / sources in context where possible.
- If the page is empty, return {"facts": [], "suggested_links": [], "summary": ""}.
- Absolute cap: 30 facts, 10 suggested links."""


async def _extract_facts_llm(
    text: str,
    query_context: str,
    source_url: str,
    llm: Any,
) -> tuple[list[ExtractedFact], list[LinkCandidate], str, float]:
    """Run the LLM fact extractor on a page. Returns (facts, link_suggestions, summary, cost_usd).

    On any error or non-JSON response, returns ([], [], "", 0.0) — the
    rule-based extractor still runs independently so the tree walk
    doesn't lose data on LLM failures.
    """
    if not llm or not getattr(llm, "is_configured", False):
        return [], [], "", 0.0

    # Trim text aggressively to control token count
    sample = (text or "")[:6000]
    if len(sample) < 100:
        return [], [], "", 0.0

    user = f"Query: {query_context}\n\nPage URL: {source_url}\n\nPage text:\n{sample}"

    try:
        # Self-attribute so the cost meter doesn't bucket this under
        # "uncategorized" when called from a context without an
        # outer cost_tracker.feature() wrap.
        from . import cost_tracker
        with cost_tracker.feature("link_investigator"):
            result = await llm.complete(
                _LLM_EXTRACT_SYSTEM,
                user,
                max_tokens=1500,
                timeout=45.0,
            )
        raw = getattr(result, "text", "") or ""
        cost = _estimate_llm_cost(result)
    except Exception as e:
        logger.debug("link_investigator: LLM extraction failed for %s: %s", source_url, e)
        return [], [], "", 0.0

    # Parse JSON — tolerate a leading/trailing fence
    import json as _json
    try:
        # Strip common fencing
        stripped = raw.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        # Find the outermost JSON object
        brace_start = stripped.find("{")
        brace_end = stripped.rfind("}")
        if brace_start < 0 or brace_end < 0:
            return [], [], "", cost
        doc = _json.loads(stripped[brace_start : brace_end + 1])
    except Exception as e:
        logger.debug("link_investigator: LLM JSON parse failed for %s: %s", source_url, e)
        return [], [], "", cost

    facts: list[ExtractedFact] = []
    for f in (doc.get("facts") or [])[:30]:
        if not isinstance(f, dict):
            continue
        value = (f.get("value") or "").strip()
        if not value or len(value) > 300:
            continue
        facts.append(ExtractedFact(
            kind=(f.get("kind") or "other").strip().lower()[:40],
            value=value,
            context=(f.get("context") or "")[:300],
            source_url=source_url,
            extractor="llm",
            confidence="ASSESSED",
        ))

    link_suggestions: list[LinkCandidate] = []
    for sl in (doc.get("suggested_links") or [])[:10]:
        if not isinstance(sl, dict):
            continue
        url = (sl.get("url") or "").strip()
        if not url or not url.startswith(("http://", "https://")):
            continue
        link_suggestions.append(LinkCandidate(
            url=url,
            anchor_text=(sl.get("reason") or "")[:200],
            score=3,  # LLM-suggested links get a base boost
            reasons=["llm_suggested"],
            kind=(sl.get("kind") or "other").strip().lower()[:40],
        ))

    summary = (doc.get("summary") or "")[:500]
    return facts, link_suggestions, summary, cost


def _estimate_llm_cost(result: Any) -> float:
    """Best-effort USD cost estimate for an LLM completion result."""
    try:
        from . import cost_tracker
        model = getattr(result, "model", "") or ""
        input_tokens = getattr(result, "input_tokens", 0) or 0
        output_tokens = getattr(result, "output_tokens", 0) or 0
        return cost_tracker.estimate_cost_usd(model, input_tokens, output_tokens)
    except Exception:
        return 0.0


# =============================================================================
# PAGE FETCH
# =============================================================================

async def _fetch_page(url: str, timeout_s: float = DEFAULT_PAGE_TIMEOUT_S) -> tuple[str, str, str]:
    """Fetch a page and return (text, html, status).

    Uses httpx directly rather than researcher.extract_url so we get
    raw HTML for link harvesting. researcher.extract_url strips HTML
    in its text-only output path.
    """
    import httpx
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ARIA-LinkInvestigator/1.0; +https://arkmurus.com)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en;q=1.0, *;q=0.5",
    }
    try:
        async with httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
            headers=headers,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        ) as client:
            r = await client.get(url)
            if r.status_code >= 400:
                return "", "", f"http_{r.status_code}"
            html = r.text or ""
            # Best-effort plain-text extraction
            text = _TAG_STRIP_RE.sub(" ", html)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:80000], html, "ok"
    except httpx.TimeoutException:
        return "", "", "timeout"
    except httpx.HTTPError as e:
        return "", "", f"http_error:{str(e)[:60]}"
    except Exception as e:
        logger.debug("fetch_page %s: %s", url, e)
        return "", "", f"error:{str(e)[:60]}"


# =============================================================================
# INTELLIGENCE FUSION
# =============================================================================

def _normalise_fact_value(kind: str, value: str) -> str:
    """Normalise a fact value for deduplication.
    Lower-cases, strips punctuation for most kinds; preserves case
    for names/roles where case matters for recognition."""
    if not value:
        return ""
    v = value.strip()
    if kind in ("email", "phone", "url", "registration", "date", "year", "founding_year"):
        return v.lower()
    if kind in ("name", "role", "address"):
        # Keep case but collapse whitespace
        return re.sub(r"\s+", " ", v)
    return re.sub(r"\s+", " ", v.lower())


def _fuse_tree(result: LinkTreeResult) -> None:
    """Merge facts across every visited page, dedupe by (kind, norm_value),
    count triangulation, and split into fused_facts + single_source_facts."""
    by_key: dict[tuple[str, str], FusedFact] = {}
    for page in result.pages:
        for fact in page.facts:
            key = (fact.kind, _normalise_fact_value(fact.kind, fact.value))
            if not key[1]:
                continue
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = FusedFact(
                    kind=fact.kind,
                    value=fact.value,
                    source_urls=[fact.source_url],
                    triangulation=1,
                    first_context=fact.context,
                    confidence=fact.confidence,
                )
            else:
                if fact.source_url and fact.source_url not in existing.source_urls:
                    existing.source_urls.append(fact.source_url)
                    existing.triangulation += 1
    fused = list(by_key.values())
    fused.sort(key=lambda f: (-f.triangulation, f.kind, f.value))
    result.fused_facts = [f for f in fused if f.triangulation >= 2]
    result.single_source_facts = [f for f in fused if f.triangulation < 2]

    # Key findings — surface triangulated facts in a human-readable line
    result.key_findings = [
        f"{ff.kind.upper()}: {ff.value} (×{ff.triangulation})"
        for ff in result.fused_facts[:15]
    ]


# =============================================================================
# TREE WALKER — the heart of the investigator
# =============================================================================

async def _visit(
    url: str,
    depth: int,
    parent_url: Optional[str],
    result: LinkTreeResult,
    visited: set[str],
    query_context: str,
    max_depth: int,
    max_links_per_page: int,
    max_pages: int,
    wall_deadline_epoch: float,
    cost_budget_usd: float,
    llm: Any,
) -> None:
    # Budget checks BEFORE fetching
    if time.time() > wall_deadline_epoch:
        result.budget_exceeded = True
        result.budget_exceeded_reason = "wall_time"
        node = PageNode(url=url, depth=depth, parent_url=parent_url,
                        status="budget_exceeded", status_detail="wall_time")
        result.pages.append(node)
        result.pages_skipped += 1
        return
    if result.pages_fetched >= max_pages:
        result.budget_exceeded = True
        result.budget_exceeded_reason = "page_count"
        node = PageNode(url=url, depth=depth, parent_url=parent_url,
                        status="budget_exceeded", status_detail="page_count")
        result.pages.append(node)
        result.pages_skipped += 1
        return

    norm = _normalise_url(url)
    if norm in visited:
        node = PageNode(url=url, depth=depth, parent_url=parent_url,
                        status="skipped", status_detail="already_visited")
        result.pages.append(node)
        result.pages_skipped += 1
        return
    visited.add(norm)

    # Fetch
    t0 = time.time()
    text, html, fetch_status = await _fetch_page(url)
    fetch_ms = int((time.time() - t0) * 1000)

    node = PageNode(
        url=url,
        depth=depth,
        parent_url=parent_url,
        fetch_duration_ms=fetch_ms,
        text_length=len(text),
        title=_extract_title(html),
    )

    if fetch_status != "ok" or not text:
        node.status = "error"
        node.status_detail = fetch_status
        result.pages.append(node)
        result.pages_failed += 1
        return

    node.status = "ok"
    result.pages_fetched += 1
    if depth > result.max_depth_reached:
        result.max_depth_reached = depth

    # Rule-based fact extraction (always runs)
    rule_facts = _extract_facts_rules(text, url)
    node.facts.extend(rule_facts)

    # LLM fact extraction (optional, budgeted)
    llm_suggested_links: list[LinkCandidate] = []
    if llm is not None and result.total_cost_usd < cost_budget_usd:
        llm_facts, llm_suggested_links, llm_summary, cost = await _extract_facts_llm(
            text, query_context, url, llm,
        )
        node.facts.extend(llm_facts)
        node.llm_extractor_used = True
        node.llm_cost_usd = cost
        result.total_cost_usd += cost
        if llm_summary:
            node.title = node.title or llm_summary[:200]
    elif llm is not None:
        # Budget exceeded — skip LLM but note it
        node.status_detail = (node.status_detail or "") + "; llm_budget_exceeded"

    # Harvest + score links for child expansion
    if depth < max_depth:
        harvested = _harvest_links(html, url)
        node.harvested_links = len(harvested)
        scored: list[LinkCandidate] = []
        for link_url, anchor in harvested:
            cand = _score_link(link_url, anchor, url, query_context)
            if cand.score > 0 and cand.kind != "skip":
                scored.append(cand)
        # Merge LLM-suggested links, boosting already-scored ones if overlap
        llm_map = {_normalise_url(c.url): c for c in llm_suggested_links}
        for c in scored:
            hit = llm_map.pop(_normalise_url(c.url), None)
            if hit is not None:
                c.score += hit.score
                c.reasons.append("llm_confirmed")
        for extra in llm_map.values():
            scored.append(extra)

        # Sort by score descending and take top N
        scored.sort(key=lambda c: -c.score)
        top = scored[:max_links_per_page]
        node.followed_children = [c.url for c in top]

        result.pages.append(node)

        # Recurse into children sequentially to keep request rate civil
        for child in top:
            await _visit(
                url=child.url,
                depth=depth + 1,
                parent_url=url,
                result=result,
                visited=visited,
                query_context=query_context,
                max_depth=max_depth,
                max_links_per_page=max_links_per_page,
                max_pages=max_pages,
                wall_deadline_epoch=wall_deadline_epoch,
                cost_budget_usd=cost_budget_usd,
                llm=llm,
            )
    else:
        # Leaf node
        result.pages.append(node)


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================

async def investigate_link_tree(
    seed_url: str,
    query_context: str = "",
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_links_per_page: int = DEFAULT_MAX_LINKS_PER_PAGE,
    max_pages: int = DEFAULT_MAX_PAGES,
    wall_budget_s: int = DEFAULT_WALL_BUDGET_S,
    cost_budget_usd: float = DEFAULT_COST_BUDGET_USD,
    llm: Any = None,
) -> LinkTreeResult:
    """Walk the link graph from `seed_url` out to `max_depth` levels,
    extracting facts at every node and fusing them into a single result.

    Pass llm=None for rule-based-only extraction (free, ~5s per page).
    Pass an LLMProvider for semantic extraction (~$0.003 per page, better
    fact coverage + link suggestions).

    The walk terminates early if any budget is exceeded (wall time,
    cost, or page count). `LinkTreeResult.budget_exceeded` signals
    which limit bit.
    """
    if max_depth > 3:
        max_depth = 3  # user spec: max depth 3
    max_depth = max(0, max_depth)

    result = LinkTreeResult(
        seed_url=seed_url,
        query_context=query_context,
        max_depth_requested=max_depth,
    )
    t_start = time.time()
    wall_deadline = t_start + wall_budget_s
    visited: set[str] = set()

    await _visit(
        url=seed_url,
        depth=0,
        parent_url=None,
        result=result,
        visited=visited,
        query_context=query_context,
        max_depth=max_depth,
        max_links_per_page=max_links_per_page,
        max_pages=max_pages,
        wall_deadline_epoch=wall_deadline,
        cost_budget_usd=cost_budget_usd,
        llm=llm,
    )

    _fuse_tree(result)
    result.duration_ms = int((time.time() - t_start) * 1000)

    # Persist
    try:
        from . import redis_store as rs
        await rs.set_json(
            _TREE_REDIS_KEY.format(tree_id=result.tree_id),
            result.as_dict(),
            ex=_TREE_REDIS_TTL,
        )
    except Exception as e:
        logger.debug("link_investigator: persist failed: %s", e)

    logger.info(
        "[link_investigator] tree %s complete — seed=%s pages=%d failed=%d "
        "depth=%d cost=$%.4f duration=%dms",
        result.tree_id,
        seed_url,
        result.pages_fetched,
        result.pages_failed,
        result.max_depth_reached,
        result.total_cost_usd,
        result.duration_ms,
    )

    # ── Brain hook: feed extracted facts + key findings ──
    try:
        from . import brain_hook
        _facts_summary = "; ".join(
            f"{f.category}: {f.value[:80]}" for f in (result.fused_facts or [])[:10]
        ) if result.fused_facts else "no facts extracted"
        await brain_hook.absorb(
            module="link_investigator",
            summary=f"Link investigation {result.tree_id}: {seed_url} — {result.pages_fetched} pages, {len(result.fused_facts or [])} facts, depth {result.max_depth_reached}",
            detail=_facts_summary,
            entity_name=seed_url,
            success=result.pages_fetched > 0,
            source_id=result.tree_id,
            confidence="ASSESSED",
        )
    except Exception as _bh:
        logger.debug("link_investigator brain_hook failed: %s", _bh)

    return result


async def get_tree(tree_id: str) -> Optional[dict]:
    """Retrieve a persisted link tree by tree_id."""
    try:
        from . import redis_store as rs
        return await rs.get_json(_TREE_REDIS_KEY.format(tree_id=tree_id))
    except Exception:
        return None
