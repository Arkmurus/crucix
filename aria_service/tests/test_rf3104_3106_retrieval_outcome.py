"""R-F3104/R-F3105/R-F3106 — three more sources that read "down" as "clean".

Stage 1b of evidence governance, continuing R-F3101/R-F3102/R-F3103.

R-F3104 — MY OWN GUARD WAS OVER-BROAD. The R-F3103 conformance test flagged any
public function returning a dict without an outcome key. Mapping the four allowlisted
adapters showed three of the flagged functions are PURE SCORERS that perform no I/O
at all — `cert_transparency.detect_shell_pattern(hits)`, `ais_gap_detector
.detect_gaps(positions)`, `gleif.build_profile(attrs, lei)`. A function that never
retrieves anything has no retrieval outcome, and forcing `ok: True` onto it would be
cargo-cult that devalues the field everywhere else. The outcome belongs to the
RETRIEVAL, not to the analysis — and the scorers' real problem was never a missing
key, it was being handed `[]` by a failed retrieval and being unable to tell.

R-F3105 — the three genuine retrieval false-cleans behind them:
  * cert_transparency.search_certs returned [] on HTTP error, non-JSON and any
    exception ("honest fall-through to []"). detect_shell_pattern([]) answers
    {"score": 0, "signals": ["no_certs_found"]} — so crt.sh being DOWN scored the
    subject 0/100, a dead source read as evidence of legitimacy, inside the shell
    detector whose whole job is catching entities with no substance.
  * eccn_lookup._load_data returned {"categories": {}} on read error "so the rest of
    the system keeps running". Every lookup_by_eccn then misses and the item reads as
    NOT export-controlled — a clean export-control screen from a dataset that never
    loaded.
  * gleif.lookup returns None for SEVEN distinct situations: breaker open, query too
    short, HTTP != 200, no records, name did not confirm, and any exception. Three
    mean "GLEIF could not answer", three mean "GLEIF answered: no such entity". A
    down GLEIF therefore renders as "this entity has no LEI".

R-F3106 — and the consumer that turned it into a score.
"""
import pytest

from aria_service.intel.sources import _common
from aria_service.intel.sources import cert_transparency as ct
from aria_service.intel.sources import eccn_lookup
from aria_service.intel.sources import gleif


# ── R-F3105 — cert transparency ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rf3105_crtsh_down_is_unavailable_not_empty(monkeypatch):
    async def _boom(*_a, **_kw):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(ct, "search_certs", _boom, raising=False)
    # drive the REAL wrapper against a failing client instead
    monkeypatch.undo()

    class _Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise RuntimeError("connection reset")
    monkeypatch.setattr(ct.httpx, "AsyncClient", _Boom)

    res = await ct.search_certs_result("acmecorp", use_cache=False)
    assert res["hits"] == []
    assert res["outcome"] == _common.OUTCOME_UNAVAILABLE
    assert _common.answered(res) is False, (
        "R-F3105 REGRESSION: a dead crt.sh reads as an answered search")


@pytest.mark.asyncio
async def test_rf3105_crtsh_http_error_is_unavailable(monkeypatch):
    class _Resp:
        status_code = 503
        def json(self): return []
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()
    monkeypatch.setattr(ct.httpx, "AsyncClient", _Client)

    res = await ct.search_certs_result("acmecorp", use_cache=False)
    assert res["outcome"] == _common.OUTCOME_UNAVAILABLE
    assert "503" in str(res.get("error") or "")


@pytest.mark.asyncio
async def test_rf3105_genuinely_no_certs_is_a_real_negative(monkeypatch):
    """The fix must not turn a working, quiet lookup into an alarm."""
    class _Resp:
        status_code = 200
        def json(self): return []
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()
    monkeypatch.setattr(ct.httpx, "AsyncClient", _Client)

    res = await ct.search_certs_result("acmecorp", use_cache=False)
    assert res["outcome"] == _common.OUTCOME_EMPTY
    assert _common.answered(res) is True, "crt.sh answered; nothing found is meaningful"


@pytest.mark.asyncio
async def test_rf3105_search_certs_list_contract_is_unchanged(monkeypatch):
    """Every existing caller passes no `_outcome` and must be unaffected."""
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise RuntimeError("down")
    monkeypatch.setattr(ct.httpx, "AsyncClient", _Client)
    out = await ct.search_certs("acmecorp", use_cache=False)
    assert out == [], "the list contract must survive exactly"


