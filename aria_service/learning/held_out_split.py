"""Held-out 80/20 split — deterministic train/eval split for honest eval.

R-F1462/R-F1401: before any training cycle, split the corpus into a training
set (80%) and a held-out eval set (20%). The split is deterministic (seeded
hash of seed_id or question text) so the same example always lands in the
same split across runs. A zero-overlap assertion guarantees no example
appears in both sets.

Usage:
    splitter = HeldOutSplit(seed=42)
    train, eval = splitter.split(examples)
    assert splitter.verify_no_overlap(train, eval)
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger("aria.learning.held_out_split")

# Default split ratio: 80% train, 20% eval
_DEFAULT_TRAIN_RATIO = 0.80


class HeldOutSplit:
    """Deterministic 80/20 split of training examples.

    Each example is assigned to train or eval based on a seeded hash of its
    seed_id (preferred) or question text (fallback). This ensures the same
    example always lands in the same split across runs, regardless of the
    order examples are presented.
    """

    def __init__(
        self,
        train_ratio: float = _DEFAULT_TRAIN_RATIO,
        seed: int = 42,
    ) -> None:
        if not 0 < train_ratio < 1:
            raise ValueError(f"train_ratio must be between 0 and 1, got {train_ratio}")
        self.train_ratio = train_ratio
        self.seed = seed

    @staticmethod
    def _split_key(example: dict[str, Any]) -> str:
        """Derive a stable key for an example.

        Uses seed_id from metadata if available, otherwise falls back to
        the question text. This ensures the same content always hashes to
        the same split.
        """
        meta = example.get("meta") or {}
        seed_id = meta.get("seed_id") or ""
        if seed_id:
            return f"seed:{seed_id}"
        question = example.get("user") or example.get("question") or ""
        return f"text:{question[:200]}"

    def _assign_split(self, key: str) -> str:
        """Deterministically assign 'train' or 'eval' based on a seeded hash.

        Uses SHA-256 of (seed + ":" + key). The first byte determines the
        split: if byte / 256 < train_ratio → train, else eval.
        """
        digest = hashlib.sha256(
            f"{self.seed}:{key}".encode("utf-8", errors="ignore")
        ).digest()
        # Use the first byte as a deterministic random value in [0, 255]
        bucket = digest[0] / 256.0
        return "train" if bucket < self.train_ratio else "eval"

    def split(
        self,
        examples: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split examples into train (80%) and eval (20%).

        Args:
            examples: List of training example dicts. Each should have
                      either meta.seed_id or user/question text.

        Returns:
            (train_examples, eval_examples) — both lists preserve the
            original order within each split.
        """
        train: list[dict[str, Any]] = []
        eval_set: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for ex in examples:
            key = self._split_key(ex)
            if key in seen_keys:
                # Duplicate key — keep the first assignment
                continue
            seen_keys.add(key)
            split = self._assign_split(key)
            if split == "train":
                train.append(ex)
            else:
                eval_set.append(ex)

        logger.info(
            "[held_out_split] split %d examples: %d train (%.0f%%), %d eval (%.0f%%)",
            len(examples), len(train),
            100 * len(train) / max(len(examples), 1),
            len(eval_set),
            100 * len(eval_set) / max(len(examples), 1),
        )
        return train, eval_set

    @staticmethod
    def verify_no_overlap(
        train: list[dict[str, Any]],
        eval_set: list[dict[str, Any]],
    ) -> bool:
        """Assert zero overlap between train and eval sets.

        Compares by split_key — if any example in train has the same key
        as an example in eval, the split is contaminated.

        Returns True if clean, False if overlap detected.
        """
        train_keys = {HeldOutSplit._split_key(ex) for ex in train}
        eval_keys = {HeldOutSplit._split_key(ex) for ex in eval_set}
        overlap = train_keys & eval_keys
        if overlap:
            logger.error(
                "[held_out_split] OVERLAP DETECTED: %d example(s) appear in both train and eval",
                len(overlap),
            )
            for key in list(overlap)[:5]:
                logger.error("  overlapping key: %s", key)
            return False
        return True


# ── Convenience function ──────────────────────────────────────────────────────

def split_examples(
    examples: list[dict[str, Any]],
    train_ratio: float = _DEFAULT_TRAIN_RATIO,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convenience function — one-shot split."""
    splitter = HeldOutSplit(train_ratio=train_ratio, seed=seed)
    return splitter.split(examples)
