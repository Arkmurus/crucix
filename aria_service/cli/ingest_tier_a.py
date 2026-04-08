"""Tier A bulk corpus ingest CLI.

Walks the corpus_registry for tier='A' entries and ingests each one into
ARIA's chromadb via the /api/aria/corpus/ingest endpoint. The companion to
ingest_corpus.py — that one ingests files from a local folder; this one
fetches the source URLs directly per the registry's ingest_strategy.

Why this is separate from ingest_corpus.py
═══════════════════════════════════════════
ingest_corpus.py expects you to have files on disk and walks a folder.
This script reads the registry, fetches each URL via HTTP, dispatches by
ingest_strategy (html_crawl / pdf / sub_index), extracts text, and POSTs
the result. Stdlib only — no extra deps so it runs from any machine.

Strategies handled
══════════════════
  html_crawl  — fetch HTML, strip tags, extract main text
  pdf         — fetch PDF bytes, send as base64 (server has PyMuPDF)
  sub_index   — fetch index page, extract internal links matching the
                same domain, recurse one level with a small cap

Strategies NOT handled (skipped with a note)
════════════════════════════════════════════
  api              — needs custom per-API code; skip
  manual           — needs human upload; skip
  principles_only  — Tier D, never ingested as RAG content; skip
  rss              — could implement, but no Tier A entries use it; skip

Usage
═════
    # Dry run — show what would be ingested
    python -m aria_service.cli.ingest_tier_a --dry-run

    # All Tier A sources
    python -m aria_service.cli.ingest_tier_a \\
        --aria-url https://aria-intel.fly.dev \\
        --token $ARIA_INT_TOKEN

    # Just one publisher (test with a small batch first)
    python -m aria_service.cli.ingest_tier_a \\
        --source-class SIPRI \\
        --aria-url https://aria-intel.fly.dev \\
        --token $ARIA_INT_TOKEN

    # Limit to first N sources
    python -m aria_service.cli.ingest_tier_a --limit 3

    # Skip sources by ingest strategy
    python -m aria_service.cli.ingest_tier_a --strategy html_crawl

Exit codes
══════════
    0 — all sources ingested successfully (or dry-run completed)
    1 — at least one source failed (others may still have succeeded)
    2 — bad arguments / registry empty / unrecoverable error
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

# Make `from aria_service.intel import corpus_registry` work when run as
# `python -m aria_service.cli.ingest_tier_a` from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

# Round 1 (60s) was too short for gov.uk / nato.int / eur-lex / un.org —
# many big government pages take 60-90s to respond. Bumped to 120s.
DEFAULT_TIMEOUT_S = 120
# Real browser UA so we don't get 403'd by sites that block the obvious
# bot string. DIANA NATO and UN SC reject the previous "ARIA-CorpusIngest"
# UA outright. Mozilla compat string is widely accepted.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36 ARIA-CorpusIngest/1.1"
)
SUB_INDEX_DEPTH = 1
SUB_INDEX_MAX_LINKS = 10
INTER_REQUEST_DELAY_S = 1.0

# File extensions we never want to follow as sub-links — these are assets
# (CSS, JS, images, fonts), not content. The first version of the
# sub_index strategy followed every internal link, including favicons and
# CSS bundles, which produced ~15 wasted requests per source.
_ASSET_EXTENSIONS = (
    ".css", ".js", ".ico", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".webp", ".woff", ".woff2", ".eot", ".ttf", ".otf", ".xml", ".rss",
    ".atom", ".map", ".webmanifest", ".json", ".mp4", ".webm", ".mp3",
    ".wav", ".zip", ".tar", ".gz", ".7z",
)
# Path fragments that strongly indicate an asset URL even without an
# obvious extension (some assets are served with hashed query strings).
_ASSET_PATH_FRAGMENTS = (
    "/sites/default/files/css/",
    "/sites/default/files/js/",
    "/sites/default/files/asset_injector/",
    "/assets/frontend/",
    "/wp-content/themes/",
    "/wp-content/plugins/",
    "/_next/static/",
    "/static/css/",
    "/static/js/",
    "/dist/",
    "/build/",
    "/favicon",
)


# ── HTTP helpers ────────────────────────────────────────────────────────

def _http_get(url: str, *, timeout: int = DEFAULT_TIMEOUT_S) -> tuple[int, bytes, str]:
    """Fetch a URL. Returns (status_code, body_bytes, content_type)."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5",
            "Accept-Language": "en",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            return resp.status, resp.read(), content_type
    except urllib.error.HTTPError as e:
        return e.code, b"", ""
    except Exception as e:
        return -1, b"", str(e)[:200]


