"""build_grounded_corpus — Stage 1 of the grounded "bulletproof ARIA" workstream (R-F1535).

Distils the SKILL of grounded synthesis (not memorised facts) from DeepSeek: for each
(question, retrieved-context) pair, DeepSeek answers using ONLY the context, cites inline,
and honestly abstains when the context lacks the answer. The result is grounded SFT rows
the sovereign model can learn — teaching it to reason over ARIA's real evidence and cite it,
which is the only path past the closed-book teacher ceiling (~0.32).

Input: a JSONL where each row has `question` and (optionally) `context` — e.g. the open-book
eval set produced by build_openbook_eval.py. Rows with empty context become ABSTENTION
training rows (the honest "I cannot confirm without a source" behaviour).

Quality gates (CLAUDE.md §24 — training must be REAL, bulletproof honesty):
  - answer non-empty / substantive
  - if context present: the answer must CITE ([from ...]) OR be an explicit abstention —
    an answer that does NEITHER is rejected (it likely hallucinated from memory)
  - if context empty: the answer must ABSTAIN (no outside-knowledge guessing) — else rejected
  - dedupe by question; resumable checkpoint so a crash/re-run continues

Output rows match the R-F1533 eval + serving format EXACTLY (train/serve consistency):
  {"messages":[{"role":"user","content":"[CONTEXT…]\n…\n[QUESTION]\n…"},
               {"role":"assistant","content":"<grounded cited answer>"}],
   "topic":…, "grounded": bool, "source":"grounded_deepseek_v1"}

Usage:
  python scripts/train/build_grounded_corpus.py \
    --in data/eval_reports/aria_eval_500q_openbook.jsonl \
    --out data/training/aria_grounded_v1.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

DEEPSEEK_URL = "https://api.deepseek.com/v1"
MIN_ANSWER_LEN = 20
# substrings that mark an honest abstention (no fabrication when context is thin)
_ABSTAIN_MARKERS = (
    "cannot confirm", "can't confirm", "cannot provide", "insufficient",
    "no source", "not contain", "does not contain", "without a source",
    "without a recent", "unable to verify", "cannot verify", "no evidence",
    "not available in the provided", "not in the provided context",
)

_SYS = (
    "You produce TRAINING TARGETS for a grounded due-diligence assistant. Answer the "
    "QUESTION using ONLY the facts in CONTEXT. Cite each fact inline as [from <source>] "
    "using the source labels that appear in the context. If the context does NOT contain "
    "the answer, reply with a brief HONEST ABSTENTION (state you cannot confirm without a "
    "current source) and DO NOT use any outside knowledge. Never invent facts, numbers, "
    "names, dates, or sources that are not in the context. Be concise and decision-grade."
)


def _user_block(question: str, context: str) -> str:
    if context.strip():
        return (
            "[CONTEXT — answer ONLY from this evidence; cite inline as [from <source>]; "
            "if it does not contain the answer, say so]\n"
            f"{context}\n\n[QUESTION]\n{question}"
        )
    return (
        "[NO RETRIEVED CONTEXT — you have no source for this. Give an honest abstention; "
        "do NOT answer from memory.]\n\n[QUESTION]\n" + question
    )


async def _deepseek(api_key: str, user: str, max_tokens: int = 700) -> tuple[bool, str]:
    import httpx
    body = {
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": _SYS}, {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": 0.2,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await asyncio.wait_for(
                client.post(f"{DEEPSEEK_URL}/chat/completions", headers=headers, json=body),
                timeout=65.0)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        data = resp.json()
        return True, (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    except Exception as e:
        return False, str(e)[:160]


def _passes_gates(answer: str, has_context: bool) -> tuple[bool, str]:
    a = (answer or "").strip()
    if len(a) < MIN_ANSWER_LEN:
        return False, "empty_or_short"
    low = a.lower()
    abstained = any(m in low for m in _ABSTAIN_MARKERS)
    cited = "[from" in low
    if has_context:
        if not (cited or abstained):
            return False, "no_citation_no_abstention"   # likely hallucinated from memory
        return True, "grounded" if cited else "grounded_abstain"
    # no context => MUST abstain (no outside-knowledge guessing)
    if not abstained:
        return False, "answered_without_context"
    return True, "abstain"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", dest="out", type=Path, required=True)
    ap.add_argument("--concurrency", type=int, default=4)   # DeepSeek API is concurrency-safe (unlike the shim)
    ap.add_argument("--limit", type=int, default=0, help="cap rows (0 = all) — handy for a smoke test")
    args = ap.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        for ln in Path(".env").read_text(encoding="utf-8").splitlines():
            if ln.startswith("DEEPSEEK_API_KEY="):
                api_key = ln.split("=", 1)[1].strip().strip('"').strip("'"); break
    if not api_key:
        print("FATAL: DEEPSEEK_API_KEY not set / not in .env", file=sys.stderr); return 1

    rows = [json.loads(ln) for ln in args.inp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if args.limit:
        rows = rows[: args.limit]

    ckpt = Path(str(args.out) + ".partial.jsonl")
    done: dict[int, dict] = {}
    if ckpt.exists():
        for ln in ckpt.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    r = json.loads(ln); done[int(r["idx"])] = r["row"]
                except Exception:
                    pass
        if done:
            print(f"resuming — {len(done)}/{len(rows)} already in checkpoint")

    sem = asyncio.Semaphore(max(1, args.concurrency))
    lock = asyncio.Lock()
    stats = {"kept": 0, "rejected": 0, "errors": 0, "seen_q": set()}

    async def one(idx: int, rec: dict) -> None:
        q = (rec.get("question") or "").strip()
        ctx = (rec.get("context") or "").strip()
        if not q or q in stats["seen_q"]:
            return
        stats["seen_q"].add(q)
        async with sem:
            ok, ans = await _deepseek(api_key, _user_block(q, ctx))
        if not ok:
            stats["errors"] += 1; return
        passed, label = _passes_gates(ans, bool(ctx))
        if not passed:
            stats["rejected"] += 1; row = None
        else:
            stats["kept"] += 1
            row = {
                "messages": [
                    {"role": "user", "content": _user_block(q, ctx)},
                    {"role": "assistant", "content": ans.strip()},
                ],
                "topic": rec.get("topic", "general"),
                "grounded": bool(ctx),
                "label": label,
                "source": "grounded_deepseek_v1",
            }
        done[idx] = row
        async with lock:
            with ckpt.open("a", encoding="utf-8") as cf:
                cf.write(json.dumps({"idx": idx, "row": row}, ensure_ascii=False) + "\n")

    todo = [(i, r) for i, r in enumerate(rows) if i not in done]
    # seed seen_q from checkpoint to keep dedupe correct on resume
    for r in done.values():
        if r:
            uc = r["messages"][0]["content"]
            stats["seen_q"].add(uc.split("[QUESTION]\n")[-1].strip())
    if todo:
        await asyncio.gather(*[one(i, r) for i, r in todo])

    kept = [done[i] for i in sorted(done) if done.get(i)]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in kept) + "\n", encoding="utf-8")
    print(f"wrote {len(kept)} grounded rows -> {args.out}")
    print(f"kept={stats['kept']} rejected={stats['rejected']} errors={stats['errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
