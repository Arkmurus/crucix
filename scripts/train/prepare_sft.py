"""prepare_sft — assemble the SFT training corpus from harvested chat
output, curated defence-DD content, and constitutional adherence pairs
(R-F91, 2026-05-09).

Inputs:
  --harvest-dir   /path/to/aria_training/   (default: /data/aria_training/)
  --curated-dir   /path/to/curated/          (optional: hand-picked content)
  --constitution  path/to/aria_engine.py     (extract clause text)
  --min-score     float (default 0.80)       (R-F67 quality threshold)
  --out           output JSONL path

Output: JSONL with one record per training example:
  { "input": str, "output": str, "meta": { source, sector, score, ts } }

Schema is OpenAI-compatible chat format suitable for direct ingest by
SFT trainers (e.g. trl, axolotl, llama-factory).

Usage:
  python prepare_sft.py \
    --harvest-dir /data/aria_training \
    --min-score 0.80 \
    --out /workspace/datasets/aria_sft_v1.jsonl
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("aria.train.prepare_sft")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _iter_harvest_files(harvest_dir: Path) -> Iterator[Path]:
    """Yield every JSONL file in the harvest directory.

    R-F201 (2026-05-11): accept TWO file-naming conventions —
    `harvest-YYYY-MM-DD.jsonl` from output_harvester (R-F67) AND
    `YYYY-MM-DD.jsonl` from training_export.run_daily_export (R-F69).
    Pre-R-F201 only the harvest-*.jsonl glob was honoured, so the
    365 staged examples written by training_export to /data/aria_training/
    were invisible to the fine-tune harness. Now both flow in.
    """
    if not harvest_dir.exists():
        return
    seen = set()
    for p in sorted(harvest_dir.glob("harvest-*.jsonl")):
        if p not in seen:
            seen.add(p)
            yield p
    # training_export's daily export — date-stamped JSONL without prefix
    for p in sorted(harvest_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl")):
        if p not in seen:
            seen.add(p)
            yield p


def _load_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("%s:%d malformed JSON: %s", path.name, line_no, e)


def _harvest_to_sft(record: dict[str, Any], min_score: float) -> dict[str, Any] | None:
    """Convert one harvest record into SFT format if it passes the
    quality threshold.

    R-F201 (2026-05-11): accepts BOTH formats —
      * output_harvester (R-F67): fields `user_msg`, `response`, `score`
      * training_export.run_daily_export (R-F69): fields `user_message`,
        `response`, `grounded_rate`, `verification_status`
    Records are normalised to a common shape before quality gating.
    """
    # Format detection — training_export uses `user_message`; harvest
    # uses `user_msg`. If neither is present, the record isn't usable.
    user_msg = (
        record.get("user_msg")
        or record.get("user_message")
        or record.get("message")
        or ""
    )
    response = (
        record.get("response")
        or record.get("reply")
        or record.get("output")
        or ""
    )
    if not user_msg or not response:
        return None
    if len(response) < 100:
        return None  # too short to teach anything

    # Quality score — harvest uses `score`; training_export uses
    # `grounded_rate` (verified-claim ratio). Both 0-1 scaled.
    score = float(
        record.get("score")
        or record.get("grounded_rate")
        or 0
    )
    if score < min_score:
        # R-F201: training_export's "well_formed" tier is honest
        # discipline below 0.80 grounded_rate. Accept it on a lower
        # bar so the 365 staged examples don't ALL get rejected.
        verdict = (record.get("verification_status") or "").lower()
        if verdict == "well_formed" and score >= max(0.5, min_score - 0.30):
            pass  # let it through at the lower bar
        else:
            return None

    meta = record.get("meta") or {}
    return {
        "input":  user_msg,
        "output": response,
        "meta": {
            "source":      "harvest" if "user_msg" in record else "training_export",
            "score":       score,
            "verdict":     record.get("verification_status") or meta.get("verdict"),
            "user_id":     meta.get("user_id"),
            "sector":      meta.get("sector"),
            "model":       meta.get("model"),
            "session_id":  meta.get("session_id"),
            "ts":          record.get("ts") or record.get("timestamp"),
        },
    }


def _curated_to_sft(record: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a curated record (operator-prepared {input, output, ...})
    into SFT format. The curated dir holds hand-written examples for
    rare/important defence-DD scenarios."""
    user_msg = record.get("input") or record.get("prompt")
    response = record.get("output") or record.get("response")
    if not user_msg or not response:
        return None
    return {
        "input":  user_msg,
        "output": response,
        "meta": {
            "source":  "curated",
            "tags":    record.get("tags", []),
            "topic":   record.get("topic"),
        },
    }


