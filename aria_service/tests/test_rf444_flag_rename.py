"""R-F444 — rename `_has_legal_name_corroboration` → `_has_caller_supplied_aliases`.

The R-F434 verifier flagged that `_has_legal_name_corroboration`
overstates the guarantee: the flag was True whenever ANY
known_aliases were passed, including the R-F312 brandified-re-fire
path which passes the raw hostname as an alias (not a verified
legal name).

R-F444 renames the flag to the honest, narrower description:
`_has_caller_supplied_aliases`. The old key is preserved as a
DEPRECATED alias for one release so any in-flight consumer keeps
working. Both keys appear on:
  - per-match dicts (set by sanctions.screen_with_aliases)
  - the screen_with_aliases result dict
The classify_match cap reads either flag (renamed first, deprecated
second) so old + new tags both correctly trigger the AMBER cap.
"""
from __future__ import annotations

import asyncio


# ───────────────────────── Public result dict has both keys ─────────────────


def test_rf444_screen_result_exposes_both_flag_keys(monkeypatch):
    """Top-level result dict from screen_with_aliases carries the new
    canonical key AND the deprecated alias for one release."""
    from aria_service.intel import sanctions

    async def _fake_fuzzy_screen(name, *, threshold=0.78):
        return {"name": name, "matches": [], "top_score": 0, "blocked": False}
    monkeypatch.setattr(sanctions, "fuzzy_screen", _fake_fuzzy_screen)

    result = asyncio.run(sanctions.screen_with_aliases(
        "ngast.com",
        known_aliases=["Nebraska Gas Turbine Inc"],
    ))
    assert result["has_caller_supplied_aliases"] is True
    assert result["has_legal_name_corroboration"] is True


def test_rf444_per_match_carries_both_flag_keys(monkeypatch):
    """The flag on each surfaced match also carries both keys."""
    from aria_service.intel import sanctions

    async def _fake_fuzzy_screen(name, *, threshold=0.78):
        return {
            "name": name,
            "matches": [{
                "name": "Some Match",
                "score": 0.9,
                "topics": ["info"],
                "lists": ["info_list"],
                "list": "info_list",
            }],
            "top_score": 0.9,
            "blocked": False,
        }
    monkeypatch.setattr(sanctions, "fuzzy_screen", _fake_fuzzy_screen)

    result = asyncio.run(sanctions.screen_with_aliases(
        "ngast.com",  # hostname triggers brandification → origin tagging
    ))
    assert result["matches"], "expected at least one match"
    m = result["matches"][0]
    assert "_has_caller_supplied_aliases" in m
    assert "_has_legal_name_corroboration" in m
    # Empty aliases at entry → both False
    assert m["_has_caller_supplied_aliases"] is False
    assert m["_has_legal_name_corroboration"] is False


# ───────────────────────── Classifier reads either key ──────────────────────


def test_rf444_classifier_caps_on_deprecated_key_for_backcompat():
    """A match carrying ONLY the deprecated `_has_legal_name_corroboration`
    key (e.g. a stale cached match from a pre-rename code path) must
    still trigger the R-F434 cap correctly. The classifier reads the
    new key first, falls back to the deprecated one."""
    from aria_service.intel._sanctions_classify import classify_match
    m = {
        "name": "Oscar Noe Medina Gonzalez",
        "score": 0.95,
        "topics": ["sanction"],
        "lists": ["us_ofac_sdn"],
        "_from_brandified_hostname": True,
        "_brandified_stem": "ngast",
        # Old key only — simulate pre-rename in-flight match dict
        "_has_legal_name_corroboration": False,
    }
    severity = classify_match(m, query_name="")
    assert severity == "amber", (
        f"R-F444 backward-compat broken: classifier must still cap "
        f"when only the deprecated key is present. Got severity={severity!r}"
    )


def test_rf444_classifier_reads_new_key_when_both_keys_present():
    """When BOTH keys are present (the canonical case post-R-F444), the
    cap logic prefers the new key. Behaviour is identical when both
    keys carry the same value (they always do today)."""
    from aria_service.intel._sanctions_classify import classify_match
    m = {
        "name": "Sanctioned Real Co Ltd",
        "score": 0.97,
        "topics": ["sanction"],
        "lists": ["us_ofac_sdn"],
        "_from_brandified_hostname": True,
        "_has_caller_supplied_aliases": True,
        "_has_legal_name_corroboration": True,
    }
    severity = classify_match(m, query_name="Sanctioned Real Co Ltd")
    assert severity == "hard_stop", (
        f"R-F444: corroborated match must preserve hard_stop. Got {severity!r}"
    )


def test_rf444_classifier_caps_when_only_new_key_present():
    """A match carrying ONLY the new `_has_caller_supplied_aliases` key
    must trigger the cap correctly (no leaked dependence on the
    deprecated key)."""
    from aria_service.intel._sanctions_classify import classify_match
    m = {
        "name": "Shazand Petrochemical Co",
        "score": 0.88,
        "topics": ["sanction"],
        "lists": ["us_ofac_sdn"],
        "_from_brandified_hostname": True,
        "_brandified_stem": "armesavn",
        "_has_caller_supplied_aliases": False,  # new key only
    }
    severity = classify_match(m, query_name="")
    assert severity == "amber"