# ── R-F3105 — ECCN dataset ─────────────────────────────────────────────────
def test_rf3105_unloaded_eccn_dataset_is_not_an_empty_control_list(monkeypatch):
    """A dataset that failed to load must not clear every item for export."""
    from pathlib import Path
    eccn_lookup._load_data.cache_clear()
    monkeypatch.setattr(eccn_lookup, "_DATA_PATH",
                        Path("/nonexistent/eccn_lookup.json"))
    try:
        data = eccn_lookup._load_data()
        assert data["_load_failed"] is True
        assert data["version"] == "unavailable"
        assert eccn_lookup.dataset_available() is False, (
            "R-F3105 REGRESSION: a missing dataset reports as a usable one, so every "
            "ECCN miss reads as 'not export-controlled'")
    finally:
        eccn_lookup._load_data.cache_clear()


def test_rf3105_a_real_dataset_reports_available():
    eccn_lookup._load_data.cache_clear()
    try:
        assert eccn_lookup.dataset_available() is True
        assert eccn_lookup._load_data().get("_load_failed") is False
    finally:
        eccn_lookup._load_data.cache_clear()


# ── R-F3105 — GLEIF ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rf3105_gleif_breaker_open_is_unavailable_not_no_such_entity(monkeypatch):
    class _OpenBreaker:
        def is_open(self): return True
        def record_failure(self, **k): pass
        def record_success(self): pass
    monkeypatch.setattr(gleif, "get_breaker", lambda *a, **k: _OpenBreaker())

    res = await gleif.lookup_with_outcome("Acme Ltd")
    assert res["outcome"] == _common.OUTCOME_UNAVAILABLE
    assert _common.answered(res) is False, (
        "R-F3105 REGRESSION: a tripped breaker reads as 'this entity has no LEI'")
    assert res["record"] is None


@pytest.mark.asyncio
async def test_rf3105_gleif_answered_with_nothing_is_a_real_negative(monkeypatch):
    class _Breaker:
        def is_open(self): return False
        def record_failure(self, **k): pass
        def record_success(self): pass
    class _Resp:
        status_code = 200
        def json(self): return {"data": []}
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()
    monkeypatch.setattr(gleif, "get_breaker", lambda *a, **k: _Breaker())
    monkeypatch.setattr(gleif.httpx, "AsyncClient", _Client)

    res = await gleif.lookup_with_outcome("Acme Ltd")
    assert res["outcome"] == _common.OUTCOME_EMPTY
    assert _common.answered(res) is True, "GLEIF answered — no LEI is a real finding"


@pytest.mark.asyncio
async def test_rf3105_gleif_none_or_dict_contract_is_unchanged(monkeypatch):
    """registry_adapters.lookup_entity depends on this exact contract."""
    class _OpenBreaker:
        def is_open(self): return True
        def record_failure(self, **k): pass
        def record_success(self): pass
    monkeypatch.setattr(gleif, "get_breaker", lambda *a, **k: _OpenBreaker())
    assert await gleif.lookup("Acme Ltd") is None


# ── R-F3106 — the consumer that turned a dead source into a score ──────────
@pytest.mark.asyncio
async def test_rf3106_unreachable_ct_does_not_score_the_subject_clean(monkeypatch):
    """CAPABILITY: drive the real DD extension. Pre-fix this returned
    shell_score 0 / severity NONE for a source that never answered."""
    from aria_service.intel import dd_layer_extensions as ext

    async def _unavailable(*_a, **_kw):
        return _common.stamp_outcome(
            {"source": "crt.sh", "hits": [], "hit_count": 0},
            _common.OUTCOME_UNAVAILABLE, detail="HTTP 503")
    monkeypatch.setattr(ct, "search_certs_result", _unavailable)

    out = await ext.run_cert_transparency_check({"entity": "Acme Corp"}, None)
    assert out is not None
    assert out["severity"] == "UNKNOWN", (
        "R-F3106 REGRESSION: an unreachable CT source is being scored as NONE")
    assert out["shell_score"] is None, "0 is a MEASURED clean score; None is unknown"
    assert "source_unavailable" in out["signals"]
    assert "UNCHECKED, not a clean result" in out["summary"]


@pytest.mark.asyncio
async def test_rf3106_a_working_ct_lookup_still_scores_normally(monkeypatch):
    from aria_service.intel import dd_layer_extensions as ext

    async def _ok(*_a, **_kw):
        return _common.stamp_outcome(
            {"source": "crt.sh", "hits": [], "hit_count": 0}, _common.OUTCOME_EMPTY)
    monkeypatch.setattr(ct, "search_certs_result", _ok)

    out = await ext.run_cert_transparency_check({"entity": "Acme Corp"}, None)
    assert out["severity"] in ("NONE", "LOW", "ELEVATED")
    assert out["shell_score"] is not None, "an answered source must still be scored"


