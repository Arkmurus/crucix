"""Pair builder — assembles final SFT + DPO JSONL from generated/checked candidates.

R-F1467: takes the output of the data_engine generation pipeline (or any
list of GeneratedPair objects), applies the contamination assertion, builds
a manifest with SHA-256 hashes, and writes the final training corpus.

The pair_builder does NOT call any LLM — it is pure assembly + verification.
All LLM-dependent work (generation, judging) happens upstream in the
data_engine pipeline.

Usage:
    builder = PairBuilder(output_dir="/data/aria_training")
    result = await builder.build(pairs, mode="sft")
    # result["sft_written"] == N, result["manifest"] contains corpus stats
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from aria_service.learning.data_engine_generate import GeneratedPair

logger = logging.getLogger("aria.learning.pair_builder")

# Default output directory
_OUTPUT_DIR = Path("/data/aria_training")


@dataclass
class BuildResult:
    """Result of a pair_builder build."""
    sft_written: int = 0
    dpo_written: int = 0
    sft_path: str = ""
    dpo_path: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    contamination_verified: bool = False


class PairBuilder:
    """Assembles final training pairs from generated/checked candidates.

    Pure assembly + verification — no LLM calls. All LLM-dependent work
    happens upstream in the data_engine pipeline.
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        require_contamination_check: bool = True,
        require_judge_correct: bool = False,  # R-F1468: OFF by default — distillation pairs are unjudged (sanity-checked, not self-graded). Set True for gold-referenced data.
    ) -> None:
        self.output_dir = Path(output_dir) if output_dir else _OUTPUT_DIR
        self.require_contamination_check = require_contamination_check
        self.require_judge_correct = require_judge_correct

    async def build(
        self,
        pairs: list[GeneratedPair],
        mode: str = "sft",
        label: str = "",
    ) -> BuildResult:
        """Assemble pairs into the final training corpus.

        Args:
            pairs: List of GeneratedPair objects from the generation pipeline.
            mode: "sft" (chosen only) or "dpo" (chosen + rejected).
            label: Optional label for the output filenames.

        Returns:
            BuildResult with write counts, paths, and manifest.
        """
        errors: list[str] = []

        # Verify contamination check was applied
        contamination_verified = True
        if self.require_contamination_check:
            contaminated = [p for p in pairs if not p.contamination_free]
            if contaminated:
                msg = (
                    f"{len(contaminated)} pair(s) failed contamination check — "
                    f"excluding from output"
                )
                logger.warning("[pair_builder] %s", msg)
                errors.append(msg)
                pairs = [p for p in pairs if p.contamination_free]
                contamination_verified = False

        # Verify sanity check was applied
        failed_sanity = [p for p in pairs if not p.passed_sanity]
        if failed_sanity:
            logger.warning(
                "[pair_builder] %d pair(s) failed sanity check — excluding",
                len(failed_sanity),
            )
            pairs = [p for p in pairs if p.passed_sanity]

        # R-F1468: Judge gate — only admit pairs where the judge verdict
        # is "correct". The judge is validated (ROUND 11) and discriminates
        # real answers correctly. "partial" and "wrong" are excluded.
        if self.require_judge_correct:
            pre_judge = len(pairs)
            pairs = [p for p in pairs if p.judge_verdict == "correct"]
            excluded = pre_judge - len(pairs)
            if excluded:
                msg = (
                    f"{excluded} pair(s) excluded by judge gate "
                    f"(verdict != 'correct')"
                )
                logger.warning("[pair_builder] %s", msg)
                errors.append(msg)

        if not pairs:
            return BuildResult(
                errors=["no valid pairs to build"],
                contamination_verified=contamination_verified,
            )

        # Write SFT JSONL
        date_str = time.strftime("%Y-%m-%d")
        suffix = f"_{label}" if label else ""
        sft_filename = f"pairs{suffix}_{date_str}.sft.jsonl"
        sft_path = self.output_dir / sft_filename
        self.output_dir.mkdir(parents=True, exist_ok=True)

        sft_hashes: list[str] = []
        with sft_path.open("w", encoding="utf-8") as f:
            for p in pairs:
                record = p.to_sft_dict()
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                # SHA-256 of the JSON line for manifest integrity
                line_hash = hashlib.sha256(
                    json.dumps(record, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()[:16]
                sft_hashes.append(line_hash)

        sft_written = len(pairs)

        # Write DPO JSONL (only pairs with rejected answers)
        dpo_written = 0
        dpo_path_str = ""
        dpo_pairs = [p for p in pairs if p.rejected_answer]
        if dpo_pairs and mode == "dpo":
            dpo_filename = f"pairs{suffix}_{date_str}.dpo.jsonl"
            dpo_path = self.output_dir / dpo_filename
            with dpo_path.open("w", encoding="utf-8") as f:
                for p in dpo_pairs:
                    f.write(json.dumps(p.to_dpo_dict(), ensure_ascii=False) + "\n")
                    dpo_written += 1
            dpo_path_str = str(dpo_path)

        # Build manifest
        by_topic: dict[str, int] = {}
        for p in pairs:
            topic = p.topic or "unknown"
            by_topic[topic] = by_topic.get(topic, 0) + 1

        # Judge verdict breakdown for manifest
        judge_breakdown: dict[str, int] = {"correct": 0, "partial": 0, "wrong": 0, "unjudged": 0}
        for p in pairs:
            v = p.judge_verdict or "unjudged"
            if v in judge_breakdown:
                judge_breakdown[v] += 1
            else:
                judge_breakdown["unjudged"] += 1

        manifest = {
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mode": mode,
            "label": label,
            "sft_count": sft_written,
            "dpo_count": dpo_written,
            "by_topic": by_topic,
            "sft_hashes": sft_hashes[:10],  # First 10 for spot-check
            "sft_hash_count": len(sft_hashes),
            "contamination_verified": contamination_verified,
            "judge_gate_enabled": self.require_judge_correct,
            "judge_breakdown": judge_breakdown,
            "sft_file": str(sft_path),
            "dpo_file": dpo_path_str or None,
        }

        # Save manifest alongside the data
        manifest_path = self.output_dir / f"manifest{suffix}_{date_str}.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "[pair_builder] built %d SFT + %d DPO pairs (%d topics)",
            sft_written, dpo_written, len(by_topic),
        )

        return BuildResult(
            sft_written=sft_written,
            dpo_written=dpo_written,
            sft_path=str(sft_path),
            dpo_path=dpo_path_str,
            manifest=manifest,
            errors=errors,
            contamination_verified=contamination_verified,
        )

    @staticmethod
    def verify_integrity(manifest: dict[str, Any]) -> bool:
        """Verify the integrity of a built corpus against its manifest.

        Checks that the SFT file exists and has the expected number of lines.
        Returns True if the corpus is intact.
        """
        sft_path = manifest.get("sft_file", "")
        expected_count = manifest.get("sft_count", 0)
        if not sft_path or not Path(sft_path).exists():
            logger.error("[pair_builder] integrity check FAILED: SFT file missing")
            return False
        with open(sft_path, encoding="utf-8") as f:
            actual_count = sum(1 for _ in f)
        if actual_count != expected_count:
            logger.error(
                "[pair_builder] integrity check FAILED: expected %d lines, got %d",
                expected_count, actual_count,
            )
            return False
        logger.info("[pair_builder] integrity check PASSED: %d lines", actual_count)
        return True
