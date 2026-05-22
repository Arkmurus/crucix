"""R-F812 — adapt chat_audit messages-format → harvest format for SFT prep.

The training-data dailies emitted by `training_export.run_daily_export`
(R-F69) under `/data/aria_training/YYYY-MM-DD.jsonl` ship in the
OpenAI Messages shape:

  {
    "messages": [
      {"role": "user", "content": "..."},
      {"role": "assistant", "content": "..."},
      ...
    ],
    "metadata": {
      "source": "chat_audit",
      "grounded_rate": 0.0,
      "verification_tier": "well_formed",
      "trace_id": ...
    }
  }

`prepare_sft.py` (R-F201 multi-format handler) looks for `user_msg` /
`user_message` / `message` at the top level — it doesn't unwrap the
`messages` array. This adapter does the unwrap step so the existing
prep pipeline picks up the 989 chat-audit pairs that are otherwise
invisible to it.

For each record we extract the LAST `user` → `assistant` turn (the
final exchange of a multi-turn session is usually the most coherent
training signal). The synthetic score is calibrated against
`verification_tier`:

  well_formed       → 0.85   (R-F201's lower-bar accept threshold)
  excellent         → 0.95
  partial / unknown → 0.55   (will be rejected by min-score 0.80)

Run before `prepare_sft.py` in `fly_train_pipeline.sh`.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("aria.train.adapt_chat_audit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


TIER_TO_SCORE: dict[str, float] = {
    "excellent":   0.95,
    "well_formed": 0.85,
    "partial":     0.55,
}
# Records with no tier label fall through to `default_score` (0.85 in
# `adapt()`) since prod chat_audit dailies always set verification_tier.
# Tier-less records are assumed-OK rather than assumed-bad.


def _iter_chat_audit_files(harvest_dir: Path) -> Iterator[Path]:
    """Yield YYYY-MM-DD.jsonl files (chat_audit format), skipping
    harvest-*.jsonl (already in the right format) and the
    adapted-*.jsonl files this script writes."""
    if not harvest_dir.exists():
        return
    for p in sorted(harvest_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl")):
        yield p


def _extract_last_turn(record: dict) -> tuple[str, str] | None:
    """Return (user_msg, assistant_response) from the last user→assistant
    pair in the messages array. None if the record is malformed."""
    messages = record.get("messages") or []
    if not isinstance(messages, list) or len(messages) < 2:
        return None

    # Walk backwards to find the last assistant message, then the
    # immediately-preceding user message.
    assistant_idx = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            assistant_idx = i
            break
    if assistant_idx is None:
        return None

    user_idx = None
    for i in range(assistant_idx - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, dict) and msg.get("role") == "user":
            user_idx = i
            break
    if user_idx is None:
        return None

    user_content = messages[user_idx].get("content")
    assistant_content = messages[assistant_idx].get("content")
    if not isinstance(user_content, str) or not isinstance(assistant_content, str):
        return None
    return user_content, assistant_content


def adapt(record: dict, *, default_score: float = 0.85) -> dict | None:
    """Convert one chat_audit record to the harvest-style flat shape
    that `prepare_sft._harvest_to_sft` recognises."""
    pair = _extract_last_turn(record)
    if pair is None:
        return None
    user_msg, response = pair
    if not user_msg or not response:
        return None
    if len(response) < 100:
        return None  # too short to teach anything (matches prep gate)

    meta = record.get("metadata") or {}
    tier = (meta.get("verification_tier") or "").lower()
    score = TIER_TO_SCORE.get(tier, default_score)

    return {
        # Fields prepare_sft.py picks up via R-F201 multi-format handler.
        "user_message": user_msg,
        "response": response,
        "grounded_rate": score,
        "verification_status": tier or "well_formed",
        "meta": {
            "source":   meta.get("source", "chat_audit"),
            "trace_id": meta.get("trace_id"),
            "tier":     tier,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Adapt chat_audit JSONL to harvest format")
    ap.add_argument(
        "--harvest-dir", type=Path,
        default=Path("/data/aria_training"),
        help="Directory containing YYYY-MM-DD.jsonl chat_audit files",
    )
    ap.add_argument(
        "--out-prefix", default="harvest-adapted-",
        help=(
            "Prefix for adapted files. Default starts with 'harvest-' so "
            "`prepare_sft._iter_harvest_files` picks them up via its existing "
            "`harvest-*.jsonl` glob — no changes needed in the prep script."
        ),
    )
    args = ap.parse_args()

    total_in = 0
    total_out = 0
    total_skipped = 0
    files_written = 0

    for src in _iter_chat_audit_files(args.harvest_dir):
        out_path = src.with_name(args.out_prefix + src.name)
        if out_path.exists():
            logger.info("[adapt] %s already exists — skipping", out_path.name)
            continue
        in_count = 0
        out_count = 0
        with src.open("r", encoding="utf-8") as fin, \
             out_path.open("w", encoding="utf-8") as fout:
            for line_no, line in enumerate(fin, 1):
                line = line.strip()
                if not line:
                    continue
                in_count += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning("%s:%d malformed JSON: %s", src.name, line_no, e)
                    continue
                adapted = adapt(record)
                if adapted is None:
                    total_skipped += 1
                    continue
                fout.write(json.dumps(adapted, ensure_ascii=False) + "\n")
                out_count += 1
        total_in += in_count
        total_out += out_count
        files_written += 1
        logger.info("[adapt] %s → %s (%d/%d)",
                    src.name, out_path.name, out_count, in_count)

    logger.info(
        "[adapt] DONE — %d files written, %d/%d records adapted, %d skipped",
        files_written, total_out, total_in, total_skipped,
    )


if __name__ == "__main__":
    main()
