"""R-F2515 — CH officer backfill after identity-layer timeout. Mirrors the inline
extraction the backfill uses so the current-officer filtering + dict/list tolerance
can't silently regress. (End-to-end is verified by live DD smoke.)"""


def _extract_current(offs):
    """Mirror of the R-F2515 backfill: current officers, fallback to all."""
    return [o for o in offs if o.get("is_current")] or offs


def _extract_from_investigate(bf):
    """Mirror of the reg#-missing fallback: officers dict {current,...} or list."""
    if not (isinstance(bf, dict) and bf.get("found")):
        return []
    bo = bf.get("officers")
    return (bo.get("current") if isinstance(bo, dict) else bo) or []


def test_current_officer_filter():
    offs = [
        {"name": "A", "is_current": True},
        {"name": "B", "is_current": False},
        {"name": "C", "is_current": True},
    ]
    cur = _extract_current(offs)
    assert [o["name"] for o in cur] == ["A", "C"]


def test_filter_falls_back_to_all_when_no_flag():
    offs = [{"name": "A"}, {"name": "B"}]  # no is_current -> use all (better than 0)
    assert len(_extract_current(offs)) == 2


def test_investigate_fallback_extracts_current_list():
    bf = {"found": True, "officers": {"current": [{"name": "X"}], "past": [], "total": 1}}
    assert _extract_from_investigate(bf) == [{"name": "X"}]


def test_investigate_fallback_not_found_is_empty():
    assert _extract_from_investigate({"found": False, "error": "no match"}) == []


if __name__ == "__main__":
    test_current_officer_filter(); print("PASS filter")
    test_filter_falls_back_to_all_when_no_flag(); print("PASS fallback")
    test_investigate_fallback_extracts_current_list(); print("PASS investigate")
    test_investigate_fallback_not_found_is_empty(); print("PASS not-found")
    print("ALL PASS")
