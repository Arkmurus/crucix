"""R-F2399 — Claude → ARIA teacher-signal distillation corpus.

ARIA distils from a STRONGER agent. When Claude (the senior reviewer/engineer)
gives ARIA engineering guidance, reasoning, corrections or design direction over
the Claude<->ARIA bridge, that guidance is training signal: Claude is the TEACHER,
ARIA-LLM is the STUDENT. This module captures every Claude→ARIA message into an
append-only, source-tagged corpus so a future SFT/DPO cycle can distil Claude's
reasoning into ARIA-LLM (§24 train/eval).

Before R-F2399 this signal was DARK: the Redis collab log had zero writers, the
server's ``.agent_bridge/`` file mailbox was empty (Claude writes it on the
operator's LOCAL machine, never shipped to the server), and the aria CLI's
``check_claude`` consumed Claude's replies ephemerally into agent context with NO
learning sink. So everything Claude taught ARIA was forgotten. This corpus + the
``collab_bridge.drain_for_aria`` wiring + the ``/api/aria/collab/ingest`` inbound
route close that gap: Claude's guidance now lands in RAG (via brain_hook.absorb)
AND this distill corpus tagged ``source=claude_teacher``.

Storage: append-only daily JSONL shards under a volume-backed directory (files
only, §6/§7 — no paid persistence, no eviction). Mirrors ``brave_distill.py`` so
the two teacher corpora share shape + ops surface. Best-effort: never blocks the
drain path, never raises.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("aria.claude_distill")

# Volume-backed on fly (set ARIA_CLAUDE_DISTILL_DIR=/data/claude_distill); repo-
# relative in local dev. Kept off the state_store on purpose — this is a durable
# training corpus, not hot brain state, and must not add write load to the
# single-writer sqlite ceiling (R-F2277/R-F2290), exactly like brave_distill.
_CORPUS_DIR = Path(
    os.getenv("ARIA_CLAUDE_DISTILL_DIR")
    or os.path.join(os.getenv("ARIA_DATA_DIR", "data"), "claude_distill")
)
_MAX_TEXT_LEN = 20000
_ENABLED = (os.getenv("ARIA_CLAUDE_DISTILL_ENABLED", "1") or "1").lower() in ("1", "true", "yes")

SOURCE_TAG = "claude_teacher"


def capture(
    text: str,
    *,
    direction: str = "claude->aria",
    kind: str = "note",
    msg_id: str = "",
    reply_to: str = "",
    prompt: str = "",
) -> bool:
    """Append one Claude→ARIA teacher-signal record. Returns True if written.

    Best-effort; swallows every error so it can never break the drain path.
    ``direction`` records who taught whom (we only capture the teacher signal —
    Claude→ARIA — but the field keeps the corpus self-describing).
    """
    if not _ENABLED:
        return False
    text = (text or "").strip()
    if not text:
        return False
    try:
        _CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.time(),
            "source": SOURCE_TAG,
            "direction": direction,
            "kind": (kind or "note")[:32],
            "msg_id": (msg_id or "")[:64],
            "reply_to": (reply_to or "")[:64],
            "text": text[:_MAX_TEXT_LEN],
            # R-F4304 (C-257) — the PROMPT this answered.
            #
            # An SFT row is (instruction, response). Until now the record held
            # Claude's text, a msg_id and a reply_to ID, but never the parent's
            # TEXT — so a reply could be linked and not paired, and the corpus was
            # untrainable no matter what consumed it. The parent was always
            # recoverable (_read_log returns BOTH directions and the drain already
            # calls it); nothing looked.
            #
            # Empty is honest and normal: an unprompted note has no question, and
            # the builder DROPS unpaired records rather than inventing one.
            "prompt": (prompt or "")[:_MAX_TEXT_LEN],
        }
        shard = _CORPUS_DIR / (time.strftime("%Y-%m-%d") + ".jsonl")
        with shard.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # §21a — wire the successful teacher-signal capture to the brain
        # (lightweight record_signal metric path, fire-and-forget).
        try:
            from .engine_wiring import wire_success
            wire_success(
                module="claude_distill",
                summary=f"captured Claude teacher signal ({kind}, {len(text)} chars)",
                source_id="claude_distill:capture",
            )
        except Exception:
            pass
        return True
    except Exception as e:  # never let capture break the drain
        logger.debug("claude_distill capture failed (non-fatal): %s", e)
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="claude_distill",
                detail=f"distillation capture failed: {e}",
                gap_type="engine_failure",
                source="claude_distill:capture",
            )
        except Exception:
            pass
        return False


def stats() -> dict:
    """Lightweight corpus summary for the ops / proprioception surface (§25)."""
    try:
        if not _CORPUS_DIR.exists():
            return {"records": 0, "shards": 0, "dir": str(_CORPUS_DIR), "source": SOURCE_TAG}
        shards = sorted(_CORPUS_DIR.glob("*.jsonl"))
        total = 0
        for s in shards:
            try:
                with s.open("r", encoding="utf-8") as f:
                    total += sum(1 for _ in f)
            except Exception:
                continue
        return {
            "records": total,
            "shards": len(shards),
            "dir": str(_CORPUS_DIR),
            "source": SOURCE_TAG,
            "latest": shards[-1].name if shards else None,
        }
    except Exception as e:
        return {"error": str(e), "dir": str(_CORPUS_DIR), "source": SOURCE_TAG}


# R-F2541: removed the R-F2399 import-time _wf_shutdown("module shutdown") block — it
# fired a FALSE engine_failure gap on every import (aliased-import variant the R-F2538
# sweep regex missed); do not re-add.
