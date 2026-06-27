#!/usr/bin/env python
"""R-F2010 — build the GRPO prompt dataset from the grounded corpus.

Each grounded corpus record (data/training/aria_grounded_v1.jsonl) carries a
conversational `messages` list whose user turn embeds the retrieved CONTEXT (with
[Source:]/[from] markers) + the question. For GRPO we need, per prompt:
  prompt  = [the user message]           (what the policy sees + completes)
  context = the user message text        (what grounding_reward verifies against)

We keep only prompts whose context actually contains >=1 source label — otherwise
the grounding reward can't distinguish grounded from fabricated (every citation
would read as fabricated) and the prompt would teach nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from aria_service.intel import grounding_reward as gr  # noqa: E402

SRC = REPO / "data" / "training" / "aria_grounded_v1.jsonl"
OUT = REPO / "data" / "training" / "aria_grpo_prompts_v1.jsonl"


def main() -> int:
    # R-F2033 — emit answerability so the reward can penalise abstention on
    # ANSWERABLE questions (the gaming hole). label=="grounded" => the context
    # supports an answer (answerable); "grounded_abstain"/"abstain" => the correct
    # response is to abstain (unanswerable). We keep ALL records now (the
    # unanswerable ones teach honest abstention — the balance prevents both
    # over-abstention AND over-answering); the no-source examples are valid
    # unanswerable cases (reward Case 1 handles them).
    kept, skipped_shape = 0, 0
    answerable_n, unanswerable_n = 0, 0
    with open(SRC, encoding="utf-8") as f, open(OUT, "w", encoding="utf-8") as w:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            d = json.loads(ln)
            msgs = d.get("messages") or []
            user = next((m for m in msgs if m.get("role") == "user"), None)
            if not user or not user.get("content"):
                skipped_shape += 1
                continue
            content = user["content"]
            answerable = (d.get("label") == "grounded")
            if answerable:
                answerable_n += 1
            else:
                unanswerable_n += 1
            w.write(json.dumps({
                "prompt": [{"role": "user", "content": content}],
                "context": content,
                "answerable": answerable,
            }) + "\n")
            kept += 1
    print(f"[build_grpo] kept={kept} (answerable={answerable_n}, unanswerable={unanswerable_n})  "
          f"skipped_shape={skipped_shape}")
    print(f"[build_grpo] -> {OUT}")
    return 0 if kept else 1


if __name__ == "__main__":
    raise SystemExit(main())
