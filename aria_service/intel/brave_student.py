"""R-F2339 — Brave -> SearXNG/free-stack STUDENT loop.

Closes the learning loop opened by brave_distill.py (the TEACHER-signal capture). Brave is
the teacher; ARIA's own SearXNG + free stack are the student. The goal (mirror-Claude, §6):
make the free stack imitate Brave's source-selection methodology so the paid Brave
dependency becomes droppable.

We cannot see Brave's proprietary ranking algorithm — only its INPUT->OUTPUT behaviour
(which domains it surfaces, in what order), which is exactly what brave_distill captured.
So the student learns an INTERPRETABLE model of that behaviour:

  - train_from_corpus(): read the teacher shards, learn a per-DOMAIN "Brave-preference"
    score (rank-weighted: a domain Brave ranks #1 counts more than #8) + a per-TLD backoff
    prior for unseen domains. Writes brave_student/model.json (files only — §6/§7, no paid
    store, no eviction).
  - rerank(): re-order free-stack results by blending their current order with the learned
    Brave-preference. Safe no-op when the model has no learned domains. Applied ONLY on the free-stack
    path (when Brave did not serve) so the student imitates the teacher.
  - evaluate(): STUDENT-vs-TEACHER agreement on HELD-OUT records — does the student's
    re-ranking of the free-stack candidates match Brave's top-k better than the raw
    free-stack order? Reports baseline vs student vs lift. This is the metric that proves
    the loop works and tells us when Brave can be dropped.

§21-wired: train/eval success+failure reach the brain. §25: stats() for the proprioception
surface. Staged rollout (mirrors reranker.py R-F2205): the APPLY step is ENV-gated OFF
(ARIA_BRAVE_STUDENT_ENABLED) until eval agreement justifies turning it on — the trainer
runs regardless so the model + eval accumulate first.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("aria.brave_student")

_DATA_DIR = os.getenv("ARIA_DATA_DIR", "data")
# Teacher corpus (written by brave_distill). Student model lives beside it.
_CORPUS_DIR = Path(os.getenv("ARIA_BRAVE_DISTILL_DIR") or os.path.join(_DATA_DIR, "brave_distill"))
_MODEL_DIR = Path(os.getenv("ARIA_BRAVE_STUDENT_DIR") or os.path.join(_DATA_DIR, "brave_student"))
_MODEL_PATH = _MODEL_DIR / "model.json"

# Blend weight for apply: 0.0 = keep original order, 1.0 = pure Brave-preference order.
_BLEND = float(os.getenv("ARIA_BRAVE_STUDENT_WEIGHT", "0.5") or "0.5")

_model_cache: dict | None = None
_model_mtime: float = 0.0


def apply_enabled() -> bool:
    """Whether the learned re-rank is applied live (staged rollout — default OFF)."""
    return (os.getenv("ARIA_BRAVE_STUDENT_ENABLED", "0") or "0").strip().lower() in ("1", "true", "yes", "on")


# ── helpers ──────────────────────────────────────────────────────────────────

def _domain(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _tld(domain: str) -> str:
    return domain.rsplit(".", 1)[-1] if "." in domain else ""


def _rank_weight(rank: int) -> float:
    """DCG-style position weight — the top of Brave's list carries the most signal."""
    return 1.0 / math.log2(rank + 2)


