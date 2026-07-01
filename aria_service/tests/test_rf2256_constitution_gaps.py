"""R-F2256 — close the constitution/wiring audit gaps.

(1) DiffValidator is now WIRED into the deploy gate so a fix that SILENTLY DELETES a
    critical safety line is blocked (was orphaned). (2) The dark internal loops
    (guardian dead-man's-switch, expiry_sweeper, memory_wal_drain, bg_supervisor) now
    wire failures to the brain (§21a). (3) The stale "validator removed" prompt text is
    corrected. (4) CLAUDE.md rule-count drift fixed.
"""
from __future__ import annotations
from pathlib import Path

from aria_service.autonomous.constitutional_validator import DiffValidator

_ROOT = Path(__file__).resolve().parents[2]
_MAIN = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
_SI = (Path(__file__).resolve().parent.parent / "intel" / "self_improve.py").read_text(encoding="utf-8")
_SL = (Path(__file__).resolve().parent.parent / "autonomous" / "sovereign_llm.py").read_text(encoding="utf-8")


def test_diffvalidator_blocks_silent_safety_line_removal():
    dv = DiffValidator()
    bad = ("--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,2 @@\n ctx\n"
           "-        source_verifier.verify(response)\n more\n")
    assert dv.validate_diff(bad).passed is False, "removing a source_verifier.verify line must block"


def test_diffvalidator_allows_a_safe_addition():
    dv = DiffValidator()
    ok = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,3 @@\n ctx\n+        new_feature()\n more\n"
    assert dv.validate_diff(ok).passed is True


def test_deploy_gate_now_wires_diffvalidator():
    assert "DiffValidator" in _SI and "validate_diff" in _SI
    assert "R-F2256" in _SI  # the diff gate block is present


def test_dark_loops_now_wire_failures():
    # each previously-dark loop now names itself in a wire_ call
    for mod in ("guardian_reconcile", "expiry_sweeper", "memory_wal_drain", "bg_supervisor"):
        assert f'module="{mod}"' in _MAIN, f"{mod} still dark (no brain wire)"


def test_prompt_text_corrected():
    assert "constitutional validator removed" not in _SL
    assert "ENFORCED fail-closed at deploy" in _SL


def test_claude_md_rule_count_fixed():
    claude = (_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "synced at boot with 24 rules" not in claude  # the wrong number is gone
    from aria_service.intel.constitutional_rules import CONSTITUTIONAL_RULES
    assert str(len(CONSTITUTIONAL_RULES)) in claude  # the real count is documented
