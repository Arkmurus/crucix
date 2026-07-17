"""R-F2700 — the DD relationship-graph WRITER (Grade-A Phase 1, gap #2, step 2/2).

`add_cross_reference` had a full read side — `get_cross_references` /
`get_related_cases` → `GET /dd/vault/case/{id}` → the "Cross-References" and "Related
Cases" sections of dd-reports.html — but NO prod writer (called only from tests). The
table was empty, so both UI sections rendered blank on every report. This wires the
writer at `_persist_report`, right after `record_case`.

Step 1 (R-F2697) had to land first: it put `user_id` in the table AND in the UNIQUE
key. Wiring this writer before that ACL would have filled a table whose rows were
cross-tenant readable and whose INSERT OR REPLACE let tenants delete each other's edges.

THE ANCHORING PROBLEM this pins:
network_walker emits cross-links as FREE-TEXT names (`{"name", "via_director",
"jurisdiction", "registration_number"}`), but `dd_cross_references` keys on
`canonical_entity_id` and `get_related_cases` looks cases up by that id. A writer that
stored raw names would produce edges matching no case — "Related Cases" empty forever,
and un-anchored junk in the UI. So targets resolve through `dd_versioning.canonical_entity_id`
(the same id a DD of that company produces), and anything too thin to canonicalise is
DROPPED rather than guessed.
"""
import pytest

from aria_service.intel import dd_orchestrator as _do
from aria_service.intel import dd_vault as _dv


@pytest.fixture()
def vault(tmp_path):
    """A REAL DDVault on a temp DB — real SQL, real UNIQUE key, no mocks."""
    v = _dv.DDVault(db_path=str(tmp_path / "vault.db"))
    yield v
    v.close()


@pytest.fixture(autouse=True)
def _enable_source(request, monkeypatch):
    """R-F2703 — the name-match source ships DISABLED (it is not a relationship).

    These tests cover the source-AGNOSTIC machinery (anchoring, the placeholder guard,
    pair normalisation, the cap, fail-closed attribution, the ACL) which must stay correct
    and ready for the REAL edge (a corporate PSC -> `controlled_by`). So they enable the
    flag explicitly. `test_name_match_source_is_disabled_by_default` asserts the shipped
    default — delete that one and this fixture becomes a lie.
    """
    if "shipped_default" in request.node.name:
        return          # that test asserts the REAL constant — never patch it
    monkeypatch.setattr(_do, "_XREF_NAME_MATCH_ENABLED", True)


def _write(report, vault):
    """Drive the writer helper against a real vault (real SQL, no mocks).

    An earlier draft justified this by claiming `_persist_report`'s vault section is
    "unreachable in a unit test". That was FALSE — Pass 2 disproved it: the outer
    `except` only logs, so execution continues and the section runs even with no live
    state_store. The call site is therefore covered by a real integration test below
    (`test_persist_report_actually_calls_the_writer`), not excused.
    """
    return _do._write_relationship_edges(report, vault)


def _report(*, user_id="tenant_a", cross_linked=None):
    """A minimal report shaped like the real one at the _persist_report call site."""
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    r.run_id = "run_test_1"
    r.canonical_entity_id = "company:GB:11111111"
    r.identity.entity_name = "Acme Ltd"
    r.identity.entity_type = "company"
    r.identity.jurisdiction_iso2 = "GB"
    r.user_id = user_id
    r.network.cross_linked_entities = cross_linked if cross_linked is not None else [
        {"name": "Beta Systems Ltd", "via_director": "Jane Roe",
         "jurisdiction": "GB", "registration_number": "22222222"},
    ]
    return r


def _edges(vault):
    return vault.get_cross_references("company:GB:11111111")


# ── THE capability test: the graph is no longer empty ────────────────────────

def test_persist_report_writes_the_relationship_edge(vault):
    """PRE-FIX: nothing ever wrote this table, so the UI section was always blank."""
    _write(_report(), vault)

    rows = _edges(vault)
    assert len(rows) == 1, "a cross-linked entity must produce a graph edge"
    e = rows[0]
    assert e["source_entity"] == "company:GB:11111111"
    assert e["target_entity"] == "company:GB:22222222", (
        "the target must be the SAME canonical id a DD of Beta Systems would produce — "
        "a free-text name could never match a case"
    )
    assert e["relationship"] == _do._XREF_RELATION == "name_match_to_officer", (
        "Pass 2 ship-blocker: the upstream link is a company-NAME search on the officer's "
        "name (network_walker:54 -> companies_house.search_companies), NOT an appointments "
        "lookup. Labelling it 'shared_director' would assert a relationship that does not "
        "exist — a fabrication in a DD product."
    )
    assert "Jane Roe" in e["finding_summary"]
    assert "NOT a verified appointment" in e["finding_summary"], (
        "the summary must say what the evidence supports, not what we wish it were"
    )
    assert e["user_id"] == "tenant_a", "R-F2697: an unattributed edge is invisible to its own tenant"


