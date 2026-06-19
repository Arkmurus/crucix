"""R-F988 — write-time safety net for the ARIA Coder CLI.

R-F1699: the constitutional validator is RE-ARMED for self-mode (editing the
crucix repo). R-F1191 had removed it CLI-side, leaving an R-F995-class bypass:
the CLI could overwrite honesty-critical files and deploy them with no
PROTECTED_FILES / weakening-pattern / AST check (the server side was hardened
by R-F1287 but the CLI was not). General-mode (arbitrary user projects) keeps
the truncation guard only — crucix's constitution does not apply to someone
else's codebase.

Two layers, both deterministic (no LLM judgement — same discipline as R-F462):

  1. **Truncation guard** (always on, any directory). Mirrors R-F904 in
     ``aria_service/intel/self_improve.py``: a full-file rewrite that shrinks a
     non-trivial file below half its current line count is almost always a
     truncated stub, not a real edit. Block it. This is the standing protection
     against the 2026-05-26 incident where staged "fixes" were full-file stubs
     that would have wiped core modules.

  2. **Constitutional validator** (self-mode only, R-F1699). Runs the server's
     ConstitutionalValidator — PROTECTED_FILES, weakening-pattern, learned-attack
     and AST checks — fail-closed: if it can't run, the write is refused.
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
        # R-F1699: the constitutional validator is ACTIVE in self-mode (editing
        # the crucix repo). General-mode (arbitrary user projects) stays
        # truncation-guard-only — crucix's honesty constitution does not apply
        # to someone else's codebase.
        return self.self_mode

    def review(self, target_path: str, old_content: str, new_content: str) -> WriteVerdict:
        # Layer 1 — truncation guard (every mode).
        safe, reason = check_truncation(old_content, new_content)
        if not safe:
            return WriteVerdict(allowed=False, reason=reason)

        # Layer 2 — constitutional validator (SELF-MODE only). R-F1699 closes the
        # R-F995-class bypass: before this, the CLI could overwrite honesty-
        # critical files (constitutional_validator.py, safety.py, sanctions.py,
        # …) and deploy them with NO PROTECTED_FILES / weakening-pattern / AST
        # check — the exact path by which ARIA once gutted her own validator.
        # The server self-coder runs this validator (R-F1287); the CLI must
        # match it. FAIL-CLOSED: if the validator cannot be loaded or run in
        # self-mode, the write is REFUSED — an unvalidated write to the
        # constitutional repo is the hole, so "can't check" must mean "don't
        # write", never "allow".
        if self.self_mode:
            rel_path = self._resolve(target_path) if self._resolve else target_path
            try:
                from aria_service.autonomous.constitutional_validator import (
                    ConstitutionalValidator,
                )
                res = ConstitutionalValidator().validate(new_content, rel_path)
            except Exception as exc:  # noqa: BLE001
                return WriteVerdict(
                    allowed=False,
                    reason=(
                        f"constitutional validator could not run on {rel_path} "
                        f"({type(exc).__name__}: {exc}) — refusing the write "
                        f"(fail-closed in self-mode)."
                    ),
                )
            if not res.passed:
                return WriteVerdict(
                    allowed=False,
                    reason=(
                        f"constitutional validator BLOCKED {rel_path} "
                        f"(risk={res.risk_score:.2f}): " + "; ".join(res.violations)
                    ),
                )
            return WriteVerdict(allowed=True, warnings=tuple(res.warnings))

        # General-mode: truncation guard only (editing arbitrary user projects).
        return WriteVerdict(allowed=True)
