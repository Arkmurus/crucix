"""ARIA Golden Q&A Evaluation Framework — regressi

on catching for prompt/code
changes using a hand-curated set of question/expected-answer pairs.

Why this exists
═══════════════
LLM-as-judge without ground truth drifts. A/B testing without users gives
no signal. The cheapest reliable thing for a small team running ARIA in
production is a hand-picked set of 20-50 questions where you (Antonio) know
what a *good* answer looks like, and a fast deterministic way to detect
when a code or prompt change makes those answers worse.

This module is that. It's deliberately:
  - JSON-shaped, not a vector database (humans curate the set)
  - Backed by Redis so the seed survives restarts and we don't add a file
    write to the deploy path
  - Scored with the existing sentence-transformers model (already loaded
    by RAG / semantic_search) so there's no extra dependency
  - Manual to seed (via /eval add fb_<id> from feedback flow OR direct
    POST) — the team's judgment IS the ground truth

What it is NOT
══════════════
- Not LLM-as-judge — that's the next phase, scoped to specific claim
  types (cited-source verification, confidence-tag honesty)
- Not a vector recall benchmark — measures end-to-end answer quality,
  not retrieval precision
- Not run automatically on every deploy — that's a flag I left off so
  the team controls when regressions are checked. Trigger with
  /eval (WhatsApp) or POST /api/aria/eval/run (CI/cron)

Scoring
═══════
R-F1396 (WS-0a, learning strategy): each entry is graded by an LLM judge
(eval_judge.judge_answer — strict rubric, factual agreement not wording)
and the verdict drives the bucket: correct→pass, partial→warn, wrong→fail.
Cosine similarity is still computed and recorded per entry (continuity for
deltas) and is the FALLBACK bucket whenever the judge is disabled
(ARIA_EVAL_JUDGE_ENABLED=0), unavailable, or returns unscored:
  ≥ 0.75 → pass     (semantically equivalent)
  0.50–0.75 → warn  (related but drifted)
  < 0.50 → fail     (different answer or hallucination)
Pre-R-F1396 the cosine score was the ONLY ruler and failed correct-but-
differently-worded answers (the 21.6% v0.2 artifact class).

A run produces a summary: pass_rate, mean_score, judge_coverage, count by
bucket, and delta vs the previous run so regressions surface immediately."""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

from . import eval_judge

import logging
import re
import time
import uuid
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.eval")

GOLDEN_SET_KEY = "crucix:aria:eval:golden_set"
RUNS_KEY = "crucix:aria:eval:runs"
RUNS_TTL = 90 * 86400

PASS_THRESHOLD = 0.75
WARN_THRESHOLD = 0.50
DEFAULT_MAX_GOLDEN = 600  # Lifted from 200 → 600 for Phase A gate #6 (500-Q eval set + drift headroom)


# ── Golden set CRUD ────────────────────────────────────────────────────────

async def get_golden_set() -> list[dict]:
    """Return the full golden set, oldest first."""
    try:
        return await rs.get_json(GOLDEN_SET_KEY) or []
    except Exception as e:
        logger.warning("get_golden_set failed: %s", e)
        return []


async def get_golden_entry(entry_id: str) -> dict | None:
    items = await get_golden_set()
    for it in items:
        if it.get("id") == entry_id:
            return it
    return None


async def add_golden_entry(
    question: str,
    expected_answer: str,
    *,
    category: str = "general",
    notes: str = "",
    source: str = "manual",
    added_by: str = "",
    feedback_id: str = "",
) -> dict:
    """Append a question/expected-answer pair to the golden set.

    The expected_answer is what the team considers correct — typically
    the answer ARIA *gave* on a 👍-rated reply (promoted from feedback)
    or hand-written by an analyst for tricky cases.
    """
    if not question or not expected_answer:
        return {"ok": False, "reason": "question and expected_answer required"}
    # ── R-F3702 — a FROZEN set is not writable ──────────────────────────────
    #
    # THE DEFECT: neither this function nor remove_golden_entry ever consulted
    # FREEZE_KEY, while `DAILY-GOLDEN-AUTOGEN` (tasks.yaml:1122) runs at 05:30
    # UTC with `max_candidates: 20` and auto-promotes straight into the golden
    # set — its own comment says "no human re-gate". So the gate-#6 pin the
    # operator earned on 2026-07-16 (count 500, hash a07b6af7…) breaks on the
    # FIRST non-duplicate promotion, and gate #6 flips to `drifted_from_pin`.
    #
    # A benchmark anyone can still edit is not a benchmark. Refusing the write
    # while frozen is the whole point of the pin — the alternative is a
    # benchmark that silently becomes a different benchmark, which makes every
    # historical pass_rate delta meaningless.
    #
    # Unfreezing is deliberate and already exists (unfreeze_golden_set), so a
    # genuine set revision is one explicit operator action, not a side effect
    # of a cron job.
    _frozen = await _is_golden_set_frozen()
    if _frozen:
        logger.info(
            "[R-F3702] golden-set ADD refused — the set is FROZEN (gate #6 pin). "
            "source=%s question=%r", source, question[:80],
        )
        return {
            "ok": False,
            "reason": "golden set is FROZEN (Phase A gate #6 pin) — unfreeze "
                      "deliberately before revising the benchmark",
            "frozen": True,
        }
    items = await get_golden_set()
    entry = {
        "id": f"gold_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
        "question": question[:4000],
        "expected_answer": expected_answer[:6000],
        "category": (category or "general")[:64],
        "notes": (notes or "")[:600],
        "source": (source or "manual")[:64],  # manual|feedback|incident
        "added_by": (added_by or "")[:120],
        "feedback_id": feedback_id or "",
        "added_at": time.time(),
    }
    # De-dupe on question (case-insensitive, whitespace-normalised)
    norm = " ".join(question.strip().lower().split())
    for existing in items:
        if " ".join((existing.get("question") or "").strip().lower().split()) == norm:
            return {"ok": False, "reason": "duplicate question", "existing_id": existing.get("id")}
    items.append(entry)
    # ── R-F3702 — overflow must REFUSE, never DELETE ────────────────────────
    #
    # `items = items[-DEFAULT_MAX_GOLDEN:]` silently dropped the OLDEST entries
    # once the set passed 600. With DAILY-GOLDEN-AUTOGEN promoting up to 20/day
    # the set crosses 600 in about five days, at which point the cap begins
    # destroying PINNED benchmark questions — unrecoverably, and with no signal.
    #
    # That is also a §7 violation (no eviction; overflow → cold storage, never
    # delete). A cap that deletes is a data-loss bug wearing a limit's clothes.
    if len(items) > DEFAULT_MAX_GOLDEN:
        logger.warning(
            "[R-F3702] golden-set ADD refused — at capacity (%d/%d). The old "
            "behaviour silently deleted the OLDEST entries here, which destroys "
            "pinned benchmark questions. Raise ARIA_EVAL_MAX_GOLDEN or curate "
            "the set explicitly.",
            len(items) - 1, DEFAULT_MAX_GOLDEN,
        )
        return {
            "ok": False,
            "reason": f"golden set at capacity ({DEFAULT_MAX_GOLDEN}) — refusing "
                      f"to evict an existing entry to make room",
            "at_capacity": True,
            "total": len(items) - 1,
        }
    try:
        await rs.set_json(GOLDEN_SET_KEY, items)
    except Exception as e:
        logger.warning("add_golden_entry persist failed: %s", e)
        return {"ok": False, "reason": str(e)}
    logger.info("Golden entry added: %s (%s)", entry["id"], entry["category"])
    return {"ok": True, "entry": entry, "total": len(items)}