# ── R-F3104 — the guard targets RETRIEVAL, not pure analysis ───────────────
def test_rf3104_pure_scorers_are_exempt_by_rule_not_by_allowlist():
    """A function that performs no I/O has no retrieval outcome to report. Requiring
    `ok: True` on it would be cargo-cult and would devalue the field wherever it does
    mean something."""
    import ast
    import pathlib

    def _is_retrieval(fn) -> bool:
        if isinstance(fn, ast.AsyncFunctionDef):
            return True
        src = ast.dump(fn)
        return any(m in src for m in ("httpx", "http_get_json", "http_get_text", "read_text"))

    tree = ast.parse(pathlib.Path(
        "aria_service/intel/sources/cert_transparency.py").read_text(encoding="utf-8"))
    scorer = next(f for f in tree.body
                  if isinstance(f, ast.FunctionDef) and f.name == "detect_shell_pattern")
    assert _is_retrieval(scorer) is False, (
        "detect_shell_pattern is a pure scorer — the outcome belongs to search_certs")

    retriever = next(f for f in tree.body
                     if isinstance(f, (ast.AsyncFunctionDef,)) and f.name == "search_certs")
    assert _is_retrieval(retriever) is True


# ── R-F3108 — END TO END, through the REAL leaves ──────────────────────────
@pytest.mark.asyncio
async def test_rf3108_real_leaves_failing_makes_search_all_unavailable(monkeypatch):
    """THE TEST THAT WAS MISSING. The R-F3102 test patched the leaves to RAISE, but
    both leaves catch their own exceptions and return [] — so it exercised a mode
    that cannot occur while the live path stayed broken (§23). This drives the REAL
    functions with a failing HTTP client, which is what production does."""
    from aria_service.intel.sources import court_records as cr

    class _Down:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise RuntimeError("network unreachable")
    monkeypatch.setattr(cr.httpx, "AsyncClient", _Down)

    res = await cr.search_all("Acme Widgets Ltd")
    assert res["hits"] == []
    assert _common.answered(res) is False, (
        "R-F3108 REGRESSION: both court sources unreachable still reads as answered")
    assert res["outcome"] == _common.OUTCOME_UNAVAILABLE
    assert {f["source"] for f in res["sources_failed"]} == {"courtlistener", "bailii"}


@pytest.mark.asyncio
async def test_rf3108_real_leaves_answering_empty_is_a_real_negative(monkeypatch):
    """And the inverse must hold, or every quiet search becomes an alarm."""
    from aria_service.intel.sources import court_records as cr

    class _Empty:
        status_code = 200
        text = "<rss></rss>"
        def json(self): return {"results": []}
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Empty()
    monkeypatch.setattr(cr.httpx, "AsyncClient", _Client)

    res = await cr.search_all("Acme Widgets Ltd")
    assert res["outcome"] == _common.OUTCOME_EMPTY
    assert _common.answered(res) is True
    assert res["sources_failed"] == []


# ── R-F3113 — the failure must reach the BRAIN, not just the caller ────────
def test_rf3113_a_non_answering_source_records_a_gap(monkeypatch):
    """§21a: a path is wired only if BOTH branches reach the brain. R-F3101-R-F3106
    made a dead source legible to the CALLER and stopped there, so ARIA could not
    know a source had gone dark and the self-heal loop had nothing to act on."""
    seen = []
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure",
                        lambda **kw: seen.append(kw))

    _common.stamp_outcome({"source": "crt.sh", "hits": []},
                          _common.OUTCOME_UNAVAILABLE, detail="HTTP 503")
    assert seen, "R-F3113 REGRESSION: a dead source no longer reaches the brain"
    assert seen[0]["gap_type"] == "source_failure", "must be a REGISTERED gap type"
    assert "crt.sh" in seen[0]["module"]
    assert "did not answer" in seen[0]["detail"]


def test_rf3113_an_answered_source_does_not_record_a_gap(monkeypatch):
    seen = []
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: seen.append(kw))
    _common.stamp_outcome({"source": "crt.sh", "hits": []}, _common.OUTCOME_EMPTY)
    _common.stamp_outcome({"source": "crt.sh", "hits": [1]}, _common.OUTCOME_OK)
    assert seen == [], "an answered source is not a gap"


def test_rf3113_skipped_is_not_wired(monkeypatch):
    """A deliberate non-attempt (optional credential absent, query too short) is
    already a DD data_gap. Wiring it would flood the ledger with a standing config
    fact and drown the signal this exists to carry."""
    seen = []
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: seen.append(kw))
    _common.stamp_outcome({"source": "acled", "hits": []}, _common.OUTCOME_SKIPPED)
    assert seen == []


def test_rf3113_wiring_never_breaks_the_retrieval(monkeypatch):
    """An observability wire must never take down the thing it observes."""
    import aria_service.intel.engine_wiring as ew
    def _boom(**kw):
        raise RuntimeError("brain down")
    monkeypatch.setattr(ew, "wire_failure", _boom)
    out = _common.stamp_outcome({"source": "x", "hits": []},
                                _common.OUTCOME_UNAVAILABLE)
    assert out["outcome"] == _common.OUTCOME_UNAVAILABLE
