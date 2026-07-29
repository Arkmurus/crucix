"""R-F2441 — FATF typology library ingested into the searchable knowledge corpus.

Drives the REAL fatf_typologies.ingest_to_corpus() with knowledge.store_fact
mocked, proving every encoded typology is stored as a searchable fact whose text
carries 'FATF' + a 'typolog' stem (discoverable + matches the R-F2439 coverage
domain token), with a stable per-id source (idempotent re-ingest), CONFIRMED
provenance, and the real FATF description as content.

Run: python aria_service/tests/test_rf2441_fatf_ingest.py
"""
import asyncio


def test_fatf_ingest():
    from aria_service.intel import fatf_typologies as ft
    from aria_service.intel import knowledge as kb
    from aria_service.intel import engine_wiring as ew
    from aria_service.intel import coverage_heatmap as ch

    captured = []

    async def fake_store(topic, content, source="user", confidence="CONFIRMED", **kw):
        captured.append({"topic": topic, "content": content, "source": source,
                         "confidence": confidence, **kw})
        return {"action": "stored"}

    # R-F3449 — these three were assigned PERMANENTLY, with no restore. This file sorts
    # before test_rf2620_*, test_rf3109_*, test_rf772_* and test_store_fact_skip_rag.py, so
    # in full-suite order every one of those then ran against a mocked `store_fact` and
    # no-op `wire_success`/`wire_failure` — which is why they passed standalone and failed
    # in-suite. `wire_*` are the §21 brain-wiring functions used by nearly every module, so
    # silencing them for the remainder of the session is the widest possible leak.
    #
    # try/finally rather than the `monkeypatch` fixture on purpose: this file keeps a
    # `__main__` block that calls the test directly, and a fixture parameter would break
    # running it as a script.
    _orig = (kb.store_fact, ew.wire_success, ew.wire_failure)
    kb.store_fact = fake_store
    ew.wire_success = lambda **k: None
    ew.wire_failure = lambda **k: None
    try:
        res = asyncio.run(ft.ingest_to_corpus())
    finally:
        kb.store_fact, ew.wire_success, ew.wire_failure = _orig

    fails = []
    ok = lambda c, m: (print(f"  {'✓' if c else '✗'} {m}"), fails.append(m) if not c else None)

    size = res["library_size"]
    ok(size >= 5, f"library is non-trivial ({size} typologies)")
    ok(res["ingested"] == size and res["errors"] == 0, f"all {size} ingested, 0 errors")
    ok(len(captured) == size, "store_fact called once per typology")

    seen_sources = set()
    for f in captured:
        tl = f["topic"].lower()
        ok_line = ("fatf" in tl and "typolog" in tl
                   and "FATF money-laundering typology" in f["content"]
                   and f["source"].startswith("fatf_typologies:")
                   and f.get("confidence") == "CONFIRMED"
                   and f.get("fact_type") == "fatf_typology")
        if not ok_line:
            ok(False, f"malformed fact: {f['topic'][:50]}")
            break
        seen_sources.add(f["source"])
    else:
        ok(True, "every fact: FATF+typolog topic, real description, CONFIRMED, fact_type=fatf_typology")

    ok(len(seen_sources) == size, "stable per-id sources (idempotent — no source collisions)")

    # the fact's text carries the coverage domain tokens (would count under
    # fatf_ml_typologies IF a jurisdiction were present — global facts have none,
    # which is the honest reason the grid stays per-jurisdiction-empty).
    sample = captured[0]
    txt = ch._fact_text({"topic": sample["topic"], "content": sample["content"],
                         "entity": sample.get("entity_name", "")})
    ok(all(tok in txt for tok in ch._domain_tokens("fatf_ml_typologies")),
       "fact text carries the fatf_ml_typologies domain tokens (discoverable)")

    assert not fails, f"{len(fails)} failure(s)"


if __name__ == "__main__":
    test_fatf_ingest()
    print("PASS")