def _http_post_json(url: str, body: dict, token: str, timeout: int) -> tuple[int, dict | str]:
    """POST a JSON body to the ingest endpoint."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}" if token else "",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body_text or str(e)
    except urllib.error.URLError as e:
        return -1, f"URL error: {e}"
    except Exception as e:
        return -1, f"unexpected error: {e}"


# ── HTML text extraction ────────────────────────────────────────────────

# Strip script, style, and other non-text containers before tag removal so
# their contents don't end up in the extracted text.
_NON_TEXT_TAGS_RE = re.compile(
    r"<(script|style|nav|footer|header|aside|noscript|iframe|svg)\b[^>]*>"
    r".*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_HTML_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&apos;": "'", "&hellip;": "…",
    "&mdash;": "—", "&ndash;": "–", "&ldquo;": '"', "&rdquo;": '"',
    "&lsquo;": "'", "&rsquo;": "'",
}


def _extract_html_text(html_bytes: bytes) -> str:
    """Strip HTML tags and return plain text. Stdlib-only — not as good as
    trafilatura but doesn't require an extra dep."""
    if not html_bytes:
        return ""
    try:
        html = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        return ""
    # Drop script/style/nav blocks first
    html = _NON_TEXT_TAGS_RE.sub(" ", html)
    # Strip remaining tags
    text = _TAG_RE.sub(" ", html)
    # Decode common entities
    for ent, repl in _HTML_ENTITIES.items():
        text = text.replace(ent, repl)
    # Collapse whitespace
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


_LINK_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def _is_asset_url(url: str) -> bool:
    """True if a URL points to an asset file (CSS, JS, image, font, etc).
    Used to filter out garbage sub-links from the sub_index crawler."""
    if not url:
        return True
    u = url.lower()
    # Strip query string and fragment for extension check
    path = u.split("?", 1)[0].split("#", 1)[0]
    if path.endswith(_ASSET_EXTENSIONS):
        return True
    for fragment in _ASSET_PATH_FRAGMENTS:
        if fragment in u:
            return True
    return False


def _extract_internal_links(html_bytes: bytes, base_url: str, max_links: int) -> list[str]:
    """Find href= attributes pointing to the same domain as base_url.
    Returns absolute URLs, deduped, capped at max_links. Skips asset URLs
    (CSS, JS, favicons, images, fonts) — those are not content."""
    if not html_bytes:
        return []
    try:
        html = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        return []
    base_parsed = urllib.parse.urlparse(base_url)
    base_host = base_parsed.netloc.lower()
    seen: set[str] = set()
    out: list[str] = []
    for m in _LINK_RE.finditer(html):
        href = m.group(1).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        # Resolve relative URLs against the base
        absolute = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(absolute)
        if parsed.netloc.lower() != base_host:
            continue
        # Strip fragment for dedup
        absolute = absolute.split("#", 1)[0]
        if absolute == base_url or absolute in seen:
            continue
        # Skip assets — they're not content
        if _is_asset_url(absolute):
            continue
        seen.add(absolute)
        out.append(absolute)
        if len(out) >= max_links:
            break
    return out


# ── Per-strategy ingest ─────────────────────────────────────────────────

def _ingest_via_endpoint(
    *,
    aria_url: str,
    token: str,
    timeout: int,
    text: str,
    filename: str,
    source,  # Source dataclass
    extra_notes: str = "",
) -> tuple[bool, str]:
    """POST extracted text to /api/aria/corpus/ingest with the source's
    tier metadata. Returns (success, message)."""
    if not text or len(text.strip()) < 200:
        return False, f"text too short ({len(text)} chars)"
    body = {
        "filename": filename,
        "text": text,
        "tier": source.tier,
        "source_class": source.source_class,
        "region": source.region,
        "cplp_relevant": source.cplp_relevant,
        "publication_date": "",
        "notes": (source.notes + (" — " + extra_notes if extra_notes else ""))[:300],
    }
    endpoint = aria_url.rstrip("/") + "/api/aria/corpus/ingest"
    status, payload = _http_post_json(endpoint, body, token, timeout)
    if status == 200 and isinstance(payload, dict) and payload.get("ingested"):
        chunks = payload.get("chunks", "?")
        return True, f"ingested {chunks} chunks"
    return False, f"HTTP {status}: {str(payload)[:200]}"