def _iter_corpus_records(shards: list | None = None):
    if not _CORPUS_DIR.exists():
        return
    files = shards if shards is not None else sorted(_CORPUS_DIR.glob("*.jsonl"))
    for s in files:
        try:
            with open(s, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
        except Exception:
            continue


# ── train ────────────────────────────────────────────────────────────────────

def train_from_corpus(records: list | None = None, *, persist: bool = True) -> dict:
    """Learn per-domain Brave-preference + per-TLD backoff prior from the teacher corpus.

    `records` overrides the on-disk corpus (used by evaluate() to train on a split without
    clobbering the production model). `persist=False` returns the model WITHOUT writing it.
    Never raises."""
    try:
        domain_raw: dict[str, float] = {}
        tld_raw: dict[str, float] = {}
        seen = 0
        recs = records if records is not None else _iter_corpus_records()
        for rec in recs:
            brave = ((rec.get("backends") or {}).get("brave")) or []
            if not brave:
                continue
            seen += 1
            for rank, url in enumerate(brave):
                d = _domain(url)
                if not d:
                    continue
                w = _rank_weight(rank)
                domain_raw[d] = domain_raw.get(d, 0.0) + w
                t = _tld(d)
                if t:
                    tld_raw[t] = tld_raw.get(t, 0.0) + w

        def _norm(raw: dict) -> dict:
            m = max(raw.values()) if raw else 0.0
            return {k: round(v / m, 4) for k, v in raw.items()} if m > 0 else {}

        model = {
            "version": 1,
            "trained_at": time.time(),
            "records_seen": seen,
            "domain_pref": _norm(domain_raw),
            "tld_prior": _norm(tld_raw),
            "domain_count": len(domain_raw),
        }
        if persist:
            _MODEL_DIR.mkdir(parents=True, exist_ok=True)
            tmp = _MODEL_PATH.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(model, f, ensure_ascii=False)
            os.replace(tmp, _MODEL_PATH)  # atomic
            global _model_cache, _model_mtime
            _model_cache = model
            try:
                _model_mtime = _MODEL_PATH.stat().st_mtime
            except Exception:
                _model_mtime = time.time()
            try:
                from .engine_wiring import wire_success
                wire_success(
                    module="brave_student",
                    summary=f"student trained: {seen} teacher records, {len(model['domain_pref'])} domains",
                    source_id="brave_student:train",
                )
            except Exception:
                pass
        return model
    except Exception as e:
        logger.warning("brave_student train failed: %s", e)
        try:
            from .engine_wiring import wire_failure
            wire_failure(module="brave_student", detail=f"train failed: {e}",
                         gap_type="engine_failure", source="brave_student:train")
        except Exception:
            pass
        return {"version": 1, "records_seen": 0, "domain_pref": {}, "tld_prior": {}, "error": str(e)}


def _load_model() -> dict:
    global _model_cache, _model_mtime
    try:
        if not _MODEL_PATH.exists():
            return _model_cache or {}
        mt = _MODEL_PATH.stat().st_mtime
        if _model_cache is None or mt > _model_mtime:
            with open(_MODEL_PATH, "r", encoding="utf-8") as f:
                _model_cache = json.load(f)
            _model_mtime = mt
        return _model_cache or {}
    except Exception:
        return _model_cache or {}


def _pref_for_url(url: str, model: dict) -> float:
    d = _domain(url)
    if not d:
        return 0.0
    prefs = model.get("domain_pref") or {}
    if d in prefs:
        return prefs[d]
    # Backoff to the (dampened) TLD prior for domains not seen in the teacher corpus.
    tp = model.get("tld_prior") or {}
    return 0.5 * tp.get(_tld(d), 0.0)


# ── apply ────────────────────────────────────────────────────────────────────

def rerank(results: list, *, weight: float | None = None, model: dict | None = None) -> list:
    """Re-order free-stack results by blending their current order with the learned
    Brave-preference. Returns a NEW ordered list (does not mutate items). Safe no-op when
    the model has no learned domains or there is nothing to reorder. Accepts `.url` objects or dicts
    with 'url'."""
    try:
        m = model if model is not None else _load_model()
        if not (m.get("domain_pref") or {}) or not results or len(results) < 2:
            return results
        w = _BLEND if weight is None else weight
        n = len(results)
        scored = []
        for i, r in enumerate(results):
            url = getattr(r, "url", None)
            if url is None and isinstance(r, dict):
                url = r.get("url")
            base = (n - i) / n                     # preserve the original ranking signal
            pref = _pref_for_url(url or "", m)
            score = (1.0 - w) * base + w * pref
            scored.append((score, i, r))
        scored.sort(key=lambda t: (-t[0], t[1]))   # stable on ties via original index
        return [r for _, _, r in scored]
    except Exception as e:
        logger.debug("brave_student rerank no-op (%s)", e)
        return results


# ── evaluate (student vs teacher) ────────────────────────────────────────────

def _free_stack_order(rec: dict) -> list[str]:
    """Merge the NON-Brave backends into one deduped ranked list — imitates what the free
    stack alone would surface (rank-fair round-robin interleave, first-seen wins)."""
    backends = rec.get("backends") or {}
    lists = [urls for name, urls in backends.items() if name != "brave" and urls]
    merged: list[str] = []
    seen: set[str] = set()
    depth = max((len(l) for l in lists), default=0)
    for rank in range(depth):
        for l in lists:
            if rank < len(l):
                u = l[rank]
                if u not in seen:
                    seen.add(u)
                    merged.append(u)
    return merged


def _topk_overlap(order_a: list[str], order_b: list[str], k: int) -> float:
    b = list(dict.fromkeys(order_b))[:k]
    if not b:
        return 0.0
    a = set(order_a[:k])
    return len([x for x in b if x in a]) / len(b)


def evaluate(k: int = 5, holdout: float = 0.3) -> dict:
    """STUDENT-vs-TEACHER agreement on HELD-OUT records. Trains on the first (1-holdout)
    of the corpus, then measures on the rest: does the student's re-ranking of the free
    stack match Brave's top-k domains better than the raw free-stack order (baseline)?
    Reports baseline / student / lift. Does NOT touch the production model."""
    try:
        recs = [r for r in _iter_corpus_records() if ((r.get("backends") or {}).get("brave"))]
        if len(recs) < 4:
            return {"ok": False, "reason": "insufficient corpus (need >=4 Brave records)", "records": len(recs)}
        split = max(1, int(len(recs) * (1.0 - holdout)))
        train_recs, eval_recs = recs[:split], recs[split:] or recs[split - 1:]
        model = train_from_corpus(train_recs, persist=False)   # train split only, in-memory
        base_sum, stud_sum, n = 0.0, 0.0, 0
        for rec in eval_recs:
            brave_domains = [_domain(u) for u in ((rec.get("backends") or {}).get("brave") or []) if _domain(u)]
            free = _free_stack_order(rec)
            if not free or not brave_domains:
                continue
            base_domains = [_domain(u) for u in free]
            reranked = rerank([{"url": u} for u in free], weight=1.0, model=model)  # pure student signal
            stud_domains = [_domain(u.get("url")) for u in reranked]
            base_sum += _topk_overlap(base_domains, brave_domains, k)
            stud_sum += _topk_overlap(stud_domains, brave_domains, k)
            n += 1
        if n == 0:
            return {"ok": False, "reason": "no evaluable held-out records"}
        baseline = round(base_sum / n, 4)
        student = round(stud_sum / n, 4)
        out = {
            "ok": True, "k": k, "eval_records": n, "train_records": len(train_recs),
            "baseline_topk_overlap": baseline, "student_topk_overlap": student,
            "lift": round(student - baseline, 4),
        }
        try:
            from .engine_wiring import wire_success
            wire_success(module="brave_student",
                         summary=f"student eval: baseline {baseline} -> student {student} (lift {out['lift']})",
                         source_id="brave_student:eval")
        except Exception:
            pass
        return out
    except Exception as e:
        logger.warning("brave_student evaluate failed: %s", e)
        try:
            from .engine_wiring import wire_failure
            wire_failure(module="brave_student", detail=f"evaluate failed: {e}",
                         gap_type="engine_failure", source="brave_student:eval")
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


def stats() -> dict:
    """Proprioception surface (§25): model coverage + top learned domains + corpus size."""
    model = _load_model()
    prefs = model.get("domain_pref") or {}
    try:
        from . import brave_distill as _bd
        corpus = _bd.stats()
    except Exception:
        corpus = {}
    return {
        "apply_enabled": apply_enabled(),
        "blend_weight": _BLEND,
        "model_trained_at": model.get("trained_at"),
        "records_seen": model.get("records_seen", 0),
        "domains_learned": len(prefs),
        "top_domains": sorted(prefs.items(), key=lambda kv: -kv[1])[:10],
        "corpus": corpus,
    }


# R-F2339 §21a — module-level failure-handler registration.
try:
    from .engine_wiring import wire_failure as _wf_shutdown
    _wf_shutdown(module="brave_student", detail="module shutdown",
                 gap_type="engine_failure", source="brave_student:shutdown")
except Exception:
    pass
