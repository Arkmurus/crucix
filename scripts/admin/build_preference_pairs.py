"""R-F1943 — build VERIFIED DPO preference pairs for the grounding cap-breaker.

For each grounded corpus example:
  chosen   = the grounded answer (grounded in OUR retrieved context, cites it)
  rejected = DeepSeek's answer to the BARE question (no context) — parametric /
             ungrounded relative to our sources (what we want the model to STOP
             doing: relying on parametric knowledge instead of our proprietary
             intelligence)

The preference is then VERIFIED objectively by the R-F1942 grounding reward — we
keep a pair ONLY when reward(chosen) - reward(rejected) >= margin. So DPO trains
on an ungameable, confirmed signal (the analog of the coder's tests-pass gold),
not on an LLM's say-so. Output: TRL-conversational DPO format
{prompt:[user msg], chosen:[assistant], rejected:[assistant]} + the rewards.

Local + cheap (DeepSeek direct), concurrent + resumable + fault-tolerant.
Usage:
  python scripts/admin/build_preference_pairs.py --limit 5   # vet a few
  python scripts/admin/build_preference_pairs.py             # full (resumable)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from aria_service.intel import grounding_reward as gr  # noqa: E402

CORPUS = REPO / "data" / "training" / "aria_grounded_v1.jsonl"
OUT = REPO / "data" / "training" / "aria_dpo_pairs_v1.jsonl"
_DS_URL = "https://api.deepseek.com/v1/chat/completions"
_CONCURRENCY = 6
_MARGIN = 0.15  # chosen must out-score rejected by at least this on grounding

# Deliberately ungrounded: answer confidently from parametric knowledge, no
# context — this produces the "rejected" the model must learn to avoid.
_REJ_SYSTEM = (
    "You are a confident analyst. Answer the question directly and specifically "
    "with concrete details and source citations. Do NOT say you lack information "
    "or need more context — always give a definitive answer."
)


def _question_of(user_content: str) -> str:
    return (user_content.split("[QUESTION]", 1)[1].strip()
            if "[QUESTION]" in user_content else user_content.strip())


def _done() -> set:
    if not OUT.exists():
        return set()
    out = set()
    for l in OUT.read_text(encoding="utf-8").splitlines():
        if l.strip():
            try:
                out.add(json.loads(l)["prompt"][0]["content"])
            except Exception:
                pass
    return out


async def _make_pair(client, key, row: dict) -> dict | None:
    import httpx
    user = row["messages"][0]["content"]
    chosen = row["messages"][1]["content"]
    question = _question_of(user)
    body = {"model": "deepseek-chat",
            "messages": [{"role": "system", "content": _REJ_SYSTEM},
                         {"role": "user", "content": question}],
            "temperature": 0.7, "max_tokens": 1024}
    rejected = None
    for attempt in range(3):
        try:
            r = await client.post(_DS_URL, headers={"Authorization": f"Bearer {key}"},
                                  json=body, timeout=90.0)
            if r.status_code == 200:
                rejected = r.json()["choices"][0]["message"]["content"].strip()
                break
            await asyncio.sleep(2 * (attempt + 1))
        except Exception:
            await asyncio.sleep(2 * (attempt + 1))
    if not rejected:
        return None
    rc = gr.reward(chosen, user)
    rr = gr.reward(rejected, user)
    if rc - rr < _MARGIN:   # preference NOT objectively confirmed -> drop
        return {"_dropped": True, "chosen_reward": rc, "rejected_reward": rr}
    return {
        "prompt": [{"role": "user", "content": user}],
        "chosen": [{"role": "assistant", "content": chosen}],
        "rejected": [{"role": "assistant", "content": rejected}],
        "chosen_reward": round(rc, 4),
        "rejected_reward": round(rr, 4),
        "label": row.get("label"),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        print("BLOCKED: DEEPSEEK_API_KEY unset."); return 2
    rows = [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if isinstance(r.get("messages"), list) and len(r["messages"]) >= 2]
    done = _done()
    todo = [r for r in rows if r["messages"][0]["content"] not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"[dpo] {len(rows)} corpus, {len(done)} done, {len(todo)} to build (margin>={_MARGIN})")
    if not todo:
        print("[dpo] nothing to do."); return 0

    import httpx
    sem = asyncio.Semaphore(_CONCURRENCY)
    lock = asyncio.Lock()
    st = {"kept": 0, "dropped": 0, "fail": 0, "margin_sum": 0.0}
    f = OUT.open("a", encoding="utf-8")
    async with httpx.AsyncClient() as client:
        async def worker(row):
            async with sem:
                try:
                    rec = await _make_pair(client, key, row)
                except Exception:
                    rec = None
                async with lock:
                    if rec is None:
                        st["fail"] += 1
                    elif rec.get("_dropped"):
                        st["dropped"] += 1
                    else:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
                        st["kept"] += 1
                        st["margin_sum"] += rec["chosen_reward"] - rec["rejected_reward"]
                    n = st["kept"] + st["dropped"] + st["fail"]
                    if n % 25 == 0 or n == len(todo):
                        print(f"[dpo] {n}/{len(todo)} kept={st['kept']} dropped={st['dropped']} fail={st['fail']}", flush=True)
        await asyncio.gather(*(worker(r) for r in todo))
    f.close()
    avg = st["margin_sum"] / st["kept"] if st["kept"] else 0
    print(f"[dpo] DONE kept={st['kept']} dropped={st['dropped']} fail={st['fail']} "
          f"avg_verified_margin={avg:.3f} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