def _process_html_crawl(source, *, aria_url: str, token: str, timeout: int) -> list[tuple[bool, str]]:
    """html_crawl: fetch one URL, extract text, ingest as one document."""
    status, body, ctype = _http_get(source.url, timeout=timeout)
    if status != 200:
        return [(False, f"fetch failed: HTTP {status}")]
    text = _extract_html_text(body)
    if not text or len(text) < 200:
        return [(False, f"no usable text extracted ({len(text)} chars)")]
    fname = f"{source.source_class.replace(' ', '_')}_{urllib.parse.urlparse(source.url).path.replace('/', '_').strip('_') or 'index'}.html.txt"
    return [_ingest_via_endpoint(
        aria_url=aria_url, token=token, timeout=timeout,
        text=text, filename=fname[:200], source=source,
    )]


def _process_pdf(source, *, aria_url: str, token: str, timeout: int) -> list[tuple[bool, str]]:
    """pdf: fetch PDF bytes, send as base64 — server-side PyMuPDF extracts."""
    status, body, ctype = _http_get(source.url, timeout=timeout)
    if status != 200 or not body:
        return [(False, f"fetch failed: HTTP {status}")]
    # Build the binary-content body explicitly so the endpoint dispatches
    # to the corpus_ingest.extract_text_from_bytes path.
    fname = (urllib.parse.urlparse(source.url).path.split("/")[-1] or "document.pdf")
    if not fname.lower().endswith(".pdf"):
        fname += ".pdf"
    request_body = {
        "filename": fname,
        "content_b64": base64.b64encode(body).decode("ascii"),
        "mimetype": "application/pdf",
        "tier": source.tier,
        "source_class": source.source_class,
        "region": source.region,
        "cplp_relevant": source.cplp_relevant,
        "notes": source.notes[:300],
    }
    endpoint = aria_url.rstrip("/") + "/api/aria/corpus/ingest"
    status, payload = _http_post_json(endpoint, request_body, token, timeout)
    if status == 200 and isinstance(payload, dict) and payload.get("ingested"):
        chunks = payload.get("chunks", "?")
        return [(True, f"ingested {chunks} chunks (PDF)")]
    return [(False, f"HTTP {status}: {str(payload)[:200]}")]


def _process_sub_index(source, *, aria_url: str, token: str, timeout: int) -> list[tuple[bool, str]]:
    """sub_index: fetch the index page, then crawl up to N internal links."""
    results: list[tuple[bool, str]] = []
    status, body, ctype = _http_get(source.url, timeout=timeout)
    if status != 200:
        return [(False, f"index fetch failed: HTTP {status}")]
    # Ingest the index page itself
    text = _extract_html_text(body)
    if text and len(text) >= 200:
        fname = f"{source.source_class.replace(' ', '_')}_index.html.txt"
        results.append(_ingest_via_endpoint(
            aria_url=aria_url, token=token, timeout=timeout,
            text=text, filename=fname[:200], source=source,
            extra_notes="index page",
        ))
    # Then crawl child links
    links = _extract_internal_links(body, source.url, SUB_INDEX_MAX_LINKS)
    for i, link in enumerate(links, 1):
        time.sleep(INTER_REQUEST_DELAY_S)
        sub_status, sub_body, sub_ctype = _http_get(link, timeout=timeout)
        if sub_status != 200:
            results.append((False, f"sub link {i} HTTP {sub_status}: {link}"))
            continue
        # Dispatch sub-link by content type
        if "pdf" in sub_ctype.lower():
            fname = (urllib.parse.urlparse(link).path.split("/")[-1] or f"sub_{i}.pdf")
            if not fname.lower().endswith(".pdf"):
                fname += ".pdf"
            request_body = {
                "filename": fname,
                "content_b64": base64.b64encode(sub_body).decode("ascii"),
                "mimetype": "application/pdf",
                "tier": source.tier,
                "source_class": source.source_class,
                "region": source.region,
                "cplp_relevant": source.cplp_relevant,
                "notes": (source.notes + " — sub link from index")[:300],
            }
            endpoint = aria_url.rstrip("/") + "/api/aria/corpus/ingest"
            sstatus, spayload = _http_post_json(endpoint, request_body, token, timeout)
            if sstatus == 200 and isinstance(spayload, dict) and spayload.get("ingested"):
                results.append((True, f"sub {i}: PDF {spayload.get('chunks', '?')} chunks ({link[:80]})"))
            else:
                results.append((False, f"sub {i}: HTTP {sstatus} ({link[:80]})"))
        else:
            sub_text = _extract_html_text(sub_body)
            if sub_text and len(sub_text) >= 200:
                fname = f"{source.source_class.replace(' ', '_')}_sub_{i}.html.txt"
                ok, msg = _ingest_via_endpoint(
                    aria_url=aria_url, token=token, timeout=timeout,
                    text=sub_text, filename=fname[:200], source=source,
                    extra_notes=f"sub link {i} from index: {link}",
                )
                results.append((ok, f"sub {i}: {msg} ({link[:80]})"))
            else:
                results.append((False, f"sub {i}: no text ({link[:80]})"))
    return results


