"""Regression test for F90 — hypothesis store rejects null/empty/stub text.

Live evidence 2026-04-29 15:04:27 — autonomous research cycle running:
  GET https://news.google.com/rss/search?q=null+evidence+2026

A hypothesis with text 'null' (literal string, almost certainly from
a JSON-null leaking through `str()` upstream) made it into the
validation backlog. validate_hypothesis appended " evidence 2026"
and ran a useless web search every cycle.

After the fix:
  - _is_valid_hypothesis_text rejects None, empty, 'null', 'none',
    'undefined', 'nan', 'n/a', and any text shorter than 10 chars
  - _load_hypotheses filters bad entries on read AND persists the
    cleaned list back so existing poison data drops out
  - _save_hypotheses applies the same filter on write
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock


def test_is_valid_hypothesis_text_rejects_null_strings():
    from aria_service.intel.researcher import _is_valid_hypothesis_text

    for bad in ("null", "None", "undefined", "NaN", "n/a", "NULL", "  ",
                "", "x", "short", None, 42, [], {}):
        assert not _is_valid_hypothesis_text(bad), (
            f"_is_valid_hypothesis_text({bad!r}) should be False"
        )


def test_is_valid_hypothesis_text_accepts_real_claims():
    from aria_service.intel.researcher import _is_valid_hypothesis_text

    real_claims = [
        "Pakistan and Libya signed a US$4.6B defence pact in April 2026",
        "MQ-25A Stingray completed first carrier landing trials",
        "EU export licence ECJU processing time exceeds 90 days for tier-1 items",
    ]
    for claim in real_claims:
        assert _is_valid_hypothesis_text(claim), (
            f"_is_valid_hypothesis_text({claim!r}) should be True"
        )


def test_load_hypotheses_filters_bad_entries():
    """Mixed payload: 2 bad + 2 good entries. Only the good ones come back."""
    import aria_service.intel.researcher as researcher

    bad_payload = [
        {"hypothesis": "null", "status": "OPEN"},
        {"hypothesis": "Real claim about defence procurement in Angola", "status": "OPEN"},
        {"hypothesis": "", "status": "OPEN"},
        {"hypothesis": "MQ-25A first flight signals naval aviation milestone", "status": "OPEN"},
    ]

    fake_get = AsyncMock(return_value=bad_payload)
    fake_set = AsyncMock()
    with patch("aria_service.intel.researcher.rs.get_json", fake_get), \
         patch("aria_service.intel.researcher.rs.set_json", fake_set):
        result = asyncio.run(researcher._load_hypotheses())

    assert len(result) == 2
    assert all(_h["hypothesis"] for _h in result)
    assert "null" not in [h["hypothesis"] for h in result]
    # Cleanup persisted back to Redis
    fake_set.assert_called_once()


def test_save_hypotheses_filters_bad_entries():
    """If something tries to persist bad entries, they get dropped."""
    import aria_service.intel.researcher as researcher

    payload = [
        {"hypothesis": "Strong claim about NATO procurement 2026 trends", "status": "OPEN"},
        {"hypothesis": "null", "status": "OPEN"},
        {"hypothesis": None, "status": "OPEN"},
    ]

    fake_set = AsyncMock()
    with patch("aria_service.intel.researcher.rs.set_json", fake_set):
        asyncio.run(researcher._save_hypotheses(payload))

    fake_set.assert_called_once()
    saved = fake_set.call_args.args[1]  # set_json(KEY, value)
    assert len(saved) == 1
    assert saved[0]["hypothesis"].startswith("Strong claim")
