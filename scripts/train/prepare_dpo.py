"""prepare_dpo — assemble DPO preference pairs from R-F59 + R-F80
adversarial outputs (R-F91, 2026-05-09).

DPO training pairs each have a CHOSEN (correct refusal/response) and
a REJECTED (incorrect/leak response) for the same prompt. The model
learns to prefer chosen over rejected.

Sources:
  1. R-F59 social-engineering attack library — for each attack with
     a passing model response, take that as `chosen`. For each failing
     response (or hand-written counterexample), take that as `rejected`.
  2. R-F80 prompt-injection attack library — same shape.

Inputs:
  --r-f59-dir   /path/to/adversarial_runs/  (R-F59 stored runs)
  --r-f80-dir   /path/to/prompt_injection_runs/
  --hand-pairs  /path/to/manual_pairs.jsonl  (operator-curated pairs)
  --out         output JSONL path

Output schema: {prompt, chosen, rejected, meta}

Usage:
  python prepare_dpo.py \
    --r-f59-dir /data/aria_runs/adversarial \
    --r-f80-dir /data/aria_runs/prompt_injection \
    --out /workspace/datasets/aria_dpo_v1.jsonl
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("aria.train.prepare_dpo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _load_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _adversarial_to_dpo(
    runs_dir: Path,
    library_label: str,
) -> Iterator[dict[str, Any]]:
    """Convert adversarial run records into DPO pairs.

    Expected record shape (per R-F59 + R-F80 grade_response output):
        { attack_id, name, severity, passed, response_excerpt, refusal_markers_hit, leak_markers_hit }

    For DPO we need both a CHOSEN (passing response) and a REJECTED
    (failing response) for the same attack. We pair them within an
    attack_id by taking the first passing + first failing.
    """
    if not runs_dir.exists():
        return
    by_attack: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for jp in runs_dir.glob("*.jsonl"):
        for rec in _load_jsonl(jp):
            attack_id = rec.get("attack_id")
            if not attack_id:
                continue
            slot = "chosen" if rec.get("passed") else "rejected"
            by_attack.setdefault(attack_id, {"chosen": [], "rejected": []})
            by_attack[attack_id][slot].append(rec)

    for attack_id, recs in by_attack.items():
        chosens = recs.get("chosen") or []
        rejecteds = recs.get("rejected") or []
        if not chosens or not rejecteds:
            continue
        # Pair the first of each
        c, r = chosens[0], rejecteds[0]
        # We need the prompt — but adversarial records typically only
        # have the attack_id, not the input. Look up the attack input
        # from the library.
        prompt = c.get("prompt") or c.get("input") or _lookup_attack_input(attack_id, library_label)
        if not prompt:
            continue
        yield {
            "prompt":   prompt,
            "chosen":   c.get("response_excerpt") or c.get("response") or "",
            "rejected": r.get("response_excerpt") or r.get("response") or "",
            "meta": {
                "source":     library_label,
                "attack_id":  attack_id,
                "name":       c.get("name") or r.get("name"),
                "severity":   c.get("severity") or r.get("severity"),
            },
        }


def _lookup_attack_input(attack_id: str, library_label: str) -> str | None:
    """Last-resort prompt lookup: import the library module + find the
    attack record. Best-effort; returns None on import failure."""
    try:
        if "prompt_injection" in library_label.lower() or attack_id.startswith("PI-"):
            from aria_service.intel import prompt_injection_suite
            attack = next((a for a in prompt_injection_suite._LIBRARY  # noqa
                           if a["id"] == attack_id), None)
            return attack.get("input") if attack else None
        # R-F59 social-engineering attacks — would need to import their
        # library module here; left as exercise for operator curation.
        return None
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Prepare ARIA-LLM DPO dataset")
    ap.add_argument("--r-f59-dir", type=Path,
                    help="Directory of R-F59 social-engineering adversarial runs")
    ap.add_argument("--r-f80-dir", type=Path,
                    help="Directory of R-F80 prompt-injection adversarial runs")
    ap.add_argument("--hand-pairs", type=Path,
                    help="Operator-curated DPO pairs JSONL (optional)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    counts = {"r_f59": 0, "r_f80": 0, "hand": 0, "total": 0}

    with args.out.open("w", encoding="utf-8") as outf:
        if args.r_f59_dir:
            for rec in _adversarial_to_dpo(args.r_f59_dir, "r_f59_social"):
                outf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                counts["r_f59"] += 1
                counts["total"] += 1

        if args.r_f80_dir:
            for rec in _adversarial_to_dpo(args.r_f80_dir, "r_f80_prompt_injection"):
                outf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                counts["r_f80"] += 1
                counts["total"] += 1

        if args.hand_pairs and args.hand_pairs.exists():
            for raw in _load_jsonl(args.hand_pairs):
                if not raw.get("prompt") or not raw.get("chosen") or not raw.get("rejected"):
                    continue
                outf.write(json.dumps(raw, ensure_ascii=False) + "\n")
                counts["hand"] += 1
                counts["total"] += 1

    logger.info("DPO dataset assembled at %s", args.out)
    for k, v in counts.items():
        logger.info("  %s: %d", k, v)

    if counts["total"] < 100:
        logger.warning(
            "DATASET SIZE WARNING — only %d DPO pairs. Phase 3 DPO should "
            "target ≥500 pairs. Run R-F59 and R-F80 adversarial suites "
            "multiple times across model checkpoints to accumulate.",
            counts["total"],
        )


if __name__ == "__main__":
    main()
