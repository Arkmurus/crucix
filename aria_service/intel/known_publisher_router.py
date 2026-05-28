"""Known-publisher API router — skip scraping when the publisher has an API.

Why this exists
───────────────
Past incident 2026-04-18: /teach https://www.nature.com/articles/... timed
out at 3 minutes. Nature has aggressive bot detection + heavy JS; even
Lightpanda couldn't render it in time. But Nature (like arxiv, pubmed,
and most academic publishers) has a PROPER API that returns structured
metadata in milliseconds — we just weren't using it.

This module detects known publisher/academic domains in URLs and routes
to their API instead of scraping. Benefits:
  - Bypasses bot detection / JS-rendering requirements
  - Returns structured metadata (authors, abstract, DOI, publication
    date, citations) that the scraper couldn't extract anyway
  - Sub-second response vs 3-minute timeout
  - Consistent shape across publishers so downstream ingest doesn't
    need per-source branching

Adapters
────────
  - arxiv.org         → export.arxiv.org/api/query (abs + html URLs)
  - nature.com        → CrossRef DOI lookup from URL pattern
  - pubmed            → E-utilities (NCBI)
  - doi.org           → CrossRef (works for any DOI)
  - openalex.org      → openalex.org API (open academic graph)
  - sciencedirect.com → CrossRef DOI lookup
  - springer.com      → CrossRef DOI lookup
  - ieee.org          → CrossRef DOI lookup

All adapters return the same shape for the caller:
  {
    "source": str,
    "ok": bool,
    "title": str,
    "authors": list[str],
    "abstract": str,
    "publication_date": str (ISO),
    "doi": str,
    "citations": int | None,
    "text_for_ingest": str,   # concatenated title + authors + abstract
    "url_canonical": str,
    "error": str | None,
  }

Zero LLM calls. Zero scraping. Just targeted API hits.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger("aria.known_publisher_router")

_UA = "ARIA-Research-Router/1.0 research@arkmurus.com"


# ── Domain → adapter function mapping ──────────────────────────────────────

def detect_publisher(url: str) -> str | None:
    """Return adapter name if the URL is a known publisher, else None.

    Matched by host suffix so subdomain variants (www., api., etc.) work.
    """
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return None
    if not host:
        return None

    # arxiv — both /abs and /html paths
    if host.endswith("arxiv.org"):
        return "arxiv"
    if host.endswith("nature.com"):
        return "nature"
    if host.endswith("ncbi.nlm.nih.gov") or host.endswith("pubmed.ncbi.nlm.nih.gov"):
        return "pubmed"
    if host == "doi.org" or host.endswith(".doi.org"):
        return "crossref_doi"
    if host.endswith("openalex.org"):
        return "openalex"
    if host.endswith("sciencedirect.com"):
        return "crossref_doi"  # we'll extract the DOI from the URL
    if host.endswith("springer.com") or host.endswith("link.springer.com"):
        return "crossref_doi"
    if host.endswith("ieee.org") or host.endswith("ieeexplore.ieee.org"):
        return "crossref_doi"
    if host.endswith("tandfonline.com"):
        return "crossref_doi"
    if host.endswith("wiley.com") or host.endswith("onlinelibrary.wiley.com"):
        return "crossref_doi"
    if host.endswith("sagepub.com"):
        return "crossref_doi"

    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="known_publisher_router",
        summary="Detect Publisher",
        source_id="known_publisher_router:R-F996",
    )

    return None


# ── Canonical output shape ─────────────────────────────────────────────────

def _empty(source: str, url: str) -> dict:
    return {
        "source": source,
        "ok": False,
        "title": "",
        "authors": [],
        "abstract": "",
        "publication_date": "",
        "doi": "",
        "citations": None,
        "text_for_ingest": "",
        "url_canonical": url,
        "error": None,
    }


def _pack_text(title: str, authors: list[str], abstract: str) -> str:
    """Build the body we'll feed into RAG ingest. Abstract carries most
    of the signal; title + authors front-loaded for retrieval keyword
    matching."""
    parts: list[str] = []
    if title:
        parts.append(f"TITLE: {title}")
    if authors:
        parts.append(f"AUTHORS: {', '.join(authors[:10])}")
    if abstract:
        parts.append(f"ABSTRACT: {abstract}")
    return "\n\n".join(parts)


# ── arxiv ──────────────────────────────────────────────────────────────────

_ARXIV_ID_RE = re.compile(r"arxiv\.org/(?:abs|html|pdf)/([\d.]+(?:v\d+)?)")


async def _arxiv(url: str) -> dict:
    """arxiv API — returns atom XML. Extract id, title, authors, abstract."""
    out = _empty("arxiv", url)
    m = _ARXIV_ID_RE.search(url)
    if not m:
        out["error"] = "no arxiv id found in URL"
        return out
    paper_id = m.group(1)

    import httpx
    api_url = f"https://export.arxiv.org/api/query?id_list={paper_id}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(api_url, headers={"User-Agent": _UA})
            r.raise_for_status()
            xml = r.text
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return out

    # Parse atom XML — lightweight, no need for full bs4
    try:
        try:
            from defusedxml import ElementTree as ET  # type: ignore
        except ImportError:
            from xml.etree import ElementTree as ET
        root = ET.fromstring(xml)
    except Exception as e:
        out["error"] = f"xml parse: {str(e)[:120]}"
        return out

    def _ltag(t: str) -> str:
        return t.rsplit("}", 1)[-1] if "}" in t else t

    title = ""
    abstract = ""
    pub_date = ""
    authors: list[str] = []

    # Find first <entry>
    entry = None
    for child in root.iter():
        if _ltag(child.tag) == "entry":
            entry = child
            break
    if entry is None:
        out["error"] = "no entry in arxiv response"
        return out

    for sub in entry.iter():
        tag = _ltag(sub.tag)
        text = (sub.text or "").strip()
        if tag == "title" and not title:
            title = re.sub(r"\s+", " ", text)
        elif tag == "summary":
            abstract = re.sub(r"\s+", " ", text)
        elif tag == "published":
            pub_date = text[:10]  # YYYY-MM-DD
        elif tag == "name":
            if text and text not in authors:
                authors.append(text)

    out["ok"] = bool(title)
    out["title"] = title
    out["abstract"] = abstract
    out["authors"] = authors
    out["publication_date"] = pub_date
    out["doi"] = f"10.48550/arXiv.{paper_id}"
    out["url_canonical"] = f"https://arxiv.org/abs/{paper_id}"
    out["text_for_ingest"] = _pack_text(title, authors, abstract)
    return out


# ── CrossRef (DOI lookup — used by Nature, ScienceDirect, Springer, IEEE, etc.) ──

_DOI_URL_RE = re.compile(r"(?:doi\.org/|doi:\s*|\bDOI:\s*)(10\.\d{4,9}/[^\s<>\"']+)", re.IGNORECASE)
_NATURE_DOI_RE = re.compile(
    r"nature\.com/articles/([^/?#]+)",
    re.IGNORECASE,
)
_SPRINGER_DOI_RE = re.compile(
    r"link\.springer\.com/(?:article|chapter|book)/([^?#]+)",
    re.IGNORECASE,
)
_ELSEVIER_PII_RE = re.compile(
    r"sciencedirect\.com/science/article/(?:pii|abs/pii)/([A-Z0-9]+)",
    re.IGNORECASE,
)


def _derive_doi(url: str) -> str | None:
    """Extract a DOI from common URL patterns."""
    # Direct DOI URL
    m = _DOI_URL_RE.search(url)
    if m:
        return m.group(1).rstrip("/.,);")
    # Nature — article slug maps directly to DOI (10.1038/<slug>)
    m = _NATURE_DOI_RE.search(url)
    if m:
        slug = m.group(1).split("?")[0].split("#")[0]
        return f"10.1038/{slug}"
    # Springer — link path contains DOI
    m = _SPRINGER_DOI_RE.search(url)
    if m:
        return m.group(1).split("?")[0].split("#")[0]
    # Elsevier / ScienceDirect — use PII to look up DOI via CrossRef
    # (requires an extra hop; we return the PII as a pseudo-identifier
    # and let the adapter resolve)
    return None


async def _crossref_doi(url: str) -> dict:
    """Look up a publication via CrossRef using a DOI derived from the URL."""
    out = _empty("crossref", url)
    doi = _derive_doi(url)
    if not doi:
        # For ScienceDirect PII URLs, do an extra CrossRef search-by-PII step
        m = _ELSEVIER_PII_RE.search(url)
        if m:
            pii = m.group(1)
            doi = await _crossref_pii_to_doi(pii)
        if not doi:
            out["error"] = "could not derive DOI from URL"
            return out

    import httpx
    api_url = f"https://api.crossref.org/works/{doi}"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(api_url, headers={"User-Agent": _UA})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        out["error"] = f"crossref fetch: {str(e)[:160]}"
        out["doi"] = doi
        return out

    msg = data.get("message", {})
    title = (msg.get("title") or [""])[0]
    abstract = (msg.get("abstract") or "").strip()
    # Strip JATS XML tags from abstract
    abstract = re.sub(r"<[^>]+>", " ", abstract)
    abstract = re.sub(r"\s+", " ", abstract).strip()

    authors: list[str] = []
    for a in (msg.get("author") or [])[:15]:
        given = (a.get("given") or "").strip()
        family = (a.get("family") or "").strip()
        full = f"{given} {family}".strip()
        if full:
            authors.append(full)

    # Publication date: published-print / published-online / issued
    pub_date = ""
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (msg.get(key) or {}).get("date-parts") or []
        if parts and parts[0]:
            y = parts[0][0] if len(parts[0]) >= 1 else None
            m = parts[0][1] if len(parts[0]) >= 2 else 1
            d = parts[0][2] if len(parts[0]) >= 3 else 1
            if y:
                pub_date = f"{y:04d}-{int(m):02d}-{int(d):02d}"
                break

    cite_count = msg.get("is-referenced-by-count")

    out["ok"] = bool(title)
    out["title"] = title
    out["abstract"] = abstract
    out["authors"] = authors
    out["publication_date"] = pub_date
    out["doi"] = doi
    out["citations"] = int(cite_count) if cite_count is not None else None
    out["url_canonical"] = f"https://doi.org/{doi}"
    out["text_for_ingest"] = _pack_text(title, authors, abstract)
    return out


async def _crossref_pii_to_doi(pii: str) -> str | None:
    """Look up a DOI from an Elsevier PII via CrossRef search."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.crossref.org/works",
                params={"query.bibliographic": pii, "rows": 1},
                headers={"User-Agent": _UA},
            )
            if r.status_code != 200:
                return None
            items = (r.json().get("message", {}).get("items") or [])
            if items:
                return items[0].get("DOI")
    except Exception:
        pass
    return None