def test_edge_lights_up_related_cases_once_the_target_has_a_case(vault):
    """The whole point: this is what the 'Related Cases' UI section renders."""
    vault.record_case(canonical_entity_id="company:GB:22222222",
                      entity_name="Beta Systems Ltd", risk_score=42.0, risk_level="MEDIUM")
    _write(_report(), vault)

    related = vault.get_related_cases(
        "company:GB:11111111", user_id="tenant_a",
        visible_entity_ids={"company:GB:11111111", "company:GB:22222222"},
    )
    assert [c["canonical_entity_id"] for c in related] == ["company:GB:22222222"]


# ── Fail-closed: never guess, never write unattributed ───────────────────────

def test_unanchorable_name_is_dropped_not_guessed(vault):
    """No jurisdiction / no registration number → no anchor → drop, never guess.

    This caught a real bug in the first cut: `canonical_entity_id` does NOT return None
    here — it falls back to `company:??:<name_hash>`. A real DD of the same company
    resolves to `company:GB:<regnum>`, so a ?? id can NEVER match a case: it would be
    permanent junk in the graph and in the UI, while looking like a working edge.
    """
    written, dropped = _write(_report(cross_linked=[
        {"name": "Some Vague Trading Name", "via_director": "Jane Roe"},
    ]), vault)
    assert (written, dropped) == (0, 1)
    assert _edges(vault) == [], "an id must never be invented to force an edge"


def test_partial_anchor_is_also_dropped(vault):
    """Jurisdiction but no registration number is still a guess."""
    _write(_report(cross_linked=[
        {"name": "Half Known Ltd", "via_director": "Jane Roe", "jurisdiction": "GB"},
    ]), vault)
    assert _edges(vault) == []


@pytest.mark.parametrize("regnum", ["---", "N/A", "  ", "n/a"])
def test_junk_regnum_scrubs_to_nothing_and_is_dropped(vault, regnum):
    """Pass 2 finding B: the guard must test the SCRUBBED regnum, not the raw string.

    `_scrub_regnum` strips non-alphanumerics, so "---" -> "" and canonical_entity_id
    falls back to company:GB:<name_hash> — a fabricated anchor that can never match a
    real case while LOOKING like a working edge. The old ":??:" check could not catch it
    (jurisdiction was present, so the id was never the ?? form).
    """
    _write(_report(cross_linked=[
        {"name": "Junk Regnum Ltd", "via_director": "Jane Roe",
         "jurisdiction": "GB", "registration_number": regnum},
    ]), vault)
    assert _edges(vault) == [], f"regnum {regnum!r} scrubs to nothing — must not anchor an edge"


def test_ownerless_run_writes_no_edges(vault):
    """R-F2469's precedent: owner-less runs write nothing → stay owner-less."""
    _write(_report(user_id=None), vault)
    assert _edges(vault) == [], (
        "an unattributed edge is invisible to the tenant who produced it (R-F2697), "
        "so writing one is pure noise"
    )


def test_self_link_is_skipped(vault):
    """A walker echo of the subject itself is not a relationship."""
    _write(_report(cross_linked=[
        {"name": "Acme Ltd", "via_director": "Jane Roe",
         "jurisdiction": "GB", "registration_number": "11111111"},
    ]), vault)
    assert _edges(vault) == []


# ── Re-runs, tenants, and the cap ────────────────────────────────────────────

def test_reverse_direction_does_not_double_render(vault):
    """Pass 2 finding D: the relation is SYMMETRIC and get_cross_references matches
    `source = ? OR target = ?`, so A->B plus B->A rendered ONE real link TWICE in the
    Cross-References UI and inflated the count. The pair is normalised on write."""
    fwd = _report()                                   # Acme's DD finds Beta
    rev = _report()                                   # Beta's DD finds Acme
    rev.canonical_entity_id = "company:GB:22222222"
    rev.identity.entity_name = "Beta Systems Ltd"
    rev.network.cross_linked_entities = [
        {"name": "Acme Ltd", "via_director": "Jane Roe",
         "jurisdiction": "GB", "registration_number": "11111111"},
    ]
    _write(fwd, vault)
    _write(rev, vault)
    assert len(_edges(vault)) == 1, "one real-world relationship = one edge"