async def _is_golden_set_frozen() -> bool:
    """R-F3702 — is the golden set pinned for Phase A gate #6?

    Reads with `get_json_strict` deliberately: on a store failure this must
    fail CLOSED (treat as frozen and refuse the write). Assuming "not frozen"
    when we cannot tell would let a cron job mutate a pinned benchmark during
    exactly the store trouble that makes the mutation hardest to notice.
    """
    try:
        rec = await rs.get_json_strict(FREEZE_KEY) or {}
        return bool(rec.get("frozen"))
    except Exception as e:
        logger.warning(
            "[R-F3702] freeze state unreadable (%s) — treating the golden set as "
            "FROZEN and refusing the write", e,
        )
        return True


async def remove_golden_entry(entry_id: str) -> dict:
    # R-F3702 — removal is a mutation too. Gate #6's pin covers count AND a
    # content hash, so a delete drifts it exactly as an add does.
    if await _is_golden_set_frozen():
        logger.info("[R-F3702] golden-set REMOVE refused (frozen): %s", entry_id)
        return {
            "ok": False,
            "reason": "golden set is FROZEN (Phase A gate #6 pin) — unfreeze "
                      "deliberately before revising the benchmark",
            "frozen": True,
        }
    items = await get_golden_set()
    new_items = [it for it in items if it.get("id") != entry_id]
    if len(new_items) == len(items):
        return {"ok": False, "reason": "not found"}
    try:
        await rs.set_json(GOLDEN_SET_KEY, new_items)
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    return {"ok": True, "removed": entry_id, "remaining": len(new_items)}


# ── Freeze pin — Phase A gate #6 (R-F2640) ─────────────────────────────────
#
# Gate #6 is "500-Q eval FROZEN". Before R-F2640 nothing measured `frozen`:
#   * main.py's aggregator passed the gate on `len(get_golden_set()) >= 500`
#     — that is the SIZE of a mutable, appendable list. It cannot detect a
#     mutation at all, so it certified "frozen" for a set that was still
#     being written to. Same class as R-F560's vacuous gate-#3 pass.
#   * routes/aria.py's fork read `crucix:aria:eval:500q:status`.frozen — a key
#     that NO code in the tree ever wrote, so it read {} → False → the gate was
#     structurally UNCLOSEABLE (a fabricated FAIL, the mirror sin).
#
# A frozen set is one an operator has PINNED: we record the count + a content
# hash at freeze time. The gate then passes only while the live set still
# matches that pin. Drift (an entry added, edited, or removed) re-OPENS the
# gate, which is the entire point of freezing an eval set — a benchmark you can
# still edit is not a benchmark. Absence of a pin is reported as `not_frozen`,
# never assumed either way (CLAUDE.md §1 / R-F2622).
FREEZE_KEY = "crucix:aria:eval:500q:status"  # reuses the fork's key — now WRITTEN
GATE_6_TARGET = 500


def golden_set_hash(items: list[dict]) -> str:
    """Stable content hash over the golden set.

    Sorted by id so ordering churn is not drift; covers id + question +
    expected_answer (the things that define the benchmark). Mutating any of
    them changes the hash and therefore re-opens gate #6.
    """
    import hashlib

    h = hashlib.sha256()
    for it in sorted(items, key=lambda x: str(x.get("id", ""))):
        h.update(str(it.get("id", "")).encode())
        h.update(b"\x1f")
        h.update(str(it.get("question", "")).encode())
        h.update(b"\x1f")
        h.update(str(it.get("expected_answer", "")).encode())
        h.update(b"\x1e")
    return h.hexdigest()


