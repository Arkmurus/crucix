"""gen_questions_from_facts_v07 — SHARPER question generator (R-F1654).

v0.5/v0.6 plateaued at ~0.30-0.31 because ~38% of the grounded corpus was
ABSTENTION: questions were hard multi-jurisdictional hypotheticals the RAG facts
couldn't answer, so the teacher (correctly) abstained — which over-teaches
abstention and caps accuracy. v0.7 fixes the BOTTLENECK: generate questions
DERIVED FROM actual indexed RAG facts, so each is answerable-by-construction —
the teacher cites instead of abstaining (target abstention ~15%, not 38%).

Flow:
  1. For each topic, pull real chunks from the LIVE RAG store via /rag/search
     (several facet queries per topic for diversity).
  2. Dedupe chunks; for each, ask DeepSeek to write ONE specific DD question
     that is DIRECTLY answerable from that fact alone.
  3. Contamination-exclude vs the frozen eval + existing question pools.
  4. Emit {question, topic, source} — fed to build_openbook_remote (re-retrieve
     production context, k=12) then build_grounded_corpus.

PAID: DeepSeek question-gen (~$1-2). Read-only on /rag/search (no load).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

DEEPSEEK_URL = "https://api.deepseek.com/v1"
BASE = "https://aria-intel.fly.dev"

TOPICS = [
    "sanctions", "export_control", "diversion", "procurement", "tender",
    "compliance", "brokering", "trade_finance", "maritime", "defence",
    "intelligence", "cyber", "financial_crime", "money_laundering", "corruption",
    "human_trafficking", "weapons_proliferation", "critical_technology", "dual_use", "ubo",
]
FACETS = ["{t} red flags", "{t} regulation", "{t} entity case", "{t} typology", "{t} enforcement"]

_QGEN_SYS = (
    "You write ONE specific due-diligence question that is DIRECTLY and FULLY "
    "answerable from the single intelligence fact provided — no outside knowledge "
    "needed. The question must be concrete (names/dates/entities/mechanisms in the "
    "fact), not a broad hypothetical. It must be STANDALONE: do NOT reference 'the "
    "fact', 'the text', 'the extracted text', 'the document', or 'the context' — phrase "
    "it as a natural question an analyst would ask cold. Output ONLY the question."
)


def _norm(q: str) -> str:
    return " ".join((q or "").lower().split())


def _load_excludes(paths: list[str]) -> set[str]:
    seen: set[str] = set()
    for p in paths or []:
        fp = Path(p)
        if not fp.exists():
            continue
        for ln in fp.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            q = rec.get("question")
            if not q and rec.get("messages"):
                q = rec["messages"][0].get("content", "").split("[QUESTION]\n")[-1]
            if q:
                seen.add(_norm(q))
    return seen


async def _search(client, token, query, k):
    import httpx
    try:
        r = await client.post(f"{BASE}/api/aria/rag/search",
                              headers={"Authorization": f"Bearer {token}"},
                              json={"query": query, "k": k}, timeout=30.0)
        return r.json().get("results") or [] if r.status_code == 200 else []
    except Exception:
        return []


async def _qgen(client, key, fact):
    import httpx
    try:
        r = await client.post(f"{DEEPSEEK_URL}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "max_tokens": 120, "temperature": 0.4,
                  "messages": [{"role": "system", "content": _QGEN_SYS},
                               {"role": "user", "content": f"FACT:\n{fact[:1500]}"}]},
            timeout=60.0)
        if r.status_code != 200:
            return None
        return (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception:
        return None


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--per-topic", type=int, default=60, help="target chunks per topic")
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--concurrency", type=int, default=6)
    args = ap.parse_args()

    key = os.environ.get("DEEPSEEK_API_KEY")
    token = os.environ.get("ARIA_INTERNAL_TOKEN") or os.environ.get("INTERNAL_TOKEN")
    if (not key or not token) and Path(".env").exists():
        for ln in Path(".env").read_text(encoding="utf-8").splitlines():
            if ln.startswith("DEEPSEEK_API_KEY=") and not key:
                key = ln.split("=", 1)[1].strip().strip('"').strip("'")
            if "TOKEN=" in ln and not token and not ln.strip().startswith("#"):
                token = ln.split("=", 1)[1].strip().strip('"').strip("'")
    if not key or not token:
        print("BLOCKED: need DEEPSEEK_API_KEY + a token (env/.env)", file=sys.stderr); return 2

    import httpx
    exclude = _load_excludes(args.exclude)
    print(f"[v07] exclude seed: {len(exclude)} existing questions")

    # 1+2. pull + dedupe chunks
    chunks: list[tuple[str, str]] = []  # (text, topic)
    seen_txt: set[str] = set()
    async with httpx.AsyncClient() as client:
        for t in TOPICS:
            per = 0
            for f in FACETS:
                if per >= args.per_topic:
                    break
                res = await _search(client, token, f.format(t=t), max(8, args.per_topic // len(FACETS) + 4))
                for r in res:
                    txt = (r.get("text") or "").strip()
                    if len(txt) < 80:
                        continue
                    kkey = txt[:120]
                    if kkey in seen_txt:
                        continue
                    seen_txt.add(kkey); chunks.append((txt, t)); per += 1
            print(f"[v07] {t}: {per} chunks")
        print(f"[v07] total unique chunks: {len(chunks)}")

        # 3. generate one answerable question per chunk (bounded concurrency)
        sem = asyncio.Semaphore(args.concurrency)
        out: list[dict] = []
        async def one(txt, topic):
            async with sem:
                q = await _qgen(client, key, txt)
            if q and 15 < len(q) < 400 and _norm(q) not in exclude:
                exclude.add(_norm(q))
                out.append({"question": q, "topic": topic, "source": "v07_fact_derived"})
        await asyncio.gather(*[one(t, top) for t, top in chunks])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8", newline="\n")
    print(f"[v07] wrote {len(out)} fact-derived questions -> {args.out}")
    return 0 if out else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
