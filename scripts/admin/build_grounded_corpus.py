"""R-F1939 — top up the GROUNDED training corpus to full openbook coverage.

A prior build produced 608 grounded examples in aria_grounded_v1.jsonl
(message-format: {messages:[user,assistant], topic, grounded, label, source};
labels grounded/grounded_abstain/abstain — it teaches honest abstention, not
fabrication). That covers 608/689 openbook questions. This script generates
grounded answers for the REMAINING openbook items in the SAME schema so the
corpus is consistent and complete — via DeepSeek, concurrent + resumable.

Local + cheap. Usage:
  python scripts/admin/build_grounded_corpus.py --limit 3     # vet a few first
  python scripts/admin/build_grounded_corpus.py               # all missing (resumable)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IN = REPO / "data" / "training" / "aria_train_689_openbook.jsonl"
OUT = REPO / "data" / "training" / "aria_grounded_v1.jsonl"
_DS_URL = "https://api.deepseek.com/v1/chat/completions"
_CONCURRENCY = 6

# Matches the 608-corpus framing: the instruction lives in the user message.
_CTX_PREFIX = ("[CONTEXT — answer ONLY from this evidence; cite inline as "
               "[from <source>]; if it does not contain the answer, say so]\n")
_SYSTEM = (
    "You are ARIA, an elite due-diligence and intelligence analyst. Answer using "
    "ONLY the provided evidence, citing each fact inline with its [Source: ...] "
    "label. Be precise and analytical. If the evidence does not support an answer, "
    "say exactly what is missing — NEVER fabricate facts or sources."
)
_ABSTAIN = ("cannot confirm", "does not contain", "not supported", "cannot determine",
            "no information", "context does not", "not enough information", "cannot answer",
            "insufficient", "does not provide", "unable to", "cannot be determined")


def _q_of_corpus_msg(u: str) -> str:
    return (u.split("[QUESTION]", 1)[1].strip().lower() if "[QUESTION]" in u
            else u.strip().lower()[-200:])


def _covered() -> set:
    if not OUT.exists():
        return set()
    out = set()
    for l in OUT.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            r = json.loads(l)
            if "messages" in r:
                out.add(_q_of_corpus_msg(r["messages"][0]["content"]))
        except Exception:
            pass
    return out


def _label(ans: str):
    a = ans.lower()
    has_abstain = any(m in a for m in _ABSTAIN)
    has_cite = "[source:" in a or "[from " in a
    if has_abstain and has_cite:
        return "grounded_abstain", True
    if has_abstain:
        return "abstain", False
    return "grounded", True


async def _gen(client, key, item: dict) -> dict | None:
    user = _CTX_PREFIX + item["context"] + "\n[QUESTION]\n" + item["question"]
    body = {"model": "deepseek-chat",
            "messages": [{"role": "system", "content": _SYSTEM},
                         {"role": "user", "content": user}],
            "temperature": 0.3, "max_tokens": 1024}
    for attempt in range(3):
        try:
            r = await client.post(_DS_URL, headers={"Authorization": f"Bearer {key}"},
                                  json=body, timeout=90.0)
            if r.status_code != 200:
                await asyncio.sleep(2 * (attempt + 1)); continue
            ans = r.json()["choices"][0]["message"]["content"].strip()
            if not ans:
                return None
            label, grounded = _label(ans)
            return {"messages": [{"role": "user", "content": user},
                                 {"role": "assistant", "content": ans}],
                    "topic": item.get("topic"), "grounded": grounded,
                    "label": label, "source": "grounded_deepseek_v1"}
        except Exception:
            await asyncio.sleep(2 * (attempt + 1))
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        print("BLOCKED: DEEPSEEK_API_KEY unset."); return 2
    items = [json.loads(l) for l in IN.read_text(encoding="utf-8").splitlines() if l.strip()]
    covered = _covered()
    todo = [it for it in items
            if it.get("question") and it.get("context")
            and it["question"].strip().lower() not in covered]
    if a.limit:
        todo = todo[:a.limit]
    print(f"[grounded] {len(items)} openbook, {len(covered)} already grounded, {len(todo)} to generate")
    if not todo:
        print("[grounded] corpus complete — nothing to do."); return 0

    import httpx
    sem = asyncio.Semaphore(_CONCURRENCY)
    lock = asyncio.Lock()
    stats = {"ok": 0, "fail": 0, "grounded": 0, "grounded_abstain": 0, "abstain": 0}
    f = OUT.open("a", encoding="utf-8")
    async with httpx.AsyncClient() as client:
        async def worker(it):
            async with sem:
                try:
                    rec = await _gen(client, key, it)
                except Exception:
                    rec = None
                async with lock:
                    if rec:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
                        stats["ok"] += 1; stats[rec["label"]] += 1
                    else:
                        stats["fail"] += 1
                    n = stats["ok"] + stats["fail"]
                    if n % 20 == 0 or n == len(todo):
                        print(f"[grounded] {n}/{len(todo)} {stats}", flush=True)
        await asyncio.gather(*(worker(it) for it in todo))
    f.close()
    print(f"[grounded] DONE {stats} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