def test_rerun_refreshes_rather_than_duplicates(vault):
    """A DD re-run must not grow the graph — UNIQUE(src,tgt,rel,user_id) dedups."""
    _write(_report(), vault)
    _write(_report(), vault)
    assert len(_edges(vault)) == 1


def test_two_tenants_keep_their_own_edges(vault):
    """R-F2697's key widening, proven through the real writer."""
    _write(_report(user_id='tenant_a'), vault)
    _write(_report(user_id='tenant_b'), vault)
    assert len(vault.get_cross_references("company:GB:11111111", user_id="tenant_a")) == 1
    assert len(vault.get_cross_references("company:GB:11111111", user_id="tenant_b")) == 1
    assert len(_edges(vault)) == 2, "internal view sees both tenants' edges"


def test_cap_is_enforced_and_not_silent(vault, caplog):
    """A hub director must not write unbounded rows — and truncation must be LOGGED."""
    many = [
        {"name": f"Sub {i} Ltd", "via_director": "Jane Roe",
         "jurisdiction": "GB", "registration_number": f"{30000000 + i}"}
        for i in range(_do._XREF_MAX_PER_RUN + 5)
    ]
    with caplog.at_level("INFO"):
        _write(_report(cross_linked=many), vault)
    assert len(_edges(vault)) == _do._XREF_MAX_PER_RUN
    assert any("R-F2700" in r.message for r in caplog.records), (
        "a capped graph must never read as a complete one"
    )


def test_writer_propagates_so_the_call_site_can_wire_the_failure(vault, monkeypatch):
    """The helper must NOT swallow — the call site catches and brain-wires it.

    `_persist_report` wraps this call in try/except + `wire_failure(gap_type=
    "persistence_failure")`, so a vault blow-up never breaks the DD run AND never goes
    dark (§21a). If the helper swallowed instead, that wire could never fire — the
    silent-failure class this repo keeps paying for.
    """
    def _boom(*a, **kw):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(vault, "add_cross_reference", _boom)
    with pytest.raises(RuntimeError):
        _write(_report(), vault)


# ── The call site itself — Pass 2 proved this IS testable ────────────────────

def test_persist_report_actually_calls_the_writer(tmp_path, monkeypatch):
    """End-to-end: the writer must be WIRED, not merely correct in isolation.

    Note `_persist_report` RECOMPUTES canonical_entity_id from `report.identity` (R-F573),
    so identity.registration_number must be set or the case+edges land under
    company:GB:<name_hash> and a read-back at the id you set on the object finds nothing.
    That subtlety is what made an earlier probe misread this path as unreachable.
    """
    import asyncio
    v = _dv.DDVault(db_path=str(tmp_path / "persist.db"))
    monkeypatch.setattr(_dv, "get_vault", lambda: v)

    r = _report()
    r.identity.registration_number = "11111111"      # so the recomputed id matches
    asyncio.run(_do._persist_report(r))

    edges = v.get_cross_references("company:GB:11111111")
    v.close()
    assert len(edges) == 1, "_persist_report must invoke the graph writer"
    assert edges[0]["target_entity"] == "company:GB:22222222"
    assert edges[0]["relationship"] == "name_match_to_officer"


# ── R-F2703 — the USP decision, pinned ───────────────────────────────────────

def test_name_match_source_is_disabled_by_default(vault, monkeypatch):
    """A relationship graph must carry RELATIONSHIPS, not fuzzy name hits.

    R-F2700 made this edge honest (`name_match_to_officer`, not the fabricated
    "shared_director"). Honest was necessary but NOT sufficient: the upstream lookup is a
    company-NAME search on an officer's name, so a row means "this company's name resembles
    a director's name" — truthful noise. Shipping leads under a "Cross-References" heading
    degrades the evidence grade the product sells, so the source ships OFF and the graph
    stays honestly empty until a real edge (corporate PSC -> controlled_by) lands.
    """
    monkeypatch.setattr(_do, "_XREF_NAME_MATCH_ENABLED", False)   # the shipped default
    assert _write(_report(), vault) == (0, 0)
    assert _edges(vault) == [], "truthful-but-useless still fails the USP"


def test_shipped_default_is_off():
    """Guard the constant itself — a stray flip re-enables the noise."""
    assert _do._XREF_NAME_MATCH_ENABLED is False
