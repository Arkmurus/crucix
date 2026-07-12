#!/usr/bin/env python
"""R-F2558 — build the composite-win GRPO dataset (v2).

Cycle #1 won fabrication but recall stayed flat because the training data had:
  (a) NO expected_keywords  -> the recall term in grounding_reward was always 0
  (b) answerable as the STRING "True"/"False" -> score()'s `is True/is False`
      identity checks never matched, killing the anti-abstention-gaming + over-claim
      logic and miscounting every unanswerable row as answerable.

This rebuilds data/training/grpo_grounded_prompts.jsonl ->  _v2.jsonl with:
  * answerable coerced to a real bool
  * expected_keywords for each ANSWERABLE row: 5-8 salient grounded facts a correct
    answer must state, extracted by DeepSeek STRICTLY from the context, then gated to
    terms literally present in the context (case-insensitive) so recall is achievable
    and can never reward an ungrounded token. Unanswerable rows get NO keywords (they
    should abstain; recall is N/A).

Resumable: append-only sidecar keyed by row index; re-run continues. Idempotent merge
at the end. DeepSeek only (§6/§18 — the one active provider).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx

SRC = Path("data/training/grpo_grounded_prompts.jsonl")
OUT = Path("data/training/grpo_grounded_prompts_v2.jsonl")
SIDE = Path("data/training/_grpo_kw_v2.sidecar.jsonl")  # {idx, keywords}
DSK = os.environ.get("DEEPSEEK_API_KEY", "").strip()
CONC = int(os.environ.get("KW_CONC", "8"))

_STOP = {  # structural / boilerplate tokens that are not substantive answer facts
    "CONTEXT", "RAG", "RETRIEVED", "SOURCE", "SOURCES", "EVIDENCE", "ANSWER",
    "INLINE", "PROPRIETARY", "INTELLIGENCE", "INDEXED", "KNOWLEDGE", "FROM",
    "NOTE", "DOCUMENT", "ATTACHED", "SUMMARY", "REPORT",
}


def _to_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def _present(term: str, ctx_low: str) -> bool:
    """Keyword is achievable+grounded only if it literally occurs in the context."""
    t = (term or "").strip()
    if len(t) < 2:
        return False
    if t.upper() in _STOP:
        return False
    return t.lower() in ctx_low


_EXTRACT_SYS = (
    "You extract the key factual keywords a correct answer must contain. "
    "Return ONLY a JSON array of 5-8 short strings. Each MUST be a substantive fact "
    "COPIED VERBATIM from the provided context: proper nouns, organisation/person "
    "names, acronyms, jurisdictions, specific numbers/dates, or key domain terms. "
    "No sentences, no explanations, no markdown — just the JSON array. Never invent "
    "a term that is not present verbatim in the context."
)


async def _extract(client: httpx.AsyncClient, ctx: str) -> list[str]:
    # Trim the structural header; keep the evidence body for extraction.
    body = ctx[:5000]
    r = await client.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {DSK}"},
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": _EXTRACT_SYS},
                {"role": "user", "content": f"CONTEXT:\n{body}\n\nJSON array of 5-8 keywords:"},
            ],
            "temperature": 0.0,
            "max_tokens": 200,
        },
        timeout=60.0,
    )
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    ctx_low = ctx.lower()
    seen, out = set(), []
    for k in arr:
        if not isinstance(k, str):
            continue
        k = k.strip().strip('"').strip("'")
        kl = k.lower()
        if kl in seen or not _present(k, ctx_low):
            continue
        seen.add(kl)
        out.append(k)
    return out[:8]


async def main():
    if not DSK:
        print("FATAL: DEEPSEEK_API_KEY unset"); sys.exit(1)
    rows = [json.loads(l) for l in SRC.open(encoding="utf-8") if l.strip()]
    done = {}
    if SIDE.exists():
        for l in SIDE.open(encoding="utf-8"):
            if l.strip():
                d = json.loads(l); done[d["idx"]] = d["keywords"]
    print(f"[kw] {len(rows)} rows | {sum(1 for r in rows if _to_bool(r.get('answerable')))} answerable | resume {len(done)} done")

    todo = [i for i, r in enumerate(rows)
            if _to_bool(r.get("answerable")) and i not in done]
    print(f"[kw] extracting keywords for {len(todo)} answerable rows (conc={CONC})…")
    sem = asyncio.Semaphore(CONC)
    lock = asyncio.Lock()
    async with httpx.AsyncClient() as client:
        async def work(i):
            async with sem:
                for attempt in range(3):
                    try:
                        kw = await _extract(client, rows[i].get("context", ""))
                        break
                    except Exception as e:
                        if attempt == 2:
                            print(f"[kw] idx {i} failed: {e}"); kw = []
                        await asyncio.sleep(2 * (attempt + 1))
            async with lock:
                with SIDE.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"idx": i, "keywords": kw}) + "\n")
                done[i] = kw
                if len(done) % 25 == 0:
                    print(f"[kw] {len(done)} done")
        await asyncio.gather(*(work(i) for i in todo))

    # merge -> v2
    kept = 0
    with OUT.open("w", encoding="utf-8") as f:
        for i, r in enumerate(rows):
            r2 = dict(r)
            r2["answerable"] = _to_bool(r.get("answerable"))
            if r2["answerable"]:
                kw = done.get(i, [])
                if len(kw) >= 3:
                    r2["expected_keywords"] = kw; kept += 1
            f.write(json.dumps(r2, ensure_ascii=False) + "\n")
    n_ans = sum(1 for r in rows if _to_bool(r.get("answerable")))
    print(f"[kw] wrote {OUT} | answerable={n_ans} | with>=3 keywords={kept} "
          f"({100*kept//max(1,n_ans)}% of answerable)")


if __name__ == "__main__":
    asyncio.run(main())
