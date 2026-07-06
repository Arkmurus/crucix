"""indexer — sanitise + dedup + upsert fetched pages into the search index.

R-F502 (2026-05-14). Sits between `crawler.fetcher` (which produced a
raw fetch result) and `db.upsert_document` (which writes the row +
triggers FTS5 sync).

Responsibilities:
  1. Sanitise the body — collapse whitespace, drop NUL / C0 control
     chars, cap absolute size to keep one document from monopolising
     the index (max 200 KB body).
  2. Detect language (heuristic, zero-dep): when the seed_list-declared
     language is None or contradicts a confident heuristic guess, take
     the heuristic. Otherwise trust the seed metadata.
  3. Drop content too thin to be useful — anything <20 words or <100
     chars is almost certainly a redirect / login wall / 404 dressed
     up as 200.
  4. Idempotent on content: if the (canonical_url, content_sha256)
     pair matches an existing row, skip the upsert.
  5. Upsert into documents → FTS5 sync triggers fire automatically.

Public:
    async def index_fetch_result(result: dict) -> str
        Returns the doc_id on success, "" when skipped.

    async def index_many(results: Iterable[dict]) -> dict
        Bulk-index with a summary {indexed, skipped, errors}.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from typing import Iterable

from . import db

logger = logging.getLogger("aria.search_index.indexer")


_MIN_WORDS = 20
_MIN_CHARS = 100
_MAX_BODY_BYTES = 200_000  # 200 KB cap per document


# ─────────────────────────────────────────────────────────────────
# Sanitisation
# ─────────────────────────────────────────────────────────────────

# Drop C0 control chars except \t \n \r; drop C1 controls; collapse
# whitespace; strip zero-width characters that hide injected text.
_CONTROL_RX = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]")
_ZWS_RX = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_MULTI_WS_RX = re.compile(r"[ \t]{2,}")
_MULTI_NL_RX = re.compile(r"\n{3,}")


def _sanitise(text: str) -> str:
    if not text:
        return ""
    # NFC normalise so visually-identical sequences hash the same.
    try:
        text = unicodedata.normalize("NFC", text)
    except Exception:
        pass
    text = _CONTROL_RX.sub("", text)
    text = _ZWS_RX.sub("", text)
    text = _MULTI_WS_RX.sub(" ", text)
    text = _MULTI_NL_RX.sub("\n\n", text)
    text = text.strip()
    # Cap absolute size — large pages get truncated, not refused. Keep
    # the head (titles, lede, first sections) over the tail.
    if len(text.encode("utf-8", errors="replace")) > _MAX_BODY_BYTES:
        # Walk back to a clean newline boundary near the byte cap.
        cap = _MAX_BODY_BYTES
        truncated = text.encode("utf-8", errors="replace")[:cap].decode(
            "utf-8", errors="ignore"
        )
        nl = truncated.rfind("\n")
        if nl > cap // 2:
            truncated = truncated[:nl]
        text = truncated + "\n\n[…truncated for index size cap…]"
    return text


# ─────────────────────────────────────────────────────────────────
# Heuristic language detection — zero external deps.
#
# Scores each candidate by counting occurrences of a small stoplist of
# very common short words / function words. We aren't trying to win
# polyglot benchmarks — we're trying to distinguish English news from
# Portuguese news from French / Spanish / Arabic / Russian / German /
# Italian / Turkish content well enough that FTS5 lang-filter works.
# ─────────────────────────────────────────────────────────────────

_LANG_STOPWORDS = {
    "en": {"the", "and", "of", "to", "in", "is", "that", "for", "on",
           "with", "as", "by", "this", "from", "was", "are", "an"},
    "pt": {"o", "a", "e", "de", "do", "da", "para", "com", "que", "em",
           "no", "na", "um", "uma", "os", "as", "por", "como", "mas"},
    "es": {"el", "la", "y", "de", "que", "en", "los", "las", "para",
           "con", "por", "del", "un", "una", "se", "es", "como"},
    "fr": {"le", "la", "les", "de", "du", "des", "et", "que", "qui",
           "dans", "pour", "avec", "sur", "par", "ce", "une", "est"},
    "it": {"il", "la", "lo", "di", "che", "e", "per", "con", "del",
           "della", "una", "un", "non", "si", "in"},
    "de": {"der", "die", "das", "und", "ist", "von", "den", "im", "mit",
           "auf", "für", "ein", "eine", "nicht", "sich", "auch"},
    "ru": {"и", "в", "не", "на", "что", "с", "по", "это", "как", "из",
           "от", "у", "за", "к", "о", "но", "же"},
    "ar": {"في", "من", "على", "أن", "إلى", "عن", "مع", "هذا", "هذه",
           "كان", "قال", "ما", "لا"},
    "tr": {"ve", "bir", "bu", "için", "ile", "olarak", "ama", "her",
           "çok", "değil", "şey"},
    "zh": set(),  # CJK doesn't tokenise on whitespace — handled separately
}


def detect_language(text: str, default: str | None = None) -> str | None:
    """Best-effort language code. Returns `default` when uncertain.

    Method: count stopword hits in the first 4000 chars; pick the
    language with the largest count, but require it to win by at least
    2× the runner-up AND to have ≥ 5 stopword hits. Otherwise return
    default. This is intentionally conservative — we prefer 'no guess'
    over wrong guess when the page is short or mixed-language."""
    if not text:
        return default
    sample = text[:4000].lower()
    # CJK heuristic: any CJK ideograph in the sample → 'zh'.
    if any("一" <= ch <= "鿿" for ch in sample[:500]):
        return "zh"
    tokens = re.findall(r"[\wЀ-ӿ؀-ۿ]+", sample,
                        flags=re.UNICODE)
    if len(tokens) < 30:
        return default
    scores: dict[str, int] = {}
    for lang, stops in _LANG_STOPWORDS.items():
        if not stops:
            continue
        scores[lang] = sum(1 for t in tokens if t in stops)
    if not scores:
        return default
    best = max(scores.items(), key=lambda kv: kv[1])
    runner_up = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0
    if best[1] < 5:
        return default
    if best[1] < runner_up * 2:
        return default
    return best[0]


# ─────────────────────────────────────────────────────────────────
# Indexing
# ─────────────────────────────────────────────────────────────────

def _content_sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()


async def index_fetch_result(result: dict) -> str:
    """Sanitise + dedup + upsert one fetched page.

    Returns the doc_id on success, "" when skipped (thin content,
    invalid URL, content unchanged since last index, or DB unready).
    """
    if not result:
        return ""
    url = result.get("url") or ""
    domain = result.get("domain") or ""
    if not url or not domain:
        return ""

    body = _sanitise(result.get("body") or "")
    title = _sanitise(result.get("title") or "")[:1000]
    headings = _sanitise(result.get("headings") or "")[:4000]

    # Thin-content guard.
    word_count = len(body.split()) if body else 0
    if word_count < _MIN_WORDS or len(body) < _MIN_CHARS:
        logger.debug("indexer: skipping thin content for %s (%d words, %d chars)",
                     url[:120], word_count, len(body))
        return ""

    # Language: trust the seed-declared one unless the heuristic gives
    # a confident contradicting answer.
    seed_lang = result.get("language")
    detected = detect_language(body, default=seed_lang)
    if seed_lang and detected and detected != seed_lang:
        # Only override on confident detection. detect_language only
        # returns a non-default on confident win.
        logger.info("indexer: lang override for %s — seed=%s, detected=%s",
                    domain, seed_lang, detected)
    language = detected or seed_lang

    # Idempotent on (canonical_url, sha256): if the page hasn't changed
    # since the last index, skip the write — saves WAL traffic.
    sha = _content_sha(body)
    canonical = result.get("canonical_url") or db._canonicalize(url)
    existing = await db.get_document(doc_id=db._doc_id(canonical))
    if existing and existing.get("content_sha256") == sha:
        logger.debug("indexer: content unchanged for %s, skipping upsert",
                     url[:120])
        return existing["doc_id"]

    doc_id = await db.upsert_document(
        url=url,
        domain=domain,
        title=title,
        headings=headings,
        body=body,
        language=language,
        source_tier=result.get("source_tier"),
        http_status=result.get("http_status") or 200,
        fetched_at=result.get("fetched_at"),
        canonical_url=canonical,
    )
    return doc_id


async def index_many(results: Iterable[dict]) -> dict:
    """Bulk index. Returns {indexed, skipped, errors}."""
    indexed = skipped = errors = 0
    for r in results:
        try:
            d = await index_fetch_result(r)
            if d:
                indexed += 1
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            logger.warning("indexer.index_many: %s", e)
    return {"indexed": indexed, "skipped": skipped, "errors": errors}
