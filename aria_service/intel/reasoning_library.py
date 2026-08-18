"""
ARIA Reasoning Library — case-based reasoning store.

This is the seed of ARIA's independent reasoning capacity. Every time her
brain (currently DeepSeek) successfully answers a question, the entire
(question, context, response, confidence, outcome) tuple is captured here.
The next time a similar question arrives, ARIA looks it up locally FIRST —
if there is a high-similarity prior with confirmed positive outcome, she
serves that answer without calling out to any LLM at all.

Architecture
════════════
The library is a Redis-backed store of "cases":

    case = {
        id, question, normalised_question, response,
        confidence_tag,             # CONFIRMED / PROBABLE / ASSESSED / etc
        confidence_score,           # 0..1
        intent,                     # screen, conflict, profile, ...
        context_keys,               # list of intel layers that mattered
        embedding,                  # semantic vector (sentence-transformers)
        access_count,
        positive_outcomes,          # times user accepted this
        negative_outcomes,          # times user corrected this
        ts_created, ts_last_used,
        source_brain,               # deepseek / anthropic / ollama / symbolic
    }

How retrieval works
═══════════════════
1. Normalise the incoming query (lowercase, strip stopwords, sort tokens)
2. Lexical first pass — match against normalised_question (fast)
3. Semantic second pass — cosine similarity against embeddings (precise)
4. Combine the two scores with a confidence multiplier
5. Apply a recency + outcome quality boost
6. Return the top match if above threshold (default 0.78)

How it grows
════════════
- Every successful chat call writes a case (via record_response).
- User corrections decrement positive_outcomes and increment negative.
- Stale cases (>180 days, low access) get pruned in consolidation.
- A "promotion" pass moves high-quality cases to a permanent layer.

Independence trajectory
═══════════════════════
On day 1: every query hits DeepSeek. 0% local.
After 100 conversations: ~15% of queries match a prior case → 15% local.
After 1000 conversations: ~40% local.
After 10000 conversations + symbolic reasoner: ~70% local.

The library is the engine of ARIA's slow detachment from cloud reasoning.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import asyncio
import json
import logging
import math
import re
import time
import uuid
from typing import Any, Optional

from . import redis_store as rs
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.reasoning_library")

LIBRARY_KEY = "crucix:aria:reasoning_library"
INDEX_KEY = "crucix:aria:reasoning_library:index"
META_KEY = "crucix:aria:reasoning_library:meta"

MAX_CASES = 50_000
# 2026-04-08 round 3: raised from 0.78 to 0.85 after the case library was
# replaying correction-acknowledgement replies on unrelated queries (Antonio
# asked for "Ghana opportunity summary" and got back the apology about the
# wrong defence minister, repeatedly). Combined with the recency-boost
# neutralisation in _recency_multiplier and the correction-filter in
# record_response, this should stop the over-eager replay.
DEFAULT_MATCH_THRESHOLD = 0.85
RETENTION_DAYS = 36500  # 100 years — permanent; was 180d before 2026-04-21
TTL_SECONDS = RETENTION_DAYS * 86400

# A normalised question must have at least this many salient tokens before
# it can be cached or matched. Reason: short greetings like "are you online?"
# normalise down to 1 token ("online") because aria/are/you are stopwords,
# and that 1 token then collides with any other case whose normalised string
# happens to also be "online" — exact-normalised match gives score ≥ 0.95
# and the cached answer is returned regardless of intent. Incident
# 2026-04-08: every "Aria are you online?" returned the same 4339-char
# Angola briefing 9× because it had been miscached against "online".
MIN_SALIENT_TOKENS = 3

# Hard-deny list for questions that should NEVER hit the case library, even
# if they happen to have ≥ MIN_SALIENT_TOKENS. These are identity / liveness
# / greeting probes whose answer is context-dependent and not cacheable.
_TRIVIAL_QUESTION_RE = re.compile(
    r"^\s*(aria[,\s!]*)?("
    r"are\s+you\s+(online|there|alive|awake|working|up|ready|here)"
    r"|you\s+(online|there|alive|awake)"
    r"|hello|hi|hey|good\s+(morning|afternoon|evening|night)"
    r"|who\s+are\s+you|what\s+are\s+you|what(?:'s| is)\s+your\s+name"
    r"|test|ping|status|ok\??|yes\??|no\??|thanks?|thank\s+you"
    r")\s*[?.!]*\s*$",
    re.IGNORECASE,
)


def _is_trivial_question(q: str) -> bool:
    """True if the question is a greeting / liveness probe / identity check
    whose answer should never be served from the case library."""
    if not q:
        return True
    return bool(_TRIVIAL_QUESTION_RE.match(q.strip()))


@fail_wire(module="reasoning_library", gap_type="engine_failure")
def trivial_reply(q: str) -> str | None:
    """Return a fixed reply for a trivial question (greeting / liveness /
    identity probe), or None if the question isn't trivial. Used by the
    chat handler to short-circuit BEFORE the LLM round-trip — these
    questions don't deserve a tool-use orchestration that fetches URLs
    out of recent group context (incident 2026-04-08: 'are you online?'
    triggered a deepseek call → website read → second deepseek call →
    connectivity failure → no reply at all).

    Categories handled:
      - liveness   ('are you online', 'you there', 'are you alive', ...)
      - greeting   ('hello', 'hi', 'good morning', ...)
      - identity   ('who are you', "what's your name", ...)
      - probe      ('test', 'ping', 'status', 'ok?')
      - thanks     ('thanks', 'thank you')
    """
    if not q:
        return None
    s = q.strip().lower().rstrip("?.! ")
    # Strip an optional leading "aria" or "aria,"
    s = re.sub(r"^aria[,!\s]*", "", s).strip()
    if not s:
        return None

    if re.match(r"^(are\s+you\s+(online|there|alive|awake|working|up|ready|here)|you\s+(online|there|alive|awake))$", s):
        return "✅ Yes, I'm online and ready. Send me a question, drop a document or image, or use /help for commands."
    if re.match(r"^(hello|hi|hey|good\s+(morning|afternoon|evening|night))$", s):
        return "Hi — ARIA here. Ask me anything about compliance, defence procurement, or market intel. /help shows the full command list."
    if re.match(r"^(who\s+are\s+you|what\s+are\s+you|what(?:'s| is)\s+your\s+name)$", s):
        return ("I'm ARIA — Arkmurus Research Intelligence Agent. I do compliance screening "
                "(sanctions, export controls, country risk), defence procurement intel, and "
                "market/competitor research. Run /help for the full menu.")
    if re.match(r"^(test|ping|status)$", s):
        return "✅ Pong. Service is up. /help for commands."
    if re.match(r"^(ok|yes|no)$", s):
        return "👍"
    if re.match(r"^(thanks?|thank\s+you)$", s):
        return "You're welcome."
    # Background/status meta probes — added 2026-04-08. Keep in sync with the
    # 3 JS copies (waListener.mjs, server.mjs, lib/aria/aria.mjs).
    if (re.match(r"^(can\s+you\s+(confirm|tell\s+me|verify)\s+)?(you('?re|\s+are)?\s+|are\s+you\s+)?(still|actually|really)?\s*(working|processing|running|on\s+it|there|alive)(\s+on\s+(it|that|this))?(\s+in\s+the\s+background)?$", s)
        or re.match(r"^(still|actually)\s+(working|on\s+it|there)(\s+on\s+(it|that|this))?(\s+in\s+the\s+background)?$", s)
        or re.match(r"^did\s+you\s+(get|hear|see)\s+(that|me|it|my\s+(message|question))$", s)):
        return ("✅ I'm here. I don't persist long-running tasks across messages — "
                "if your last question is still pending after ~60s, please re-send it "
                "and I'll work on it now.")
    return None

# Common English/intelligence stopwords stripped during normalisation
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "as", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "this", "that", "these", "those", "it", "its",
    "what", "when", "where", "why", "how", "who", "which", "i", "you", "he", "she",
    "we", "they", "them", "us", "my", "your", "our", "their", "tell", "me", "about",
    "please", "aria", "show", "give",
})

# Domain-priority terms that should always survive normalisation
_DOMAIN_TOKENS = frozenset({
    "angola", "mozambique", "cplp", "lusophone", "fadm", "faa", "simportex",
    "sitcl", "ofac", "ofsi", "itar", "ear", "eccn", "ml1", "ml2", "ml3", "ml4",
    "ml6", "ml9", "ml10", "ml11", "ml13", "ml15", "k9", "himars", "patriot",
    "bayraktar", "tb2", "rafale", "f-35", "f-16", "leopard", "abrams",
})

_meta_cache: dict | None = None
_index_cache: list[dict] | None = None  # in-memory mirror of the index

# R-F268 (2026-05-11) — no-scaffold-write rule. Meta is scaffolded with
# zero-counter defaults; without the dirty flag, those defaults could
# overwrite the destination backend across a flip. Same pattern as R-F267.
_meta_dirty: bool = False


def _mark_meta_dirty() -> None:
    global _meta_dirty
    _meta_dirty = True


# ── Normalisation ───────────────────────────────────────────────────────────

def _normalise_question(q: str) -> str:
    """Strip stopwords, lowercase, sort tokens. Cheap lexical-match key."""
    if not q:
        return ""
    text = re.sub(r"[^\w\s]", " ", q.lower())
    tokens = [t for t in text.split() if t and len(t) > 1]
    kept = [
        t for t in tokens
        if t in _DOMAIN_TOKENS or (t not in _STOPWORDS and len(t) > 2)
    ]
    return " ".join(sorted(set(kept)))


def _token_set(q: str) -> set[str]:
    return set(_normalise_question(q).split())


# R-F518 (2026-05-14) — entity-overlap gate for non-exact matches.
# Live failure 2026-05-14 11:00 BST: probed /chat with "Is Bumblestaff
# Industries sanctioned?" — reasoning_library semantically matched at
# 0.912 against a cached "Is Hikvision sanctioned in the UK?" answer
# and served the Hikvision response verbatim. Question-shape similarity
# beats entity identity in the embedding space, so every sanctions-
# shaped question was being served the same cached answer regardless of
# the entity asked about. Severe honesty bug — wrong-entity factual
# answers shipping live.
#
# Fix: before accepting a non-exact match (semantic / lexical_jaccard),
# require at least ONE shared content token after stripping shared
# sanctions/jurisdiction/compliance jargon. The remaining tokens are
# overwhelmingly entity names (Hikvision / Bumblestaff / Acme) — if
# they don't overlap, the cached answer is about a different subject
# and must not be served.
#
# Exact-normalised matches are unaffected — they already require full
# text equality, which pins the entity.
_DOMAIN_JARGON = frozenset({
    # sanctions / compliance verbs and nouns
    "sanction", "sanctioned", "sanctions", "designate", "designated",
    "designation", "list", "listed", "lists", "consolidated", "blacklist",
    "blocked", "blocking", "embargo", "embargoed", "screen", "screened",
    "screening", "check", "checked", "checking", "compliance", "watchlist",
    "freeze", "frozen", "asset", "assets", "restriction", "restrictions",
    "restricted", "exclude", "excluded", "exclusion", "exclusions",
    "ban", "banned", "denied", "debarment", "debarred", "violation",
    # Sanctions list names / regulators (covered by global token check too)
    "sdn", "ofac", "ofsi", "fcdo", "eu", "uk", "us", "usa", "un", "unsc",
    "seco", "dfat", "mofa", "bis", "ddtc",
    # Common interrogative + jurisdictional fillers that survive stopword
    # stripping
    "country", "regime", "jurisdiction", "financial", "export", "import",
    "trade", "procurement", "regulation", "regulations", "regulatory",
    "entity", "person", "company",
})


def _entity_tokens(normalised: str) -> set[str]:
    """Return tokens left after stripping shared sanctions/jurisdiction
    jargon. What remains is dominated by entity names (Hikvision /
    Bumblestaff / Acme Industries). Two queries with empty intersection
    on this set are about different subjects, even if their semantic
    embeddings cluster together.
    """
    if not normalised:
        return set()
    return set(normalised.split()) - _DOMAIN_JARGON


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


# ── Optional embedding-based similarity ─────────────────────────────────────
# Uses the existing sentence-transformers infrastructure (already in
# requirements.txt). If unavailable, falls back to pure Jaccard.

_embedder = None
_embedder_failed = False

def _get_embedder():
    global _embedder, _embedder_failed
    if _embedder is not None:
        return _embedder
    if _embedder_failed:
        return None
    try:
        # semantic_search exposes _get_embedder() — same name, same purpose.
        from .semantic_search import _get_embedder as _semantic_get_embedder
        _embedder = _semantic_get_embedder()
        return _embedder
    except Exception as e:
        logger.debug("reasoning_library: embedder unavailable, using lexical only: %s", e)
        _embedder_failed = True
        return None


def _embed(text: str) -> list[float] | None:
    embedder = _get_embedder()
    if embedder is None:
        return None
    try:
        v = embedder.encode([text], normalize_embeddings=True)
        return v[0].tolist() if hasattr(v[0], "tolist") else list(v[0])
    except Exception as e:
        logger.debug("reasoning_library: embed failed: %s", e)
        return None


async def _embed_async(text: str) -> list[float] | None:
    """Async wrapper around _embed — runs the sync model.encode() in a worker
    thread so the FastAPI event loop is free during the GIL-blocking C call.
    Use this from any async path (find_match, add_case). Past incident
    2026-04-08: sync _embed() on the event loop starved the chat handler."""
    # R-F4143 (C-172) — this used to pre-check `_get_embedder() is not None`
    # HERE, on the event loop, before offloading. That pre-check is what the
    # docstring above was trying to protect against: `_get_embedder` does a cold
    # `import torch`, `from sentence_transformers import SentenceTransformer`
    # (which imports transformers) and then LOADS the model. A 5.17s wedge dump
    # caught the loop thread mid-`import transformers`, inside
    # `importlib.metadata.packages_distributions()` walking every installed
    # distribution's metadata.
    #
    # It was also redundant: `_embed` performs the identical None check on the
    # first line of its own body, and that body already runs in the worker
    # thread. Deleting the pre-check removes the loop block without changing a
    # single observable outcome — a None embedder still yields None.
    import asyncio
    return await asyncio.to_thread(_embed, text)


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    # Already normalised by sentence-transformers, but be safe
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── Storage helpers ─────────────────────────────────────────────────────────

async def _load_index() -> list[dict]:
    """Load the lightweight index (without full responses) into memory."""
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    raw = await rs.get_json(INDEX_KEY)
    _index_cache = raw if isinstance(raw, list) else []
    return _index_cache


async def _save_index() -> None:
    global _index_cache
    if _index_cache is None:
        return
    # R-F374 (2026-05-12) — infinite-memory regression guard. Same pattern
    # as R-F371 neural_memory. The reasoning-library index can grow to
    # 50,000 cases (MAX_CASES). If disk has materially more entries than
    # in-memory cache (e.g., Upstash→SQLite migration just landed a bigger
    # snapshot), overwriting would silently lose entries. Reload from disk
    # and skip the write.
    try:
        disk_index = await rs.get_json(INDEX_KEY)
        if isinstance(disk_index, list) and len(disk_index) > max(len(_index_cache), 0) * 1.1:
            _index_cache = disk_index
            logger.warning(
                "[reasoning_library] R-F374 — disk had %d cases but in-memory "
                "had %d. Reloaded from disk (infinite-memory protection).",
                len(disk_index), len(_index_cache or []),
            )
            return
    except Exception as _guard_err:
        logger.debug("[reasoning_library] R-F374 disk-check failed (non-fatal): %s", _guard_err)

    await rs.set_json(INDEX_KEY, _index_cache, ex=TTL_SECONDS)


async def _load_meta() -> dict:
    global _meta_cache
    if _meta_cache is not None:
        return _meta_cache
    raw = await rs.get_json(META_KEY)
    _meta_cache = raw if isinstance(raw, dict) else {
        "total_cases": 0,
        "total_lookups": 0,
        "total_hits": 0,
        "total_writes": 0,
        "born": time.time(),
    }
    return _meta_cache


async def _save_meta() -> None:
    """R-F268: persist only when meta has been touched by actual writes/lookups
    since the last load. Scaffold-only caches are kept in memory but not
    flushed to the active backend, preventing default-overwrite across flips."""
    global _meta_dirty
    if _meta_cache is None or not _meta_dirty:
        return
    await rs.set_json(META_KEY, _meta_cache, ex=TTL_SECONDS)
    _meta_dirty = False


def _case_key(case_id: str) -> str:
    return f"{LIBRARY_KEY}:{case_id}"


# ── Public API: writing cases ───────────────────────────────────────────────

_CONFIDENCE_RANK = {
    "CONFIRMED": 1.0,
    "PROBABLE":  0.75,
    "ASSESSED":  0.55,
    "UNCERTAIN": 0.35,
    "SPECULATIVE": 0.20,
}

def _detect_confidence_tag(response: str) -> tuple[str, float]:
    """Pick the highest confidence tag present in the response text."""
    if not response:
        return "ASSESSED", 0.55
    upper = response.upper()
    for tag in ("CONFIRMED", "PROBABLE", "ASSESSED", "UNCERTAIN", "SPECULATIVE"):
        if f"[{tag}]" in upper:
            return tag, _CONFIDENCE_RANK[tag]
    return "ASSESSED", 0.55


# ── Anti-replay filters (2026-04-08 round 3) ─────────────────────────────────
# These three predicates protect the case library from being polluted by
# correction-acknowledgements and feedback exchanges. Past incident: Antonio
# corrected ARIA on the Ghana defence minister; ARIA's apology reply got
# cached against tokens like "ghana correction wrong"; the next question that
# happened to mention Ghana matched the cached apology and replayed it
# instead of producing a fresh answer.

_USER_CORRECTION_RE = re.compile(
    r"\b(?:that\s+(?:was|is)\s+(?:wrong|incorrect|the\s+wrong\s+answer)"
    r"|you\s+(?:are|got)\s+wrong"
    r"|you('?re|\s+are)\s+(?:wrong|incorrect|mistaken)"
    r"|wrong\s+answer"
    r"|not\s+relevant\s+to"
    r"|this\s+(?:was|is)\s+not\s+relevant"
    r"|don'?t\s+present\s+outdated"
    r"|stop\s+(?:making\s+up|inventing|fabricating)"
    r"|that(?:'s|\s+is)\s+not\s+correct"
    r"|please\s+correct"
    r"|the\s+\w+\s+breakdown\s+was\s+(?:useful|good)\s+but"
    r")\b",
    re.IGNORECASE,
)

_CORRECTION_ACK_RE = re.compile(
    r"(?:your\s+correction\s+is\s+(?:entirely\s+)?correct"
    r"|i\s+(?:apologi[sz]e|stand\s+corrected|presented\s+(?:outdated|incorrect|false))"
    r"|i\s+(?:was|am)\s+wrong"
    r"|i\s+failed\s+to\s+apply"
    r"|previous\s+error\b"
    r"|analytical\s+failure\s+that\s+damages\s+credibility"
    r"|i\s+should\s+have\s+flagged"
    r"|thank\s+you\s+for\s+(?:the\s+)?correct(?:ion|ing)"
    r")",
    re.IGNORECASE,
)

_INVESTIGATION_REQUEST_RE = re.compile(
    r"\b(?:investigate|research|deep[\s-]?dive|deep[\s-]?dd|d\.d\.|dd|"
    r"profile|due\s+diligence|"
    r"background\s+check|look\s+into|dig\s+into|find\s+out\s+about|"
    r"analyse|analyze|rank|shortlist|compare|assess|evaluate|review|"
    r"screen|vet|audit)\b",
    re.IGNORECASE,
)

# Messages that depend on current-turn input (attached file, pasted text,
# "this list", "above", etc.) must never be served from cache or cached.
# The answer is intrinsically tied to the content that arrived THIS turn,
# so replaying a prior answer is always wrong — even if the semantic
# similarity is high. This was the proximate cause of the detonator
# supplier xlsx loop (2026-04-11 21:14): two consecutive "analyse this
# list" messages, neither of them a keyword-investigation, both replaying
# the same "no document reached my context" fallback 3x in a row.
_FRESH_INPUT_REF_RE = re.compile(
    # "this list", "this document", "these companies", "the attached list",
    # "the above", "this is a list", "these are suppliers"…
    r"(?:\b(?:this|these|those|that|the)\s+(?:\w+\s+){0,3}?"
    r"(?:list|document|file|spreadsheet|table|image|photo|picture|"
    r"attachment|attachments|card|contract|message|email|text|data|"
    r"excel|pdf|xlsx|csv|doc|docx|info|information|report|note|"
    r"screenshot|shortlist|rfq|tender|quote|offer|response|reply|"
    r"companies|suppliers|entities|names|candidates|items)\b)"
    r"|(?:\battached\b)|(?:\bpasted\b)|(?:\babove\b)|(?:\benclosed\b)"
    r"|(?:\[ATTACHED\s+DOCUMENT)"
    r"|(?:\[Document:\s)"
    r"|(?:\[Image[:\s])",
    re.IGNORECASE,
)


def _looks_like_fresh_input_request(q: str) -> bool:
    """True if the question references current-turn input (attached file,
    pasted text, 'this list', etc.). Such messages must always be
    processed fresh — never served from cache and never cached."""
    if not q:
        return False
    return bool(_FRESH_INPUT_REF_RE.search(q))


# R-F917 (2026-05-26) — a question carrying a URL is a request to look at THAT
# page right now. Its answer is page-specific and time-sensitive (a company's
# website today ≠ its cached snapshot), so it must ALWAYS get a fresh crawl:
# never served from the reasoning library, never cached into it. Live incident
# 2026-05-26: "what can you tell us about https://defence.csg.com/en" matched a
# prior cached *fallback* answer (0 sources, NO_CITATIONS, truncated) and
# replayed it as "confidence CONFIRMED" instead of crawling the site.
# R-F1604 — also catch BARE domains (no scheme): the operator wrote
# "do a deep dd on www.gozensecurity.com" (no https://), so the old
# https?:// -only regex missed it and the reasoning library served a STALE,
# unrelated cached answer (a UAE/Aerospace DD, "used 6x") for a brand-new
# entity. A URL/domain in the question must ALWAYS force a fresh crawl. Over-
# matching here is the SAFE direction (fresh, never stale).
_URL_IN_QUESTION_RE = re.compile(
    r"https?://"
    r"|\bwww\.[a-z0-9-]+\.[a-z]{2,}"
    r"|\b[a-z0-9][a-z0-9-]{1,}\.(?:com|org|net|io|co|gov|edu|info|biz|ai|app|"
    r"eu|uk|us|ro|bg|ae|ru|de|fr|ch|nl|pl|es|it|pt)\b",
    re.IGNORECASE,
)


def _question_has_url(q: str) -> bool:
    """True if the question contains an http(s) URL — force fresh crawl."""
    if not q:
        return False
    return bool(_URL_IN_QUESTION_RE.search(q))


# Response patterns that indicate the LLM could not process this turn's
# input (parse failed, no attachment visible, asked user to re-share).
# These are turn-specific failure states, not reusable reasoning patterns —
# they must be blocked from entering the case library.
_TURN_FAILURE_RE = re.compile(
    r"(?:no\s+(?:supplier\s+list|document|attachment|file|image|photo|"
    r"content|data|text)\s+has\s+(?:reached|arrived))"
    r"|(?:no\s+\[ATTACHED\s+DOCUMENT\])"
    r"|(?:cannot\s+(?:see|read|parse|access|analyse)\s+(?:the\s+)?"
    r"(?:attached|attachment|file|document|image|list|spreadsheet))"
    r"|(?:parse\s+failed)|(?:no\s+text\s+extracted)"
    r"|(?:could\s+not\s+extract)|(?:failed\s+to\s+parse)"
    r"|(?:please\s+(?:re-?share|re-?send|re-?attach|paste)\s+the)"
    r"|(?:i\s+cannot\s+analyse\s+the)"
    r"|(?:the\s+file\s+failed\s+to\s+parse)",
    re.IGNORECASE,
)


def _looks_like_turn_failure_response(r: str) -> bool:
    """True if the response describes a turn-specific failure to read or
    parse input that arrived this turn. These are never reusable."""
    if not r:
        return False
    return bool(_TURN_FAILURE_RE.search(r[:3000]))


def _looks_like_user_correction(q_lower: str) -> bool:
    """True if the user message is a correction or feedback statement.

    Caching ARIA's response to a correction is dangerous because the response
    is intrinsically tied to the prior turn — replaying it for any future
    question that shares overlapping vocabulary is exactly the bug we hit.
    """
    if not q_lower:
        return False
    return bool(_USER_CORRECTION_RE.search(q_lower))


def _looks_like_correction_acknowledgement(response: str) -> bool:
    """True if the response is itself an apology or correction acknowledgement.

    These should never be served from the cache to any other question — they
    only make sense in the context of the immediately prior user message.
    """
    if not response:
        return False
    return bool(_CORRECTION_ACK_RE.search(response[:2000]))


def _looks_like_investigation_request(q_lower: str) -> bool:
    """True if the user is asking for fresh research.

    Investigation results are time-sensitive — sanctions lists change, board
    members come and go, conflict events update daily. Caching an investigate
    response means the second user gets stale intel without knowing it.
    Better to always run fresh.
    """
    if not q_lower:
        return False
    return bool(_INVESTIGATION_REQUEST_RE.search(q_lower))


# R-F864 (2026-05-25) — periodic / dated briefs are inherently time-sensitive:
# a brief for one week is stale (and wrong) the next. Live incident: a
# 2026-05-18 "Arkmurus Weekly Intelligence Brief" was cached and replayed
# verbatim for the 2026-05-25 request (date header, content, all stale). The
# investigation regex didn't catch "generate the weekly intelligence brief", so
# it slipped past the time-sensitivity guard. Never cache; never serve.
_PERIODIC_BRIEF_RE = re.compile(
    r"\b(?:"
    r"(?:weekly|daily|monthly|morning|evening|monday|today'?s?|this\s+week'?s?)\s+"
    r"(?:brief|briefing|report|update|round-?up|digest|intel(?:ligence)?)"
    r"|intelligence\s+brief(?:ing)?"
    r"|strategic\s+(?:intelligence\s+)?(?:brief|briefing|report|update)"
    r"|sit-?rep|situation\s+report|market\s+update"
    r"|generate\s+(?:the\s+)?(?:[\w-]+\s+){0,5}brief"
    r")\b",
    re.IGNORECASE,
)


def _looks_like_periodic_brief(q_lower: str) -> bool:
    """True if the request is for a PERIODIC / DATED brief or report (weekly
    intelligence brief, daily briefing, situation report, market update...).
    These are time-sensitive — never cache, never serve from cache. R-F864."""
    if not q_lower:
        return False
    return bool(_PERIODIC_BRIEF_RE.search(q_lower))


# R-F3608 (2026-08-01) — ONE definition of "this text is a FAILURE, not an
# answer", because two call sites disagreed and the one without the guard
# poisoned the brain.
#
# `record_response` below has refused to cache degraded/error replies since
# R-F655's sibling fix. The R-F655 brain_hook absorb in routes/aria.py did NOT
# have the same guard, and absorbed the degraded text with
# `success=True, confidence="PROBABLE"`.
#
# Live 2026-08-01 (operator WhatsApp, "Tony is flying to Bulgaria"): the reply
# ARIA served contained, as a "verified fact", a PREVIOUS degraded reply —
#   [PROBABLE] aria_chat:detail: ⚠️ ARIA degraded mode — LLM error: [deepseek_backup]…
# Each outage therefore wrote itself into the knowledge base, and the next
# outage retrieved it and printed it back. The damage COMPOUNDS per occurrence.
#
# Exported (not underscore-private) precisely so the absorb path can share it:
# a second inline copy is how the two drifted in the first place.
def is_degraded_or_error_response(text: str) -> bool:
    """True when ``text`` is an ARIA failure surface rather than an answer.

    Covers the degraded-mode banner, the error prefixes, and the local_brain
    fallback header. Never store, cache, or absorb one of these as knowledge.
    """
    if not text:
        return False
    t = text.strip()
    if t.startswith(("⚠️", "❌", "ARIA encountered")):
        return True
    return "degraded mode" in t.lower()


@fail_wire(module="reasoning_library", gap_type="engine_failure")
async def record_response(
    question: str,
    response: str,
    *,
    intent: str = "",
    source_brain: str = "deepseek",
    context_keys: list[str] | None = None,
) -> dict:
    """Capture a successful chat response into the reasoning library.

    This is THE distillation hook. Every time the LLM produces a confident
    answer, ARIA stores it locally so the next similar question can be
    answered without calling the LLM again.
    """
    if not question or not response or len(question.strip()) < 5 or len(response.strip()) < 20:
        return {"recorded": False, "reason": "too short"}

    # Don't store error/fallback or degraded-mode responses (R-F3608: this is
    # now the SHARED predicate, so the brain_hook absorb path cannot drift).
    if is_degraded_or_error_response(response):
        return {"recorded": False, "reason": "error or degraded response"}

    # Don't cache greetings / liveness probes / identity questions — their
    # answers are context-dependent and not safe to reuse (incident 2026-04-08).
    if _is_trivial_question(question):
        return {"recorded": False, "reason": "trivial question (greeting/liveness/identity)"}

    # 2026-04-08 round 3: Don't cache responses to user-correction / feedback
    # messages, AND don't cache responses that are themselves correction
    # acknowledgements. Both pollute the case library — the next "Ghana
    # opportunity?" then matched the apology and replayed it.
    q_lower = question.strip().lower()
    if _looks_like_user_correction(q_lower):
        return {"recorded": False, "reason": "question is user correction/feedback"}
    if _looks_like_correction_acknowledgement(response):
        return {"recorded": False, "reason": "response is a correction acknowledgement"}
    # Investigations and reports are time-sensitive — caching them
    # creates the same staleness problem we're trying to fix. Skip.
    if _looks_like_investigation_request(q_lower):
        return {"recorded": False, "reason": "investigation request — always run fresh"}
    # R-F864 — periodic/dated briefs (weekly intelligence brief, daily briefing,
    # situation report...) are time-sensitive: caching one means it gets
    # replayed stale for a later period (the 2026-05-25 incident).
    if _looks_like_periodic_brief(q_lower):
        return {"recorded": False, "reason": "periodic/dated brief — time-sensitive, never cache (R-F864)"}
    # Fresh-input questions ("analyse this list", "review the attachment",
    # "above") depend on current-turn content — caching is always wrong.
    if _looks_like_fresh_input_request(question):
        return {"recorded": False, "reason": "fresh-input request — answer tied to this turn's content"}
    # Turn-specific failure responses ("no document reached my context",
    # "parse failed", "please re-share") must never enter the library.
    if _looks_like_turn_failure_response(response):
        return {"recorded": False, "reason": "turn-specific failure response — not reusable"}
    # R-F917 — never cache an answer to a URL-bearing question. The answer is
    # tied to that page at this moment; caching it produces the stale replay the
    # 2026-05-26 CSG-website incident exhibited. (NOTE: the incident footer's
    # "prior fallback" is NOT a degraded-path signal — it is just the
    # FallbackProvider wrapper's .name, which is "fallback" for EVERY answer per
    # R-F131. So source_brain must NOT be used to gate distillation; doing so
    # would block 100% of caching. The URL gate is the correct, sufficient fix.)
    if _question_has_url(question):
        return {"recorded": False, "reason": "URL question — page-specific, always crawl fresh (R-F917)"}
    # R-F917 — never cache an ungrounded or replayed answer. If the response text
    # already carries 0-source / NO_CITATIONS / library-replay / build-fallback
    # markers, it is not safe to re-serve as confident reasoning.
    _rl = response.lower()
    if (
        "no_citations" in _rl
        or "sources: 0 grounded" in _rl
        or "↻ retrieved from aria" in _rl
        or "runtime fallback (build-arg missing)" in _rl
    ):
        return {"recorded": False, "reason": "ungrounded/replayed response — not distilled (R-F917)"}

    confidence_tag, confidence_score = _detect_confidence_tag(response)

    case_id = str(uuid.uuid4())[:12]
    normalised = _normalise_question(question)
    if not normalised:
        return {"recorded": False, "reason": "no salient tokens"}
    # A single salient token is not enough to safely identify a question —
    # too easy to collide with unrelated cases. Require at least N tokens.
    if len(normalised.split()) < MIN_SALIENT_TOKENS:
        return {"recorded": False, "reason": f"too few salient tokens (<{MIN_SALIENT_TOKENS})"}

    embedding = await _embed_async(question)
    # R-F917 — cap stored response at 6000 chars, but cut on a whitespace
    # boundary so a replayed answer never ends mid-word (the incident's "(2"
    # truncation). Append an explicit marker so a clipped cache hit is honest.
    _resp_store = response
    if len(_resp_store) > 6000:
        _cut = response.rfind(" ", 0, 6000)
        if _cut < 5000:  # no sensible boundary — hard cut rather than lose half
            _cut = 6000
        _resp_store = response[:_cut].rstrip() + " …[answer truncated in cache]"
    case = {
        "id": case_id,
        "question": question[:1000],
        "normalised": normalised,
        "response": _resp_store,
        "confidence_tag": confidence_tag,
        "confidence_score": confidence_score,
        "intent": intent,
        "context_keys": context_keys or [],
        "embedding": embedding,
        "access_count": 0,
        "positive_outcomes": 0,
        "negative_outcomes": 0,
        "ts_created": time.time(),
        "ts_last_used": time.time(),
        "source_brain": source_brain,
    }

    # Persist the full case
    await rs.set_json(_case_key(case_id), case, ex=TTL_SECONDS)

    # Update lightweight index (used for fast scan)
    index = await _load_index()
    index.append({
        "id": case_id,
        "normalised": normalised,
        "intent": intent,
        "confidence_score": confidence_score,
        "ts_created": case["ts_created"],
        "ts_last_used": case["ts_last_used"],
        "embedding": embedding,
    })
    # Cap size — drop the oldest low-access entries
    if len(index) > MAX_CASES:
        index.sort(key=lambda c: (c.get("ts_last_used", 0)))
        index = index[-MAX_CASES:]
    _index_cache_set(index)
    await _save_index()

    # Update meta
    meta = await _load_meta()
    meta["total_cases"] = len(index)
    meta["total_writes"] = meta.get("total_writes", 0) + 1
    meta["last_write"] = time.time()
    _mark_meta_dirty()  # R-F268
    await _save_meta()

    return {"recorded": True, "id": case_id, "confidence": confidence_tag}


def _index_cache_set(new_index: list[dict]) -> None:
    global _index_cache
    _index_cache = new_index


# ── Public API: looking up cases ────────────────────────────────────────────

@fail_wire(module="reasoning_library", gap_type="engine_failure")
async def find_match(question: str, *, threshold: float = DEFAULT_MATCH_THRESHOLD) -> dict:
    """Find the best matching case for a new question.

    Returns:
        {
            "match": True/False,
            "score": 0..1,
            "case": {...} or None,
            "method": "exact_normalised" | "semantic" | "lexical_jaccard" | None,
            "threshold": threshold,
        }
    """
    if not question or len(question.strip()) < 5:
        return {"match": False, "score": 0, "case": None, "method": None, "threshold": threshold}

    # Trivial / liveness / identity questions never get served from the
    # cache. They are context-dependent and were the source of the
    # incident-2026-04-08 over-cache (every "are you online?" returned the
    # same Angola briefing because it was miscached against "online").
    if _is_trivial_question(question):
        return {"match": False, "score": 0, "case": None, "method": "skipped_trivial", "threshold": threshold}

    # 2026-04-08 round 3: investigations / corrections / feedback are NEVER
    # served from the cache, regardless of whether the index already contains
    # polluted entries from before this fix shipped. Defence in depth — the
    # record_response side now blocks NEW pollution; this side blocks REPLAY
    # of any pollution that already exists.
    q_lower = question.strip().lower()
    if _looks_like_user_correction(q_lower):
        return {"match": False, "score": 0, "case": None, "method": "skipped_user_correction", "threshold": threshold}
    if _looks_like_investigation_request(q_lower):
        return {"match": False, "score": 0, "case": None, "method": "skipped_investigation", "threshold": threshold}
    # R-F864 — never serve a periodic/dated brief from cache (defence-in-depth
    # against entries cached before this fix shipped — incl. the stale 2026-05-18
    # weekly brief). Forces fresh generation each period.
    if _looks_like_periodic_brief(q_lower):
        return {"match": False, "score": 0, "case": None, "method": "skipped_periodic_brief", "threshold": threshold}
    if _looks_like_fresh_input_request(question):
        return {"match": False, "score": 0, "case": None, "method": "skipped_fresh_input", "threshold": threshold}
    # R-F917 — a URL in the question forces a fresh crawl: never replay a cached
    # answer for "tell me about https://…". Defence-in-depth with the
    # record_response gate below (which stops new URL answers being cached).
    if _question_has_url(question):
        return {"match": False, "score": 0, "case": None, "method": "skipped_url_fresh_crawl", "threshold": threshold}

    meta = await _load_meta()
    meta["total_lookups"] = meta.get("total_lookups", 0) + 1

    index = await _load_index()
    if not index:
        return {"match": False, "score": 0, "case": None, "method": None, "threshold": threshold}

    normalised = _normalise_question(question)
    # A single salient token can't safely identify a question — bail out
    # rather than risk an exact_normalised collision against an unrelated case.
    if len(normalised.split()) < MIN_SALIENT_TOKENS:
        _mark_meta_dirty()  # R-F268
        await _save_meta()
        return {"match": False, "score": 0, "case": None, "method": "skipped_too_few_tokens", "threshold": threshold}
    query_tokens = _token_set(question)
    query_embedding = await _embed_async(question)
    # R-F518 — entity tokens for the new query, computed once and reused
    # per entry comparison. See _entity_tokens for rationale.
    query_entities = _entity_tokens(normalised)

    best_idx = None
    best_score = 0.0
    best_method = None

    for entry in index:
        # 1. Exact normalised match (cheapest)
        if entry.get("normalised") == normalised and normalised:
            score = 0.95 + (entry.get("confidence_score", 0.5) * 0.05)
            if score > best_score:
                best_score, best_idx, best_method = score, entry, "exact_normalised"
                if best_score >= 0.99:
                    break  # can't beat exact match
            continue

        # R-F518 — entity-overlap gate for non-exact matches. Skip this
        # entry entirely if the cached question is about a different
        # subject. Without this, semantic matching on question SHAPE
        # ("Is X sanctioned in Y?") serves the same cached answer for
        # any X and any Y — the 2026-05-14 Hikvision/Bumblestaff repro.
        entry_entities = _entity_tokens(entry.get("normalised", ""))
        if query_entities and entry_entities and not (query_entities & entry_entities):
            continue
        # If EITHER side has no entity tokens (pure-jargon question like
        # "are sanctions on UK companies effective?") we fall through —
        # those queries are about concepts, not entities, and the
        # original semantic + threshold gating is appropriate.

        # 2. Semantic similarity (best precision)
        if query_embedding and entry.get("embedding"):
            sim = _cosine(query_embedding, entry["embedding"])
            # Blend with confidence and recency
            recency_factor = _recency_multiplier(entry.get("ts_last_used", 0))
            blended = sim * (0.7 + entry.get("confidence_score", 0.5) * 0.3) * recency_factor
            if blended > best_score:
                best_score, best_idx, best_method = blended, entry, "semantic"
            continue

        # 3. Lexical Jaccard fallback
        entry_tokens = set((entry.get("normalised") or "").split())
        jac = _jaccard(query_tokens, entry_tokens)
        if jac > 0.3:
            blended = jac * (0.7 + entry.get("confidence_score", 0.5) * 0.3)
            if blended > best_score:
                best_score, best_idx, best_method = blended, entry, "lexical_jaccard"

    if best_idx is None or best_score < threshold:
        _mark_meta_dirty()  # R-F268
        await _save_meta()
        # R-F1169 — wire no-match to brain so ARIA learns what she doesn't know
        from .engine_wiring import wire_success, wire_failure
        wire_success(
            module="reasoning_library",
            summary=f"No match found (best score {best_score:.3f} < threshold {threshold})",
            source_id="reasoning_library:no_match",
        )
        return {"match": False, "score": best_score, "case": None, "method": best_method, "threshold": threshold}

    # Load the full case
    case = await rs.get_json(_case_key(best_idx["id"]))
    if not case:
        # Stale index entry — remove it
        index = [e for e in index if e["id"] != best_idx["id"]]
        _index_cache_set(index)
        await _save_index()
        return {"match": False, "score": 0, "case": None, "method": None, "threshold": threshold}

    # Bump access count + last_used
    case["access_count"] = case.get("access_count", 0) + 1
    case["ts_last_used"] = time.time()
    await rs.set_json(_case_key(case["id"]), case, ex=TTL_SECONDS)
    best_idx["ts_last_used"] = case["ts_last_used"]
    await _save_index()

    meta["total_hits"] = meta.get("total_hits", 0) + 1
    _mark_meta_dirty()  # R-F268
    await _save_meta()

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="reasoning_library",
        summary="Find Match",
        source_id="reasoning_library:R-F996",
    )

    return {
        "match": True,
        "score": round(best_score, 3),
        "case": case,
        "method": best_method,
        "threshold": threshold,
    }


def _recency_multiplier(ts_last_used: float) -> float:
    """Decay multiplier for stale cases.

    2026-04-08 round 3: previously this BOOSTED recently-used cases (1.10
    for <1d, 1.05 for <7d). The boost was the proximate cause of the
    correction-replay bug — a same-session correction acknowledgement
    matched at sim≈0.71 against an unrelated query, the +10% recency boost
    pushed the blended score over the 0.78 threshold, and ARIA replayed
    the apology instead of answering the new question. Now we only DECAY
    stale cases — there is no boost for recency. A truly recent case has
    to earn its match on semantic similarity alone.
    """
    if not ts_last_used:
        return 0.7
    age_days = (time.time() - ts_last_used) / 86400
    if age_days < 30:   return 1.00
    if age_days < 90:   return 0.92
    return 0.80


# ── Public API: purge polluted cases ───────────────────────────────────────

@fail_wire(module="reasoning_library", gap_type="engine_failure")
async def purge_polluted_cases(*, dry_run: bool = False) -> dict:
    """Scan the case library and remove entries whose stored response is a
    correction acknowledgement, an apology, or otherwise context-bound to
    its prior turn (and therefore unsafe to replay against any other query).

    Added 2026-04-08 round 3 after ARIA replayed a Ghana correction
    acknowledgement against unrelated queries. The find_match guard prevents
    NEW pollution from causing replay, but cannot remove entries that were
    written before the guard shipped — hence this one-shot purge.

    Returns a summary dict:
        {
            "scanned":         int,
            "removed":         int,
            "dry_run":         bool,
            "removed_samples": [{"id", "question_preview", "reason"}, ...],
        }
    """
    index = await _load_index()
    if not index:
        return {"scanned": 0, "removed": 0, "dry_run": dry_run, "removed_samples": []}

    keep: list[dict] = []
    removed_samples: list[dict] = []
    n_scanned = 0
    n_removed = 0

    for entry in index:
        n_scanned += 1
        case_id = entry.get("id")
        if not case_id:
            keep.append(entry)
            continue

        case = await rs.get_json(_case_key(case_id))
        if not case:
            # Stale index entry — drop it
            n_removed += 1
            continue

        question = (case.get("question") or "").strip()
        response = (case.get("response") or "").strip()
        reason: str | None = None

        if _looks_like_correction_acknowledgement(response):
            reason = "response is a correction acknowledgement"
        elif _looks_like_user_correction(question.lower()):
            reason = "question is a user correction"
        elif _looks_like_investigation_request(question.lower()):
            reason = "investigation request — should not be cached"
        elif _looks_like_fresh_input_request(question):
            reason = "fresh-input request — answer tied to prior turn's content"
        elif _looks_like_turn_failure_response(response):
            reason = "turn-specific failure response — not reusable"

        if reason:
            n_removed += 1
            if len(removed_samples) < 25:
                removed_samples.append({
                    "id": case_id,
                    "question_preview": question[:120],
                    "reason": reason,
                })
            if not dry_run:
                try:
                    await rs.delete(_case_key(case_id))
                except Exception as e:
                    logger.debug("purge: failed to delete %s: %s", case_id, e)
            continue

        keep.append(entry)

    if not dry_run and n_removed > 0:
        _index_cache_set(keep)
        await _save_index()
        meta = await _load_meta()
        meta["total_cases"] = len(keep)
        meta["last_purge"] = time.time()
        meta["last_purge_removed"] = n_removed
        _mark_meta_dirty()  # R-F268
        await _save_meta()

    return {
        "scanned": n_scanned,
        "removed": n_removed,
        "dry_run": dry_run,
        "removed_samples": removed_samples,
        "remaining": len(keep) if not dry_run else (n_scanned - n_removed),
    }


@fail_wire(module="reasoning_library", gap_type="engine_failure")
async def purge_by_keywords(
    keywords: list[str],
    *,
    dry_run: bool = False,
) -> dict:
    """Remove every cached case whose stored question or response contains
    any of the given keywords (case-insensitive substring match). Mirrors
    `purge_polluted_cases` but takes an explicit keyword list instead of
    a content-shape classifier — used for surgical cleanup of fabricated
    answers absorbed via the pay-once-remember-forever pattern.

    Background: 2026-04-24 OpenClaw incident — even after blocking the
    Brave route at the chat layer, the same fabrication came back from
    the reasoning_library cache (`Retrieved from ARIA's reasoning library,
    used 3x`). Stage 0 bypass in reasoning_router stops new self-infra
    queries from hitting the cache, but existing poisoned entries are
    still in `crucix:aria:reasoning_library:*` and could surface for
    adjacent (non-self-infra) queries that happen to semantically match.

    Returns:
        {scanned, removed, dry_run, keywords_used, removed_samples: [...]}
    """
    if not keywords:
        return {
            "scanned": 0, "removed": 0, "dry_run": dry_run,
            "keywords_used": [], "removed_samples": [],
        }

    needles = [k.lower().strip() for k in keywords if k and k.strip()]
    if not needles:
        return {
            "scanned": 0, "removed": 0, "dry_run": dry_run,
            "keywords_used": [], "removed_samples": [],
        }

    index = await _load_index()
    if not index:
        return {
            "scanned": 0, "removed": 0, "dry_run": dry_run,
            "keywords_used": needles, "removed_samples": [],
        }

    keep: list[dict] = []
    removed_samples: list[dict] = []
    n_removed = 0

    for entry in index:
        case_id = entry.get("id")
        if not case_id:
            keep.append(entry)
            continue
        case = await rs.get_json(_case_key(case_id))
        if not case:
            n_removed += 1
            continue
        question = (case.get("question") or "").lower()
        response = (case.get("response") or "").lower()
        haystack = question + " " + response
        hit = next((n for n in needles if n in haystack), None)
        if hit is None:
            keep.append(entry)
            continue
        n_removed += 1
        if len(removed_samples) < 25:
            removed_samples.append({
                "id": case_id,
                "question_preview": (case.get("question") or "")[:120],
                "matched_keyword": hit,
                "access_count": case.get("access_count", 0),
            })
        if not dry_run:
            try:
                await rs.delete(_case_key(case_id))
            except Exception as e:
                logger.debug(
                    "reasoning_library.purge_by_keywords: delete failed %s: %s",
                    case_id, e,
                )

    if not dry_run and n_removed > 0:
        _index_cache_set(keep)
        await _save_index()
        meta = await _load_meta()
        meta["total_cases"] = len(keep)
        meta["last_purge"] = time.time()
        meta["last_purge_removed"] = n_removed
        _mark_meta_dirty()  # R-F268
        await _save_meta()
        logger.warning(
            "reasoning_library.purge_by_keywords: removed %d cases matching %s",
            n_removed, needles,
        )

    return {
        "scanned": len(index),
        "removed": n_removed,
        "dry_run": dry_run,
        "keywords_used": needles,
        "removed_samples": removed_samples,
    }


# ── R-F605 — Purge known capability-hallucination cases ──────────────
#
# Past incident 2026-05-16 18:45 + 19:19: the operator asked the same
# capability-overview question three times in a row. ARIA's first
# answer was a cloud-LLM response with hallucinated numbers ("34
# autonomous tasks", "48/49 sources OK", "Officeholder Verification
# 18-month TTL", "5,000–10,000 verified facts"). That answer was
# absorbed into the reasoning_library via the pay-once-remember-forever
# pattern. Subsequent identical questions hit the library cache
# (footer: "used 3x") and re-emitted the SAME hallucinated content
# even after R-F593/F594/F595 shipped.
#
# R-F599 stops NEW capability questions from hitting the cache. This
# function removes the EXISTING poisoned entries so they cannot
# resurface via semantically-adjacent queries that R-F599's regex
# might miss.
#
# Needles below are EXACT substrings from the observed bad responses.
# Adding a needle is a one-line change.
_CAPABILITY_HALLUCINATION_NEEDLES: tuple[str, ...] = (
    # Numeric inventions (the R-F594 surface)
    "34 autonomous tasks",
    "48/49 sources",
    "18-month ttl",
    "5,000-10,000 verified facts",
    "5,000–10,000 verified facts",  # en-dash variant
    # Capability denials (the R-F604 surface)
    "no uk ofsi direct access",
    "i cannot query ofsi",
    "no outbound email capability",
    "no corporate registry direct adapters",
    # Fabricated component names (the 2026-04-24 OpenClaw class)
    "openclaw",
    "arkmurus gateway",
)


@fail_wire(module="reasoning_library", gap_type="engine_failure")
async def purge_capability_hallucinations(*, dry_run: bool = False) -> dict:
    """R-F605: remove cached responses containing the 2026-05-16
    capability-overview hallucinations + the 2026-04-24 OpenClaw
    fabrications. Wraps `purge_by_keywords` with a curated needle list.

    Returns the same shape as purge_by_keywords:
        {scanned, removed, dry_run, keywords_used, removed_samples}
    """
    return await purge_by_keywords(
        list(_CAPABILITY_HALLUCINATION_NEEDLES),
        dry_run=dry_run,
    )


# ── Public API: outcome feedback ────────────────────────────────────────────

@fail_wire(module="reasoning_library", gap_type="engine_failure")
async def record_outcome(case_id: str, positive: bool) -> dict:
    """Mark a case as having a positive (user accepted) or negative (user
    corrected) outcome. Used by the learning loop to upweight good cases
    and downweight bad ones.
    """
    case = await rs.get_json(_case_key(case_id))
    if not case:
        return {"ok": False, "error": "case not found"}
    if positive:
        case["positive_outcomes"] = case.get("positive_outcomes", 0) + 1
        case["confidence_score"] = min(1.0, case.get("confidence_score", 0.5) + 0.05)
    else:
        case["negative_outcomes"] = case.get("negative_outcomes", 0) + 1
        case["confidence_score"] = max(0.1, case.get("confidence_score", 0.5) - 0.15)
    await rs.set_json(_case_key(case["id"]), case, ex=TTL_SECONDS)

    # Sync confidence into the index
    index = await _load_index()
    for e in index:
        if e["id"] == case_id:
            e["confidence_score"] = case["confidence_score"]
            break
    await _save_index()
    return {"ok": True, "new_confidence": case["confidence_score"]}


# ── Public API: stats and consolidation ─────────────────────────────────────

@fail_wire(module="reasoning_library", gap_type="engine_failure")
async def get_stats() -> dict:
    meta = await _load_meta()
    index = await _load_index()
    hits = meta.get("total_hits", 0)
    lookups = meta.get("total_lookups", 0)
    hit_rate = (hits / lookups) if lookups > 0 else 0.0

    by_intent: dict[str, int] = {}
    by_source: dict[str, int] = {}
    confidence_dist: dict[str, int] = {}
    for entry in index:
        intent = entry.get("intent", "unknown") or "unknown"
        by_intent[intent] = by_intent.get(intent, 0) + 1
        # source comes from the full case, not the index — skip for speed
        cs = entry.get("confidence_score", 0)
        bucket = "high" if cs >= 0.75 else "medium" if cs >= 0.5 else "low"
        confidence_dist[bucket] = confidence_dist.get(bucket, 0) + 1

    return {
        "total_cases": len(index),
        "total_lookups": lookups,
        "total_hits": hits,
        "hit_rate": round(hit_rate, 3),
        "total_writes": meta.get("total_writes", 0),
        "by_intent": by_intent,
        "confidence_distribution": confidence_dist,
        "born": meta.get("born"),
        "age_days": round((time.time() - meta.get("born", time.time())) / 86400, 1),
        # R-F4143 (C-172) — offloaded. This is THE line the 5.17s wedge dump
        # caught: `_proactive_loop` -> `daily_briefing_check` -> `get_stats` ->
        # `_get_embedder` -> `import transformers`, on the event loop, in a
        # function whose whole job is to report numbers.
        #
        # NOTE for whoever touches this next: offloading is the semantics-
        # preserving fix, but the deeper oddity is left deliberately unchanged —
        # a STATS call still triggers a cold model load (seconds of CPU, ~100MB
        # RSS) as a side effect of asking "is the embedder available?". Making
        # it report only an ALREADY-loaded embedder would be cheaper and is
        # arguably right, but it changes what the field means, so it is an
        # operator-visible decision rather than something to slip into a
        # loop-blocking fix.
        "embedder_available": (await asyncio.to_thread(_get_embedder)) is not None,
    }


@fail_wire(module="reasoning_library", gap_type="engine_failure")
async def purge_unsafe_cases() -> dict:
    """One-shot cleanup: remove cached cases whose normalised question has
    fewer than MIN_SALIENT_TOKENS tokens. These are the entries that caused
    the 2026-04-08 over-cache incident — single-token normalised strings
    collide on exact match and serve junk to unrelated queries.

    Idempotent. Safe to call on every startup.
    """
    index = await _load_index()
    if not index:
        return {"purged": 0, "remaining": 0}

    purged = 0
    new_index: list[dict] = []
    for entry in index:
        norm = (entry.get("normalised") or "").strip()
        if not norm or len(norm.split()) < MIN_SALIENT_TOKENS:
            try:
                await rs.set_json(_case_key(entry["id"]), None, ex=1)
            except Exception:
                pass
            purged += 1
            continue
        new_index.append(entry)

    if purged:
        _index_cache_set(new_index)
        await _save_index()
        meta = await _load_meta()
        meta["total_cases"] = len(new_index)
        meta["last_purge"] = time.time()
        meta["last_purge_count"] = purged
        _mark_meta_dirty()  # R-F268
        await _save_meta()
        logger.info("reasoning_library purge: removed %d unsafe cases (<%d salient tokens), %d remain",
                    purged, MIN_SALIENT_TOKENS, len(new_index))
    return {"purged": purged, "remaining": len(new_index)}


@fail_wire(module="reasoning_library", gap_type="engine_failure")
async def consolidate() -> dict:
    """Periodic maintenance: prune stale low-quality cases, promote high-quality."""
    index = await _load_index()
    if not index:
        return {"pruned": 0, "promoted": 0, "remaining": 0}

    now = time.time()
    cutoff = now - RETENTION_DAYS * 86400

    pruned = 0
    # R-F242 (2026-05-13): replace DELETE with archive flag.
    # Operator directive [[aria_infinite_memory]] (2026-05-11):
    # "ARIA has INFINITE memory — never forgets, never loses data.
    # No TTL on knowledge, no oldest-first prune, no eviction."
    # R-F173 prune was reversed by R-F238 for the same reason.
    # Same bug-shape here: this consolidate() was deleting cases that
    # had access_count=0 + age>30d + confidence<0.5 OR had >3 negative
    # outcomes with no positives. Both branches violate the directive.
    # Now: flip case["archived"] = True with the reason — case STAYS
    # in storage for forensic recall via the new include_archived=True
    # search parameter. Index drops the entry (keeps fast search fast)
    # but the case body is preserved.
    new_index = []
    archived = 0
    for entry in index:
        case = await rs.get_json(_case_key(entry["id"]))
        if not case:
            # Missing case data (Redis lost it) — count as pruned for
            # the diag log but no further action; the index already
            # drops the entry by skipping the append.
            pruned += 1
            continue
        age_days = (now - case.get("ts_created", now)) / 86400
        # Archive stale + unused + low-confidence cases.
        if (case.get("access_count", 0) == 0
                and age_days > 30
                and case.get("confidence_score", 0) < 0.5):
            case["archived"] = True
            case["archived_at"] = now
            case["archived_reason"] = (
                f"R-F242 archive: age={int(age_days)}d, access_count=0, "
                f"confidence={case.get('confidence_score', 0):.2f}<0.5. "
                f"Case preserved for forensic recall; no longer in active "
                f"index (per aria_infinite_memory.md, never deleted)."
            )
            await rs.set_json(_case_key(case["id"]), case)
            archived += 1
            continue
        # Archive cases with consistent negative outcomes (>3 neg, 0 pos).
        if case.get("negative_outcomes", 0) > 3 and case.get("positive_outcomes", 0) == 0:
            case["archived"] = True
            case["archived_at"] = now
            case["archived_reason"] = (
                f"R-F242 archive: negative_outcomes="
                f"{case.get('negative_outcomes', 0)}>3, positive_outcomes=0. "
                f"Case preserved for forensic recall; no longer in active "
                f"index (per aria_infinite_memory.md, never deleted)."
            )
            await rs.set_json(_case_key(case["id"]), case)
            archived += 1
            continue
        new_index.append(entry)

    _index_cache_set(new_index)
    await _save_index()

    meta = await _load_meta()
    meta["total_cases"] = len(new_index)
    meta["last_consolidate"] = now
    _mark_meta_dirty()  # R-F268
    await _save_meta()

    logger.info(
        "reasoning_library consolidated (R-F242): archived %d (preserved), "
        "missing %d, remaining %d in active index",
        archived, pruned, len(new_index),
    )
    return {
        "archived": archived,        # R-F242: replaces "pruned" for active archives
        "missing": pruned,           # data lost from Redis (not our archive action)
        "remaining": len(new_index),
        "pruned": pruned + archived,  # legacy field for old consumers; will be removed when consumers updated
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
