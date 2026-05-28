"""R-F988 — write-time safety net for the ARIA Coder CLI.

Two layers, both deterministic (no LLM judgement — same discipline as R-F462):

  1. **Truncation guard** (always on, any directory). Mirrors R-F904 in
     ``aria_service/intel/self_improve.py``: a full-file rewrite that shrinks a
     non-trivial file below half its current line count is almost always a
     truncated stub, not a real edit. Block it. This is the standing protection
     against the 2026-05-26 incident where staged "fixes" were full-file stubs
     that would have wiped core modules.

  2. **Constitutional validator** (self-mode only — i.e. editing the crucix
     repo). Reuses ``aria_service.autonomous.constitutional_validator`` so the
     CLI applies the exact same guard ARIA's autonomous coder does: protected
     files, dangerous imports, guard-removal / constitution-clearing patterns.
     Loaded lazily; if unavailable the CLI still runs (general coding agent).
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


def _load_constitutional_validator():
    """Lazy import; returns an instance or None if aria_service isn't importable."""
    try:
        from aria_service.autonomous.constitutional_validator import (
            ConstitutionalValidator,
        )
    except Exception:  # noqa: BLE001 — CLI must run without the brain installed
        return None
    try:
        return ConstitutionalValidator()
    except Exception:  # noqa: BLE001
        return None


class WriteGuard:
    """Validates a proposed file write before it touches disk."""

    def __init__(self, self_mode: bool, repo_relative_resolver=None) -> None:
        self.self_mode = self_mode
        # resolver maps an absolute/relative path to a repo-relative path string
        # (what ConstitutionalValidator's PROTECTED_FILES are keyed on).
        self._resolve = repo_relative_resolver
        self._validator = _load_constitutional_validator() if self_mode else None

    @property
    def constitution_active(self) -> bool:
        return self._validator is not None

    def review(self, target_path: str, old_content: str, new_content: str) -> WriteVerdict:
        # Layer 1 — truncation guard (every mode).
        safe, reason = check_truncation(old_content, new_content)
        if not safe:
            return WriteVerdict(allowed=False, reason=reason)

        # Layer 2 — constitution (self-mode, crucix files only).
        warnings: list[str] = []
        if self._validator is not None:
            rel = self._resolve(target_path) if self._resolve else target_path
            try:
                result = self._validator.validate(new_content, rel)
            except Exception as exc:  # noqa: BLE001 — never let the guard crash the write path
                warnings.append(f"constitution validator errored (allowing): {exc}")
                return WriteVerdict(allowed=True, warnings=tuple(warnings))
            if not result.passed:
                return WriteVerdict(
                    allowed=False,
                    reason="constitutional validator blocked this write: "
                    + "; ".join(result.violations),
                )
            warnings.extend(result.warnings or [])

        return WriteVerdict(allowed=True, warnings=tuple(warnings))
