"""R-F2501 — a GB DD must SURFACE the root cause when Companies House has no API key
(the CH REST API 401s without it → zero registry data → INSUFFICIENT). Instead of a
vague "registry incomplete", the report carries an actionable notice naming the exact
operator action. companies_house.missing_key_gap() is the single source of truth the
DD identity layer appends.
"""
import aria_service.intel.companies_house as ch


def test_missing_key_gap_actionable_when_no_key():
    saved = ch._API_KEY
    ch._API_KEY = ""              # simulate the live state (no key set)
    try:
        gap = ch.missing_key_gap()
    finally:
        ch._API_KEY = saved
    assert gap is not None
    assert "COMPANIES_HOUSE_API_KEY" in gap
    assert "developer.company-information.service.gov.uk" in gap
    assert "directors" in gap.lower() and "incorporation" in gap.lower()


def test_no_gap_when_key_present():
    saved = ch._API_KEY
    ch._API_KEY = "test-key-123"
    try:
        assert ch.missing_key_gap() is None
    finally:
        ch._API_KEY = saved


def test_headers_gate_on_key():
    # the root cause: no key -> no Authorization -> CH 401s; key -> Basic auth
    saved = ch._API_KEY
    try:
        ch._API_KEY = ""
        assert "Authorization" not in ch._headers()
        ch._API_KEY = "abc"
        assert ch._headers().get("Authorization", "").startswith("Basic ")
    finally:
        ch._API_KEY = saved


if __name__ == "__main__":
    test_missing_key_gap_actionable_when_no_key(); print("PASS missing_key_gap_actionable")
    test_no_gap_when_key_present(); print("PASS no_gap_when_key_present")
    test_headers_gate_on_key(); print("PASS headers_gate_on_key")
    print("ALL PASS")
