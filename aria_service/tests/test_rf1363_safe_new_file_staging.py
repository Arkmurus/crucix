"""R-F1363 — coder may CREATE new files in safe dirs (not just modify existing).

MODIFIABLE_FILES (R-F996) only contains EXISTING tracked files, so a new-
capability gap (every operator "add X" request) produced a file that failed
staging with "ARIA cannot modify X — not in whitelist" — the coder could
improve existing code but never grow the ecosystem. _is_safe_new_file allows
NEW .py files in intel/ + tests/ while keeping PROTECTED_FILES locked; staging
still routes to operator review (never auto-deploy).
"""
import pytest

from aria_service.intel import self_improve as SI


def test_allows_new_intel_capability():
    assert SI._is_safe_new_file("aria_service/intel/entity_keyer.py") is True


def test_allows_new_test_file():
    assert SI._is_safe_new_file("aria_service/tests/test_entity_keyer.py") is True


def test_blocks_protected_file_even_in_safe_dir():
    # self_improve.py lives in intel/ but is PROTECTED — must stay locked.
    assert SI._is_safe_new_file("aria_service/intel/self_improve.py") is False


def test_blocks_outside_safe_dirs():
    assert SI._is_safe_new_file("server.mjs") is False
    assert SI._is_safe_new_file("aria_service/autonomous/safety.py") is False
    assert SI._is_safe_new_file("aria_service/main.py") is False


def test_blocks_non_python():
    assert SI._is_safe_new_file("aria_service/intel/config.yaml") is False


def test_blocks_path_traversal():
    assert SI._is_safe_new_file("aria_service/intel/../../../etc/passwd.py") is False


@pytest.mark.asyncio
async def test_stage_improvement_accepts_new_safe_file(monkeypatch):
    """Capability: stage_improvement no longer rejects a new safe-dir file at the
    whitelist gate (the exact symptom: 'not in whitelist')."""
    # A brand-new file path not in MODIFIABLE_FILES
    new_file = "aria_service/intel/entity_keyer.py"
    SI.MODIFIABLE_FILES.discard(new_file)
    code = (
        "def entity_match_key(name: str) -> str:\n"
        '    """Normalize an entity name for DD matching."""\n'
        "    return name.strip().lower()\n"
    )
    res = await SI.stage_improvement(
        new_file, code, "bug_fix", "add entity keyer", reasoning="test",
    )
    # It must get PAST the whitelist gate (no "not in whitelist" error).
    err = res.get("error", "") if isinstance(res, dict) else ""
    assert "not in whitelist" not in err, f"still blocked at whitelist gate: {res}"
