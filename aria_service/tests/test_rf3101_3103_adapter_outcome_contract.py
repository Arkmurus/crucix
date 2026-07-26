"""R-F3101/R-F3102/R-F3103 — an adapter that cannot express failure cannot be governed.

FOUND while scoping evidence governance (2026-07-26). The gap assessment's item 3 is
"adapter enforcement": no source activity without a recorded outcome. It cannot be
built on the adapters as they are. An AST scan of `aria_service/intel/sources/` found
FIVE adapters whose public entry points return dicts carrying NO outcome field:

    court_records.search_all        -> {entity, hits, us_count, uk_count, sources}
    eccn_lookup.lookup_by_eccn      -> {version, categories}
    cert_transparency.detect_*      -> {score, signals}
    gleif.build_profile / lookup
    ais_gap_detector.detect_gaps

`EvidenceRecord.retrieval_outcome` is REQUIRED. An adapter that cannot say whether it
succeeded has no honest value to put there, so wiring it to the evidence contract
would mean inventing one — the exact failure the contract exists to prevent. Only 7
of 15 adapters route through `_common`, so `_common` was not yet a choke point; the
R-F3103 guard is what makes it one.

AND IT WAS ALREADY A LIVE FALSE CLEAN (R-F3102). `court_records.search_all` did
`if isinstance(us_hits, Exception): us_hits = []` for BOTH sources and its docstring
called the result "an honest zero-result". Downstream `_emit_court_record_findings`
returned [] on empty hits — so CourtListener AND BAILII both being down produced zero
findings and zero data gaps. The report showed no litigation and no disclosure that
litigation was never checked.
"""
import ast
import pathlib

import pytest

from aria_service.intel.sources import _common
from aria_service.intel.sources import court_records

_SOURCES = pathlib.Path("aria_service/intel/sources")


# ── R-F3101 — the outcome vocabulary ───────────────────────────────────────
def test_rf3101_answered_is_the_question_every_caller_must_ask():
    assert _common.answered({"outcome": _common.OUTCOME_OK}) is True
    assert _common.answered({"outcome": _common.OUTCOME_EMPTY}) is True, (
        "a source that answered with nothing DID answer — that is a real negative")
    for bad in (_common.OUTCOME_UNAVAILABLE, _common.OUTCOME_TIMEOUT,
                _common.OUTCOME_ERROR, _common.OUTCOME_SKIPPED):
        assert _common.answered({"outcome": bad}) is False, (
            f"{bad} means UNKNOWN — a caller must never read it as CLEAR")


def test_rf3101_empty_and_unavailable_are_different_claims():
    """The whole point: 'we looked and found nothing' vs 'we could not look'."""
    assert _common.source_outcome({"ok": True, "hits": []}) == _common.OUTCOME_EMPTY
    assert _common.source_outcome({"ok": True, "hits": [{"a": 1}]}) == _common.OUTCOME_OK
    assert _common.source_outcome({"ok": False, "error": "boom"}) == _common.OUTCOME_ERROR
    assert _common.source_outcome(
        {"ok": True, "hits": [], "source_unavailable": True}) == _common.OUTCOME_UNAVAILABLE


def test_rf3101_an_unknown_shape_fails_CLOSED():
    """A result carrying no outcome signal must never read as a successful screen —
    failing closed here is what makes the R-F3103 guard meaningful."""
    assert _common.source_outcome({"entity": "x", "hits": []}) == _common.OUTCOME_ERROR
    assert _common.answered({"entity": "x", "hits": []}) is False
    assert _common.answered(None) is False
    assert _common.answered("nonsense") is False


def test_rf3101_stale_cache_counts_as_not_answering():
    """R-F2167 established a stale screen is not a current one; keep that meaning."""
    assert _common.source_outcome({"ok": True, "hits": [], "stale": True}) \
        == _common.OUTCOME_UNAVAILABLE


def test_rf3101_stamp_is_additive_and_never_clobbers():
    r = {"hits": [], "existing": "kept"}
    _common.stamp_outcome(r, _common.OUTCOME_UNAVAILABLE, detail="feed down")
    assert r["existing"] == "kept", "stamping must not disturb existing keys"
    assert r["outcome"] == _common.OUTCOME_UNAVAILABLE
    assert r["ok"] is False and "feed down" in r["error"]
    # an explicit ok already set by the adapter wins
    r2 = {"ok": True, "error": "prior"}
    _common.stamp_outcome(r2, _common.OUTCOME_ERROR, detail="new")
    assert r2["ok"] is True and r2["error"] == "prior"


# ── R-F3102 — the live false clean ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_rf3102_both_courts_down_is_UNAVAILABLE_not_empty(monkeypatch):
    """THE LIVE DEFECT: both sources failing used to yield a clean litigation file."""
    async def _boom(*_a, **_kw):
        raise RuntimeError("upstream 503")
    monkeypatch.setattr(court_records, "search_us_courts", _boom)
    monkeypatch.setattr(court_records, "search_uk_courts", _boom)

    res = await court_records.search_all("Acme Ltd")
    assert res["hits"] == []
    assert _common.answered(res) is False, (
        "R-F3102 REGRESSION: a failed court search reads as an answered one")
    assert res["outcome"] == _common.OUTCOME_UNAVAILABLE
    assert {f["source"] for f in res["sources_failed"]} == {"courtlistener", "bailii"}
    assert res["sources_answered"] == []


