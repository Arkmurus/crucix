"""R-F1993 — DD deterministic primitives auto-run in the orchestrator.

The 12 primitives used to be manual paste-buttons in the DD report UI. They now
run automatically inside orchestrate_dd (LAYER 11 + the pre-existing Layers 8-10).
These tests pin (a) the integration contracts the orchestrator relies on — the
profile shapes built from resolved identity must produce valid results — and
(b) that all four newly-wired primitives are actually referenced in LAYER 11
(regression guard against accidental un-wiring), and (c) the manual pipeline panel
is gone from the customer UI.
"""
import asyncio

from aria_service.intel import economic_substance, fatf_typologies


def test_economic_substance_accepts_orchestrator_profile_shape():
    # The exact profile dict LAYER 11 builds from report.identity + target.
    profile = {
        "employees": None,
        "claimed_revenue_usd": 50_000_000,
        "paid_up_capital_usd": 1000,
        "claimed_contract_size_usd": None,
        "incorporation_date": "2024-01-01",
        "directors_count": 1,
        "registered_address": "Suite 100, Regus, BVI",
    }
    out = economic_substance.score_substance(profile)
    assert isinstance(out, dict)
    assert "substance_score" in out and "grade" in out


def test_fatf_typologies_accepts_orchestrator_profile_shape():
    profile = {
        "jurisdictions": ["BVI"],
        "ubo_disclosure": "undisclosed",
        "payment_method": None,
        "entity_type": "company",
        "registration_status": None,
    }
    out = fatf_typologies.match_typologies(profile)
    assert isinstance(out, dict)
    assert "matches" in out and "weak" in out


def test_rca_and_crypto_functions_exist_with_expected_signatures():
    # §3b — the names LAYER 11 calls must exist (catch typos that py_compile misses).
    from aria_service.intel import rca_screening, crypto_sanctions
    assert asyncio.iscoroutinefunction(rca_screening.screen_with_relatives)
    assert asyncio.iscoroutinefunction(crypto_sanctions.screen_wallet)


def test_layer11_wires_all_four_new_primitives():
    src = open("aria_service/intel/dd_orchestrator.py", encoding="utf-8").read()
    assert "LAYER 11: DETERMINISTIC PRIMITIVES" in src
    layer11 = src.split("LAYER 11: DETERMINISTIC PRIMITIVES", 1)[1]
    for fn in ("screen_with_relatives", "score_substance",
               "match_typologies", "screen_wallet"):
        assert fn in layer11, f"{fn} not wired into LAYER 11"
    assert 'layer_name = "deterministic_primitives"' in layer11
    assert "report.layers_run.append(layer_name)" in layer11


def test_manual_pipeline_panel_removed_from_ui():
    html = open("public/dd-reports.html", encoding="utf-8").read()
    # The manual tool grid + its JS must be gone (only harmless CSS may remain).
    assert 'id="dd-pipeline-grid"></div>' not in html, "panel div still present"
    assert "const TOOLS = [" not in html, "TOOLS array still present"
    assert "function renderTool(" not in html, "renderTool still present"