async def freeze_golden_set(frozen_by: str = "operator", target: int = GATE_6_TARGET) -> dict:
    """Pin the current golden set — the operator action that CAN close gate #6.

    Refuses to pin a set smaller than the target: freezing 12 questions would
    otherwise "close" a 500-Q gate.
    """
    items = await get_golden_set()
    if len(items) < target:
        return {
            "ok": False,
            "reason": f"golden set has {len(items)} entries — need >= {target} to freeze",
            "count": len(items),
            "target": target,
        }
    record = {
        "frozen": True,
        "count": len(items),
        "hash": golden_set_hash(items),
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "frozen_by": frozen_by,
        "target": target,
    }
    try:
        await rs.set_json(FREEZE_KEY, record)  # no TTL — a pin that expires is not a pin (§7)
    except Exception as e:
        logger.warning("freeze_golden_set persist failed: %s", e)
        wire_failure(module="eval_runner", detail=f"freeze persist failed: {e}",
                     source="eval_runner.freeze_golden_set")
        return {"ok": False, "reason": str(e)}
    wire_success(module="eval_runner",
                 summary=f"golden set FROZEN at {len(items)} entries (gate #6 pin) by {frozen_by}")
    return {"ok": True, **record}


async def unfreeze_golden_set(reason: str = "") -> dict:
    """Release the pin (deliberately re-opens gate #6)."""
    try:
        await rs.set_json(FREEZE_KEY, {"frozen": False, "unfrozen_reason": reason,
                                       "unfrozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    wire_success(module="eval_runner", summary=f"golden set UNFROZEN — gate #6 re-opens ({reason})")
    return {"ok": True, "frozen": False}


async def get_freeze_status() -> dict:
    """Honest gate-#6 measurement: is the 500-Q eval set frozen AND intact?

    Never raises — but never fabricates either: a store failure reports
    measurable=False, which is NOT the same as "measured, and it is not
    frozen" (R-F2375/R-F2622 keep that distinction).
    """
    try:
        # STRICT reads (R-F1392). Both the plain get_json() and get_golden_set()
        # swallow store failures and yield None/[] — so on a wedged store (the
        # R-F2277 class) this function would hash an EMPTY set, mismatch the
        # pin, and report a confident `not_frozen`/`drifted` with
        # "golden set has 0 entries" — a definite statement about data it never
        # read. That is the exact mirror-sin this module's docstring claims to
        # prevent, and the except-branch below would be dead code without these.
        record = await rs.get_json_strict(FREEZE_KEY) or {}
        items = await rs.get_json_strict(GOLDEN_SET_KEY) or []
    except Exception as e:
        return {"measurable": False, "error": f"store_read_failed:{type(e).__name__}",
                "gate_pass": None}
    live_count = len(items)
    if not record.get("frozen"):
        return {
            "measurable": True, "gate_pass": False, "frozen": False,
            "reason": "not_frozen",
            "live_count": live_count, "target": GATE_6_TARGET,
            "detail": (
                f"golden set has {live_count} entries but has never been pinned. "
                f"Size is not frozenness — POST /api/aria/eval/golden/freeze to pin it."
            ),
        }
    pinned_hash = record.get("hash")
    live_hash = golden_set_hash(items)
    drifted = pinned_hash != live_hash
    pinned_count = record.get("count")
    target = record.get("target", GATE_6_TARGET)
    return {
        "measurable": True,
        "gate_pass": bool(not drifted and live_count >= target),
        "frozen": True,
        "drifted": drifted,
        "reason": "drifted_from_pin" if drifted else ("below_target" if live_count < target else "frozen_and_intact"),
        "live_count": live_count,
        "pinned_count": pinned_count,
        "live_hash": live_hash[:16],
        "pinned_hash": (pinned_hash or "")[:16],
        "frozen_at": record.get("frozen_at"),
        "frozen_by": record.get("frozen_by"),
        "target": target,
    }


async def promote_feedback_to_golden(
    feedback_id: str,
    *,
    category: str = "feedback",
    notes: str = "",
    added_by: str = "",
) -> dict:
    """Take a feedback record and turn it into a golden entry. Only positive
    feedback should be promoted directly (the 👍 means 'this answer was
    correct'); for negative feedback the analyst should rewrite the
    expected_answer manually before promoting."""
    from . import feedback as feedback_store
    rec = await feedback_store.get_feedback(feedback_id)
    if not rec:
        return {"ok": False, "reason": "feedback not found"}
    if not rec.get("has_context"):
        return {"ok": False, "reason": "feedback has no Q&A snapshot — too old"}
    question = rec.get("question") or ""
    answer = rec.get("answer") or ""
    if not question or not answer:
        return {"ok": False, "reason": "feedback missing question or answer"}
    sentiment = rec.get("sentiment")
    if sentiment != "positive":
        # Allow it but warn — the caller might want to edit the expected
        # answer first via direct POST
        notes = f"[promoted from {sentiment} feedback — REVIEW expected_answer] {notes}".strip()
    return await add_golden_entry(
        question=question,
        expected_answer=answer,
        category=category,
        notes=notes,
        source="feedback",
        added_by=added_by,
        feedback_id=feedback_id,
    )


# ── Scoring ────────────────────────────────────────────────────────────────

def _cosine_score(actual: str, expected: str) -> float:
    """Return cosine similarity between two strings using the shared
    sentence-transformers model. Falls back to a token-overlap Jaccard
    score if the model isn't available so the eval still produces a
    useful number on a degraded service."""
    if not actual or not expected:
        return 0.0
    try:
        from . import semantic_search
        model = semantic_search._get_embedder()
    except Exception:
        model = None

    if model is not None:
        try:
            import numpy as np
            v1 = model.encode(actual[:2000], normalize_embeddings=True)
            v2 = model.encode(expected[:2000], normalize_embeddings=True)
            return float(np.dot(v1, v2))
        except Exception as e:
            logger.debug("embed-based scoring failed (%s) — falling back", e)

    # Jaccard fallback
    a = set(t.lower() for t in actual.split() if len(t) > 2)
    e = set(t.lower() for t in expected.split() if len(t) > 2)
    if not a or not e:
        return 0.0
    return len(a & e) / len(a | e)


def _bucket(score: float) -> str:
    if score >= PASS_THRESHOLD:
        return "pass"
    if score >= WARN_THRESHOLD:
        return "warn"
    return "fail"


# R-F1396: judge verdict → bucket. Kept beside _bucket so the two bucket
# semantics live in one place.
_JUDGE_BUCKET = {"correct": "pass", "partial": "warn", "wrong": "fail"}


# ── R-F1401/R-F1462: Held-out 80/20 split ──────────────────────────────────

# When set, run_eval will only evaluate entries in the held-out (eval) split.
# The split is deterministic (seeded SHA-256 of entry id) so the same entries
# always land in the same split across runs. Set to "eval" to score only the
# untouched 20%, or "train" to score only the training 80%.
# Default: None (evaluate ALL entries — backward compatible).
_EVAL_SPLIT = None  # "eval" | "train" | None


def set_eval_split(split: str | None) -> None:
    """Set the eval split mode. 'eval' = untouched 20%, 'train' = 80%, None = all."""
    global _EVAL_SPLIT
    if split not in ("eval", "train", None):
        raise ValueError(f"split must be 'eval', 'train', or None, got {split!r}")
    _EVAL_SPLIT = split


def get_eval_split() -> str | None:
    """Return the current eval split mode."""
    return _EVAL_SPLIT


def _split_held_out(items: list[dict], split: str, seed: int = 42) -> list[dict]:
    """Filter items to only those in the requested split.

    Uses the same deterministic seeded-SHA-256 approach as HeldOutSplit
    (aria_service/learning/held_out_split.py) but operates on the golden
    set's entry IDs rather than training example seed_ids. This keeps the
    eval runner independent of the learning package.

    Args:
        items: Full golden set.
        split: "eval" (20%) or "train" (80%).
        seed: Deterministic seed (default 42, matching HeldOutSplit).

    Returns:
        Filtered list of items in the requested split.
    """
    import hashlib as _hl

    def _in_split(entry: dict) -> bool:
        entry_id = entry.get("id") or entry.get("seed_id") or ""
        if not entry_id:
            return True  # No ID = include in both splits (backward compat)
        digest = _hl.sha256(f"{seed}:{entry_id}".encode("utf-8", errors="ignore")).digest()
        bucket = digest[0] / 256.0
        is_train = bucket < 0.80
        return is_train if split == "train" else not is_train

    return [it for it in items if _in_split(it)]


# ── Run ────────────────────────────────────────────────────────────────────

# R-F4239 (C-214 addendum) — a question that would make ARIA actually RETRIEVE.
# Imperative lookup/screen/investigate on a named entity. Deliberately loose: it
# is a screen for "could this entry EVER produce tool context", not a classifier.
_LOOKUP_SHAPE = re.compile(
    r"\b(run a|screen|investigate|look up|search for|check whether|"
    r"due diligence on|dd on|profile|who owns|find out)\b", re.I)


def honesty_seed_suitability(items: list[dict]) -> dict:
    """Can this question set populate the composite's HONESTY signal at all?

    R-F4235 made a zero-honesty recording run report itself and advised
    "choose tool-backed questions". **Measured against the frozen 500-Q set on
    2026-08-23, that advice was largely unavailable** — 11 of 500 entries have an
    entity-lookup shape, 75 are refusal-by-design, and the two largest categories
    (`sanctions_divergence`, `counter_intel`, 50 each) are policy-reasoning while
    `dd_layer_*` are self-knowledge. None of those retrieve.

    That matters because the honesty judge grades the grounding of claims against
    **RETRIEVED CONTEXT**. A set that retrieves nothing cannot produce judgments,
    however many entries you drive — and driving all 500 costs roughly $7 and
    ~4.5 hours (measured: $0.138 per 10, ~1 min/entry).

    This exists so that is a FUNCTION CALL rather than a hand measurement someone
    repeats every few weeks. It is deliberately pure and offline: no LLM, no
    store, no cost.

    `verdict` is tri-state-ish and conservative:
      * ``no_data``   — empty set; says nothing about suitability.
      * ``unsuitable``— fewer retrieving-shaped entries than the scorer's min samples,
                        so even a perfect run cannot clear the scorer's minimum.
      * ``possible``  — enough to be worth spending on. **NOT a promise, and the
                        gap is measured**: this counts question SHAPE, not actual
                        retrieval. A targeted run of 6 of the live set's 11
                        lookup-shaped entries on 2026-08-23 yielded
                        ``honesty_recorded 1, honesty_skipped_no_context 5`` — a
                        1-in-6 yield, because several "lookup" entries are
                        themselves refusal or fabricated-identity tests. At that
                        rate the live set's 11 candidates top out around 2
                        judgments, still short of ``min_samples``. Read
                        ``possible`` as "worth a targeted run", never as "this
                        will clear the threshold".

    Notably ``honesty_skipped_no_tags`` was **0** in that run: when context IS
    retrieved, ARIA tags the claim. Context is the bottleneck, not tagging.
    """
    # The threshold is the SCORER's, read at call time — never a copy. A second
    # constant here would drift from autonomy_scorer._MIN_SIGNAL_SAMPLES and this
    # function would confidently report "possible" for a set that cannot clear it.
    try:
        from .autonomy_scorer import _MIN_SIGNAL_SAMPLES as _min_samples
    except Exception:
        _min_samples = None       # could not read it — say so, never guess

    total = len(items or [])
    if not total:
        return {"total": 0, "lookup_shaped": 0, "refusal_by_design": 0,
                "min_samples": _min_samples, "suitable_ids": [],
                "verdict": "no_data"}
    lookup, refusal, ids = 0, 0, []
    for it in items:
        cat = str((it or {}).get("category") or "")
        if cat.startswith("refusal_"):
            refusal += 1
        if _LOOKUP_SHAPE.search(str((it or {}).get("question") or "")):
            lookup += 1
            if len(ids) < 25:
                ids.append((it or {}).get("id"))
    return {
        "total": total,
        "lookup_shaped": lookup,
        "refusal_by_design": refusal,
        "min_samples": _min_samples,
        "suitable_ids": ids,
        "verdict": ("unknown" if _min_samples is None
                    else "possible" if lookup >= _min_samples else "unsuitable"),
    }


async def run_eval(
    llm: Any, *, ids: list[str] | None = None, label: str = "", record: bool = False,
    limit: int = 0,
) -> dict:
    """Execute the golden set against the current ARIA chat path.

    Each entry's question is fed through aria_chat() (the same code the
    WhatsApp user hits, including tool-use NLU). The actual response is
    embedded and compared to the expected answer.

    R-F2390 — when ``record`` is True, each answered entry ALSO flows through
    the SAME grounding/honesty recorders the live chat path uses
    (source_verifier.record_verification + honesty_judge.record_judgment),
    writing to the exact stores autonomy_scorer.compute_composite reads. This
    is the deterministic, offline way to populate the composite's verification
    (45%) + honesty (25%) signals from the frozen golden set — without hammering
    the live chat writer. Default OFF so a normal scoring eval records nothing.
    Recording runs frame the tool context into numbered snippets
    (ground_markers=True) so grounding is genuinely attributable, and validate
    against the EXACT context the LLM saw.

    Returns a run record with per-entry results, summary, and delta vs
    the previous run for regression detection.
    """
    items = await get_golden_set()
    if ids:
        items = [it for it in items if it.get("id") in set(ids)]
    # R-F1401/R-F1462: held-out split — when _EVAL_SPLIT is set, only
    # evaluate entries in the requested split (eval=20% or train=80%).
    # This ensures weekly eval scores report on data the model did NOT
    # train on (the untouched 20%), preventing contaminated metrics.
    split_mode = _EVAL_SPLIT
    if split_mode and not ids:
        pre_split = len(items)
        items = _split_held_out(items, split_mode)
        logger.info(
            "[eval_runner] held-out split '%s': %d/%d entries",
            split_mode, len(items), pre_split,
        )
    if not items:
        return {"ok": False, "reason": "no golden entries to run"}

    # R-F2390 — representative cap: when limit>0 and no explicit ids, take an
    # evenly-strided sample across the (category-ordered) golden set so the
    # subset spans categories rather than the first N (which would be one
    # category). Keeps the recording run honest + bounded on cost.
    if limit and limit > 0 and not ids and len(items) > limit:
        stride = len(items) / float(limit)
        items = [items[int(i * stride)] for i in range(limit)]
        logger.info("[eval_runner] R-F2390 representative sample: %d entries", len(items))

    # R-F1068/R-F1073: run through llm_eval_framework for deeper analysis.
    # evaluate() takes a model name string and list[EvalQuestion], not an LLM object.
    # We pass "deepseek" as the model name (the primary provider) and let the
    # framework load its own questions from the golden seed if items is empty.
    try:
        from .llm_eval_framework import evaluate as _framework_evaluate
        from .llm_eval_framework import EvalQuestion
        # Convert items to EvalQuestion objects if we have them
        # R-F3115 — this dropped EVERY question. EvalQuestion requires `id`
        # (llm_eval_framework.py:53) and it was never passed, so construction raised
        # TypeError for each item and the bare `except: continue` swallowed all of
        # them. `_fw_questions` was therefore ALWAYS empty, evaluate() ran over zero
        # questions, and eval_runner still logged "Framework eval: score_a=0.000"
        # as though it had measured something. Golden entries already carry a stable
        # id (eval_runner.py:111 "gold_..."), so the value was there the whole time.
        # Drops are now counted and reported — a silent `continue` is what let this
        # survive, so the fix is not only the id.
        _fw_questions = []
        _fw_dropped = 0
        for _idx, item in enumerate(items or []):
            try:
                _fw_questions.append(EvalQuestion(
                    id=str(item.get("id") or f"golden_{_idx}"),
                    question=item.get("question", ""),
                    expected_answer=item.get("expected_answer", ""),
                    category=item.get("category", "general"),
                    requires_refusal=False,
                ))
            except Exception as _qe:  # noqa: BLE001
                _fw_dropped += 1
                if _fw_dropped == 1:
                    logger.warning(
                        "[eval_runner] R-F3115 golden entry could not be converted "
                        "to EvalQuestion (%s) — further drops counted only", _qe)
        if _fw_dropped:
            logger.warning(
                "[eval_runner] R-F3115 %d/%d golden entries dropped before the "
                "framework eval — the score below covers only the remainder",
                _fw_dropped, len(items or []))
        _fw_result = await _framework_evaluate(
            model_a="deepseek",
            questions=_fw_questions if _fw_questions else [],
        )
        if _fw_result:
            logger.info(
                "[eval_runner] Framework eval: run_id=%s score_a=%.3f",
                _fw_result.run_id,
                _fw_result.model_a.overall_score if _fw_result.model_a else 0,
            )
    except Exception as _fe:
        logger.debug("[eval_runner] Framework eval skipped: %s", _fe)

    # Lazy import to avoid a circular dep at module load
    from .. import routes  # noqa: F401  ensure routes package importable
    from ..routes.aria import _aria_chat_session  # see below — we'll add it

    started = time.time()
    results: list[dict] = []
    pass_n = warn_n = fail_n = 0
    judged_n = 0  # R-F1396: entries bucketed by the LLM judge (vs cosine fallback)
    total_score = 0.0
    # R-F197 (2026-05-11): degraded-mode detection. If the LLM is dead
    # and _aria_chat_session returns "" for every entry, the cosine
    # score is 0 → pass_rate=0 → R-F169 feeds 0% into calibration →
    # R-F166 fires downward 3pp/run → mastery drops to 10% floor in
    # ~30h. We detect ≥80% empty responses and mark the run as degraded.
    # Degraded runs are PERSISTED so the operator audit trail shows
    # them, but R-F199 keeps them out of the calibration signal mean.
    empty_resp_count = 0

    # R-F2390 — recorders (lazy import; only when recording is requested).
    _rec_verify_n = 0
    _rec_honesty_n = 0
    _repair_used_n = 0  # R-F2396 — grounding repairs that improved the score
    _grounded_rates: list[float] = []
    # R-F4235 (C-214) — why an honesty judgment was NOT produced.
    _skip_no_ctx = 0    # answered from memory / refused — nothing to ground against
    _skip_no_tags = 0   # tool-backed but the answer carried no confidence tag
    _sv = _hj = None
    _record_unavailable = ""
    if record:
        # R-F3701 — this import is now on the DAILY path (RUN-EVAL-DAILY passes
        # record=True), so a module-level failure in either recorder would take
        # down the whole eval rather than just the recording. Degrade instead:
        # the regression signal is independently valuable, and losing it as
        # collateral would be worse than losing the composite inputs.
        try:
            from . import source_verifier as _sv  # type: ignore[no-redef]
            from . import honesty_judge as _hj    # type: ignore[no-redef]
        except Exception as _rec_imp_err:
            _sv = _hj = None
            _record_unavailable = str(_rec_imp_err)[:200]
            logger.error(
                "[R-F3701] eval recorders unavailable (%s) — the run will still "
                "score, but Phase A gate #1's verification/honesty stores will "
                "NOT be populated this cycle",
                _record_unavailable,
            )

    for entry in items:
        q = entry.get("question", "")
        expected = entry.get("expected_answer", "")
        _entry_ctx = ""  # R-F2390 — framed tool_context the LLM saw (recording)
        try:
            if record:
                actual, _entry_ctx = await _aria_chat_session(
                    q, llm, ground_markers=True, return_context=True,
                )
            else:
                actual = await _aria_chat_session(q, llm)
        except Exception as e:
            logger.warning("eval entry %s threw: %s", entry.get("id"), e)
            results.append({
                "id": entry.get("id"),
                "question_preview": q[:120],
                "category": entry.get("category"),
                "score": 0.0,
                "bucket": "fail",
                "actual_preview": f"(error: {str(e)[:200]})",
                "expected_preview": expected[:160],
                "error": str(e)[:300],
            })
            fail_n += 1
            continue

        # R-F197: empty / very-short response = LLM didn't actually answer.
        if not (actual or "").strip() or len((actual or "").strip()) < 20:
            empty_resp_count += 1

        # Wrapped in to_thread: _cosine_score makes 2 sync model.encode()
        # calls per entry, which would freeze the event loop for the duration
        # of a 50-entry eval run otherwise.
        import asyncio as _aio
        score = await _aio.to_thread(_cosine_score, actual, expected)

        # R-F1396: LLM judge is the primary ruler; cosine is the fallback.
        # An unscored judge result NEVER buckets an entry (no silent zeros —
        # the R-F197 lesson applied to the judge itself).
        judge = None
        if eval_judge.judge_enabled() and llm is not None:
            judge = await eval_judge.judge_answer(llm, q, expected, actual)
        if judge and judge.get("ok"):
            bucket = _JUDGE_BUCKET[judge["verdict"]]
            judged_n += 1
        else:
            bucket = _bucket(score)
        if bucket == "pass":
            pass_n += 1
        elif bucket == "warn":
            warn_n += 1
        else:
            fail_n += 1
        total_score += score
        results.append({
            "id": entry.get("id"),
            "question_preview": q[:120],
            "category": entry.get("category"),
            "score": round(score, 3),
            "bucket": bucket,
            "judge": {
                "verdict": judge.get("verdict"),
                "grounded": judge.get("grounded", False),
                "reason": (judge.get("reason") or "")[:200],
            } if judge and judge.get("ok") else None,
            "actual_preview": (actual or "")[:280],
            "expected_preview": expected[:160],
        })

        # R-F2390/R-F2396 — record verification + honesty into the SAME stores
        # the composite reads, scoring grounding the way ARIA actually cites
        # (source name + confidence tag, verified by the judge's REAL support
        # check). Order: judge FIRST → feed the support verdict to verify_response
        # → (optional) one grounding repair if weak → record. Best-effort: a
        # recorder failure must never fail the eval run.
        if record and _sv is not None and _hj is not None and (actual or "").strip():
            _q_actual = actual
            _q_ctx = _entry_ctx or ""
            try:
                # 1. Honesty judgment (real LLM support check) — the source of
                #    truth for native grounding. Same gate as the live path.
                # R-F4235 (C-214) — COUNT WHY, not just how many.
                # The two halves of this gate fail for opposite reasons and need
                # opposite remedies: no tool context means the entry was answered
                # from memory / refused / clarified (nothing to ground against, so
                # skipping is CORRECT); no confidence tags means ARIA answered from
                # a source but did not tag the claim, which is a prompt/behaviour
                # problem. Recording only the total left "honesty_recorded: 0"
                # indistinguishable between them.
                _judgment = None
                if not _q_ctx:
                    _skip_no_ctx += 1
                elif not _hj.has_confidence_tags(_q_actual):
                    _skip_no_tags += 1
                else:
                    _judgment = await _hj.judge_response(llm, _q_actual, _q_ctx)

                # 2. Deterministic verify, fed the support judgment so native
                #    (source-name) grounding is credited honestly.
                _verification = _sv.verify_response(
                    _q_actual, _q_ctx, support_judgment=_judgment,
                )

                # 3. R-F2396 grounding repair — ONE retry from the trusted
                #    position when a tool-backed turn is weakly grounded.
                _gr = _verification.get("grounded_rate")
                _weak = _q_ctx and (_gr is None or _gr < 0.5)
                if _weak:
                    from ..routes.aria import _regenerate_with_stricter_grounding
                    _repaired = await _regenerate_with_stricter_grounding(llm, q, _q_ctx)
                    if (_repaired or "").strip():
                        _rj = None
                        if _hj.has_confidence_tags(_repaired):
                            _rj = await _hj.judge_response(llm, _repaired, _q_ctx)
                        _rv = _sv.verify_response(_repaired, _q_ctx, support_judgment=_rj)
                        _rgr = _rv.get("grounded_rate")
                        # Keep the repair only if it genuinely grounds better.
                        if _rgr is not None and (_gr is None or _rgr > _gr):
                            _q_actual, _judgment, _verification = _repaired, _rj, _rv
                            _repair_used_n += 1

                # 4. Record verification (once, the better of the two).
                await _sv.record_verification(
                    _verification,
                    request_id=entry.get("id", ""),
                    session_id="eval_gate1",
                    user="eval_runner",
                    question_preview=q,
                    response_preview=_q_actual,
                    tool_used="eval",
                )
                _rec_verify_n += 1
                _gr2 = _verification.get("grounded_rate")
                if _gr2 is not None:
                    _grounded_rates.append(_gr2)

                # 5. Record honesty judgment when one was produced.
                if _judgment is not None:
                    await _hj.record_judgment(
                        _judgment,
                        trace_id="",
                        session_id="eval_gate1",
                        user="eval_runner",
                        question_preview=q,
                        response_preview=_q_actual,
                    )
                    _rec_honesty_n += 1
            except Exception as _re:
                logger.debug("[eval_runner] R-F2396 record path failed: %s", _re)

    n = len(results)
    # R-F197: degraded flag. ≥80% empty responses → the LLM was effec-
    # tively offline for this run; score data is meaningless for
    # calibration purposes.
    degraded = bool(n > 0 and empty_resp_count >= int(n * 0.80))
    summary = {
        "total": n,
        "pass": pass_n,
        "warn": warn_n,
        "fail": fail_n,
        "pass_rate": round(pass_n / n, 3) if n else 0,
        "mean_score": round(total_score / n, 3) if n else 0,
        # R-F1396: how much of this run was judge-bucketed. A run with low
        # coverage (judge down → cosine fallback) is a weaker signal — the
        # Friday promotion ritual must check this before trusting pass_rate.
        "judge_coverage": round(judged_n / n, 3) if n else 0,
        "scorer": "llm_judge+cosine_fallback" if judged_n else "cosine",
        "duration_ms": int((time.time() - started) * 1000),
        "empty_response_count": empty_resp_count,
        "degraded": degraded,
        # R-F2390 — signal-recording stats (only meaningful when record=True).
        "recorded": bool(record),
        "verification_recorded": _rec_verify_n,
        "honesty_recorded": _rec_honesty_n,
        "grounding_repairs_used": _repair_used_n,
        "grounded_rate_samples": len(_grounded_rates),
        "mean_grounded_rate": (
            round(sum(_grounded_rates) / len(_grounded_rates), 3)
            if _grounded_rates else None
        ),
        # R-F4235 (C-214) — the two reasons a judgment was skipped, kept apart.
        "honesty_skipped_no_context": _skip_no_ctx,
        "honesty_skipped_no_tags": _skip_no_tags,
    }
    if degraded:
        logger.warning(
            "[eval_runner] R-F197 — run marked DEGRADED (%d/%d empty responses). "
            "Calibration loop will skip this run's pass_rate.",
            empty_resp_count, n,
        )

    # ── R-F4235 (C-214) — A POPULATE THAT POPULATED NOTHING MUST SAY SO ──────
    #
    # `record=True` exists for exactly one stated purpose: "the deterministic,
    # offline way to populate the composite's verification (45%) + honesty (25%)
    # signals". Measured 2026-08-23, a run labelled `rf-gate1-honesty-seed` had
    # been sitting in the store for weeks:
    #
    #     verification_recorded: 30      honesty_recorded: 0
    #
    # It achieved 0% of half its purpose, published that in a summary field, and
    # nothing consumed it — so Phase A gate #1's honesty axis stayed dark and the
    # cost of the run (30 LLM-driven chat turns) bought nothing. That is the C-96
    # defect exactly: publishing a number no verdict reads is why the degradation
    # went unnoticed.
    #
    # Deliberately fires only when `record=True` — a normal scoring eval records
    # nothing BY DESIGN and must not page. And it reports the two skip reasons,
    # because they need opposite responses: no-context is CORRECT behaviour on a
    # memory-served or refused answer (choose tool-backed entries for the seed),
    # while no-tags means ARIA used a source and did not tag the claim.
    if record and _rec_honesty_n == 0:
        _why = (f"{_skip_no_ctx} answered without tool context (memory/refusal — "
                f"nothing to ground against), {_skip_no_tags} tool-backed but "
                f"carrying no confidence tag")
        # R-F4248 (C-218) — WARNING, not ERROR. `error_streak.is_reset_type`
        # counts `log:error` and resets the durable Phase A gate-#3 anchor
        # (0 fly ERRORs / 7 days). MEASURED: this exact line reset the streak on
        # 2026-08-23 and was the ONLY log:error in the 7-day window
        # (`level_breakdown_7d: {log:warning: 199, log:error: 1}`) — a signal
        # about the golden set's composition set a Phase A EXIT GATE back to
        # zero. Worse, a zero-honesty populate is the EXPECTED outcome on the
        # current set (measured yield 1 in 6), so the gate could never accrue
        # seven clean days while anyone ran a recording eval.
        #
        # R-F2663 / R-F2668 are the same class and §1 records the same remedy.
        # The `wire_failure` below is the reporting channel §21a asks for; the
        # log level is not. Nothing is hidden by this change.
        logger.warning(
            "[R-F4235] recording eval '%s' populated ZERO honesty judgments "
            "(%d entries, %d verification recorded): %s. Phase A gate #1's "
            "honesty axis (25%%) stays UNMEASURED.",
            label or "unlabelled", n, _rec_verify_n, _why,
        )
        try:
            from .engine_wiring import wire_failure as _wf4235
            _wf4235(
                module="eval_runner",
                detail=(f"recording eval '{label or 'unlabelled'}' recorded 0 "
                        f"honesty judgments across {n} entries ({_why}). The "
                        f"offline gate-#1 populate is the designed way to fill "
                        f"the honesty signal; a zero run means gate #1 stays "
                        f"unmeasured and the run's LLM cost bought nothing.")[:600],
                gap_type="engine_failure",
                source="eval_runner:honesty_populate_empty",
            )
        except Exception:
            logger.debug("[R-F4235] zero-honesty wiring failed", exc_info=True)

    # Regression delta vs previous run
    prev_runs = await get_recent_runs(limit=1)
    delta = None
    if prev_runs:
        prev = prev_runs[0]
        prev_summary = prev.get("summary", {})
        delta = {
            "pass_rate_delta": round(summary["pass_rate"] - prev_summary.get("pass_rate", 0), 3),
            "mean_score_delta": round(summary["mean_score"] - prev_summary.get("mean_score", 0), 3),
            "fail_delta": summary["fail"] - prev_summary.get("fail", 0),
            "vs_run_id": prev.get("id"),
        }

    run_record = {
        "id": f"run_{int(started * 1000)}_{uuid.uuid4().hex[:6]}",
        "label": (label or "")[:120],
        "eval_split": split_mode,  # R-F1401: which split was evaluated (None=all)
        "started_at": started,
        "completed_at": time.time(),
        "summary": summary,
        "delta": delta,
        "results": results,
    }
    await _save_run(run_record)
    logger.info(
        "Eval run %s: %d/%d pass (%.0f%%), mean=%.2f, judge_coverage=%.2f",
        run_record["id"], pass_n, n, summary["pass_rate"] * 100,
        summary["mean_score"], summary["judge_coverage"],
    )
    # R-F1396 (§21a): the run outcome reaches the brain on both branches —
    # wire_success here; judge infrastructure failures fire wire_failure
    # inside eval_judge. The wire_success import predates this call (it was
    # imported but never invoked — a dark path this closes).
    try:
        wire_success(
            "eval_runner",
            f"eval run {run_record['id']}: {pass_n}/{n} pass "
            f"(judge_coverage={summary['judge_coverage']}, degraded={degraded})",
            detail=f"scorer={summary['scorer']} mean={summary['mean_score']}",
        )
    except Exception:
        pass  # wiring is fire-and-forget; never fail the run for it
    return run_record


async def _save_run(run: dict) -> None:
    try:
        runs = await rs.get_json(RUNS_KEY) or []
        runs.insert(0, {
            "id": run["id"],
            "label": run.get("label", ""),
            "started_at": run.get("started_at"),
            "summary": run.get("summary", {}),
            "delta": run.get("delta"),
            "results_count": len(run.get("results", [])),
        })
        runs = runs[:50]  # last 50 runs in the index
        await rs.set_json(RUNS_KEY, runs, ex=RUNS_TTL)
        # Full record under its own key for later inspection
        await rs.set_json(f"{RUNS_KEY}:{run['id']}", run, ex=RUNS_TTL)
    except Exception as e:
        logger.warning("save_run failed: %s", e)


async def get_recent_runs(limit: int = 10) -> list[dict]:
    try:
        return (await rs.get_json(RUNS_KEY) or [])[: max(1, min(limit, 50))]
    except Exception:
        return []


async def get_run(run_id: str) -> dict | None:
    try:
        return await rs.get_json(f"{RUNS_KEY}:{run_id}")
    except Exception:

        return None

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
