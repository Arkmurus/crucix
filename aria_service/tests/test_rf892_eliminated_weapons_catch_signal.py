"""R-F892 — ARIA records banned/eliminated-weapon CATCHES to the brain.

Ecosystem-360 P0-3: the eliminated-weapons watchlist screen-HIT (a treaty-
banned weapon on a procurement list — ARIA's highest-value compliance action)
was invisible to the brain; only the DD report carried it, so ARIA never
"remembered" what she screened out and the coder/learning loop could not see
it. R-F892 fires brain_hook.absorb_silent on the match.

The FAILURE side (engine raises) is covered by R-F886 + R-F891: dd_orchestrator
logs under the "ARIA.*" tree, and R-F891 made error_log_handler catch that tree
case-insensitively, so the per-line WARNING now reaches the ledger on its own.
"""
from __future__ import annotations

from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8")


def test_catch_signal_wired_in_eliminated_weapons_branch():
    assert "R-F892" in SRC
    # absorbed via the never-raises wrapper, fire-and-forget (must not block DD)
    assert "absorb_silent(" in SRC
    assert 'module="dd_orchestrator"' in SRC
    assert "Banned/eliminated-weapon screen HIT" in SRC
    assert "success=True" in SRC


def test_absorb_module_key_is_valid():
    # module= must be a real _MODULE_TOPICS key, else the signal is silently
    # dropped — the exact class of bug R-F891 fixed for the logger namespace.
    import aria_service.intel.brain_hook as bh
    assert "dd_orchestrator" in bh._MODULE_TOPICS


def test_engine_matches_canonical_banned_weapon():
    # Precondition for the catch branch firing: the engine must HIT on a
    # canonical treaty-eliminated weapon (the 2026-05-17 ADSM SS-20 case).
    import aria_service.intel.eliminated_weapons_watchlist as ewl
    f = ewl.render_finding_for_text("Procurement list: SS-20 rockets x 10000")
    assert f is not None
    assert f["severity"] == "hard_stop"
    assert f.get("elimination_status") == "eliminated"
