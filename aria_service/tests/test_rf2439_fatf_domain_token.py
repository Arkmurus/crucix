"""R-F2439 — coverage_heatmap fatf_ml_typologies domain-token fix.

The snake_case split gave ["fatf","typologies"] and _matches_cell requires ALL
tokens, so a fact had to literally contain the PLURAL "typologies" — even
"FATF typology" (singular) failed → the cell showed a false 0. The override
uses the stem "typolog" (matches typology/typologies) while staying
FATF-specific (still requires "fatf" too), so it corrects an under-report
WITHOUT counting generic money-laundering facts (no metric inflation).

Run: python aria_service/tests/test_rf2439_fatf_domain_token.py
"""
from aria_service.intel import coverage_heatmap as ch


def test_fatf_domain_token():
    fails = []
    ok = lambda c, m: (print(f"  {'✓' if c else '✗'} {m}"), fails.append(m) if not c else None)

    ok(ch._domain_tokens("fatf_ml_typologies") == ["fatf", "typolog"], "override → [fatf, typolog]")
    ok(ch._domain_tokens("sanctions_screening") == ["sanctions", "screening"], "other domains unchanged")
    ok(ch._matches_cell("us fatf professional money laundering typology report", ["fatf", "typolog"], ["us"]) is True,
       "US FATF typology (singular) → matches")
    ok(ch._matches_cell("fatf typologies of trade-based laundering in the uk", ["fatf", "typolog"], ["uk"]) is True,
       "UK FATF typologies (plural) → matches")
    ok(ch._matches_cell("us money laundering enforcement action", ["fatf", "typolog"], ["us"]) is False,
       "generic ML w/o fatf/typolog → NOT counted (no inflation)")
    ok(ch._matches_cell("us fatf professional money laundering typology", ["fatf", "typologies"], ["us"]) is False,
       "proof: old plural-only token missed the singular fact (the bug)")

    assert not fails, f"{len(fails)} failure(s)"


if __name__ == "__main__":
    test_fatf_domain_token()
    print("PASS")
