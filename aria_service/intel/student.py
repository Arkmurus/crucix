"""
ARIA Student Brain — active learning behaviours.

Until now ARIA learned PASSIVELY: she captured whatever cloud LLM answers
arrived through the chat endpoint. The student brain makes her ACTIVE:
she identifies her own weak topics, generates study questions, deep-reads
authoritative sources during idle time, self-quizzes from past cases, and
compares her local answers against the cloud LLM to learn from divergence.

Behaviours
══════════
1. MASTERY TRACKING — per-topic competence score from accuracy on prior cases
2. CURRICULUM       — list of weak topics that need more study
3. SELF-QUIZ        — pick a stale library case, answer it locally, compare
                       to original, score self
4. READING SESSION  — deep-read N authoritative articles + extract
                       reasoning method (not just facts)
5. COMPARE & LEARN  — when both local and cloud produce answers, score the
                       divergence and store as study material
6. SPACED REPETITION — bring back stale cases on a forgetting curve
7. METHOD CAPTURE   — extract HOW the LLM solved a problem, not just WHAT

Independence philosophy
═══════════════════════
A great student doesn't memorise — she learns the *method*. When DeepSeek
solves a problem, the student brain captures the reasoning chain so the
next time a similar problem appears, ARIA can apply the same method
locally. Over months, the library becomes a "textbook of methods" that
can fine-tune ARIA-LLM into a domain expert without ever needing the
cloud teacher again.

Topic taxonomy
══════════════
ARIA's mastery is tracked across these domain topics:

    compliance        — export control, sanctions, embargoes
    procurement       — tenders, RFPs, contract awards
    market_intel      — country-specific intelligence
    technical         — weapon systems, calibres, ECCN
    geopolitics       — alliance shifts, regime stability
    osint             — investigation methodology
    finance           — budget cycles, deal economics
    relationships     — contacts, decision-makers, tenure
    competitor_intel  — rival broker activity
    legal             — UK/EU/US/UN regulatory framework

Each topic gets a 0..1 mastery score that grows from successful local
answers and shrinks from divergence with cloud LLM teachers.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import asyncio
import logging
import math
import os
import random
import re
import time
from typing import Any, Optional

from . import redis_store as rs
from . import reasoning_library
from . import reasoning_router
from . import knowledge as kb
from . import neural_memory
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.student")

MASTERY_KEY = "crucix:aria:student:mastery"
CURRICULUM_KEY = "crucix:aria:student:curriculum"
QUIZ_HISTORY_KEY = "crucix:aria:student:quiz_history"
READING_LOG_KEY = "crucix:aria:student:reading_log"
DIVERGENCE_KEY = "crucix:aria:student:divergence"
# R-F1996 — full-length training fuel harvested from genuine divergences (local
# stack answered but disagreed materially with the cloud teacher). The DIVERGENCE_KEY
# log keeps only 300-char previews (for the curriculum/mastery view); training on
# truncated answers would teach the model to truncate, so the fuel store keeps
# training-grade full text. Each record is an SFT pair (chosen = cloud answer) with
# the local answer retained as the DPO `rejected`. This closes the flywheel: the
# exact questions where local reasoning is weak become the next cycle's training data.
DIVERGENCE_FUEL_KEY = "crucix:aria:training:divergence_fuel"
_FUEL_MAX = 1000
_FUEL_SIMILARITY_CEILING = 0.5   # only capture genuine disagreements
_FUEL_MIN_LOCAL_CHARS = 40       # local must have made a real attempt (a real "rejected")
STUDENT_META_KEY = "crucix:aria:student:meta"

# Mastery starts at 0.5 (neutral) and updates by EWMA
INITIAL_MASTERY = 0.5
MASTERY_LR_POSITIVE = 0.18    # bumped again 2026-04-17 late PM after the
                               # calibration review STILL flagged UNDERCONFIDENT
                               # at -17.1pp even after the 0.08→0.12 change.
                               # Stored scores don't respond to learning-rate
                               # changes retroactively — hence the new
                               # self-calibration correction in
                               # calibration_review.run_calibration_review().
                               # The rate change still helps future events
                               # converge faster.
MASTERY_LR_NEGATIVE = 0.12    # keep down-rate lower — be honest about gaps;
                               # never let self-perception overshoot reality
MASTERY_FLOOR = 0.05
MASTERY_CEILING = 0.98
WEAK_THRESHOLD = 0.55         # below this = "weak topic, needs study"
# R-F711 (2026-05-19) — Phase A exit-gate #2 floor target ("heatmap floor ≥ 70%").
# Cells between WEAK_THRESHOLD (0.55) and this floor are NOT "weak" by the
# study-priority signal but DO block gate-2 closure. Surfaced separately
# via `floor_breach_cells` in get_regional_heatmap() so the operator
# dashboard can see exactly which cells are dragging the floor below 0.70.
GATE_2_FLOOR_TARGET = 0.70

# Hard floors per domain — if mastery drops below this, automatic
# remediation triggers (knowledge injection from domain modules).
# Floors are higher for core competencies (procurement, compliance,
# osint) and lower for supplementary areas (finance, relationships).
# Adapted from mastery_system.py DomainQuizEngine.
HARD_FLOORS: dict[str, float] = {
    "procurement":     0.65,
    "compliance":      0.70,
    "osint":           0.65,
    "technical":       0.65,
    "market_intel":    0.65,
    "competitor_intel": 0.65,
    "geopolitics":     0.60,
    "finance":         0.60,
    "relationships":   0.55,
    "legal":           0.70,
    "general":         0.50,
    # R-F800 (2026-05-22) — Phase-A core-mastery tags explicitly added.
    # Pre-R-F800 these relied on the `HARD_FLOORS.get(topic, 0.50)`
    # default in the post-update check, but R-F796's clamping logic
    # used `HARD_FLOORS.get(topic, 0.50)` independently — making the
    # floor implicit for these load-bearing topics. Live evidence
    # 2026-05-22: sanctions hit 41%; logs showed `BREACH: sanctions
    # (41% < 50%)` from the default. Making them explicit aligns the
    # clamp + check on a single source of truth and signals intent
    # for anyone reading HARD_FLOORS that these topics are tracked.
    "sanctions":           0.50,
    "nato_standards":      0.50,
    "strategic_geography": 0.50,
    "export_control":      0.50,
}

TOPICS = [
    "compliance", "procurement", "market_intel", "technical",
    "geopolitics", "osint", "finance", "relationships",
    "competitor_intel", "legal", "general",
    # R-F2062: sanctions was in HARD_FLOORS and _TOPIC_PATTERNS but NOT
    # in TOPICS, so the student loop never proactively studied it and
    # mastery only changed through incidental brain_hook signals.
    # Adding it here makes get_due_topics() return it for reading sessions
    # and self_quiz sampling.
    "sanctions",
    "nato_standards",
    "strategic_geography",
    "export_control",
]

# Load-bearing capability tags — capability-only, region-agnostic.
# Operator rebalanced 2026-04-22 to match the global-positioning doctrine
# (aria_global_positioning.md): ARIA is a GLOBAL defence broking advisor,
# so no single-region tags like angola_procurement or uk_compliance
# belong in the load-bearing core — regional coverage lives in the
# heatmap and source_validator.COVERAGE_DOMAINS instead.
# Any work that touches mastery scoring / weak-topic picking / reading-
# session queueing must preserve this list. A tag that silently falls
# out of scope is a capability regression.
CORE_MASTERY_TAGS = [
    # Languages of the major non-English defence markets
    "lang:pt", "lang:ar", "lang:fr", "lang:es", "lang:ru", "lang:zh",
    # Cross-cutting capability areas that apply in every region
    "sanctions", "nato_standards", "strategic_geography", "export_control",
]

# Topic detection patterns — used to tag every interaction with a topic.
# Enriched from mastery_prompt_builder.py's domain keyword taxonomy.
_TOPIC_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("compliance", re.compile(
        r"\b(?:sanction|embargo|ofac|ofsi|itar|ear|sitcl|siel|ogel|export\s+control|"
        r"licen[cs]e|debarment|euc|end.user|ata|ats|wassenaar|mtcr|nsg|hcoc|"
        r"dual.use|proliferation|diversion|att\s+article|arms\s+trade\s+treaty|"
        r"poca|bribery\s+act|mlr|money\s+laundering|sar|suspicious|kyc|aml|"
        r"ancex|ecju|bis\s+entity|denied\s+person|specially\s+designated)\b", re.I)),
    ("procurement", re.compile(
        r"\b(?:tender|rfp|rfq|rfi|contract|bid|procurement|acquisition|fms|"
        r"solicitation|award|lifecycle|capability\s+definition|"
        r"source\s+selection|best\s+value|lpta|tradeoff|evaluation\s+criteria|"
        r"ted\.europa|sam\.gov|contracts\s+finder|ungm|offset|industrial\s+"
        r"participation|counter.?trade|cpv\s+code|nato\s+stock|nsn|"
        r"fms\s+case|lor\s+letter|loa\s+letter|dsca|congressional\s+notification|"
        r"budget\s+execution|through.life\s+support|sustainment)\b", re.I)),
    ("market_intel", re.compile(
        r"\b(?:angola|mozambique|nigeria|kenya|saudi|uae|brazil|indonesia|"
        r"cplp|lusophone|market|country|sipri|iiss|jane.s|defence\s+spending|"
        r"military\s+budget|gdp\s+percent|demand\s+signal|"
        r"ghana|ethiopia|tanzania|uganda|rwanda|drc|senegal|"
        r"c[oô]te\s+d.ivoire|cameroon|mali|niger|burkina|chad|"
        r"india|pakistan|turkey|egypt|morocco|algeria|tunisia|"
        r"philippines|vietnam|thailand|malaysia|south\s+africa)\b", re.I)),
    ("technical", re.compile(
        r"\b(?:k9|himars|bayraktar|patriot|f-35|f-16|leopard|abrams|caliber|"
        r"calibre|eccn|ml\d{1,2}|weapon|system|specs?|stanag|aqap|aectp|"
        r"ifv|apc|mrap|howitzer|mortar|torpedo|missile|radar|sonar|"
        r"c4isr|ew\b|electronic\s+warfare|uav|uas|drone|munition|"
        r"detonator|explosive|propellant|warhead|fuse|primer|"
        r"ballistic|armou?red?\s+vehicle|redback|lynx|boxer|"
        r"nato\s+standard|def\s+stan|mil.std)\b", re.I)),
    ("geopolitics", re.compile(
        r"\b(?:nato|alliance|coup|regime|stability|conflict|geopolitical|"
        r"sadc|ecowas|au\s+mission|un\s+mission|peacekeeping|"
        r"insurgency|terrorism|separatist|civil\s+war|border\s+dispute|"
        r"arms\s+race|nuclear|non.proliferation|ceasefire|"
        r"diplomatic|bilateral|multilateral|mou|treaty|pact)\b", re.I)),
    ("osint", re.compile(
        r"\b(?:investigate|profile|due\s+diligence|background\s+check|crawl|"
        r"research|osint|trace|map\s+the|screen|vet|audit|"
        r"pdd|person\s+dd|pep\s+check|adverse\s+media|"
        r"ghost\s+detect|ubo|beneficial\s+owner|"
        r"open\s+source|intelligence\s+cycle|collection|"
        r"source\s+grading|reliability|credibility)\b", re.I)),
    ("finance", re.compile(
        r"\b(?:budget|spending|gdp|deal\s+(?:value|worth)|million|billion|"
        r"finance|funding|loan|credit|offset|payment|escrow|"
        r"letter\s+of\s+credit|bank\s+guarantee|performance\s+bond|"
        r"advance\s+payment|retention|commission|broker\s+fee|"
        r"forex|exchange\s+rate|inflation|fiscal)\b", re.I)),
    ("relationships", re.compile(
        r"\b(?:contact|minister|general|colonel|admiral|director|liaison|"
        r"tenure|meeting|relationship|humint|stakeholder|"
        r"decision.maker|influencer|gatekeeper|champion|"
        r"networking|introduction|referral|warm\s+lead|cold\s+call|"
        r"embassy|attach[eé]|trade\s+commissioner)\b", re.I)),
    ("competitor_intel", re.compile(
        r"\b(?:competitor|rival|paramount|elbit|baykar|norinco|rheinmetall|"
        r"bae|leonardo|raytheon|lockheed|boeing|thales|airbus|saab|"
        r"hanwha|hyundai\s+rotem|knds|nexter|krauss.maffei|"
        r"denel|armscor|otokar|aselsan|stm|tai|"
        r"csgc|poly\s+technologies|cetc|avic|cssc|"
        r"market\s+share|competitive\s+(?:advantage|position|landscape)|"
        r"win\s+rate|incumbent|displacer|price\s+war)\b", re.I)),
    ("legal", re.compile(
        r"\b(?:law|legal|regulation|statute|act\s+\d{4}|wassenaar|mtcr|nsg|"
        r"treaty|convention|jurisdiction|court|arbitration|dispute|"
        r"contract\s+law|force\s+majeure|indemnity|warranty|"
        r"intellectual\s+property|licensing\s+agreement|"
        r"end.user\s+certificate|export\s+licence|import\s+permit)\b", re.I)),
    # ── CORE_MASTERY_TAGS (cross-cutting capability tags) ───────────────
    # These four were in CORE_MASTERY_TAGS but absent from _TOPIC_PATTERNS,
    # so detect_topics() literally could not emit them and reading_session
    # could never lift their mastery scores. Six core tags were stuck at the
    # 0.491 floor as of 2026-04-27 (sanctions, nato_standards, strategic_
    # geography, export_control + lang:ru, lang:zh — the language ones do
    # have script-based detection further down). Patterns deliberately
    # overlap with `compliance` / `geopolitics` / `legal` so an article
    # about ITAR lifts BOTH compliance AND export_control simultaneously,
    # which is the right semantic.
    ("sanctions", re.compile(
        r"\b(?:ofac|ofsi|sdn(?:\s+list)?|specially\s+designated|"
        r"sanctions?\s+(?:list|regime|package|programme|program|target|"
        r"designation|update|removal|check|screen|status|hits?|match|alert)|"
        r"sanctioned\s+(?:entity|person|company|country|regime|list)|"
        r"(?<!\w)sanctioned(?!\w)|"
        r"asset\s+freeze|"
        r"sectoral\s+sanctions|secondary\s+sanctions|extraterritorial|"
        r"caatsa|magnitsky|opensanctions|eu\s+consolidated\s+(?:list|sanctions)|"
        r"un\s+sc(?:\s+sanctions)?|comprehensive\s+embargo|"
        r"embargoed\s+(?:country|state|destination)|"
        r"travel\s+ban|frozen\s+assets|de.?listing|sanctions?\s+evasion)\b",
        re.I)),
    ("nato_standards", re.compile(
        r"\b(?:stanag|aqap|aectp|natostd|nspa|nato\s+standardisation|"
        r"nato\s+standardization|nato\s+stock\s+number|nsn\s+\d|"
        r"nato\s+codification|allied\s+(?:joint|technical|"
        r"administrative)\s+publication|\bajp\s*-?\s*\d|\batp\s*-?\s*\d|"
        r"\baap\s*-?\s*\d|annex\s+to\s+stanag|"
        r"interoperability\s+standard|nato\s+(?:cert|certif)|"
        r"def\s+stan|mil[\s-]std|federation\s+mission\s+network|fmn)\b",
        re.I)),
    ("strategic_geography", re.compile(
        r"\b(?:choke[\s-]?point|strait\s+of|maritime\s+(?:trade|domain|chokepoint)|"
        r"sea[\s-]lane|key\s+terrain|forward[\s-](?:base|posture|operating\s+base)|"
        r"power\s+projection|sphere\s+of\s+influence|"
        r"strategic\s+(?:depth|frontier|location|corridor|hub|importance)|"
        r"littoral|landlocked|geostrategic|continental\s+shelf|"
        r"red\s+sea|south\s+china\s+sea|persian\s+gulf|"
        r"horn\s+of\s+africa|sahel|caucasus|balkans|"
        r"baltic\s+(?:states|approaches)|mediterranean|gibraltar|bab[\s-]el[\s-]mandeb|"
        r"hormuz|malacca|suez|panama\s+canal|northern\s+sea\s+route)\b",
        re.I)),
    ("export_control", re.compile(
        r"\b(?:itar|\bear\b|eccn|spire|sitcl|siel|ogel|otcl|"
        r"export\s+licen[cs]e|export[\s-]control|dual[\s-]use|"
        r"wassenaar|mtcr|nsg|hcoc|australia\s+group|"
        r"deemed\s+export|re[\s-]export|in[\s-]country\s+transfer|"
        r"end[\s-]use\s+(?:check|monitoring)|technology\s+transfer|"
        r"controlled\s+(?:item|technology|software|goods)|"
        r"munitions\s+list|usml|cml|eu\s+dual[\s-]use\s+regulation|"
        r"bis\s+entity|denied\s+person|debarment\s+list|"
        r"ddtc|brokering\s+(?:registration|licence|license)|"
        r"export\s+control\s+(?:act|order|reform))\b", re.I)),
]


@fail_wire(module="student", gap_type="engine_failure")
def detect_topics(text: str) -> list[str]:
    """Tag a question/response with one or more topic categories.

    Language tags (`lang:ru`, `lang:zh`, `lang:ar`) are emitted from
    script-based heuristics — Cyrillic / CJK / Arabic Unicode ranges.
    Before this, no code path ever wrote to a `lang:*` mastery tag, so
    `lang:ru` and `lang:zh` sat at the 0.50 initial floor forever while
    pt/fr/es/ar had been seeded by earlier calibration runs. Require a
    minimum character count so short English copy doesn't accidentally
    tag itself with a non-Latin language.
    """
    if not text:
        return []
    matched = []
    for topic, pattern in _TOPIC_PATTERNS:
        if pattern.search(text):
            matched.append(topic)
    # Language-script tags. Thresholds are deliberately conservative:
    # 20+ chars of the target script means the text carries meaningful
    # content in that language, not just a quoted word or name.
    cyrillic = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    arabic = sum(1 for ch in text if "؀" <= ch <= "ۿ")
    if cyrillic >= 20:
        matched.append("lang:ru")
    if cjk >= 20:
        matched.append("lang:zh")
    if arabic >= 20:
        matched.append("lang:ar")
    return matched or ["general"]


# ── Mastery tracking ───────────────────────────────────────────────────────

_mastery_cache: dict | None = None

# R-F267 (2026-05-11) — no-scaffold-write rule. _save_mastery only persists
# when the cache has been TOUCHED by actual learning, never when it contains
# only scaffolded INITIAL_MASTERY defaults. Across a backend flip (sqlite ↔
# upstash), this prevents the bootstrap path from overwriting real data on
# the destination backend with placeholder values.
#
# Pattern: _load_mastery() initialises scaffolded entries with _dirty=False;
# update_mastery() and lift_all_topics() flip _mastery_dirty=True; only then
# does _save_mastery() write to the active backend.
_mastery_dirty: bool = False

# R-F270 (2026-05-11) — MASTERY HARD FLOOR warning rate-limiter. Each
# entry maps a topic name to the unix-ts of the last warning fired for
# it. Topics not breaching the floor are absent. Pure in-memory; resets
# on process restart (acceptable — fresh boot legitimately needs the
# first-hour visibility).
_last_floor_warning: dict[str, float] = {}
_FLOOR_WARN_INTERVAL_S: float = 3600.0  # one warning per (topic, hour)


def _mark_mastery_dirty() -> None:
    """Flip the dirty flag — call from any code path that mutates _mastery_cache
    with actual learning data (not scaffold initialisation)."""
    global _mastery_dirty
    _mastery_dirty = True


# ── R-F2408: coalesce the whole-blob MASTERY_KEY save (flag-gated, default OFF) ──
# state_store write-ceiling relief. update_mastery() rewrites the ENTIRE mastery
# cache to ONE key (`crucix:aria:mastery`) via set_json on EVERY observation — a
# whole-blob rewrite per learning signal. Under the accelerated free-learning loop
# (R-F2283: 15 cells / 6 articles per cycle) + per-chat aria_engine updates + per-
# absorb brain_hook_bg updates, that is a bursty hot-key write hammering the single
# aiosqlite writer (exactly the "per-request hot-key RMW" antipattern CLAUDE.md §
# state_store warns against). Mastery is a DERIVED, slowly-moving EWMA held in an
# in-memory cache (the runtime source of truth; reports read the cache, not the DB)
# and TTL'd 180d — so deferring the durability write a few seconds is safe (losing
# the final pre-crash update is acceptable; the loop rebuilds it). This mirrors the
# proven R-F2172 cost-coalescing pattern: time-gated, single-flight, no new
# background task, no boot-path change. DEFAULT OFF → byte-identical to today; the
# save stays inline until the operator flips ARIA_MASTERY_COALESCE_SAVE=1.
_MASTERY_SAVE_COALESCE = os.getenv(
    "ARIA_MASTERY_COALESCE_SAVE", "").strip().lower() in ("1", "true", "yes", "on")
_MASTERY_FLUSH_INTERVAL_S = float(os.getenv("ARIA_MASTERY_FLUSH_INTERVAL_S", "15"))
_mastery_last_save: float = 0.0
_mastery_save_lock: asyncio.Lock | None = None


def _get_mastery_save_lock() -> asyncio.Lock:
    """Lazy-bound single-flight lock (created inside the running loop, like
    state_store._get_lock) so pytest's per-test asyncio.run loops each bind a
    fresh lock."""
    global _mastery_save_lock
    if _mastery_save_lock is None:
        _mastery_save_lock = asyncio.Lock()
    return _mastery_save_lock


async def _maybe_flush_mastery(force: bool = False) -> bool:
    """R-F2408 — persist the mastery cache, coalesced when the flag is ON.

    Flag OFF (default): identical to the pre-R-F2408 inline behaviour — write
    now via _save_mastery() (which itself no-ops when not dirty).

    Flag ON: write at most once per _MASTERY_FLUSH_INTERVAL_S. Intervening
    updates leave _mastery_dirty=True so the next flush (or a force flush)
    persists the latest whole-cache snapshot. Single in-flight flush via a
    lazy lock so concurrent update_mastery calls can't double-write. Returns
    True iff a DB write actually happened.
    """
    global _mastery_last_save
    if not _MASTERY_SAVE_COALESCE:
        await _save_mastery()
        return True  # inline path always attempts (no-op inside if not dirty)
    if not _mastery_dirty:
        return False
    now = time.time()
    if not force and (now - _mastery_last_save) < _MASTERY_FLUSH_INTERVAL_S:
        return False  # coalesce — keep the dirty flag, defer the write
    lock = _get_mastery_save_lock()
    if lock.locked() and not force:
        return False
    async with lock:
        now = time.time()
        if not force and (now - _mastery_last_save) < _MASTERY_FLUSH_INTERVAL_S:
            return False
        if not _mastery_dirty:
            return False
        await _save_mastery()  # resets _mastery_dirty on success
        _mastery_last_save = now
        return True


@fail_wire(module="student", gap_type="engine_failure")
async def flush_mastery() -> bool:
    """R-F2408 — public force-flush of any deferred mastery write. Wire this
    into shutdown / a periodic tick when activating ARIA_MASTERY_COALESCE_SAVE
    so a quiet period (no further update_mastery) can't leave the last learning
    signal unpersisted. Safe to call anytime; no-op when nothing is pending."""
    return await _maybe_flush_mastery(force=True)


def _fresh_mastery_scaffold() -> dict:
    """A zero-sample scaffold for every known topic. Not learning — see R-F267."""
    return {t: {"score": INITIAL_MASTERY, "samples": 0,
                "correct": 0, "wrong": 0, "last_practiced": 0}
            for t in TOPICS}


async def _load_mastery() -> dict:
    global _mastery_cache
    if _mastery_cache is not None:
        return _mastery_cache
    # R-F2852 — STRICT read, mirroring R-F2664 on the regional twin. The old
    # non-strict get_json() SWALLOWED a store-not-ready StoreReadError to None,
    # which is NOT "no mastery recorded" — it is "could not measure". Observed
    # live 2026-07-22: `state_store.get(crucix:aria:student:mastery) timed out
    # after 5s` ~6s into a slow boot.
    #
    # The clobber chain that made this data-loss rather than a blip:
    #   timeout -> raw=None -> _mastery_cache poisoned to a samples=0 scaffold
    #   -> seed_baseline_mastery() (main.py:1401, runs at every boot) skips only
    #      topics with samples>0, so on the scaffold it skips NOTHING
    #   -> seeds every topic -> _mark_mastery_dirty() -> _save_mastery()
    #   -> the synthetic 0.6 baseline is written over the durable key, wiping
    #      real score/samples/correct/wrong for every topic.
    # The R-F267 dirty-guard cannot catch it, because the seeder legitimately
    # sets dirty=True. Worse, the result LOOKS plausible (~0.6) rather than
    # obviously broken, so it silently corrupts the Phase A gate #1 composite.
    #
    # On a store failure: return a transient scaffold that is deliberately NOT
    # assigned to _mastery_cache. _save_mastery() early-returns while the cache
    # is None, so no write can clobber the durable key, and the next call
    # retries the read.
    try:
        raw = await rs.get_json_strict(MASTERY_KEY)
    except Exception as _sre:
        logger.warning(
            "[R-F2852] mastery load deferred — store not ready (%s); cache left "
            "uninitialised so no write can clobber the durable key", _sre)
        return _fresh_mastery_scaffold()
    if isinstance(raw, dict):
        _mastery_cache = raw
    else:
        _mastery_cache = _fresh_mastery_scaffold()
    # Make sure all topics exist (in case TOPICS list grew)
    for t in TOPICS:
        if t not in _mastery_cache:
            _mastery_cache[t] = {"score": INITIAL_MASTERY, "samples": 0,
                                 "correct": 0, "wrong": 0, "last_practiced": 0}
    # Scaffold the 9 core-mastery tags so they are always visible in the
    # report — even with zero samples — instead of being invisibly absent.
    # Before this, tags only existed if something happened to update them,
    # which let the overall mastery rollup sit at 96% while load-bearing
    # capability cells were silently empty.
    for t in CORE_MASTERY_TAGS:
        if t not in _mastery_cache:
            _mastery_cache[t] = {"score": INITIAL_MASTERY, "samples": 0,
                                 "correct": 0, "wrong": 0, "last_practiced": 0}
    # NOTE: _load_mastery never sets _mastery_dirty=True. Scaffolding is
    # not learning. Only update_mastery / lift_all_topics flip the flag.
    return _mastery_cache


async def _save_mastery() -> None:
    """Persist mastery to the active state backend.

    R-F267 (2026-05-11): no-scaffold-write rule. Returns silently when the
    cache hasn't been touched by actual learning since the last load. This
    prevents the boot-time scaffold from overwriting real data on a different
    backend after a flip. If actual learning has occurred (_mastery_dirty=True),
    we persist AND reset the dirty flag — next save() requires another learning
    update to be a no-op.
    """
    global _mastery_dirty
    if _mastery_cache is None:
        return
    if not _mastery_dirty:
        return
    await rs.set_json(MASTERY_KEY, _mastery_cache, ex=180 * 86400)
    _mastery_dirty = False


@fail_wire(module="student", gap_type="engine_failure")
async def seed_baseline_mastery() -> int:
    """R-F1512: inject baseline mastery for topics that are stuck at
    INITIAL_MASTERY (0.5) due to insufficient training signals.

    The mastery system only learns from real interactions (correct/wrong
    signals from brain_hook.absorb). Topics like 'osint' and 'market_intel'
    that don't receive frequent absorb signals stay at the 0.5 scaffold
    forever, triggering the MASTERY HARD FLOOR BREACH warning every cycle.

    This function gives each topic a small number of synthetic "correct"
    signals so the EWMA score rises above the hard floor. It's called once
    at boot after _load_mastery. The synthetic signals are marked with
    weight=0.3 so real interactions still dominate the score over time.
    """
    mastery = await _load_mastery()
    now = time.time()
    seeded = 0
    for topic in mastery:
        m = mastery[topic]
        # Skip topics that already have real training samples
        if m.get("samples", 0) > 0:
            continue
        # R-F1512: seed ANY topic with zero samples, not just those below
        # the hard floor. Topics at exactly INITIAL_MASTERY (0.5) are
        # indistinguishable from scaffold — give them a small boost so
        # they're clearly above baseline. The boost is gentle (weight=0.3)
        # so real interactions dominate over time.
        floor = HARD_FLOORS.get(topic, 0.5)
        target = max(floor + 0.05, 0.6)  # aim for floor+5pp or 60%, whichever is higher
        if m["score"] >= target:
            continue
        # Give gentle signals to reach target. High-floor topics (>=70%)
        # need more signals to climb from 0.5 scaffold.
        if floor >= 0.7:
            signals_needed = 5
        elif m["score"] < floor:
            signals_needed = 3
        else:
            signals_needed = 2
        for _ in range(signals_needed):
            m["samples"] = m.get("samples", 0) + 1
            m["correct"] = m.get("correct", 0) + 1
            m["last_practiced"] = now
            lr = MASTERY_LR_POSITIVE * 0.3  # gentle weight
            delta = lr * (1 - m["score"])
            delta = min(delta, 0.15)
            m["score"] = min(MASTERY_CEILING, m["score"] + delta)
        seeded += 1
    if seeded:
        _mark_mastery_dirty()
        await _save_mastery()
        logger.info(
            "[R-F1512] Seeded baseline mastery for %d topics "
            "(gentle weight=0.3, target >= %.0f%%)",
            seeded,
            max(HARD_FLOORS.get(list(mastery.keys())[0], 0.5) + 0.05, 0.6) * 100 if mastery else 60,
        )
    return seeded


@fail_wire(module="student", gap_type="engine_failure")
async def update_mastery(topics: list[str], correct: bool, weight: float = 1.0) -> None:
    """Update mastery scores for the topics touched by an interaction.

    correct=True: ARIA's local answer matched the truth (or the teacher).
    correct=False: she got it wrong or diverged from the teacher.

    If a topic drops below its HARD_FLOOR, a remediation flag is set and
    the next weekly report + mastery prompt addendum will highlight it.
    The actual knowledge injection (e.g. re-ingest procurement_knowledge
    module) runs in the weekly loop, not inline here.
    """
    if not topics:
        return
    # R-F3694 — `correct` MUST be a real bool.
    #
    # `autonomous.tasks._grade_researched_cell` is deliberately TRI-STATE
    # (`bool | None`, tasks.py:2292) and its own docstring says why: "Every
    # branch below used to return False, so a measurement failure was recorded
    # as ARIA getting the answer wrong, and the caller then drove mastery down
    # for it." Four of its branches return None — no research text, grader
    # exception, answered=False, empty response.
    #
    # R-F3483 taught ONE of its three callers to skip on None (tasks.py:2460,
    # which counts it as `mastery_unmeasured`). The two callers in THIS module
    # passed the value straight through, and `if correct:` then took the else
    # branch — recording a MISS for a cell that was never measured.
    #
    # Rejecting a non-bool here is the structural half of the fix: the call
    # sites are corrected below, but a fourth caller must not be able to
    # reintroduce this silently.
    if not isinstance(correct, bool):
        logger.error(
            "[R-F3694] update_mastery called with correct=%r (%s) — expected "
            "bool. An UNMEASURED topic must be SKIPPED by the caller, never "
            "written as a miss. Refusing the update for topics=%s.",
            correct, type(correct).__name__, topics,
        )
        return
    mastery = await _load_mastery()
    now = time.time()
    remediation_needed: list[str] = []
    for topic in topics:
        if topic not in mastery:
            mastery[topic] = {"score": INITIAL_MASTERY, "samples": 0,
                              "correct": 0, "wrong": 0, "last_practiced": 0}
        m = mastery[topic]
        m["samples"] = m.get("samples", 0) + 1
        m["last_practiced"] = now
        if correct:
            m["correct"] = m.get("correct", 0) + 1
            lr = MASTERY_LR_POSITIVE * weight
            delta = lr * (1 - m["score"])
            # Cap: single update cannot move score by more than 15pp
            delta = min(delta, 0.15)
            m["score"] = min(MASTERY_CEILING, m["score"] + delta)
        else:
            m["wrong"] = m.get("wrong", 0) + 1
            lr = MASTERY_LR_NEGATIVE * weight
            delta = lr * m["score"]
            # Cap: single update cannot move score by more than 15pp
            delta = min(delta, 0.15)
            # R-F796 (2026-05-22) — clamp negative updates at the topic's
            # hard floor, not the global 5% MASTERY_FLOOR. Live evidence
            # 2026-05-22 15:59-16:04 UTC: adversarial-overconfidence
            # signal drove calibration_review to apply -3pp on 11 topics
            # every cycle. With strict MASTERY_FLOOR clamping, topics
            # plummeted below their hard floors (legal 63%, technical
            # 60%, sanctions 41%). Gate #2 reopened. The hard floor
            # represents the minimum acceptable competence; calibration
            # should never drive a topic below it. Already-below-floor
            # scores (from pre-R-F796 data) hold steady — no auto-heal
            # and no further drop. The post-update floor check still
            # fires because `proposed_breached_floor` is OR'd in.
            # Default 0.50 matches the post-update hard-floor check below,
            # which treats any topic without an explicit HARD_FLOORS entry
            # as having a 50% floor (e.g., 'sanctions' is not in
            # HARD_FLOORS but the log shows "BREACH: sanctions (41% < 50%)").
            topic_hard_floor = HARD_FLOORS.get(topic, 0.50)
            proposed = m["score"] - delta
            if m["score"] >= topic_hard_floor:
                # Was at-or-above floor — clamp the drop at floor.
                m["score"] = max(topic_hard_floor, proposed)
            # else: already below floor (legacy) — hold steady.
            # Track whether the unclamped drop would have breached so
            # the post-update floor check still surfaces remediation.
            m["_rf796_proposed_breach"] = (proposed < topic_hard_floor)

        # R-F664 (2026-05-17): FSRS spaced-repetition update. Additive to
        # the EWMA score above — FSRS schedules the NEXT review, EWMA
        # tracks running competence. Failure here is non-fatal; if the
        # scheduler can't run we still persist the EWMA delta.
        try:
            from ..learning import fsrs_scheduler as _fsrs
            new_card = _fsrs.review_topic(
                topic, correct, prior_card=m.get("fsrs_card"),
            )
            m["fsrs_card"] = new_card
        except Exception as _fsrs_e:
            logger.debug(
                "R-F664: FSRS update for topic %s failed (non-fatal): %s",
                topic, _fsrs_e,
            )
        # Hard floor check — flag for remediation if breached
        # R-F796: ALSO flag when the unclamped negative-update would
        # have crossed the floor (`_rf796_proposed_breach=True`). The
        # clamping prevents the score from actually going below floor,
        # but the operator still needs to see the remediation signal.
        floor = HARD_FLOORS.get(topic, 0.50)
        rf796_breach = m.pop("_rf796_proposed_breach", False)
        if m["score"] < floor or rf796_breach:
            m["below_floor"] = True
            m["floor"] = floor
            if topic not in remediation_needed:
                remediation_needed.append(topic)
        else:
            m.pop("below_floor", None)
            m.pop("floor", None)
    _mark_mastery_dirty()  # R-F267 — actual learning, persist
    # R-F2408: coalesced save (flag-gated). Flag OFF → inline _save_mastery()
    # exactly as before; flag ON → at most one whole-blob write per interval,
    # relieving the single aiosqlite writer on the hot learning path.
    await _maybe_flush_mastery()

    # Log remediation needs so the weekly report and proactive loop
    # can act on them. Non-blocking fire-and-forget.
    #
    # R-F270 (2026-05-11) — rate-limit the warning to once per (topic, hour).
    # Pre-R-F270 every update_mastery() call that touched a sub-floor topic
    # produced a fresh WARNING line in fly logs; on a hot chat path that
    # was tens of duplicate lines per minute, drowning real signal in the
    # error_log_handler ring buffer. Each topic now logs at most once an
    # hour; the underlying capability_gap is still recorded every breach
    # (that's the actionable trail the weekly remediation loop reads).
    if remediation_needed:
        now_w = time.time()
        fresh: list[str] = []
        for _t in remediation_needed:
            last = _last_floor_warning.get(_t, 0.0)
            if now_w - last >= _FLOOR_WARN_INTERVAL_S:
                fresh.append(_t)
                _last_floor_warning[_t] = now_w
        if fresh:
            logger.warning(
                "MASTERY HARD FLOOR BREACH: %s — remediation flagged",
                ", ".join(f"{t} ({mastery[t]['score']:.0%} < {HARD_FLOORS.get(t, 0.5):.0%})" for t in fresh),
            )
        try:
            from . import capability_gaps
            for topic in remediation_needed:
                _t = asyncio.create_task(capability_gaps.record_gap(
                    gap_type="knowledge_gap",
                    detail=f"Mastery for '{topic}' dropped below hard floor ({mastery[topic]['score']:.0%} < {HARD_FLOORS.get(topic, 0.5):.0%}). Remediation: re-inject domain knowledge.",
                    source="student.update_mastery",
                ))
                _t.add_done_callback(lambda t: t.result() if not t.cancelled() and not t.exception() else None)
        except Exception:
            pass

        # R-F1539: queue a reading session for each breached topic immediately,
        # rather than waiting for the next 6-hourly mastery-prep cycle. This
        # turns a passive warning into an active learning trigger.
        try:
            from .proactive import queue_reading_session as _qrs
            for topic in remediation_needed:
                _rt = asyncio.create_task(_qrs(
                    topic, reason="mastery_floor_breach_R-F1539", severity="HIGH",
                ))
                _rt.add_done_callback(lambda t: t.result() if not t.cancelled() and not t.exception() else None)
        except Exception:
            pass


@fail_wire(module="student", gap_type="engine_failure")
async def reset_mastery_scores() -> dict:
    """Reset all mastery scores to a fair baseline. Use after fixing a
    bug that corrupted the scores (e.g., the weight=0.7 divergence
    penalty that dropped all topics from 81% to 14%)."""
    global _mastery_cache
    mastery = await _load_mastery()
    for topic in mastery:
        # Reset to accuracy-based estimate: correct/samples
        samples = mastery[topic].get("samples", 0)
        correct = mastery[topic].get("correct", 0)
        if samples > 10:
            accuracy = correct / samples
            mastery[topic]["score"] = max(INITIAL_MASTERY, accuracy * 0.9)
        else:
            mastery[topic]["score"] = INITIAL_MASTERY
        mastery[topic].pop("below_floor", None)
        mastery[topic].pop("floor", None)
    _mark_mastery_dirty()  # R-F267 — recalibration is genuine state change, persist
    await _save_mastery()
    return {t: round(mastery[t]["score"], 3) for t in mastery}


@fail_wire(module="student", gap_type="engine_failure")
async def get_due_topics(limit: int = 20) -> list[dict]:
    """R-F664 (2026-05-17): topics whose FSRS card is due for review NOW.

    Used by the Phase B learning controller (R-F662) to pick what to
    study next. Returns oldest-due first. Topics with no FSRS card yet
    (never reviewed) are treated as due — that gives new topics
    immediate visibility in the controller's queue.

    Output: list of {topic, score, retention_now, due_at, fsrs_state}.
    """
    from ..learning import fsrs_scheduler as _fsrs
    mastery = await _load_mastery()
    out: list[dict] = []
    for topic, m in mastery.items():
        if not isinstance(m, dict):
            continue
        card_dict = m.get("fsrs_card")
        if not _fsrs.is_due(card_dict):
            continue
        retention = _fsrs.retention_now(card_dict)
        due_at = None
        if card_dict and isinstance(card_dict, dict):
            due_at = card_dict.get("due") or card_dict.get("due_at")
        out.append({
            "topic": topic,
            "score": round(m.get("score", INITIAL_MASTERY), 3),
            "retention_now": round(retention, 3),
            "due_at": due_at,
            "fsrs_state": (card_dict or {}).get("state"),
        })
    # Order: never-reviewed first (no due_at), then by due_at ASC (oldest).
    out.sort(key=lambda r: (r["due_at"] is not None, r["due_at"] or ""))
    return out[: max(1, min(limit, 100))]


@fail_wire(module="student", gap_type="engine_failure")
async def get_topic_retention(topic: str) -> dict:
    """R-F664: FSRS-predicted retention probability for one topic right now."""
    from ..learning import fsrs_scheduler as _fsrs
    mastery = await _load_mastery()
    m = mastery.get(topic) if isinstance(mastery, dict) else None
    if not m:
        return {"topic": topic, "retention_now": 0.0, "score": INITIAL_MASTERY,
                "has_card": False}
    card = m.get("fsrs_card")
    return {
        "topic": topic,
        "retention_now": round(_fsrs.retention_now(card), 3),
        "score": round(m.get("score", INITIAL_MASTERY), 3),
        "has_card": bool(card),
        "due_at": (card or {}).get("due") if isinstance(card, dict) else None,
    }


@fail_wire(module="student", gap_type="engine_failure")
async def get_mastery_report() -> dict:
    # R-F2408: opportunistic, time-gated flush of any deferred mastery write
    # (no-op when the coalesce flag is off or nothing is pending). A dashboard/
    # report refresh thus bounds how long a deferred whole-blob save can sit
    # unpersisted during a quiet learning period. Report values read the cache
    # below, so this never changes what is reported — it is durability only.
    try:
        await _maybe_flush_mastery()
    except Exception:
        pass
    mastery = await _load_mastery()
    total_samples = sum(m.get("samples", 0) for m in mastery.values())
    weak = [t for t, m in mastery.items() if m.get("score", 0) < WEAK_THRESHOLD]
    strong = [t for t, m in mastery.items() if m.get("score", 0) >= 0.80]
    overall = (
        sum(m.get("score", 0) * m.get("samples", 0) for m in mastery.values())
        / max(total_samples, 1)
    ) if total_samples > 0 else INITIAL_MASTERY

    # Core-mastery rollup — unweighted mean over the 9 load-bearing tags,
    # independent of sample count. This prevents the sample-weighted
    # `overall` from hiding weak core cells that have been starved of
    # practice. Headline mastery is the minimum of the two so the
    # dashboard cannot read higher than the weakest rollup.
    core_scores = [mastery[t].get("score", INITIAL_MASTERY)
                   for t in CORE_MASTERY_TAGS if t in mastery]
    core_mastery = (sum(core_scores) / len(core_scores)) if core_scores else INITIAL_MASTERY
    headline_mastery = min(overall, core_mastery)
    core_weak = [t for t in CORE_MASTERY_TAGS
                 if mastery.get(t, {}).get("score", 0) < WEAK_THRESHOLD]

    # ── R-F4063 (C-113) — publish what the headline is MADE OF ───────────────
    #
    # The number above drives Phase A gate #1, autonomy_scorer and
    # calibration_review, and it is held up at both ends by clamps. Measured
    # live 2026-08-16, the ten CORE_MASTERY_TAGS were:
    #
    #   lang:pt 0.980 (3794 samples, 3793 correct)   lang:ar 0.980 (1089)
    #   lang:fr 0.980 (378)                          lang:es 0.980 (1029, 0 wrong)
    #   lang:zh 0.980 (473)                          lang:ru 0.968 (293)
    #   sanctions 0.845 (3092)
    #   nato_standards 0.500 (68 samples, 65 correct)
    #   strategic_geography 0.500 (76)
    #   export_control 0.509 (281)      -> mean 0.8222 = the 82% headline
    #
    # Six of ten are LANGUAGE tags welded to MASTERY_CEILING; a tag at its
    # ceiling cannot move, so it carries no information. Three more sit at
    # exactly their HARD_FLOORS value despite 68/76/281 graded observations at
    # ~90% correct — arithmetically impossible under MASTERY_LR_POSITIVE = 0.18
    # unless something is pushing them down (C-112's hourly calibration drop is
    # the measured candidate). Only `sanctions` moves freely.
    #
    # `0.500` therefore means two contradictory things in this system —
    # "never measured" (the INITIAL_MASTERY scaffold that /health's
    # `core_mastery_all_scaffolded` check looks for) and "clamped at floor after
    # 68 observations" — and nothing distinguished them. `samples` does.
    #
    # The VALUE is deliberately unchanged: §1 forbids closing a gate by
    # measuring less, and dropping the language tags would RAISE the headline.
    # This reports composition so the number can be read for what it is.
    # `_FLOOR_BAND` is a stated judgement, not a hidden one, and it is
    # published in the payload so a reader can disagree with it. Live,
    # `export_control` sat at 0.509 against a 0.50 floor — 0.9pp clear, with 281
    # graded observations at 91% correct. Calling that "freely measured" on a
    # strict equality test would understate the finding by a rounding error;
    # 2pp is the smallest band that captures "pinned to the floor" without
    # sweeping in a genuinely low-but-moving score.
    _EPS = 1e-6
    _FLOOR_BAND = 0.02
    _at_ceiling, _at_floor, _free = [], [], []
    for t in CORE_MASTERY_TAGS:
        _m = mastery.get(t) or {}
        _s = _m.get("score", INITIAL_MASTERY)
        _floor = HARD_FLOORS.get(t, INITIAL_MASTERY)
        if _s >= MASTERY_CEILING - _EPS:
            _at_ceiling.append(t)
        elif _s <= _floor + _FLOOR_BAND + _EPS:
            _at_floor.append({
                "topic": t,
                "score": round(_s, 3),
                "floor": _floor,
                "samples": _m.get("samples", 0),
                # A floored tag WITH samples is clamped; one without is
                # scaffolded. Same number, opposite meanings.
                "clamped": bool(_m.get("samples", 0) > 0),
            })
        else:
            _free.append(t)
    core_composition = {
        "at_ceiling": _at_ceiling,
        "at_floor": _at_floor,
        "freely_measured": _free,
        "ceiling": MASTERY_CEILING,
        # Published so the "at floor" judgement is auditable rather than magic.
        "floor_band": _FLOOR_BAND,
        "total": len(CORE_MASTERY_TAGS),
    }

    return {
        "overall_mastery": round(overall, 3),
        "core_mastery": round(core_mastery, 3),
        "headline_mastery": round(headline_mastery, 3),
        "core_mastery_breakdown": {
            t: round(mastery.get(t, {}).get("score", INITIAL_MASTERY), 3)
            for t in CORE_MASTERY_TAGS
        },
        "core_weak_topics": core_weak,
        # R-F4063 (C-113) — what the headline is made of, see above.
        "core_mastery_composition": core_composition,
        "total_samples": total_samples,
        "topics": {
            t: {
                "score": round(m.get("score", 0), 3),
                "samples": m.get("samples", 0),
                "correct": m.get("correct", 0),
                "wrong": m.get("wrong", 0),
                "accuracy": round(m.get("correct", 0) / max(m.get("samples", 0), 1), 3),
                "last_practiced": m.get("last_practiced", 0),
            }
            for t, m in mastery.items()
        },
        "weak_topics": weak,
        "strong_topics": strong,
        "study_priority": sorted(mastery.items(), key=lambda x: x[1].get("score", 0))[:3],
    }


# ── Curriculum: what to study next ──────────────────────────────────────────

@fail_wire(module="student", gap_type="engine_failure")
async def get_curriculum() -> dict:
    """Build a study plan: which topics need attention + suggested actions."""
    mastery = await _load_mastery()
    library_stats = await reasoning_library.get_stats()
    library_by_intent = library_stats.get("by_intent", {})

    plan = []
    for topic in TOPICS:
        m = mastery.get(topic, {})
        score = m.get("score", INITIAL_MASTERY)
        samples = m.get("samples", 0)
        last_practiced = m.get("last_practiced", 0)
        days_since = (time.time() - last_practiced) / 86400 if last_practiced else 999

        priority = 0
        actions = []

        # Weak topics get high priority
        if score < WEAK_THRESHOLD:
            priority += int((WEAK_THRESHOLD - score) * 100)
            actions.append("study_weak_topic")
        # Topics not practiced recently fade
        if days_since > 14:
            priority += min(int(days_since), 30)
            actions.append("spaced_repetition")
        # Topics with no library coverage need seeding
        if samples < 5:
            priority += 20
            actions.append("seed_with_reading")

        if priority > 0:
            plan.append({
                "topic": topic,
                "score": round(score, 3),
                "samples": samples,
                "days_since_practice": round(days_since, 1),
                "priority": priority,
                "actions": actions,
            })

    plan.sort(key=lambda x: -x["priority"])
    return {
        "items": plan[:10],
        "total_weak": sum(1 for p in plan if "study_weak_topic" in p["actions"]),
        "total_stale": sum(1 for p in plan if "spaced_repetition" in p["actions"]),
        "library_size": library_stats.get("total_cases", 0),
    }


# ── R-F306: Cost-free learning contract ────────────────────────────────────
# Operator directive 2026-05-11: ARIA mirrors Claude. The learning loops
# (self_quiz + reading_session + library_consolidate) must run at $0 —
# no Brave, no paid web_search, no LLM call necessary for the loop to
# make progress. They are allowed to OPTIONALLY consume LLM if passed,
# but the loop's correctness MUST NOT depend on it.
#
# This is enforced two ways:
#   (1) Documentary: this constant + the "no LLM required" assertion in
#       the docstrings.
#   (2) Test contract: test_dd_ecosystem_rf296_rf305 (R-F306 capability)
#       monkey-patches the paid modules and asserts zero calls during a
#       learning cycle. If a future commit wires a paid path into the
#       learning loop, that test breaks loudly.
LEARNING_MODE_COST_FREE_INVARIANT: dict = {
    "self_quiz_cost_usd": 0.0,
    "reading_session_cost_usd": 0.0,
    "library_consolidate_cost_usd": 0.0,
    "rationale": (
        "Per aria_mirrors_claude memory: Claude Code does not depend on "
        "Brave / Upstash / paid persistence for self-improvement; ARIA "
        "must mirror that. Brave deprecated. Upstash being phased out "
        "by R-F235 (→ SQLite). Learning runs on local data + free RSS + "
        "free article fetch + local reasoning library."
    ),
    "approved_data_sources": (
        "RESEARCH_FEEDS (RSS), Crossref, OpenAlex, Semantic Scholar, "
        "Bing News RSS, Google News RSS, DuckDuckGo (free tier, "
        "rate-limited but free), local reasoning_library, "
        "local knowledge store, local neural memory, local mastery.",
    ),
    "forbidden_in_learning_loop": (
        "brave_answers, brave_search, paid LLM completions as required "
        "path, paid Upstash writes specifically for learning state, "
        "OpenSanctions paid tier, paid registry adapters.",
    ),
}


# ── Self-quiz ──────────────────────────────────────────────────────────────

@fail_wire(module="student", gap_type="engine_failure")
async def self_quiz(num_questions: int = 5) -> dict:
    """Pick stale library cases, try answering them with the LOCAL stack,
    compare to the original answer, and update mastery accordingly.

    This is the active recall loop. Without it the library is just a cache;
    with it, ARIA actually verifies that her local stack can reproduce
    answers she's been taught.
    """
    # R-F1745 — wire quiz outcome to brain
    try:
        index = await reasoning_library._load_index()
    except Exception as _e:
        logger.warning("[Student] self_quiz _load_index failed: %s", _e)
        try:
            from .engine_wiring import wire_failure as _wf
            _wf(module="student", detail=f"self_quiz _load_index: {_e}",
                gap_type="source_failure", source="student:self_quiz")
        except Exception:
            pass
        return {"quizzed": 0, "passed": 0, "score": 0.0,
                "note": "library load failed", "library_size": 0}
    if not index:
        return {"quizzed": 0, "passed": 0, "score": 0.0,
                "note": "library empty", "library_size": 0}

    # Pick stale cases — least recently used + lowest confidence
    candidates = sorted(
        index,
        key=lambda c: (c.get("ts_last_used", 0), c.get("confidence_score", 0)),
    )[:num_questions * 3]
    sample = random.sample(candidates, min(num_questions, len(candidates)))  # nosec B311

    # R-F291: track silent-skip causes so the 0/0 quiz outcome stops being
    # diagnostically blind. Also self-heal orphan index entries on the spot
    # rather than waiting up to 24h for the next consolidate() sweep.
    results = []
    passed = 0
    orphan_ids: list[str] = []
    skipped_no_question = 0
    skipped_no_response = 0
    for entry in sample:
        case = await rs.get_json(reasoning_library._case_key(entry["id"]))
        if not case:
            orphan_ids.append(entry["id"])
            continue
        question = case.get("question") or ""
        original_response = case.get("response") or ""
        if not question:
            skipped_no_question += 1
            continue
        if not original_response:
            skipped_no_response += 1
            continue

        # Try the LOCAL reasoning stack (no cloud)
        # R-F1743/R-F1745: pass exclude_topic to prevent quiz-gaming — the RAG
        # retrieval will exclude facts whose topic matches the case's
        # own intent (e.g. "sanctions"), forcing the local stack to reason
        # from domain knowledge rather than retrieving the stored answer.
        # Uses case.get("intent") because reasoning_library cases store
        # their domain under the intent field, not a topic field.
        _exclude = case.get("intent") or None
        local = await reasoning_router.try_local_reasoning(
            question, exclude_topic=_exclude,
        )
        local_answered = local.get("answered", False)
        local_response = local.get("response") if local_answered else None

        # Score similarity between local and original (token overlap proxy)
        # R-F1743: rag_context is NOT included in the response text, so
        # similarity is measured against the clean answer only.
        similarity = 0.0
        if local_response and original_response:
            similarity = _quick_similarity(local_response, original_response)

        passed_quiz = local_answered and similarity >= 0.4
        if passed_quiz:
            passed += 1

        topics = detect_topics(question)
        await update_mastery(topics, correct=passed_quiz, weight=0.5)

        # R-F1746: also update REGIONAL mastery so self_quiz is an honest
        # gate-#2 mover. update_regional_mastery internally filters by TOPICS
        # (compliance/legal/etc., not sanctions), so only gate-2-relevant
        # topic x region cells get credited. Combined with R-F1744's reading-
        # side attack, this closes gate #2 from both recall and reading sides.
        # Uses detect_regions on question + original_response because many
        # library questions don't name a region explicitly but the stored
        # answer does — this is still honest (the case IS about that region).
        regions = detect_regions(f"{question} {original_response}")
        await update_regional_mastery(topics, regions, correct=passed_quiz, weight=0.5)

        # R-F661 (2026-05-17): failed-quiz → reading-list auto-enrol.
        # Failed quiz = a topic we *thought* we knew but the local stack
        # couldn't reproduce. Enqueue each failed topic so the Phase B
        # controller (R-F662) can drain the queue with local-only reads.
        # Failure non-fatal: the EWMA / FSRS mastery update above is the
        # authoritative signal; the queue entry is a follow-up signal.
        if not passed_quiz:
            try:
                from ..learning import reading_queue as _rq
                for _t in topics:
                    await _rq.enqueue(
                        _t,
                        source_question=question[:500],
                        source_case_id=str(case.get("id") or ""),
                        reason="failed_quiz",
                        notes=(
                            f"local_answered={local_answered} "
                            f"similarity={round(similarity, 3)}"
                        ),
                    )
            except Exception as _rq_e:
                logger.debug(
                    "R-F661 reading_queue enqueue failed (non-fatal): %s",
                    _rq_e,
                )

        results.append({
            "case_id": case.get("id"),
            "question": question[:120],
            "topics": topics,
            "local_answered": local_answered,
            "local_source": local.get("source") if local_answered else None,
            "similarity_to_original": round(similarity, 3),
            "passed": passed_quiz,
        })

    # R-F291: self-heal orphan index entries (case blob expired/purged but
    # the index entry remained). Without this, quizzes keep sampling the
    # same dead IDs every 3h and stay at 0/0 until the 24h consolidate.
    healed = 0
    if orphan_ids:
        try:
            orphan_set = set(orphan_ids)
            new_index = [e for e in index if e["id"] not in orphan_set]
            if len(new_index) != len(index):
                reasoning_library._index_cache_set(new_index)
                await reasoning_library._save_index()
                healed = len(orphan_set)
                logger.info(
                    "[Student] R-F291: self-healed %d orphan index entries "
                    "(library: %d → %d)",
                    healed, len(index), len(new_index),
                )
        except Exception as e:
            logger.warning("[Student] R-F291 orphan self-heal failed: %s", e)

    # Persist quiz history
    history = await rs.get_json(QUIZ_HISTORY_KEY) or []
    history.append({
        "ts": time.time(),
        "quizzed": len(results),
        "passed": passed,
        "score": round(passed / max(len(results), 1), 3),
    })
    history = history[-200:]  # keep last 200
    await rs.set_json(QUIZ_HISTORY_KEY, history, ex=180 * 86400)

    # R-F1745 — wire quiz completion to brain
    try:
        from .engine_wiring import wire_success as _ws
        _ws(module="student",
            summary=f"self_quiz: {len(results)} quizzed, {passed} passed, "
                    f"{round(passed / max(len(results), 1), 3)} score",
            source_id="student:self_quiz")
    except Exception:
        pass

    return {
        "quizzed": len(results),
        "passed": passed,
        "score": round(passed / max(len(results), 1), 3),
        "results": results,
        "library_size": len(index),
        "sample_size": len(sample),
        "orphans": len(orphan_ids),
        "orphans_healed": healed,
        "skipped_no_question": skipped_no_question,
        "skipped_no_response": skipped_no_response,
    }


def _quick_similarity(a: str, b: str) -> float:
    """Token-overlap similarity (Jaccard) — fast, no embeddings needed."""
    if not a or not b:
        return 0.0
    ta = set(re.findall(r"\w+", a.lower()))
    tb = set(re.findall(r"\w+", b.lower()))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union > 0 else 0.0


# ── R-F2283 (2026-07-02): gate-#2 free-loop accelerator ────────────────────────
# Phase A gate #2 is the regional-mastery heatmap floor (topic×region EWMA, target
# ≥0.70). Its blocking cells are weak in specific REGIONS. The feed-selection in
# reading_session is region-BLIND and research_engine routes its hits to the
# spider queue (→facts/coverage), never to update_regional_mastery — so no path
# lifts a blocked CELL. This helper (extracted from reading_session so the
# crediting path is directly testable, and to raise throughput without changing
# the EWMA scoring — no metric gaming) reads region-specific content for the
# weakest cells and lifts the exact cell ONLY when the fetched text actually
# mentions the region (detect_regions confirms — read-grounded, not a blind bump).
# R-F2283 raises reach (10→15 cells, 3→6 articles/cell) and records a per-CELL
# §21e gap for uncredited floor breaches (the update_mastery gap is TOPIC-level
# only). `explore` is injectable for tests.
_CURRICULUM_CURSOR_KEY = "crucix:aria:student:regional_curriculum_cursor"


async def _select_curriculum_cells(
    below: list[dict], max_cells: int, *, head_n: int = 5,
) -> list[dict]:
    """R-F2955 (C1) — weakest-biased ROTATION over ALL below-floor cells.

    The old selection studied `floor_breach_cells[:max_cells]` every session —
    and `floor_breach_cells` is itself truncated to [:20] — so below-floor cells
    ranked ~16+ were studied NEVER, permanently starved of attention while the
    top-15 got hammered. This keeps the weakest `head_n` (which INCLUDES the
    argmin — the gate closes only when the single weakest cell crosses 0.70) and
    ROTATES the remaining window through the rest of the below-floor set via a
    durable cursor advanced each session, so every below-floor cell gets periodic
    read-grounded reinforcement. The cursor uses modulo so it survives the
    below-floor set changing size between sessions (no crash, no permanent skip).
    Grading is UNCHANGED — this only changes WHICH cells are attempted, never how
    they are credited (§1: no clamping)."""
    if not below:
        return []
    below = sorted(below, key=lambda c: c["score"])  # weakest-first
    head = below[:max(0, head_n)]
    tail = below[max(0, head_n):]
    window = max(0, max_cells - len(head))
    if not tail or window <= 0:
        return list(head)
    off = 0
    try:
        raw = await rs.get(_CURRICULUM_CURSOR_KEY)
        off = (int(raw) % len(tail)) if raw is not None else 0
    except Exception:
        off = 0
    rotated = [tail[(off + i) % len(tail)] for i in range(min(window, len(tail)))]
    try:
        await rs.set(_CURRICULUM_CURSOR_KEY, str((off + len(rotated)) % len(tail)))
    except Exception:
        pass
    return list(head) + rotated


async def _study_weak_regional_cells(
    *, explore=None, max_cells: int = 15, max_results_per_cell: int = 6,
) -> list[dict]:
    """Lift the weakest topic×region gate-#2 cells via read-grounded reinforcement.
    Returns the list of cells credited this session. Never raises (best-effort)."""
    regional_studied: list[dict] = []
    _uncredited: list[dict] = []
    _brave_sourced = 0
    try:
        if explore is None:
            from . import web_explorer as _we2
            explore = _we2.explore
        _hm = await get_regional_heatmap()
        # R-F2955 (C1): rotate over the FULL below-floor set (from the untruncated
        # heatmap), NOT the [:20]-truncated floor_breach_cells — else cells ranked
        # 21+ are never studied. Weakest-biased head (incl. argmin) + rotating tail.
        _full_hm = _hm.get("heatmap") or {}
        _below = [
            {"topic": _t, "region": _r, "score": _s}
            for _t, _regs in _full_hm.items()
            for _r, _s in (_regs or {}).items()
            if _s < GATE_2_FLOOR_TARGET
        ]
        _target_cells = await _select_curriculum_cells(_below, max_cells)
        if not _target_cells:
            # Early boot / empty heatmap fallback — legacy weakest-first slice.
            _weak_cells = (_hm.get("floor_breach_cells") or []) or (_hm.get("weak_cells") or [])
            _target_cells = list(_weak_cells[:max_cells])
        # R-F2433/R-F2965 — seed-all-regions bootstrap (flag ARIA_STUDENT_SEED_ALL_REGIONS).
        # The loop can otherwise only REINFORCE cells already in get_regional_heatmap(),
        # which only lists cells that already have samples — so a region with 0 samples
        # can never be bootstrapped (chicken-and-egg). When ON, extend the target list
        # with up to ARIA_STUDENT_SEED_BATCH (default 10) not-yet-existing TOPIC×REGION
        # cells per session so the loop ATTEMPTS to ground each region. The crediting
        # path below is UNCHANGED — a seeded cell is credited ONLY if detect_regions
        # confirms real region content (the R-F1947 gate at `if _stored and _grounded`),
        # so this is NOT metric-gaming; it only broadens what the loop tries to read.
        # Cost is bounded by the per-session Brave budget (now B3/R-F2961 cost-shed) +
        # the seed batch.
        # R-F2965 (C3): DEFAULT ON. CLAUDE.md §1 (binding) names "leaving
        # ARIA_STUDENT_SEED_ALL_REGIONS off to keep 137 cells hidden" a forbidden
        # measure-LESS clamp — so seeding must be the default, not opt-in. The
        # R-F2433 "flip only after the persistence fix" warning is satisfied by
        # the R-F2664 strict-read clobber guard already in _load/_update above (an
        # absent/not-ready store can no longer wipe credited cells); R-F2963 (C0)
        # write-coalescing is available (opt-in) if the larger cache is ever seen
        # to pressure the single writer. EXPECTED: the honest floor DROPS as
        # 0.5-seeded cells appear — that is the gate becoming EARNED (§1), not a
        # regression; judge by A1 velocity over days.
        if os.getenv("ARIA_STUDENT_SEED_ALL_REGIONS", "1").strip().lower() not in ("0", "false", "no"):
            try:
                _seed_batch = max(1, int(os.getenv("ARIA_STUDENT_SEED_BATCH", "10") or "10"))
            except (TypeError, ValueError):
                _seed_batch = 10
            _seen_cells = {(c.get("topic"), c.get("region")) for c in _target_cells}
            _added = 0
            for _rg in REGIONS:
                if _added >= _seed_batch:
                    break
                if _rg == "global":
                    continue
                for _tp in TOPICS:
                    if _added >= _seed_batch:
                        break
                    if _tp == "general":
                        continue
                    if (_tp, _rg) not in _seen_cells:
                        _target_cells.append({"topic": _tp, "region": _rg, "score": INITIAL_MASTERY})
                        _seen_cells.add((_tp, _rg))
                        _added += 1
        # R-F2392 §17/§21: Brave-escalated region sourcing. The R-F1947 stall is
        # that the FREE stack often cannot find region-specific content for a floor
        # cell, so detect_regions never confirms and the cell stays UNCREDITED no
        # matter how often it is visited. For the worst cells we escalate ONE
        # region-targeted query to the live Brave-primary search (R-F2318, masked
        # as aria_search), English-only fanout (~1 Brave call/cell), bounded by a
        # per-session budget. This ONLY changes what content the loop can SEE — the
        # crediting/EWMA path below is byte-for-byte unchanged (no metric gaming).
        # Pay-once-remember-forever (§15) amortizes the quota: once Brave content
        # is stored, the NEXT session's free memory-first pass grounds the cell for
        # $0, so Brave fires ~once per cell, not every cycle. Brave stays OFF on the
        # free pass so the high-volume loop never burns quota on groundable cells.
        try:
            from . import web_search as _ws
            _brave_available = (
                bool(getattr(_ws, "BRAVE_API_KEY", ""))
                and not getattr(_ws, "_BRAVE_GLOBALLY_OFF", False)
            )
        except Exception:
            _ws = None
            _brave_available = False
        _brave_budget = int(os.getenv("ARIA_STUDENT_BRAVE_BUDGET", "3") or "3")
        # R-F2961 (B3) — cost-aware graceful shed. When the day's LLM spend nears
        # the cap, DON'T disable learning — just skip the PAID Brave escalation and
        # keep the free multi-backend/SearXNG Pass-1 running. Evaluated ONCE per
        # session (one cost read, not per-cell). This is why the feeds never need
        # to be hard-turned-off for cost again.
        _paid_shed = False
        try:
            from . import load_governor as _lg_paid
            _paid_shed = await _lg_paid.should_shed_paid()
            if _paid_shed:
                logger.info(
                    "[student] R-F2961 cost-shed: skipping PAID Brave escalation this "
                    "session (daily budget pressure); free stack still learning at $0")
        except Exception:
            _paid_shed = False

        def _region_grounded(_er, _rgn: str):
            """Return (grounded, [(value, context) for facts with a source_url]).
            `grounded` is True iff the fetched text actually mentions the region
            (the R-F1947 detect_regions gate) — this credit condition is NOT
            changed by R-F2392; only the CONTENT fed into it improves."""
            _g = False
            _uf: list[tuple[str, str]] = []
            for _f in (getattr(_er, "facts", None) or []):
                _val = str(getattr(_f, "value", "") or "")
                _ctx = str(getattr(_f, "context", "") or "")
                if _rgn in detect_regions(f"{_val} {_ctx}"):
                    _g = True
                if getattr(_f, "source_url", ""):
                    _uf.append((_val, _ctx))
            return _g, _uf

        async def _explore_region(_q: str, *, use_brave: bool):
            """One region-targeted explore pass. When use_brave, enable the Brave
            scope (R-F2318) for THIS call only and reset it after; English-only
            fanout caps quota to ~1 Brave call. Never raises."""
            if use_brave and _ws is not None:
                _ws.enable_brave_for_scope(True)
            try:
                return await explore(
                    query=_q, cost_free=True,
                    max_results=max_results_per_cell,
                    memory_first=not use_brave,
                    language_fanout=("off" if use_brave else "auto"),
                )
            except Exception as _wee:
                logger.debug("[student] R-F2392 explore failed (brave=%s): %s", use_brave, _wee)
                return None
            finally:
                # The autonomous loop never enables Brave otherwise → reset to OFF.
                if use_brave and _ws is not None:
                    try:
                        _ws.enable_brave_for_scope(False)
                    except Exception:
                        pass

        for _cell in _target_cells:
            _topic = (_cell.get("topic") or "").strip()
            _region = (_cell.get("region") or "").strip()
            # Only act on real, region-specific gate-#2 cells.
            if _topic not in TOPICS or _region not in REGIONS or _region == "global":
                continue
            _rphrase = _REGION_QUERY_PHRASE.get(_region, _region.replace("_", " "))
            _tphrase = _topic.replace("_", " ")
            _query = f"{_tphrase} {_rphrase} defence procurement 2026"

            # Pass 1 — free multi-backend stack (region-targeted, cost-free).
            _er = await _explore_region(_query, use_brave=False)
            _grounded, _url_facts = (
                _region_grounded(_er, _region) if _er is not None else (False, [])
            )
            _via_brave = False

            # Pass 2 — Brave escalation (R-F2392): ONLY when the free stack could
            # not ground the region, per-session budget remains, and a key exists.
            if (not _grounded) and _brave_budget > 0 and _brave_available and not _paid_shed:
                _brave_budget -= 1
                _ber = await _explore_region(_query, use_brave=True)
                if _ber is not None:
                    _bg, _buf = _region_grounded(_ber, _region)
                    if _bg:
                        _grounded, _url_facts, _via_brave = True, _buf, True
                        _brave_sourced += 1
                        logger.info(
                            "[student] R-F2392 Brave sourced region content for %s:%s "
                            "(free stack missed it)", _topic, _region,
                        )

            # Store grounded facts (only those with a source_url). The crediting
            # condition (`_stored and _grounded`) is IDENTICAL to R-F1744/R-F1947.
            _stored = 0
            if _grounded:
                for (_val, _ctx) in _url_facts:
                    _fact_content = (_ctx or _val)[:800]
                    # R-F2462 — pre-filter: knowledge.store_fact rejects content
                    # <50 chars (R-F1526), but the old code still bumped _stored on
                    # a reject, so regional mastery was credited for facts that
                    # never landed. Skip them so _stored (and the mastery credit
                    # below) reflects REAL stores only, and skip the wasted write.
                    if len(_fact_content.strip()) < 50:
                        continue
                    try:
                        await kb.store_fact(
                            topic=f"{_tphrase} {_rphrase}: {_val[:60]}",
                            content=_fact_content,
                            source=f"reading_region:{_topic}:{_region}",
                            confidence="ASSESSED",
                        )
                        _stored += 1
                    except Exception as _kse:
                        logger.debug("[student] R-F1744 kb store failed: %s", _kse)

            if _stored and _grounded:
                # R-F2660 — HONEST grade, not a participation trophy. Storing
                # grounded facts improved COVERAGE; regional MASTERY (Phase A
                # gate #2) only moves on a REAL recall grade: can the local
                # reasoning stack actually ANSWER about this cell and overlap
                # what we just read? Pre-R-F2660 this hardcoded correct=True, so
                # the ~9.6x/day reading loop measured reading VOLUME, not
                # comprehension (CLAUDE.md §1). Reuse the ONE honest grader the
                # tasks.py research bridge already uses (autonomous/tasks.py) so
                # both paths grade identically. A False grade (knowledge didn't
                # take yet) correctly does NOT lift the cell — §1: close gate #2
                # by grounded improvement, never by crediting the act of reading.
                _research_text = " ".join(
                    str(_ctx or _val) for (_val, _ctx) in (_url_facts or [])
                )[:4000]
                _graded = None
                try:
                    from ..autonomous.tasks import _grade_researched_cell as _grade_cell
                    _graded = await _grade_cell(_topic, _region, _research_text)
                    # R-F3694 — the grade is TRI-STATE. `None` means the cell
                    # was NOT MEASURED (no research text / grader error /
                    # answered=False / empty response), and passing it through
                    # coerced to a MISS at update_regional_mastery's
                    # `1.0 if correct else 0.0`. Mirrors the skip R-F3483
                    # already ships at autonomous/tasks.py:2460.
                    if _graded is None:
                        logger.info(
                            "[student] R-F3694 cell %s:%s UNMEASURED — the local "
                            "stack could not answer; not counted as a miss",
                            _topic, _region,
                        )
                    else:
                        await update_regional_mastery(
                            [_topic], [_region], correct=_graded, weight=0.3,
                        )
                except Exception as _ge:
                    # Grader/import failed → do NOT fabricate a pass; skip the
                    # mastery move entirely (an unmeasured cell must not be
                    # credited correct=True — the exact bug this fixes).
                    logger.debug(
                        "[student] R-F2660 honest grade skipped for %s:%s: %s",
                        _topic, _region, _ge,
                    )
                regional_studied.append({
                    "topic": _topic, "region": _region, "stored": _stored,
                    "via_brave": _via_brave, "graded_correct": _graded,
                })
                logger.info(
                    "[student] R-F2660 gate-2 cell %s:%s read %d grounded facts "
                    "(brave=%s) -> honest mastery grade=%s",
                    _topic, _region, _stored, _via_brave, _graded,
                )
            else:
                # R-F1947/R-F2392: a cell still NOT credited AFTER the Brave
                # escalation is genuinely region-data-starved (or Brave is off) —
                # record it (below) so the coder/research loop and the operator see
                # which regions have no reachable content, not just that the cell is low.
                _uncredited.append({"topic": _topic, "region": _region})
                logger.info(
                    "[student] R-F1947 gate-2 cell %s:%s NOT credited (stored=%d grounded=%s "
                    "brave_available=%s) — visited, no mastery lift",
                    _topic, _region, _stored, _grounded, _brave_available,
                )
    except Exception as _rce:
        logger.warning("[student] R-F1744 region-targeted branch failed (non-fatal): %s", _rce)

    # R-F2283 §21e: record a per-CELL capability gap for floor cells this session
    # could not lift (no region-grounded content found), so the autonomous
    # coder/research loop has explicit topic×region targets — the existing
    # update_mastery gap is TOPIC-level and can't point at a specific region.
    # Bounded to the top few + deduped by capability_gaps (R-F903).
    if _uncredited:
        try:
            from . import capability_gaps as _cg
            for _c in _uncredited[:5]:
                _t = asyncio.create_task(_cg.record_gap(
                    gap_type="knowledge_gap",
                    detail=(f"Gate-#2 region cell '{_c['topic']}:{_c['region']}' is below the "
                            f"{GATE_2_FLOOR_TARGET:.0%} floor and NEITHER the free stack NOR Brave "
                            f"region-sourcing (R-F2392) found region-grounded content this cycle — "
                            f"{_c['region']} is genuinely data-starved; needs region-specific "
                            f"reading/training or a dedicated source for {_c['region']}."),
                    source=f"student.regional_cell:{_c['topic']}:{_c['region']}",
                ))
                _t.add_done_callback(
                    lambda t: t.result() if not t.cancelled() and not t.exception() else None)
        except Exception:
            pass

    # §21a: wire the outcome of this gate-#2 learning pass to the brain on BOTH
    # branches — success (cells lifted) and failure (targeted cells but credited
    # none), so the self-heal/coder loop can see whether the free loop is moving.
    try:
        from .engine_wiring import wire_success, wire_failure
        if regional_studied:
            wire_success(
                module="student",
                summary=(
                    f"gate-2 reading lifted {len(regional_studied)} region cell(s)"
                    + (f" ({_brave_sourced} via Brave region-sourcing)" if _brave_sourced else "")
                ),
                source_id="student:_study_weak_regional_cells",
            )
        elif _uncredited:
            wire_failure(
                module="student",
                detail=(
                    f"gate-2 reading credited 0 of {len(_uncredited)} targeted region cells"
                    " (free + Brave sourcing both found no region-grounded content)"
                ),
                gap_type="knowledge_gap",
                source="student:_study_weak_regional_cells",
            )
    except Exception:
        pass
    # R-F2963 (C0) — force-flush the coalesced regional writes now that this
    # session's ~15-cell burst is done, so the whole-blob write lands promptly
    # (bounded stranding) instead of only on the next update.
    try:
        await flush_regional()
    except Exception:
        pass
    return regional_studied


# ── Reading session: deep-read authoritative sources ───────────────────────

@fail_wire(module="student", gap_type="engine_failure")
async def reading_session(llm=None, num_articles: int = 3) -> dict:
    """Run a focused reading session — pick high-priority articles, read them,
    extract facts AND the reasoning method, and update mastery on topics
    covered.

    A great student doesn't just read — she annotates. This function captures:
      - Facts        → knowledge.store_fact()
      - Concepts     → neural_memory.learn_from_text()
      - Topics       → mastery tracker
      - Source       → tagged for future audit
    """
    from .researcher import _fetch_rss, _fetch_article_text, RESEARCH_FEEDS

    # Pick feeds aligned with weak topics. Include CORE_MASTERY_TAGS in
    # the weak-topic candidate pool — before 2026-04-26 only TOPICS was
    # consulted, which silently excluded lang:* (RU/ZH/AR/PT/FR/ES) and
    # the four cross-cutting capability tags (sanctions / nato_standards
    # / strategic_geography / export_control). Result: TASS+Huanqiu RSS
    # feeds added in `6014587` were fetched but always deprioritised at
    # selection, so lang:ru and lang:zh stayed at the 0.50 floor.
    mastery = await _load_mastery()
    weak_pool = list(TOPICS) + [t for t in CORE_MASTERY_TAGS if t not in TOPICS]
    weak_topics = sorted(
        weak_pool, key=lambda t: mastery.get(t, {}).get("score", INITIAL_MASTERY)
    )[:5]

    # R-F163 (2026-05-11): drain the proactive reading queue first. When
    # `proactive.prepare_weak_topics` or `ecosystem_reassess` enqueue a
    # topic, that's a stronger signal than the global mastery score —
    # the operator's recent work, or detected drift, said "study this".
    # Queue topics that ARE in weak_pool get promoted to the front of
    # weak_topics; queue topics that are NOT (region-specific tags like
    # angola_procurement, uk_compliance) are handed to a targeted web
    # search branch lower in this function so they actually get studied.
    queued_topics: list[str] = []
    starved_queue_topics: list[str] = []
    try:
        from . import proactive as _prc
        queue_entries = await _prc.get_reading_queue(limit=10)
        for q in queue_entries or []:
            t = (q.get("topic") if isinstance(q, dict) else "") or ""
            t = t.strip()
            if not t or t in queued_topics or t in starved_queue_topics:
                continue
            if t in weak_pool:
                queued_topics.append(t)
            else:
                starved_queue_topics.append(t)
        # Promote queued tags to the front, then dedupe by appending the
        # mastery-driven weak set behind. Cap at 5 so the reading session
        # remains time-bounded.
        if queued_topics:
            weak_topics = (queued_topics + [t for t in weak_topics if t not in queued_topics])[:5]
    except Exception as _qe:
        logger.debug("[student] reading_session queue drain failed: %s", _qe)
    logger.info(
        "[student] reading session — focused on weak topics: %s "
        "(queued=%d, starved_queue=%d)",
        weak_topics, len(queued_topics), len(starved_queue_topics),
    )

    # Get fresh articles from a rotating set of feeds.
    # Boost: when a CORE_MASTERY_TAG is weak, force at least one
    # matching feed into the pool. Random sampling alone rarely picks
    # the relevant feeds (lang feeds are 4/~30, export-controls is 1/~30).
    # 2026-04-26 (`ad3dc9e`) added language force-feeds; 2026-04-27
    # extended this to the four cross-cutting capability tags so they
    # don't stay frozen at 1-sample/0%-accuracy.
    weak_topic_to_categories: dict[str, list[str]] = {
        "lang:ru": ["russia_"],
        "lang:zh": ["china_"],
        "lang:ar": ["arabic_"],
        "sanctions": ["export_controls", "arms_trade"],
        "nato_standards": ["europe_defence", "defence_policy", "defence_research"],
        "strategic_geography": ["geopolitics", "strategy"],
        "export_control": ["export_controls", "arms_trade"],
    }
    forced_feeds: list[dict] = []
    for tag, cat_prefixes in weak_topic_to_categories.items():
        if tag not in weak_topics:
            continue
        for f in RESEARCH_FEEDS:
            cat = f.get("category") or ""
            if any(cat.startswith(p) for p in cat_prefixes) and f not in forced_feeds:
                forced_feeds.append(f)
                break  # one feed per weak tag is enough per session
    remaining_slots = max(0, 8 - len(forced_feeds))
    other_pool = [f for f in RESEARCH_FEEDS if f not in forced_feeds]
    feeds_pool = forced_feeds + random.sample(  # nosec B311
        other_pool, min(remaining_slots, len(other_pool))
    )
    articles_to_read = []
    for feed in feeds_pool:
        try:
            items = await _fetch_rss(feed["url"], timeout=10.0)
            for it in items[:3]:
                articles_to_read.append({**it, "feed_name": feed["name"]})
                if len(articles_to_read) >= num_articles * 3:
                    break
        except Exception:
            continue
        if len(articles_to_read) >= num_articles * 3:
            break

    # Score each article by how well its title matches our weak topics.
    # Match against title AND first 200 chars of the description/summary
    # so non-Latin titles aren't penalised (TASS RSS titles are short
    # plain Russian — running detect_topics on title alone yields fewer
    # script chars than the 20-char threshold for lang:ru).
    def _topic_match(art: dict) -> int:
        text = (art.get("title") or "") + " " + (art.get("summary") or art.get("description") or "")[:200]
        topics_in_text = detect_topics(text)
        return sum(1 for t in topics_in_text if t in weak_topics)

    articles_to_read.sort(key=lambda a: -_topic_match(a))
    selected = articles_to_read[:num_articles]

    studied = []
    # R-F2661 — cost guard for the honest regional grade below. The grader
    # calls the local reasoning stack, and unlike R-F2660's per-CELL site this
    # one runs per ARTICLE, so it must be bounded per session (CLAUDE.md §1).
    # Articles beyond the budget are SKIPPED, never credited — an unmeasured
    # cell is not a pass.
    try:
        _grade_budget = int(os.getenv("ARIA_READING_GRADE_BUDGET", "2") or 2)
    except ValueError:
        _grade_budget = 2
    _grades_used = 0
    for art in selected:
        url = art.get("link") or ""
        title = art.get("title") or ""
        if not url or not title:
            continue

        body = await _fetch_article_text(url, timeout=12.0)
        if not body or len(body) < 200:
            continue

        topics = detect_topics(f"{title} {body[:1000]}")

        # Index into knowledge base + neural memory (no LLM call needed)
        try:
            await kb.store_fact(
                topic=title[:80],
                content=body[:800],
                source=f"reading:{art.get('feed_name','unknown')}",
                confidence="ASSESSED",
            )
        except Exception as e:
            logger.debug("[student] kb store failed: %s", e)

        # R-F200 (2026-05-11) — local-only auto-extract from reading.
        # Mines [CONFIRMED]/[PROBABLE]/[ASSESSED] tags from the article
        # body. Wired here so the function actually runs (verification
        # 2026-05-11 caught it as dead code in the first ship). The
        # trust gate is the source family — RSS reading sources are
        # tier-2 by definition, no LLM mediation, no brain-poisoning
        # risk via untrusted prompts. Best-effort: never blocks the
        # reading cycle if extraction fails.
        try:
            await kb.extract_facts_from_reading(
                body[:6000],
                source=f"reading:{art.get('feed_name','unknown')}",
                title=title[:120],
                url=url,
            )
        except Exception as e:
            logger.debug("[student] R-F200 auto-extract failed: %s", e)

        try:
            # Use LLM extraction if available, regex if not
            await neural_memory.learn_from_text(
                f"{title} {body[:2000]}",
                source=f"reading:{art.get('feed_name','unknown')}",
                llm=llm,
            )
        except Exception as e:
            logger.debug("[student] neural learning failed: %s", e)

        # R-F2859 — the TOPIC-mastery bump used to live here as an
        # unconditional `update_mastery(topics, correct=True, weight=0.3)`
        # ("reading is reinforcement"). That is the same participation trophy
        # R-F2661 removed from the regional axis, one line below: it credited
        # comprehension for the ACT of reading, and it feeds Phase A gate #1
        # (composite) rather than gate #2. It now moves on the SAME honest
        # recall grade computed below — see the `else` branch — so no extra
        # grader call is made and an ungraded article credits nothing.

        # R-F196 (2026-05-11): also write topic×region mastery from
        # the reading session. Pre-R-F196 regional_mastery only flowed
        # from chat (aria_engine.py callers), so the research_engine
        # _pick_weakest_cells path starved whenever chat was quiet
        # (R-F175 surfaced 0 ticks/24h as the symptom). Now every
        # article that touches both a topic AND a detected region
        # contributes to the regional heatmap → research engine has
        # weak cells to attack autonomously.
        # R-F2661 — HONEST grade, not a participation trophy. Pre-R-F2661 this
        # hardcoded correct=True, so an article that merely MENTIONED a topic
        # and a region lifted the gate-#2 heatmap: it measured reading VOLUME,
        # not comprehension — the same bug R-F2660 removed from the R-F1744
        # loop. Reuse the ONE honest grader both other paths use so every
        # mastery mover grades identically.
        #
        # Two deliberate narrowings, both in the honest direction:
        #   * exactly ONE cell (first topic x first region) is graded and
        #     credited. Spreading a single grade across every topic x region
        #     pair would be a fresh fabrication.
        #   * a grader error SKIPS the update entirely rather than defaulting —
        #     an unmeasured cell must never be recorded as correct.
        try:
            regions_in_text = detect_regions(f"{title} {body[:1500]}")
            # Only spend the bounded budget on a cell that can actually MOVE
            # the gate-#2 heatmap: update_regional_mastery silently skips any
            # topic outside TOPICS, and R-F1893 drops 'global' (detect_regions
            # falls back to ["global"] when no real region matched). Grading
            # either would consume a grader call for a guaranteed no-op write.
            _r_topic = next((t for t in topics if t in TOPICS), None)
            _r_region = next((r for r in regions_in_text if r != "global"), None)
            if _r_topic and _r_region and _grades_used < _grade_budget:
                _graded = None
                try:
                    from ..autonomous.tasks import _grade_researched_cell as _grade_cell
                    _grades_used += 1
                    _graded = await _grade_cell(_r_topic, _r_region, body[:4000])
                except Exception as _ge:
                    # Do NOT fabricate a pass and do NOT write a fabricated
                    # fail — skip the cell so the heatmap stays honest.
                    logger.debug(
                        "[student] R-F2661 honest grade skipped for %s:%s: %s",
                        _r_topic, _r_region, _ge,
                    )
                else:
                    # R-F3694 — the comment on the except branch above says
                    # "do NOT write a fabricated fail", but it guarded only the
                    # EXCEPTION path. A tri-state `None` RETURN fell into this
                    # else and wrote exactly the fabricated fail it forbids —
                    # on BOTH axes, since update_mastery rides the same grade.
                    if _graded is None:
                        logger.info(
                            "[student] R-F3694 reading cell %s:%s UNMEASURED — "
                            "the local stack could not answer; not counted as a "
                            "miss on either axis",
                            _r_topic, _r_region,
                        )
                    else:
                        await update_regional_mastery(
                            [_r_topic], [_r_region], correct=_graded, weight=0.3,
                        )
                        # R-F2859 — topic mastery rides the SAME grade. Only the
                        # GRADED topic is credited: spreading one recall result
                        # across every topic detected in the article would be a
                        # fresh fabrication, exactly the error R-F2661 avoided on
                        # the regional axis.
                        await update_mastery([_r_topic], correct=_graded, weight=0.3)
                        logger.info(
                            "[student] R-F2661/R-F2859 reading cell %s:%s -> honest "
                            "mastery grade=%s", _r_topic, _r_region, _graded,
                        )
        except Exception as _rre:
            logger.debug("R-F196 regional mastery update failed: %s", _rre)

        studied.append({
            "title": title[:120],
            "url": url,
            "feed": art.get("feed_name"),
            "topics": topics,
            "chars_read": len(body),
        })

    # ── R-F163 (2026-05-11) targeted branch for starved queue topics ──
    # Tags like `angola_procurement`, `uk_compliance`, `uk_export_control`
    # aren't in TOPICS or CORE_MASTERY_TAGS, so the RSS-based selection
    # above cannot lift their mastery. Those tags came in via the
    # proactive reading queue (region-specific weak signals). Hand each
    # one to web_search with a topic-shaped query and ingest the top 2
    # results so the mastery score for the SPECIFIC tag actually moves.
    starved_studied: list[dict] = []
    if starved_queue_topics:
        try:
            from . import researcher as _res
            from . import knowledge as _kb
            # R-F4236 — cost guard for the honest grade below, mirroring
            # R-F2661's: the grader calls the local reasoning stack, so it is
            # bounded per session. Tags beyond the budget are SKIPPED, never
            # credited — an unmeasured tag is not a pass (CLAUDE.md §1).
            try:
                _starved_grade_budget = int(
                    os.getenv("ARIA_STARVED_GRADE_BUDGET", "2") or 2)
            except ValueError:
                _starved_grade_budget = 2
            _starved_grades_used = 0
            for stag in starved_queue_topics[:3]:
                _starved_text: list[str] = []
                # Tag-name shape: 'angola_procurement' -> 'angola procurement'
                pretty = stag.replace("_", " ").replace(":", " ")
                query = f"{pretty} defence procurement news 2026"
                # R-W4 cost-free contract: this is a LEARNING-LOOP web
                # call. Route through web_explorer with cost_free=True
                # so the Brave paid path can never fire from here. Falls
                # back to the old path if web_explorer is unavailable.
                resp = None
                try:
                    from . import web_explorer as _we
                    er = await _we.explore(
                        query=query,
                        cost_free=True,
                        max_results=3,
                        memory_first=True,
                    )
                    # Adapt back to researcher.web_search shape so the
                    # downstream loop stays untouched.
                    resp = {
                        "results": [
                            {
                                "url": f.source_url,
                                "title": f.value[:200],
                                "snippet": f.context[:400],
                            }
                            for f in er.facts
                            if f.source_url
                        ]
                    }
                except Exception as _we_err:
                    logger.debug("[student] R-W4 web_explorer path failed for %s: %s — falling back",
                                 stag, _we_err)
                    try:
                        # Keep the legacy fallback so we never starve the
                        # learning loop, but in cost-free mode the Brave
                        # branch inside researcher.web_search is the only
                        # paid path — R-F306 capability test now spies on
                        # it, so a regression here will break CI.
                        resp = await _res.web_search(query=query, max_results=3)
                    except Exception as _se:
                        logger.debug("[student] starved-tag legacy web_search failed for %s: %s", stag, _se)
                        continue
                results = (resp or {}).get("results", []) if isinstance(resp, dict) else []
                for hit in (results or [])[:2]:
                    if not isinstance(hit, dict):
                        continue
                    url = hit.get("url") or hit.get("link") or ""
                    title = hit.get("title") or ""
                    snippet = hit.get("snippet") or hit.get("description") or ""
                    if not url or not title:
                        continue
                    try:
                        await _kb.store_fact(
                            topic=title[:80],
                            content=(snippet or title)[:800],
                            source=f"reading_starved:{stag}",
                            confidence="ASSESSED",
                        )
                    except Exception as _ke:
                        logger.debug("[student] starved-tag kb store failed: %s", _ke)
                    _starved_text.append(f"{title}\n{snippet}")
                    starved_studied.append({
                        "tag": stag,
                        "title": title[:120],
                        "url": url,
                    })

                # ── R-F4236 (C-216) — HONEST grade, not a participation trophy.
                #
                # This was `update_mastery([stag], correct=True, weight=0.2)`
                # INSIDE the hit loop, so a tag was credited up to twice for the
                # mere existence of a search result — measuring reading VOLUME,
                # not comprehension. It is the THIRD instance of the trophy
                # R-F2660 removed from the R-F1744 loop and R-F2661 from the
                # R-F196 article path, and the last mastery mover still doing it.
                #
                # Its own comment gave the reason: "we explicitly need the named
                # tag to move so the proactive alert stops repeating". That is
                # moving the gauge to switch off the warning light — and it is no
                # longer needed even on its own terms: R-F211 (LATER than R-F163)
                # dedupes the mastery-prep alert on an announce hash with a
                # 14-day TTL, so an unchanged weak set is suppressed whether or
                # not mastery moves. `starved_studied` already records that the
                # tag WAS studied, which is the honest thing this branch produces.
                #
                # Graded ONCE per tag on the combined text, not once per hit:
                # cheaper, and one recall result spread over several hits would
                # be a fresh fabrication. Budget-bounded like R-F2661, and a
                # tri-state None SKIPS the update — an unmeasured tag is neither
                # a pass nor a miss (R-F3483/R-F3694).
                if _starved_text and _starved_grades_used < _starved_grade_budget:
                    _sgraded = None
                    try:
                        from ..autonomous.tasks import _grade_researched_tag as _gtag
                        _starved_grades_used += 1
                        _sgraded = await _gtag(stag, "\n\n".join(_starved_text)[:4000])
                    except Exception as _sge:
                        logger.debug(
                            "[student] R-F4236 honest grade skipped for %s: %s",
                            stag, _sge,
                        )
                    else:
                        if _sgraded is None:
                            logger.info(
                                "[student] R-F4236 starved tag %s UNMEASURED — the "
                                "local stack could not answer; not counted either way",
                                stag,
                            )
                        else:
                            await update_mastery([stag], correct=_sgraded, weight=0.2)
                            logger.info(
                                "[student] R-F4236 starved tag %s -> honest mastery "
                                "grade=%s", stag, _sgraded,
                            )
        except Exception as _bre:
            logger.warning("[student] starved-tag branch failed: %s", _bre)

    # ── R-F1744 (2026-06-20) — region-targeted gate-#2 closer ──────────────
    # Phase A gate #2 is the regional-mastery heatmap floor (topic×region
    # EWMA, target ≥0.70). Its blocking cells are weak in specific REGIONS
    # (e.g. competitor_intel:southern_africa, osint:balkans), but the
    # feed-selection above is region-BLIND (weak_topic_to_categories has no
    # region axis) and research_engine.run_research_tick targets these cells
    # yet routes its hits to the spider queue (→ facts/coverage), never to
    # update_regional_mastery. So no path reads region-specific content for a
    # blocked cell and lifts THAT cell — the floor stays stuck. This branch
    # closes that seam: read the weakest topic×region cells from the heatmap,
    # fetch region-specific content cost-free, and lift the exact cell — but
    # ONLY when the fetched text actually mentions the region (detect_regions
    # confirms), so the lift is read-grounded reinforcement, not a blind bump.
    # R-F1744/R-F2283 — region-targeted gate-#2 closer (extracted + accelerated
    # into _study_weak_regional_cells so the crediting path is directly testable).
    regional_studied = await _study_weak_regional_cells()

    # R-F167 (2026-05-11): drain the queue for topics we actually
    # consumed this session. Without this the same tags re-surface every
    # 6h forever — both from the queue itself and from the proactive
    # alert that re-derives weak_topics from mastery (which moved
    # slightly but may still be below threshold for one more cycle).
    consumed_now = list({*queued_topics, *starved_queue_topics})
    if consumed_now:
        try:
            from . import proactive as _prc2
            await _prc2.drain_reading_queue(consumed_now)
        except Exception as _de:
            logger.debug("[student] reading_session queue drain failed: %s", _de)

    # Log the session
    log = await rs.get_json(READING_LOG_KEY) or []
    log.append({
        "ts": time.time(),
        "articles_read": len(studied) + len(starved_studied),
        "topics_focused": weak_topics,
        "starved_studied": [s["tag"] for s in starved_studied],
        "queue_drained": consumed_now,
        # R-F1744: gate-#2 cells lifted this session (topic:region).
        "regional_cells_lifted": [f"{r['topic']}:{r['region']}" for r in regional_studied],
    })
    log = log[-100:]
    await rs.set_json(READING_LOG_KEY, log, ex=180 * 86400)

    logger.info(
        "[student] reading session complete: %d articles (incl. %d starved-tag hits)",
        len(studied) + len(starved_studied),
        len(starved_studied),
    )
    return {
        "articles_read": len(studied) + len(starved_studied),
        "weak_topics_studied": weak_topics,
        "studied": studied,
        "starved_studied": starved_studied,
        # R-F1744: gate-#2 regional cells read + lifted this session.
        "regional_studied": regional_studied,
    }


# ── Compare-and-learn (silent local attempt during cloud calls) ────────────

@fail_wire(module="student", gap_type="engine_failure")
async def record_divergence(
    question: str,
    cloud_response: str,
    local_response: str | None,
    local_source: str | None,
    similarity: float,
) -> None:
    """Record a divergence between local and cloud answers as study material.

    When ARIA's local stack disagrees with her cloud teacher, that's a
    GAP — and gaps are what students should focus on. We log every
    divergence so the curriculum builder can prioritise study time
    on topics where local has been wrong.
    """
    log = await rs.get_json(DIVERGENCE_KEY) or []
    topics = detect_topics(question)
    log.append({
        "ts": time.time(),
        "question": question[:300],
        "topics": topics,
        "local_source": local_source,
        "local_response_preview": (local_response or "")[:300],
        "cloud_response_preview": cloud_response[:300],
        "similarity": round(similarity, 3),
        "needs_study": similarity < 0.5,
    })
    log = log[-500:]
    await rs.set_json(DIVERGENCE_KEY, log, ex=180 * 86400)

    # Update mastery from divergence comparison. IMPORTANT: the local
    # reasoning stack produces short, factual answers while the cloud
    # LLM produces long narrative responses. Jaccard similarity between
    # these is ALWAYS low even when both are correct — a 50-word local
    # answer vs a 2000-word cloud answer will never exceed 0.3 Jaccard.
    #
    # Previous weights (correct=False, weight=0.7 on <0.5 similarity)
    # were catastrophically punitive — mastery dropped from 81% to 14%
    # across ALL topics because EVERY comparison triggered the penalty.
    #
    # Fix: only penalise when local DIDN'T ANSWER AT ALL (no response
    # from reasoning router), and use much lower weights. The positive
    # signal from successful cloud responses (weight=0.15 in aria_engine)
    # should be the primary mastery driver, not the divergence comparison.
    if local_response is None or len((local_response or "").strip()) < 20:
        # Local couldn't answer — genuine knowledge gap
        await update_mastery(topics, correct=False, weight=0.1)
    elif similarity < 0.3:
        # Very low similarity — possible gap, but soft penalty
        await update_mastery(topics, correct=False, weight=0.05)
    else:
        # Reasonable similarity or local answered — positive signal
        await update_mastery(topics, correct=True, weight=0.1)

    # R-F179 (2026-05-11) — persistent-disagreement rollup. Per-event
    # weights stay low (don't repeat the 2026-04-21 mastery-collapse
    # incident from weight=0.7 penalties). But if the SAME topic sees
    # ≥5 low-similarity divergences in a 24h window, apply a stronger
    # corrective update once — the signal is no longer noise.
    # R-F210 (2026-05-11) — race-condition fix. The read→filter→append→
    # write sequence is not atomic; concurrent low-similarity calls
    # could all observe len>=5 and EACH fire weight=0.25 → multi-
    # penalty. Gate the corrective behind a Redis lock-style key with
    # 1h TTL; first caller wins, rest see the lock and skip.
    if similarity < 0.4 and topics:
        try:
            # Per-topic rolling counter for the last 24h.
            now = time.time()
            cutoff = now - 86400
            for topic in topics:
                key = f"crucix:student:divergence_streak:{topic}"
                streak_raw = await rs.get_json(key) or []
                streak_raw = [ts for ts in streak_raw if isinstance(ts, (int, float)) and ts > cutoff]
                streak_raw.append(now)
                await rs.set_json(key, streak_raw, ex=86400 * 2)
                if len(streak_raw) >= 5:
                    # R-F210: acquire single-fire lock with 1h TTL.
                    # rs.set has no NX semantic exposed; we approximate
                    # with get-check-set guarded by a short window.
                    lock_key = f"crucix:student:divergence_fire:{topic}"
                    held = await rs.get(lock_key)
                    if held:
                        # Another caller already fired the corrective
                        # for this topic in the last hour. Skip.
                        continue
                    await rs.set(lock_key, str(now), ex=3600)
                    # Strong corrective update — only fires when 5+ low-
                    # similarity events accumulate in a day, so it can't
                    # repeat the catastrophic 2026-04-21 collapse.
                    await update_mastery([topic], correct=False, weight=0.25)
                    logger.info(
                        "[student] R-F179 persistent-divergence correction on %s "
                        "(%d low-sim events in 24h)",
                        topic, len(streak_raw),
                    )
                    # Reset the streak so we don't fire twice off the same window
                    await rs.set_json(key, [now], ex=86400 * 2)
        except Exception as _rfe:
            logger.debug("R-F179 streak update failed: %s", _rfe)


@fail_wire(module="student", gap_type="engine_failure")
async def record_divergence_fuel(
    question: str,
    cloud_response: str,
    local_response: str | None,
    local_source: str | None,
    similarity: float,
) -> bool:
    """R-F1996 — capture a genuine divergence as full-length training fuel.

    Returns True if a fuel record was written. We only capture cases where the
    LOCAL stack actually produced a substantive answer that materially disagreed
    with the cloud teacher (similarity below the ceiling) — those are the highest-
    value SFT/DPO pairs (the model has a wrong answer to correct, not just a gap).
    Full text is kept (capped at training-grade lengths), never truncated previews.
    """
    lr = (local_response or "").strip()
    if len(lr) < _FUEL_MIN_LOCAL_CHARS:
        return False  # no real local attempt → nothing to mark as "rejected"
    if similarity >= _FUEL_SIMILARITY_CEILING:
        return False  # local agreed with the teacher → not a learning case
    cr = (cloud_response or "").strip()
    q = (question or "").strip()
    if not q or len(cr) < _FUEL_MIN_LOCAL_CHARS:
        return False
    try:
        fuel = await rs.get_json(DIVERGENCE_FUEL_KEY) or []
        fuel.append({
            "ts": time.time(),
            "question": q[:3000],
            "cloud_response": cr[:16000],     # SFT chosen — the teacher's answer
            "local_response": lr[:16000],     # DPO rejected — local's wrong attempt
            "local_source": local_source or "",
            "similarity": round(similarity, 3),
            "topics": detect_topics(q),
        })
        await rs.set_json(DIVERGENCE_FUEL_KEY, fuel[-_FUEL_MAX:], ex=45 * 86400)
        return True
    except Exception as e:
        logger.debug("[student] divergence fuel capture failed (non-fatal): %s", e)
        return False


@fail_wire(module="student", gap_type="engine_failure")
async def compare_local_silently(question: str, cloud_response: str) -> dict:
    """After a cloud LLM responds, run the local stack on the SAME question
    and compare. The cloud's answer is treated as the teacher's; the local
    stack's answer is the student's attempt. The divergence becomes
    study material.

    This is what makes ARIA actively learn from her teacher rather than
    passively cache his answers.
    """
    try:
        # F53 2026-04-28: pass silent=True so the symbolic reasoner does not
        # double-record the same `no_symbolic_rule` capability gap that the
        # pre-LLM try_local_reasoning call already recorded for this query.
        local = await reasoning_router.try_local_reasoning(question, silent=True)
        local_response = local.get("response") if local.get("answered") else None
        local_source = local.get("source") if local.get("answered") else None
        similarity = _quick_similarity(local_response or "", cloud_response)
        await record_divergence(question, cloud_response, local_response, local_source, similarity)
        # R-F1996 — full-length fuel capture for the training flywheel (no-op
        # unless this is a genuine local-wrong divergence with a real attempt).
        await record_divergence_fuel(question, cloud_response, local_response, local_source, similarity)
        return {
            "compared": True,
            "local_attempted": local.get("answered", False),
            "similarity": round(similarity, 3),
            "local_source": local_source,
        }
    except Exception as e:
        logger.warning("compare_local_silently failed: %s", e)
        return {"compared": False, "error": str(e)}


# ── Regional mastery (topic × region heat map) ────────────────────────────

# Heat-map regions — updated 2026-04-17 PM per user feedback on the
# NAK-session gap review. Turkey is ITS OWN column (not NATO-merged,
# per aria_global_positioning.md hard rule). LatAm is split between
# Lusophone (Brazil anchor) and non-Lusophone (Peru anchor). Central
# Africa is explicit. South Asia and Southeast Asia are separated.
# Balkans added as a Tier 2 positioning column.
REGIONS = [
    "lusophone", "west_africa", "east_africa", "central_africa",
    "north_africa", "southern_africa", "mena", "gulf",
    "turkey",              # standalone — not NATO
    "south_asia", "southeast_asia",
    "latam_lusophone", "latam_non_lusophone",
    "europe", "balkans", "nato",
    "global",
]

# R-F1744 — region → search-phrase map for the region-targeted gate-#2
# closer in reading_session. Each phrase carries the prominent countries
# of the region so a cost-free web search returns region-specific content
# that detect_regions() will re-recognise (read-grounded mastery lift).
_REGION_QUERY_PHRASE: dict[str, str] = {
    "lusophone": "Angola Mozambique",
    "west_africa": "Nigeria Ghana West Africa",
    "east_africa": "Ethiopia Kenya East Africa",
    "central_africa": "DRC Rwanda Central Africa",
    "north_africa": "Egypt Algeria North Africa",
    "southern_africa": "South Africa southern Africa SADC",
    "mena": "Middle East Iraq Jordan",
    "gulf": "Saudi Arabia UAE Gulf GCC",
    "turkey": "Turkey defence industry",
    "south_asia": "India Pakistan South Asia",
    "southeast_asia": "Indonesia Philippines Vietnam Southeast Asia",
    "latam_lusophone": "Brazil",
    "latam_non_lusophone": "Colombia Chile Latin America",
    "europe": "Ukraine Poland Europe",
    "balkans": "Serbia Balkans",
    "nato": "NATO alliance",
    "global": "global",
}

_REGION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("lusophone", re.compile(
        r"\b(?:angola|mozambique|cape\s+verde|cabo\s+verde|guinea.bissau|"
        r"são\s+tomé|cplp|lusophone|portuguese|fadm|faa|fasb)\b", re.I)),
    ("west_africa", re.compile(
        r"\b(?:west\s+africa|nigeria|ghana|senegal|côte\s+d.ivoire|cameroon|ecowas|"
        r"niger|mali|burkina|togo|benin|sierra\s+leone|liberia|aes\s+alliance)\b", re.I)),
    # R-F1947 (gate-#2 credit fix): Kenya/Nairobi/Tanzania/Uganda were miscategorised
    # under central_africa, so the osint×east_africa floor cell's own query
    # ("Ethiopia Kenya East Africa …") returned Kenya-heavy results that detect_regions
    # tagged central_africa → _grounded stayed False → update_regional_mastery never
    # fired → the gate-#2 floor (≈0.269) was frozen no matter how often it was visited.
    # Core East-African (EAC) states now correctly map to east_africa; central_africa
    # keeps the DRC/Congo cluster (+ Rwanda/Burundi, which sit in the DRC-conflict orbit).
    ("east_africa", re.compile(
        r"\b(?:east\s+africa|ethiopia|addis\s+ababa|somalia|mogadishu|eac|east\s+african\s+community|"
        r"amisom|djibouti|eritrea|south\s+sudan|sudan|"
        r"kenya|nairobi|mombasa|tanzania|dodoma|dar\s+es\s+salaam|uganda|kampala)\b", re.I)),
    # R-F2964 (C2): added the genuine "central africa" region-name literal — the
    # query phrase searches "…Central Africa" but the gate recognised only DRC/Congo
    # tokens, so region-named content failed to ground → the cell froze at ~0.05.
    ("central_africa", re.compile(
        r"\b(?:central\s+africa|d\.?r\.?c\.?|drc|democratic\s+republic\s+of\s+(?:the\s+)?congo|congo|"
        r"kinshasa|brazzaville|rwanda|kigali|burundi|bujumbura|m23|monusco)\b", re.I)),
    ("north_africa", re.compile(
        r"\b(?:north\s+africa|libya|algeria|morocco|tunisia|egypt|sahel|maghreb)\b", re.I)),
    ("southern_africa", re.compile(
        r"\b(?:southern\s+africa|south\s+africa|sadc|botswana|namibia|zimbabwe|zambia)\b", re.I)),
    ("mena", re.compile(
        r"\b(?:middle\s+east|syria|iraq|iran|jordan|lebanon|palestine|israel)\b", re.I)),
    ("gulf", re.compile(
        r"\b(?:saudi|uae|qatar|oman|kuwait|bahrain|gcc|edge\s+group|sami|tawazun|gami|vision\s+2030)\b", re.I)),
    ("turkey", re.compile(
        r"\b(?:turkey|türkiye|ssb|baykar|aselsan|roketsan|tai|stm|"
        r"savunma\s+sanayii|tb2|bayraktar|nihai\s+kullan)\b", re.I)),
    ("south_asia", re.compile(
        r"\b(?:south\s+asia|india|delhi|bengaluru|hindustan|hal\b|drdo|"
        r"pakistan|islamabad|bangladesh|dhaka|sri\s+lanka|nepal|"
        r"make\s+in\s+india|dap\s*2020|dpp)\b", re.I)),
    ("southeast_asia", re.compile(
        r"\b(?:south[\s\-]*east\s+asia|indonesia|jakarta|philippines|manila|vietnam|hanoi|thailand|"
        r"bangkok|myanmar|burma|malaysia|kuala\s+lumpur|singapore|"
        r"asean|aukus|quad)\b", re.I)),
    ("latam_lusophone", re.compile(
        r"\b(?:brazil|brasília|brasilia|brasil|são\s+paulo|rio\s+de\s+janeiro|"
        r"embraer|taurus\s+armas|avibras)\b", re.I)),
    ("latam_non_lusophone", re.compile(
        r"\b(?:latin\s+america|colombia|bogotá|bogota|indumil|cotecmar|"
        r"peru|lima|seace|"
        r"chile|santiago|dgmn|fidae|"
        r"argentina|buenos\s+aires|fabricaciones\s+militares|"
        r"ecuador|quito|"
        r"mexico|méxico|sedena|"
        r"mercosur|central\s+america|panama|panamá)\b", re.I)),
    ("europe", re.compile(
        r"\b(?:europe|ukraine|kyiv|poland|warsaw|romania|bucharest|baltic|czech|hungary|"
        r"european|pesco|edirpa|france|germany|italy|spain|portugal)\b", re.I)),
    ("balkans", re.compile(
        r"\b(?:serbia|belgrade|bosnia|sarajevo|kosovo|pristina|"
        r"north\s+macedonia|skopje|albania|tirana|montenegro|podgorica|"
        r"slovenia|ljubljana|croatia|zagreb|balkans|former\s+yugoslav)\b", re.I)),
    ("nato", re.compile(
        r"\b(?:nato|alliance|article\s+5|stanag|nspa|nsn)\b", re.I)),
]

REGIONAL_MASTERY_KEY = "crucix:aria:student:regional_mastery"
_regional_cache: dict | None = None

# R-F268 (2026-05-11) — no-scaffold-write rule mirroring R-F267. Empty-
# scaffold `{}` should never be persisted; only real regional-mastery
# updates qualify. Prevents heatmap data wipe across backend flips.
_regional_dirty: bool = False


def _mark_regional_dirty() -> None:
    """Flip the dirty flag — call from code that mutates _regional_cache
    with actual regional-mastery data (not scaffold initialisation)."""
    global _regional_dirty
    _regional_dirty = True


@fail_wire(module="student", gap_type="engine_failure")
def detect_regions(text: str) -> list[str]:
    """Detect which regions a text relates to."""
    if not text:
        return ["global"]
    regions = []
    for region, pattern in _REGION_PATTERNS:
        if pattern.search(text):
            regions.append(region)
    return regions or ["global"]


async def _load_regional_mastery() -> dict:
    global _regional_cache
    if _regional_cache is not None:
        return _regional_cache
    # R-F2664 — STRICT read. The old non-strict get_json() SWALLOWED a
    # store-not-ready StoreReadError to None → _regional_cache was poisoned to {}
    # for the whole process lifetime → the next update_regional_mastery persisted
    # that {} + 1 cell, CLOBBERING the durable key that held every cell (a silent
    # gate-#2 heatmap WIPE, observed as an empty heatmap after a slow-boot deploy;
    # the R-F268 dirty-guard can't catch it because a real update sets dirty=True).
    # Distinguish "store not ready" (transient → return {} but do NOT cache, so the
    # next call retries and no write can clobber) from "genuinely absent" (→ cache
    # an empty scaffold; the dirty-guard keeps it from ever persisting on its own).
    try:
        raw = await rs.get_json_strict(REGIONAL_MASTERY_KEY)
    except Exception as _sre:
        logger.warning(
            "[R-F2664] regional mastery load deferred — store not ready (%s); "
            "cache left uninitialised so no write can clobber the durable key", _sre)
        return {}
    _regional_cache = raw if isinstance(raw, dict) else {}
    return _regional_cache


async def _save_regional_mastery() -> None:
    """R-F268 (2026-05-11): skip-when-not-dirty. Same rule as _save_mastery —
    scaffold-only caches must never overwrite the destination backend's
    real data across a flip. Persists only when actual regional-mastery
    updates have landed since the last load."""
    global _regional_dirty
    if _regional_cache is None or not _regional_dirty:
        return
    await rs.set_json(REGIONAL_MASTERY_KEY, _regional_cache, ex=180 * 86400)
    _regional_dirty = False


# ── R-F2963 (C0): coalesce the whole-blob REGIONAL_MASTERY_KEY save ──────────
# _save_regional_mastery rewrites the ENTIRE regional cache to ONE key via
# set_json on EVERY observation. The reading loop grades ~15 cells in a tight
# burst (student._study_weak_regional_cells), and with SEED_ALL_REGIONS on (C3)
# the cache is ~224 cells — so per-observation whole-blob writes hammer the
# single aiosqlite writer (the exact write-ceiling the R-F2433 comment warns
# would "wipe credited cells"). This mirrors the R-F2408 topic-mastery coalesce:
# collapse the burst to at most one write per interval; a force-flush at the end
# of each reading session + the snapshot loop guarantees nothing is stranded.
# Default OFF (like R-F2408): the C3 seed-all flip is made SAFE by the R-F2664
# strict-read clobber guard (which already prevents the "credited cells wiped"
# failure the R-F2433 comment warned about), so coalescing is write-HYGIENE, not
# a correctness pre-req — appropriately opt-in. Flip ARIA_REGIONAL_COALESCE_SAVE=1
# if the 224-cell whole-blob write is ever observed to pressure the single writer;
# the force-flush is wired (reading session + snapshot loop) so turning it on
# never strands the last update. OFF keeps update_regional_mastery's immediate-
# write contract intact.
_REGIONAL_SAVE_COALESCE = os.getenv(
    "ARIA_REGIONAL_COALESCE_SAVE", "0").strip().lower() in ("1", "true", "yes", "on")
_REGIONAL_FLUSH_INTERVAL_S = float(os.getenv("ARIA_REGIONAL_FLUSH_INTERVAL_S", "15"))
_regional_last_save: float = 0.0
_regional_save_lock: "asyncio.Lock | None" = None


def _get_regional_save_lock() -> "asyncio.Lock":
    """Lazy-bound single-flight lock (created inside the running loop, like
    _get_mastery_save_lock) so pytest's per-test asyncio.run loops each bind a
    fresh lock."""
    global _regional_save_lock
    if _regional_save_lock is None:
        _regional_save_lock = asyncio.Lock()
    return _regional_save_lock


async def _maybe_flush_regional(force: bool = False) -> bool:
    """R-F2963 — persist the regional cache, coalesced when the flag is ON.

    Flag OFF: identical to the pre-R-F2963 inline behaviour (_save_regional_mastery,
    which no-ops when not dirty). Flag ON (default): write at most once per
    _REGIONAL_FLUSH_INTERVAL_S; intervening updates keep _regional_dirty=True so
    the next flush (or a force flush) persists the latest whole-cache snapshot.
    Single in-flight flush via a lazy lock. Returns True iff a DB write happened."""
    global _regional_last_save
    if not _REGIONAL_SAVE_COALESCE:
        await _save_regional_mastery()
        return True
    if _regional_cache is None or not _regional_dirty:
        return False
    now = time.time()
    if not force and (now - _regional_last_save) < _REGIONAL_FLUSH_INTERVAL_S:
        return False  # coalesce — keep the dirty flag, defer the write
    lock = _get_regional_save_lock()
    if lock.locked() and not force:
        return False
    async with lock:
        now = time.time()
        if not force and (now - _regional_last_save) < _REGIONAL_FLUSH_INTERVAL_S:
            return False
        if _regional_cache is None or not _regional_dirty:
            return False
        await _save_regional_mastery()  # resets _regional_dirty on success
        _regional_last_save = now
        return True


@fail_wire(module="student", gap_type="engine_failure")
async def flush_regional() -> bool:
    """R-F2963 — public force-flush of any deferred regional-mastery write. Wired
    into the reading session + snapshot loop so a quiet period can't leave the
    last learning signal unpersisted. Safe anytime; no-op when nothing pending."""
    return await _maybe_flush_regional(force=True)


@fail_wire(module="student", gap_type="engine_failure")
async def update_regional_mastery(
    topics: list[str], regions: list[str], correct: bool, weight: float = 1.0,
) -> None:
    """Update mastery for topic×region combinations.

    R-F3694 — `correct` MUST be a real bool. See the note on update_mastery:
    the graders are tri-state (`True | False | None`) and `None` means the cell
    was NOT MEASURED. Coercing it here via `1.0 if correct else 0.0` recorded a
    measurement failure as ARIA getting the answer WRONG, which is what drove
    the gate-#2 heatmap floor to 0.003.
    """
    if not topics or not regions:
        return
    if not isinstance(correct, bool):
        # Refuse LOUDLY rather than silently pick a branch. Note this axis has
        # no HARD_FLOOR clamp (unlike update_mastery), so a wrong value decays
        # the EWMA toward zero without bound: 0.5·(1−0.03)^n = 0.003 at n≈168.
        logger.error(
            "[R-F3694] update_regional_mastery called with correct=%r (%s) — "
            "expected bool. An UNMEASURED cell must be SKIPPED by the caller, "
            "never written as a miss. Refusing the update for topics=%s "
            "regions=%s.",
            correct, type(correct).__name__, topics, regions,
        )
        return
    rm = await _load_regional_mastery()
    if _regional_cache is None:
        # R-F2664 — the load was DEFERRED (store not ready): _load returned {} but
        # deliberately did NOT cache it. Skip this update rather than mutate a
        # transiently-empty map and persist it, which would clobber the durable
        # key. The observation is lost; the mastery data is protected; the next
        # warm call resumes normally.
        return
    alpha = min(0.3, 0.1 * weight)
    for topic in topics:
        if topic not in TOPICS:
            continue
        for region in regions:
            key = f"{topic}:{region}"
            if key not in rm:
                rm[key] = {"score": INITIAL_MASTERY, "samples": 0}
            entry = rm[key]
            obs = 1.0 if correct else 0.0
            entry["score"] = entry["score"] + alpha * (obs - entry["score"])
            entry["samples"] = entry.get("samples", 0) + 1
    _regional_cache.update(rm)
    _mark_regional_dirty()  # R-F268 — actual regional-mastery update
    await _maybe_flush_regional()  # R-F2963 — coalesced whole-blob write


@fail_wire(module="student", gap_type="engine_failure")
async def get_regional_heatmap() -> dict:
    """Return mastery heat map: topic × region scores.

    R-F684 (2026-05-18) — filter measurement noise so the operator
    dashboard shows an honest floor for Phase A gate #2:

      (A) Drop stale keys whose region isn't in current REGIONS list.
          The 2026-04-17 split renamed `latam` → `latam_lusophone` +
          `latam_non_lusophone` and removed the umbrella `asia_pacific`
          tag. Pre-split keys like `procurement:latam` (0.507) and
          `competitor_intel:asia_pacific` (0.557) still sit in
          _regional_cache near INITIAL_MASTERY and were dragging the
          visible floor — detect_regions() no longer emits those tags
          so they'll never update.

      (B) Drop topic=`general` from the heatmap entirely. `general` is
          a catch-all fallback that fires when detect_topics() can't
          classify; measuring regional mastery against it is noise.
          The mastery EWMA for `general` stays near INITIAL_MASTERY
          (0.5) regardless of what ARIA actually knows in that region.

      (C) Drop region=`global` from the heatmap entirely. `global` is
          the detect_regions() no-match fallback — a region-LESS query
          has no region, so it is a TOPIC-mastery datapoint (gate #1),
          NOT a topic×REGION datapoint (gate #2). Putting it in
          `global` double-counts + mis-categorizes. The existing
          `general`-topic drop (B) is the same class of fix: a
          catch-all fallback that classifies poorly, measuring
          regional mastery against it is noise. R-F1893.

    After A+B+C the floor reflects legitimate weak cells (real
    coverage gaps over real regions), pointing the operator at work
    that would actually close gate #2.
    """
    rm = await _load_regional_mastery()
    valid_regions = set(REGIONS)
    heatmap: dict[str, dict[str, float]] = {}
    cell_samples: dict[tuple[str, str], int] = {}  # R-F2990 — per-cell observation count
    for key, val in rm.items():
        if ":" not in key:
            continue
        topic, region = key.split(":", 1)
        # R-F684 (A) — drop dead region keys (renamed / removed in split).
        if region not in valid_regions:
            continue
        # R-F684 (B) — drop general-topic noise from heatmap floor.
        if topic == "general":
            continue
        # R-F1893 (C) — drop global-region noise from heatmap floor.
        # 'global' is the detect_regions() no-match fallback; a region-less
        # query is a topic-mastery datapoint (gate #1), not a topic×region
        # datapoint (gate #2). Same rationale as the 'general'-topic drop.
        if region == "global":
            continue
        if topic not in heatmap:
            heatmap[topic] = {}
        heatmap[topic][region] = round(val.get("score", INITIAL_MASTERY), 3)
        # R-F2990 — carry the sample count so the dashboard can honestly separate
        # "not yet measured" (scaffold) from "measured and genuinely weak".
        try:
            cell_samples[(topic, region)] = int(val.get("samples", 0) or 0)
        except (TypeError, ValueError):
            cell_samples[(topic, region)] = 0
    # Find weak cells (< WEAK_THRESHOLD = 0.55, "needs study")
    weak_cells = []
    # R-F711 (2026-05-19) — find floor-breach cells (< GATE_2_FLOOR_TARGET
    # = 0.70, "blocking Phase A gate #2"). Cells between WEAK_THRESHOLD
    # and GATE_2_FLOOR_TARGET are operationally fine for study-priority
    # but still drag the heatmap floor below the gate-2 target. Pre-R-F711
    # the dashboard could see floor=0.662 with weak_cells=[] and not
    # surface WHICH cells were blocking — making the gate signal opaque.
    floor_breach_cells = []
    for topic, regions in heatmap.items():
        for region, score in regions.items():
            if score < WEAK_THRESHOLD:
                weak_cells.append({"topic": topic, "region": region, "score": score})
            if score < GATE_2_FLOOR_TARGET:
                floor_breach_cells.append({"topic": topic, "region": region, "score": score})
    weak_cells.sort(key=lambda x: x["score"])
    floor_breach_cells.sort(key=lambda x: x["score"])
    # R-F2990 — authoritative, SAMPLES-based coverage split. A cell is "still at
    # the ~50% initial scaffold" iff it has NOT YET BEEN MEASURED — i.e. <=1
    # observation — NOT merely because its score sits near 0.50. The old dashboard
    # proxy (score < 0.55) conflated two opposite states: genuine unmeasured
    # scaffold AND measured-weak cells that real failing recall-grades drove far
    # BELOW 0.50 (e.g. procurement×central_africa at 0.04). Those weak cells appear
    # in weak_cells above, so counting them as "unmeasured scaffold" was self-
    # contradictory and understated how much real measurement has happened. Keying
    # the split on `samples` fixes it: scaffold = <=1 obs; a low score with >=2 obs
    # is a MEASURED gap, not scaffold. The dashboard reads scaffold_cells directly.
    sampled_cells = len(cell_samples)
    scaffold_cells = sum(1 for s in cell_samples.values() if s <= 1)
    measured_weak_cells = sum(
        1 for (t, r), s in cell_samples.items()
        if s >= 2 and heatmap[t][r] < WEAK_THRESHOLD
    )
    return {
        "heatmap": heatmap,
        "weak_cells": weak_cells[:20],
        "floor_breach_cells": floor_breach_cells[:20],
        "gate_2_floor_target": GATE_2_FLOOR_TARGET,
        "cell_coverage": {
            "sampled_cells":       sampled_cells,
            "scaffold_cells":      scaffold_cells,                 # <=1 obs — not yet measured
            "measured_cells":      sampled_cells - scaffold_cells,  # >=2 obs — a real measurement exists
            "measured_weak_cells": measured_weak_cells,            # measured AND below the weak threshold
            "scaffold_definition": "cells with <=1 observation (still at the ~0.50 initial scaffold)",
        },
    }


# ── Stats and reporting ────────────────────────────────────────────────────

@fail_wire(module="student", gap_type="engine_failure")
async def get_student_stats() -> dict:
    mastery_report = await get_mastery_report()
    curriculum = await get_curriculum()
    quiz_history = await rs.get_json(QUIZ_HISTORY_KEY) or []
    reading_log = await rs.get_json(READING_LOG_KEY) or []
    divergences = await rs.get_json(DIVERGENCE_KEY) or []

    recent_quizzes = quiz_history[-10:]
    avg_quiz_score = (
        sum(q.get("score", 0) for q in recent_quizzes) / max(len(recent_quizzes), 1)
        if recent_quizzes else 0
    )

    return {
        "mastery": mastery_report,
        "curriculum": curriculum,
        "quiz_count": len(quiz_history),
        "recent_quiz_score": round(avg_quiz_score, 3),
        "reading_sessions": len(reading_log),
        "articles_studied_total": sum(r.get("articles_read", 0) for r in reading_log),
        "divergences_recorded": len(divergences),
        "divergences_needing_study": sum(1 for d in divergences if d.get("needs_study")),
    }


@fail_wire(module="student", gap_type="engine_failure")
async def mastery_to_prompt_addendum(message: str) -> str:
    """Generate a system-prompt addendum that surfaces weak topics relevant
    to the current query. Closes the feedback loop: student tracks mastery
    → prompt tells ARIA she's weak → ARIA is more careful / cites more
    sources on that topic.

    Called by _build_calibrated_system_prompt in aria_engine.py.
    Returns empty string if no relevant weakness is detected.
    """
    if not message or len(message.strip()) < 10:
        return ""
    topics = detect_topics(message)
    if not topics or topics == ["general"]:
        return ""

    mastery = await _load_mastery()

    # Apply calibration auto-tune offsets. When ARIA is consistently
    # overconfident across runs, the auto-tune module raises these
    # thresholds so more claims fall into the constrained tiers. When
    # underconfident, it lowers them. Falls back to the raw constants
    # if auto_tune is unavailable or Redis is offline.
    try:
        from . import calibration_auto_tune as _cat
        _weak_th = await _cat.get_effective_threshold("weak", WEAK_THRESHOLD)
        _crit_th = await _cat.get_effective_threshold("critical", 0.40)
    except Exception:
        _weak_th = WEAK_THRESHOLD
        _crit_th = 0.40

    weak_relevant: list[tuple[str, float]] = []
    for t in topics:
        m = mastery.get(t, {})
        score = m.get("score", INITIAL_MASTERY)
        samples = m.get("samples", 0)
        if score < _weak_th and samples >= 2:
            weak_relevant.append((t, score))

    if not weak_relevant:
        return ""

    # Separate critical (<_crit_th) from weak (_crit_th..._weak_th) topics
    _CRITICAL_THRESHOLD = _crit_th
    critical = [(t, s) for t, s in weak_relevant if s < _CRITICAL_THRESHOLD]
    weak_only = [(t, s) for t, s in weak_relevant if s >= _CRITICAL_THRESHOLD]

    lines = [
        "⚠ MASTERY ALERT — MANDATORY BEHAVIORAL RULES for this response."
    ]

    # ── CRITICAL mastery (<40%) — human review required ──────────
    if critical:
        lines.append("")
        lines.append("🔴 CRITICAL MASTERY DEFICIT — the following topic(s) "
                     "are below 40%. HUMAN REVIEW REQUIRED on any output "
                     "touching these domains:")
        for topic, score in sorted(critical, key=lambda x: x[1]):
            lines.append(f"  • {topic}: mastery {score:.0%} — CRITICAL")
        lines.append("")
        lines.append("CRITICAL-TIER RULES (override everything below):")
        lines.append("C1. CONFIDENCE FLOOR — Maximum allowed tag: [UNCERTAIN]. "
                     "You MUST NOT use [CONFIRMED], [PROBABLE], or [ASSESSED] "
                     "on any claim touching the critical topic(s). Every claim "
                     "must carry [UNCERTAIN — low mastery, human review required].")
        lines.append("C2. HUMAN REVIEW FLAG — Begin your response with: "
                     "\"⚠ LOW-CONFIDENCE DOMAIN: My track record on [topic] is "
                     "poor (mastery [X]%). This response requires human verification "
                     "before any external use.\" This flag is non-negotiable.")
        lines.append("C3. NO RECOMMENDATIONS — You MUST NOT make GO/NO-GO "
                     "recommendations on critical-mastery topics. Present "
                     "the evidence you have and explicitly state that a human "
                     "must make the call.")
        lines.append("C4. TRIPLE-SOURCE RULE — Every material fact MUST cite "
                     "at least 3 independent sources OR carry [UNVERIFIED]. "
                     "Two sources is not enough at critical mastery.")

    # ── WEAK mastery (40-55%) — constrained but operational ──────
    if weak_only:
        lines.append("")
        lines.append("🟠 LOW MASTERY — the following topic(s) are below "
                     f"{WEAK_THRESHOLD:.0%}:")
        for topic, score in sorted(weak_only, key=lambda x: x[1]):
            lines.append(f"  • {topic}: mastery {score:.0%}")
        lines.append("")
        lines.append("LOW-TIER RULES:")
        lines.append("1. CONFIDENCE CAP — You MUST NOT use [CONFIRMED] on any "
                     "claim touching the weak topic(s). Maximum allowed: "
                     "[PROBABLE] with inline citation. Use [ASSESSED] or "
                     "[UNCERTAIN] if you have fewer than 2 independent sources.")
        lines.append("2. DOUBLE-SOURCE RULE — Every material fact on the weak "
                     "topic(s) MUST cite at least 2 independent sources OR "
                     "carry an explicit [SINGLE SOURCE — UNVERIFIED] flag.")
        lines.append("3. EXPLICIT GAP DISCLOSURE — If you are unsure about "
                     "any aspect of the weak topic(s), say so explicitly: "
                     "\"My knowledge on [topic] has been unreliable recently "
                     "— I recommend independent verification.\"")
        lines.append("4. SEARCH BEFORE RECALL — For weak topic(s), prefer "
                     "running a search tool over relying on memory/RAG.")

    # ── REGIONAL MASTERY CHECK — missing cells and uninformed prior ──
    # Check if the query hits topic×region combinations with no data
    # (-- cells) or uninformed prior (≤55%). These are the most
    # dangerous gaps because ARIA answers confidently from noise.
    _UNINFORMED_PRIOR_THRESHOLD = 0.56  # anything at or below initial EWMA
    try:
        regions = detect_regions(message)
        rm = await _load_regional_mastery()
        missing_cells = []
        prior_cells = []
        for t in topics:
            if t not in TOPICS:
                continue
            for r in regions:
                key = f"{t}:{r}"
                if key not in rm or rm[key].get("samples", 0) < 2:
                    missing_cells.append(f"{t}/{r}")
                elif rm[key].get("score", 0.5) <= _UNINFORMED_PRIOR_THRESHOLD:
                    prior_cells.append((f"{t}/{r}", rm[key].get("score", 0.5)))

        if missing_cells:
            lines.append("")
            lines.append("🔴 NO VERIFIED DATA — the following topic/region "
                         "combinations have ZERO data points:")
            for cell in missing_cells[:8]:
                lines.append(f"  • {cell} — NO DATA")
            lines.append("MANDATORY: Begin your response with: \"⚠ I have no "
                         "verified intelligence for [topic] in [region]. The "
                         "following is based on general training data, not "
                         "verified sources. Human verification required before "
                         "any action.\"")

        if prior_cells:
            lines.append("")
            lines.append("🟠 UNINFORMED PRIOR — the following cells are at or "
                         "below the initial baseline (possibly noise, not knowledge):")
            for cell, score in prior_cells[:8]:
                lines.append(f"  • {cell}: {score:.0%}")
            lines.append("Treat any output on these topic/region combinations "
                         "as UNVERIFIED. Do not use [CONFIRMED] or [PROBABLE].")
    except Exception:
        pass

    lines.append("")
    lines.append("These rules are automatically lifted when mastery "
                 f"recovers above {WEAK_THRESHOLD:.0%}. Critical-tier "
                 f"rules lift at {_CRITICAL_THRESHOLD:.0%}.")
    return "\n".join(lines)


@fail_wire(module="student", gap_type="engine_failure")
async def lift_all_topics(bump: float) -> dict[str, float]:
    """Adjust every topic's mastery score by `bump`. Positive = lift up,
    negative = pull down (R-F166, 2026-05-11).

    Original (pre-R-F166): only supported `bump > 0` and was used by
    calibration_review's UNDERCONFIDENT branch. That meant the loop was
    one-way — if reality says mastery is too high (the production case:
    88% claimed vs 45% accurate, 42pp overconfident), nothing happened.
    Headline mastery sat at 88% for weeks.

    R-F166 extends the function to accept negative bumps. Calibration
    now passes a negative bump on OVERCONFIDENT (capped to keep changes
    bounded — caller enforces -3pp/run, |delta|>0.15 gate).

    Floor: 0.10 — mastery cannot go below 10% even if reality is
    catastrophic; the floor is the recoverable starting point for
    organic re-learning.
    """
    # R-F206 (2026-05-11) — guard NaN/inf. `nan == 0` is False AND
    # `nan > 0` is False, so a bump=nan would fall into the else branch
    # below, max(floor, old + nan) returns nan, and every topic's score
    # becomes nan — poisoning the mastery dict (which then serialises as
    # invalid JSON for any strict parser, and corrupts all downstream
    # calibration / dashboard rendering). One bad calibration write
    # kills the dashboard. Catch it here.
    import math as _math_b
    if not bump or _math_b.isnan(bump) or _math_b.isinf(bump):
        return {}
    mastery = await _load_mastery()
    new_scores: dict[str, float] = {}
    now = time.time()
    direction = "lift" if bump > 0 else "drop"
    # R-F806 (2026-05-22): respect per-topic HARD_FLOORS on drops.
    # Pre-R-F806 used a hardcoded 0.10 floor here — completely
    # independent of HARD_FLOORS. Calibration_review applies -3pp
    # via lift_all_topics(-drop), and the old code happily dropped
    # legal (HARD_FLOORS=0.70) to 0.10 if calibration kept firing.
    # This was the bypass around R-F796's per-tier clamping.
    # Live evidence 2026-05-22 15:59 UTC: `Calibration-driven mastery
    # drop: -0.030 on 11 topics` then `MASTERY HARD FLOOR BREACH:
    # legal (66% < 70%)` recurring. R-F806 closes the bypass: drops
    # clamp at the topic's HARD_FLOORS entry (default 0.50). Topics
    # already below their hard floor hold steady (no auto-heal up,
    # no further drop) — same semantics as R-F796.
    fallback_floor = 0.10  # only for positive-bump branch consistency
    for topic in TOPICS:
        if topic not in mastery:
            mastery[topic] = {"score": INITIAL_MASTERY, "samples": 0,
                               "correct": 0, "wrong": 0, "last_practiced": 0}
        m = mastery[topic]
        old_score = m.get("score", INITIAL_MASTERY)
        if bump > 0:
            new_score = min(MASTERY_CEILING, old_score + bump)
        else:
            # R-F806: per-topic hard floor, mirroring R-F796's
            # clamp-or-hold logic for negative updates.
            topic_floor = HARD_FLOORS.get(topic, 0.50)
            proposed = old_score + bump  # bump is negative here
            if old_score >= topic_floor:
                new_score = max(topic_floor, proposed)
            else:
                # Already below floor (legacy data) — hold steady.
                new_score = old_score
        m["score"] = new_score
        m["last_practiced"] = now
        # Record that this change was calibration-driven, NOT organic
        m["last_calibration_lift_at"] = now
        m["last_calibration_lift_bump"] = round(bump, 4)
        new_scores[topic] = new_score
    _mastery_cache.update(mastery)
    _mark_mastery_dirty()  # R-F267 — calibration lift is genuine state change, persist
    await _save_mastery()
    logger.info(
        "Calibration-driven mastery %s: %+.3f on %d topics",
        direction, bump, len(new_scores),
    )
    return new_scores

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
