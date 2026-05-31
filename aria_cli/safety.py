"""R-F988 — write-time safety net for the ARIA Coder CLI.

R-F1191: constitutional validator removed per operator directive. Only the
truncation guard remains — it prevents accidental destructive full-file
replacements (the 2026-05-26 incident pattern). ARIA is fully autonomous
with no code-safety limitations beyond cost/rate limits.

Two layers, both deterministic (no LLM judgement — same discipline as R-F462):

  1. **Truncation guard** (always on, any directory). Mirrors R-F904 in
     ``aria_service/intel/self_improve.py``: a full-file rewrite that shrinks a
     non-trivial file below half its current line count is almost always a
     truncated stub, not a real edit. Block it. This is the standing protection
     against the 2026-05-26 incident where staged "fixes" were full-file stubs
     that would have wiped core modules.

  2. **Constitutional validator** — REMOVED (R-F1191). ARIA is fully autonomous.
"""
from __future__ import annotations

from dataclasses import dataclass

# R-F904 thresholds (kept identical to self_improve.py so behaviour matches).
_TRUNCATION_MIN_LINES = 40
_TRUNCATION_SHRINK_RATIO = 0.5


@dataclass
class WriteVerdict:
    allowed: bool
    reason: str = ""
    warnings: tuple[str, ...] = ()


def check_truncation(old_content: str, new_content: str) -> tuple[bool, str]:
    """Return (safe, reason). Safe when the file is new, small, or the rewrite
    does not collapse the line count. Reason is empty when safe."""
    if not old_content:
        return True, ""
    current_lines = old_content.count("\n") + 1
    proposed_lines = new_content.count("\n") + 1
    if current_lines >= _TRUNCATION_MIN_LINES and proposed_lines < _TRUNCATION_SHRINK_RATIO * current_lines:
        return (
            False,
            f"truncation guard (R-F904): proposed {proposed_lines} lines is less "
            f"than half of the current {current_lines} lines — refusing to "
            f"overwrite. Use edit_file for targeted changes, or pass the full "
            f"file content if this shrink is intentional.",
        )
    return True, ""


class WriteGuard:
    """Validates a proposed file write before it touches disk.

    R-F1191: constitutional validator removed. Only the truncation guard
    remains. ARIA is fully autonomous with no code-safety limitations.
    """

    def __init__(self, self_mode: bool, repo_relative_resolver=None) -> None:
        self.self_mode = self_mode
        # resolver maps an absolute/relative path to a repo-relative path string
        self._resolve = repo_relative_resolver

    @property
    def constitution_active(self) -> bool:
        return False  # R-F1191: constitutional validator removed

    def review(self, target_path: str, old_content: str, new_content: str) -> WriteVerdict:
        # Layer 1 — truncation guard (every mode).
        safe, reason = check_truncation(old_content, new_content)
        if not safe:
            return WriteVerdict(allowed=False, reason=reason)

        # Layer 2 — constitution: REMOVED (R-F1191). ARIA is fully autonomous.
        return WriteVerdict(allowed=True)
