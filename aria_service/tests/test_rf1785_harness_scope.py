"""R-F1785 — harness scan scope is honest + exemptions are coherent.

Covers:
  - GATE_SCOPE hard-blocks a configured target that doesn't exist (kills the
    silent-skip false-coverage failure mode: the old phantom "engines" dir).
  - HARD_EXEMPT is keyed by BASENAME so is_exempt() (called with basenames)
    actually resolves the routes/aria.py stream-endpoint exemption.
  - The real scope (intel/routes/autonomous/llm/search_engine + aria_engine.py
    + main.py) exists and the gates run green over it.
"""
import os

from aria_service.intel import wiring_harness as wh


def test_gate_scope_blocks_missing_target_dir():
    results = wh.run_all_gates(
        target_dirs=["aria_service/intel", "aria_service/DOES_NOT_EXIST"],
        target_files=[],
    )
    assert any("DOES_NOT_EXIST" in v for v in results["gate_scope"]), (
        f"missing dir not hard-blocked: {results['gate_scope']}"
    )


def test_gate_scope_blocks_missing_target_file():
    results = wh.run_all_gates(
        target_dirs=[],
        target_files=["aria_service/NO_SUCH_FILE.py"],
    )
    assert any("NO_SUCH_FILE" in v for v in results["gate_scope"])


def test_phantom_engines_dir_is_gone():
    assert "aria_service/engines" not in wh.TARGET_DIRS
    # And no configured target is missing (would itself be a gate_scope block).
    assert wh.run_all_gates()["gate_scope"] == []


def test_hard_exempt_keyed_by_basename_resolves():
    # routes/aria.py's stream endpoint must resolve via the basename the gates use.
    ok, reason = wh.is_exempt("aria.py", "chat_stream_ep")
    assert ok and reason, "stream-endpoint exemption did not resolve by basename"
    assert "routes/aria.py" not in wh.HARD_EXEMPT, "path-keyed entry would never match"


def test_no_unexempt_generators_in_scope():
    """Every generator in scope is HARD_EXEMPT — wrapping one breaks stream/boot.

    Locks the landmine count at 0: a generator the gates can see but that isn't
    exempt would stall Phase 1 (GATE A flags it dark; the decoration guard makes
    it impossible to wire).
    """
    landmines = []
    for d in wh.TARGET_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".py"):
                continue
            for f in wh.scan_public_functions(os.path.join(d, fn)):
                if f["is_generator"] and not wh.is_exempt(fn, f["name"])[0]:
                    landmines.append(f"{fn}:{f['name']}")
    for fpath in wh.TARGET_FILES:
        if not os.path.isfile(fpath):
            continue
        base = os.path.basename(fpath)
        for f in wh.scan_public_functions(fpath):
            if f["is_generator"] and not wh.is_exempt(base, f["name"])[0]:
                landmines.append(f"{base}:{f['name']}")
    assert landmines == [], f"unexempt generators in scope (Phase-1 landmines): {landmines}"


def test_expanded_scope_present_and_green():
    for d in ("aria_service/llm", "aria_service/search_engine"):
        assert d in wh.TARGET_DIRS and os.path.isdir(d), f"{d} not scanned"
    for f in ("aria_service/aria_engine.py", "aria_service/main.py"):
        assert f in wh.TARGET_FILES and os.path.isfile(f), f"{f} not scanned"
    results = wh.run_all_gates()
    assert all(v == [] for v in results.values()), f"gates not green: {results}"