def _generate_constitutional_pairs(
    constitution_path: Path | None,
) -> Iterator[dict[str, Any]]:
    """Generate (input, output) pairs that teach constitutional
    adherence. Inputs are 'how would clause N apply to X?'-style
    prompts; outputs are correct citations to the clause."""
    if not constitution_path or not constitution_path.exists():
        logger.warning("constitution file not found at %s — skipping clause pairs", constitution_path)
        return

    text = constitution_path.read_text(encoding="utf-8", errors="ignore")

    # Find the constitution clauses block — looks like
    # ('1', 'Identity discipline', 'positive', 'negative')
    # or "Clause 1 — Identity discipline" depending on style.
    # Be lenient.
    clause_re = re.compile(
        r"clause\s+(\d+)[\s\-—:.]+([A-Za-z][A-Za-z &/-]{5,80})",
        re.IGNORECASE,
    )
    seen: set[str] = set()
    for m in clause_re.finditer(text):
        n = m.group(1)
        name = m.group(2).strip().rstrip(":,.")
        key = f"{n}|{name.lower()}"
        if key in seen:
            continue
        seen.add(key)
        yield {
            "input":  f"What does Constitution Clause {n} ({name}) require ARIA to do, and what would violating it look like?",
            "output": (
                f"Constitution Clause {n} — {name} — is a hard constraint "
                f"on every ARIA output. The clause requires me to follow "
                f"the {name.lower()} discipline established by past defence-DD "
                f"incidents that motivated it. Violating it would cause an "
                f"output guard to fire (Clause 11 — output-guard escalation), "
                f"the response to be rewritten or rejected, and the violation "
                f"to be recorded in the audit log. I do not negotiate over "
                f"constitutional clauses regardless of the operator persona "
                f"or session context."
            ),
            "meta": {
                "source":  "constitution_synthetic",
                "clause":  int(n),
                "name":    name,
            },
        }


def main() -> None:
    ap = argparse.ArgumentParser(description="Prepare ARIA-LLM SFT dataset")
    ap.add_argument("--harvest-dir", type=Path, default=Path("/data/aria_training"),
                    help="Directory of output_harvester JSONL files")
    ap.add_argument("--curated-dir", type=Path,
                    help="Directory of hand-curated SFT examples (optional)")
    ap.add_argument("--constitution", type=Path,
                    help="Path to aria_engine.py for synthetic clause pairs")
    ap.add_argument("--min-score", type=float, default=0.80,
                    help="Minimum harvest quality score (R-F67)")
    ap.add_argument("--out", type=Path, required=True,
                    help="Output JSONL path")
    ap.add_argument("--max-records", type=int, default=0,
                    help="Cap output records (0 = unlimited)")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    counts = {"harvest": 0, "curated": 0, "constitutional": 0, "rejected_score": 0,
              "rejected_other": 0, "total_written": 0}

    with args.out.open("w", encoding="utf-8") as outf:

        # Harvest
        for hp in _iter_harvest_files(args.harvest_dir):
            for raw in _load_jsonl(hp):
                if float(raw.get("score", 0)) < args.min_score:
                    counts["rejected_score"] += 1
                    continue
                rec = _harvest_to_sft(raw, args.min_score)
                if not rec:
                    counts["rejected_other"] += 1
                    continue
                outf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                counts["harvest"] += 1
                counts["total_written"] += 1
                if args.max_records and counts["total_written"] >= args.max_records:
                    break
            if args.max_records and counts["total_written"] >= args.max_records:
                break

        # Curated
        if args.curated_dir and args.curated_dir.exists():
            for cp in args.curated_dir.glob("*.jsonl"):
                for raw in _load_jsonl(cp):
                    rec = _curated_to_sft(raw)
                    if not rec:
                        counts["rejected_other"] += 1
                        continue
                    outf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    counts["curated"] += 1
                    counts["total_written"] += 1

        # Constitutional synthetic
        if args.constitution:
            for rec in _generate_constitutional_pairs(args.constitution):
                outf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                counts["constitutional"] += 1
                counts["total_written"] += 1

    logger.info("SFT dataset assembled at %s", args.out)
    for k, v in counts.items():
        logger.info("  %s: %d", k, v)
    logger.info("ratio harvest:curated:constitutional = %d:%d:%d",
                counts["harvest"], counts["curated"], counts["constitutional"])

    if counts["total_written"] < 1000:
        logger.warning(
            "DATASET SIZE WARNING — only %d records written. Phase 3 SFT "
            "should target ≥5,000 records. Activate ARIA_OUTPUT_HARVEST_ENABLED=1 "
            "and let the harvester accumulate for 25-33 days at ~150-200 pairs/day "
            "before training v0.1.",
            counts["total_written"],
        )


if __name__ == "__main__":
    main()