_SKIP_STRATEGIES = {"api", "manual", "principles_only", "rss"}

_STRATEGY_HANDLERS = {
    "html_crawl": _process_html_crawl,
    "pdf": _process_pdf,
    "sub_index": _process_sub_index,
}


# ── Main ────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m aria_service.cli.ingest_tier_a",
        description="Walk the corpus registry for tier=A sources and ingest each via /api/aria/corpus/ingest.",
    )
    p.add_argument("--tier", default="A", help="Tier to ingest (default A — also supports B, B+, C, C+, E)")
    p.add_argument("--source-class", default=None, help="Limit to one publisher (e.g. SIPRI)")
    p.add_argument("--strategy", default=None, choices=sorted(_STRATEGY_HANDLERS.keys()),
                   help="Limit to one ingest strategy")
    p.add_argument("--limit", type=int, default=None, help="Cap the number of sources to process")
    p.add_argument("--cplp-only", action="store_true", help="Only sources tagged cplp_relevant=true")
    p.add_argument("--dry-run", action="store_true", help="List what would be ingested without sending")
    p.add_argument("--aria-url", default=os.getenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev"),
                   help="Base URL of the ARIA service (default ARIA_SERVICE_URL or https://aria-intel.fly.dev)")
    p.add_argument("--token", default=os.getenv("ARIA_API_TOKEN", "") or os.getenv("ARIA_INT_TOKEN", ""),
                   help="Bearer token sent as Authorization header (default ARIA_API_TOKEN, then ARIA_INT_TOKEN)")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="Per-request timeout in seconds")
    args = p.parse_args(argv)

    # Load registry
    try:
        from aria_service.intel import corpus_registry
    except Exception as e:
        print(f"ERROR: cannot import corpus_registry: {e}", file=sys.stderr)
        return 2
    sources = corpus_registry.get_sources(
        tier=args.tier,
        source_class=args.source_class,
        cplp_only=args.cplp_only,
        ingest_strategy=args.strategy,
    )
    if not sources:
        print(f"ERROR: no sources match the filter (tier={args.tier} source_class={args.source_class})", file=sys.stderr)
        return 2

    if args.limit is not None:
        sources = sources[: args.limit]

    print(f"Found {len(sources)} source(s) matching filters")
    print(f"Target: {args.aria_url}/api/aria/corpus/ingest")
    print()

    if args.dry_run:
        for i, s in enumerate(sources, 1):
            mark = " [SKIP]" if s.ingest_strategy in _SKIP_STRATEGIES else ""
            print(f"  [{i}/{len(sources)}] {s.source_class:25} {s.ingest_strategy:12} {s.url}{mark}")
        return 0

    # Real run
    n_ok = 0
    n_fail = 0
    n_skip = 0
    t_start = time.time()
    for i, s in enumerate(sources, 1):
        prefix = f"[{i}/{len(sources)}] {s.source_class}"
        if s.ingest_strategy in _SKIP_STRATEGIES:
            print(f"{prefix}: SKIP ({s.ingest_strategy} — handled separately)")
            n_skip += 1
            continue
        handler = _STRATEGY_HANDLERS.get(s.ingest_strategy)
        if not handler:
            print(f"{prefix}: SKIP (unknown strategy {s.ingest_strategy!r})")
            n_skip += 1
            continue
        print(f"{prefix}: fetching {s.url}")
        try:
            results = handler(s, aria_url=args.aria_url, token=args.token, timeout=args.timeout)
        except Exception as e:
            print(f"  ERROR: handler crashed: {e}")
            n_fail += 1
            continue
        for ok, msg in results:
            if ok:
                n_ok += 1
                print(f"  OK — {msg}")
            else:
                n_fail += 1
                print(f"  FAIL — {msg}")
        # Inter-source delay to be polite to remote servers
        time.sleep(INTER_REQUEST_DELAY_S)

    elapsed = time.time() - t_start
    print()
    print(f"Done. {n_ok} chunks ingested, {n_fail} failed, {n_skip} skipped, in {elapsed:.1f}s")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