# ── Nature — via CrossRef (most reliable for this publisher) ──────────────

async def _nature(url: str) -> dict:
    """Nature routes through CrossRef — Nature's own API is paid."""
    return await _crossref_doi(url)


# ── PubMed via NCBI E-utilities ────────────────────────────────────────────

_PUBMED_ID_RE = re.compile(r"pubmed(?:\.ncbi\.nlm\.nih\.gov)?/(\d+)")


async def _pubmed(url: str) -> dict:
    out = _empty("pubmed", url)
    m = _PUBMED_ID_RE.search(url)
    if not m:
        out["error"] = "no pubmed id in URL"
        return out
    pmid = m.group(1)

    import httpx
    # E-utilities esummary returns JSON metadata for a PMID
    esummary = (
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
        f"db=pubmed&id={pmid}&retmode=json&tool=ARIA-DD&email=research@arkmurus.com"
    )
    efetch = (
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
        f"db=pubmed&id={pmid}&rettype=abstract&retmode=text"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(esummary, headers={"User-Agent": _UA})
            r.raise_for_status()
            data = r.json()
            r2 = await client.get(efetch, headers={"User-Agent": _UA})
            abstract_text = r2.text if r2.status_code == 200 else ""
    except Exception as e:
        out["error"] = f"pubmed: {str(e)[:160]}"
        return out

    result = (data.get("result") or {}).get(pmid) or {}
    title = result.get("title") or ""
    authors: list[str] = []
    for a in (result.get("authors") or [])[:15]:
        name = a.get("name") or ""
        if name:
            authors.append(name)
    pub_date = (result.get("pubdate") or "")[:10]
    # abstract from efetch, first non-empty line block after "Abstract"
    abstract = ""
    if abstract_text:
        idx = abstract_text.lower().find("abstract")
        if idx > -1:
            abstract = abstract_text[idx + len("abstract"):].strip()
            # Trim noise before the citations / references section
            for cut in ("\n\nPMID:", "\nPubMed PMID:", "Trial registration"):
                k = abstract.find(cut)
                if k > -1:
                    abstract = abstract[:k]
            abstract = re.sub(r"\s+", " ", abstract).strip()[:4000]

    out["ok"] = bool(title)
    out["title"] = title
    out["abstract"] = abstract
    out["authors"] = authors
    out["publication_date"] = pub_date
    out["doi"] = (result.get("elocationid") or "").replace("doi: ", "").strip()
    out["url_canonical"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    out["text_for_ingest"] = _pack_text(title, authors, abstract)
    return out


# ── OpenAlex — open academic graph ─────────────────────────────────────────

async def _openalex(url: str) -> dict:
    """OpenAlex URLs are structured like openalex.org/W<workid>. Grab
    the work id and hit their API."""
    out = _empty("openalex", url)
    m = re.search(r"openalex\.org/(W\d+)", url)
    if not m:
        out["error"] = "no openalex work id in URL"
        return out
    work_id = m.group(1)

    import httpx
    api_url = f"https://api.openalex.org/works/{work_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                api_url,
                headers={"User-Agent": _UA},
                params={"mailto": "research@arkmurus.com"},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        out["error"] = f"openalex: {str(e)[:160]}"
        return out

    title = data.get("title") or data.get("display_name") or ""
    authors: list[str] = []
    for a in (data.get("authorships") or [])[:15]:
        name = (a.get("author") or {}).get("display_name") or ""
        if name:
            authors.append(name)
    pub_date = data.get("publication_date") or ""
    cite_count = data.get("cited_by_count")
    doi = data.get("doi") or ""
    if doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]
    # OpenAlex "abstract" is an inverted index — reconstruct
    abstract = ""
    abs_idx = data.get("abstract_inverted_index")
    if isinstance(abs_idx, dict):
        positions: list[tuple[int, str]] = []
        for word, poss in abs_idx.items():
            for p in poss:
                positions.append((p, word))
        positions.sort()
        abstract = " ".join(w for _, w in positions)[:4000]

    out["ok"] = bool(title)
    out["title"] = title
    out["abstract"] = abstract
    out["authors"] = authors
    out["publication_date"] = pub_date
    out["doi"] = doi
    out["citations"] = int(cite_count) if cite_count is not None else None
    out["url_canonical"] = f"https://openalex.org/{work_id}"
    out["text_for_ingest"] = _pack_text(title, authors, abstract)
    return out


# ── Dispatcher ─────────────────────────────────────────────────────────────

async def fetch(url: str) -> dict:
    """Main entry point — detect publisher + call the right adapter.

    Returns the canonical shape. Caller can feed `text_for_ingest` into
    rag_store.ingest_document or use the structured fields directly.
    """
    adapter_name = detect_publisher(url)
    if not adapter_name:
        return {
            **_empty("unknown", url),
            "ok": False,
            "error": "not a known publisher — use regular crawler",
        }

    dispatch = {
        "arxiv": _arxiv,
        "nature": _nature,
        "pubmed": _pubmed,
        "crossref_doi": _crossref_doi,
        "openalex": _openalex,
    }
    fn = dispatch.get(adapter_name)
    if not fn:
        return {
            **_empty(adapter_name, url),
            "ok": False,
            "error": f"no handler for adapter {adapter_name}",
        }

    try:
        result = await fn(url)
    except Exception as e:
        logger.warning("[publisher_router] %s adapter raised: %s", adapter_name, e)
        result = {
            **_empty(adapter_name, url),
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }

    # Brain signal — every API-route success/failure teaches the predictor
    # which publishers are reliable + which need scraping fallback.
    try:
        from . import brain_hook as _bh
        title = result.get("title", "")[:80] if result.get("ok") else (result.get("error", "")[:80])
        await _bh.absorb(
            module="known_publisher_router",
            summary=f"Publisher API {adapter_name} ({'ok' if result.get('ok') else 'fail'}): {title}",
            detail=f"URL: {url[:200]} → ok={result.get('ok')} doi={result.get('doi','')}",
            entity_name=result.get("doi", "") or url[:80],
            success=bool(result.get("ok")),
            gap_type=("publisher_adapter_failed" if not result.get("ok") else None),
            gap_detail=(f"{adapter_name} adapter failed for {url[:120]}: {result.get('error','')[:120]}"
                        if not result.get("ok") else None),
            confidence="CONFIRMED" if result.get("ok") else "ASSESSED",
        )
    except Exception:
        pass
    return result


def is_known_publisher(url: str) -> bool:
    """Quick pre-flight check — caller uses this to decide whether to
    call fetch() instead of the regular crawler."""
    return detect_publisher(url) is not None
