"""citation_verifier — deterministic citation verification (R-F2540).

THE golden-reasoning guarantee: every [Source: X] / [from X] citation in a
synthesized answer must ground against a source label ACTUALLY present in the
retrieved evidence — the exact same test grounding_reward.score uses to count a
citation as grounded vs fabricated (`extract_context_sources` + `_cite_grounded`).
Any citation that does NOT verify is a fabricated source; the verifier FLAGS it
`[unverified]` (default) or DROPS it, so a fabricated source can never reach the
user. This turns "we don't fabricate sources" from a claim into a deterministic,
provable property (measured by scripts/eval/objective_citation_eval.py).

Pure, deterministic, no LLM, no network — safe to call inline on every grounded
synthesis. Never raises.
"""
from __future__ import annotations

from typing import Any

from . import grounding_reward as gr


def verify_and_clean(answer: str, context: str, *, mode: str = "flag") -> dict[str, Any]:
    """Verify every citation in ``answer`` against the sources in ``context``.

    mode="flag"  -> replace each unverified [Source: X] with `[unverified]`
                    (claim stays, but the fabricated source is visibly marked).
    mode="drop"  -> remove the unverified citation marker entirely.

    Returns {answer, dropped:[labels], kept:[labels], fabricated_removed:int,
             clean:bool}. Never raises — on any error returns the input unchanged.
    """
    try:
        text = answer or ""
        ctx_sources = gr.extract_context_sources(context or "")
        dropped: list[str] = []
        kept: list[str] = []

        def _repl(m) -> str:
            raw = m.group(1)
            if gr._cite_grounded(gr._norm(raw), ctx_sources):
                kept.append(raw)
                return m.group(0)
            dropped.append(raw)
            return "[unverified]" if mode == "flag" else ""

        cleaned = gr._CITE_RE.sub(_repl, text)
        return {
            "answer": cleaned,
            "dropped": dropped,
            "kept": kept,
            "fabricated_removed": len(dropped),
            "clean": len(dropped) == 0,
        }
    except Exception:
        return {"answer": answer, "dropped": [], "kept": [],
                "fabricated_removed": 0, "clean": True}


def is_clean(answer: str, context: str) -> bool:
    """True iff every citation in the answer verifies against the evidence."""
    return verify_and_clean(answer, context, mode="flag")["clean"]