@pytest.mark.asyncio
async def test_rf3102_one_source_down_still_returns_the_other(monkeypatch):
    """The graceful-degradation behaviour was always right — keep it, but disclose."""
    async def _boom(*_a, **_kw):
        raise RuntimeError("down")
    async def _ok(*_a, **_kw):
        return [{"jurisdiction": "UK", "title": "Acme v Beta"}]
    monkeypatch.setattr(court_records, "search_us_courts", _boom)
    monkeypatch.setattr(court_records, "search_uk_courts", _ok)

    res = await court_records.search_all("Acme Ltd")
    assert len(res["hits"]) == 1, "the surviving source's hits must still come through"
    assert res["sources_answered"] == ["bailii"]
    assert res["sources_failed"][0]["source"] == "courtlistener"


@pytest.mark.asyncio
async def test_rf3102_a_genuine_clean_search_is_still_clean(monkeypatch):
    """The fix must not turn every quiet search into an alarm."""
    async def _empty(*_a, **_kw):
        return []
    monkeypatch.setattr(court_records, "search_us_courts", _empty)
    monkeypatch.setattr(court_records, "search_uk_courts", _empty)

    res = await court_records.search_all("Acme Ltd")
    assert res["outcome"] == _common.OUTCOME_EMPTY
    assert _common.answered(res) is True, "both sources answered; nothing found is a real negative"
    assert res["sources_failed"] == []


def test_rf3102_the_orchestrator_discloses_the_failed_search():
    """CAPABILITY: the renderer that returned [] on empty hits must now speak."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings
    out = _emit_court_record_findings({
        "hits": [], "sources_failed": [{"source": "courtlistener", "error": "503"},
                                       {"source": "bailii", "error": "timeout"}],
    })
    assert len(out) == 1, "a search that did not run must produce a disclosure"
    assert "did NOT complete" in out[0].title
    assert "UNCHECKED, not clear" in out[0].detail
    assert out[0].confidence == "UNVERIFIED"


def test_rf3102_a_genuinely_empty_search_still_emits_nothing():
    """No alarm when both sources answered and found nothing."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings
    assert _emit_court_record_findings({"hits": [], "sources_failed": []}) == []
    assert _emit_court_record_findings({"hits": []}) == []


# ── R-F3103 — the guard that makes it permanent ────────────────────────────
#: R-F3104 — markers of a function that actually RETRIEVES something. A retrieval can
#: fail, so it owes the caller an outcome; a pure computation cannot, and does not.
_IO_MARKERS = ("httpx", "http_get_json", "http_get_text", "read_text", "requests",
               "aiohttp", "urlopen")


def _is_retrieval(fn) -> bool:
    """R-F3104 — does this function perform I/O?

    The R-F3103 guard flagged ANY public dict-returning function, which swept in pure
    scorers — `detect_shell_pattern(hits)`, `detect_gaps(positions)`,
    `build_profile(attrs, lei)`. Those never retrieve anything, so they have no
    retrieval outcome, and stamping `ok: True` on them would be cargo-cult that
    devalues the field wherever it does carry meaning. The outcome belongs to the
    RETRIEVAL; the scorers' real defect was being handed `[]` by a failed retrieval
    and being unable to tell (fixed at the source in R-F3105/R-F3106)."""
    if isinstance(fn, ast.AsyncFunctionDef):
        return True
    dumped = ast.dump(fn)
    return any(m in dumped for m in _IO_MARKERS)


def _public_dict_returns_without_outcome(path: pathlib.Path) -> list[str]:
    """RETRIEVAL entry points returning a dict literal that carries no outcome key."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    outcome_keys = {"ok", "outcome", "error"}
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name.startswith("_") or not _is_retrieval(fn):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
                if keys and not (keys & outcome_keys):
                    offenders.append(f"{path.name}::{fn.name}:{node.lineno}")
    return offenders


def test_rf3103_court_records_now_conforms():
    """The adapter this whole thread started from."""
    assert _public_dict_returns_without_outcome(_SOURCES / "court_records.py") == []


def test_rf3103_every_common_based_adapter_conforms():
    """The seven that already route through _common must never regress."""
    for name in ("acled", "fcdo_sanctions", "ofac_sdn", "sec_edgar",
                 "un_sc_sanctions", "worldbank_debarred", "worldbank_documents"):
        assert _public_dict_returns_without_outcome(_SOURCES / f"{name}.py") == [], name


def test_rf3103_NO_adapter_may_ship_a_retrieval_without_an_outcome():
    """THE ENFORCEMENT, and there is NO allowlist any more.

    R-F3103 shipped with four names exempted. R-F3104 narrowed the rule to RETRIEVAL
    functions and R-F3105 fixed the three genuine offenders behind those names, so
    the exemption list is now EMPTY — which is the only state in which a guard like
    this is worth having. A new adapter, or a new retrieval entry point in an
    existing one, fails the build.

    This is what turns _common into a real choke point: only 7 of 15 adapters route
    through it, so nothing structural forced conformance before now."""
    offenders = {}
    for p in sorted(_SOURCES.glob("*.py")):
        if p.name in ("__init__.py", "_common.py"):
            continue
        bad = _public_dict_returns_without_outcome(p)
        if bad:
            offenders[p.name] = bad
    assert offenders == {}, (
        "these retrievals return a result a caller cannot judge — a failed fetch will "
        "be read as an empty screen (the false clean this system exists to prevent). "
        "Give every public retrieval return an `ok`/`outcome` via "
        "_common.stamp_outcome:\n"
        + "\n".join(f"  {v[0]}" for v in offenders.values()))


def test_rf3104_pure_computation_is_exempt_by_RULE_not_by_name():
    """The exemption must be structural, so it cannot rot. A scorer is exempt because
    it performs no I/O — not because someone listed it."""
    tree = ast.parse((_SOURCES / "cert_transparency.py").read_text(encoding="utf-8"))
    by_name = {f.name: f for f in tree.body
               if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert _is_retrieval(by_name["detect_shell_pattern"]) is False
    assert _is_retrieval(by_name["search_certs"]) is True
