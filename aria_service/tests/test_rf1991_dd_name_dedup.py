"""R-F1991 — DD entity-name cleaning + case-file collapse (dedup).

The Modirum-Gespi-×5 bug: the chat capture regex put the whole query tail
("Modirum Gespi, their website is https://modirumgespi.com/en · Finland") into
the entity name. Every phrasing forked canonical_entity_id, so the same company
produced 5 separate case files instead of one with versions. These tests pin:
  1. the name cleaner extracts the URL + strips descriptors → bare org name,
  2. it does NOT mangle legit names containing site/web/domain words,
  3. the index collapse merges the historical noisy duplicates into one,
  4. two genuinely different same-named entities in different countries stay split.
"""
from aria_service.intel.dd_orchestrator import _clean_entity_name, _collapse_index


# ── name cleaner ────────────────────────────────────────────────────────────
def test_clean_strips_url_and_descriptors_to_bare_name():
    variants = [
        "Modirum Gespi, https://modirumgespi.com/en · Finland",
        "Modirum Gespi, their website is https://modirumgespi.com/en · Finland",
        "Modirum Gespi, their website is https://modirumgespi.com/en",
        "Modirum Gespi, https://modirumgespi.com/en",
        "Modirum Gespi, website is https://modirumgespi.com/en",
    ]
    for v in variants:
        name, website = _clean_entity_name(v)
        assert name == "Modirum Gespi", f"{v!r} -> {name!r}"
        assert "modirumgespi.com" in website, f"{v!r} -> website {website!r}"


def test_clean_leaves_legit_names_untouched():
    # Names containing site/web/domain words must NOT be truncated.
    for legit in ["Site One Landscape Supply", "Web Solutions Ltd",
                  "Domain Registrar Group", "Acme Corp"]:
        name, website = _clean_entity_name(legit)
        assert name == legit, f"{legit!r} wrongly changed to {name!r}"
        assert website == ""


def test_clean_pure_domain_returns_empty_name_for_resolution():
    # A bare domain name → empty clean name so the caller's pure-URL path resolves it.
    name, website = _clean_entity_name("modirumgespi.com")
    assert name == "" and "modirumgespi.com" in website


# ── index collapse ──────────────────────────────────────────────────────────
def _entry(name, juris, ts, rid):
    return {"run_id": rid, "entity_name": name, "jurisdiction": juris,
            "generated_at": ts, "canonical_entity_id": f"company:XX:{rid}"}


def test_collapse_merges_noisy_duplicates_of_one_entity():
    index = [
        _entry("Modirum Gespi, https://modirumgespi.com/en · Finland", "Finland", "2026-06-01", "r1"),
        _entry("Modirum Gespi, their website is https://modirumgespi.com/en · Finland", "Finland", "2026-06-02", "r2"),
        _entry("Modirum Gespi, their website is https://modirumgespi.com/en", "", "2026-06-03", "r3"),
        _entry("Modirum Gespi, https://modirumgespi.com/en", "", "2026-06-04", "r4"),
        _entry("Modirum Gespi, website is https://modirumgespi.com/en", "", "2026-06-05", "r5"),
    ]
    out = _collapse_index(index, limit=100)
    assert len(out) == 1, f"expected 1 collapsed case, got {len(out)}"
    assert out[0]["run_id"] == "r5", "must keep the newest version"


def test_collapse_keeps_distinct_jurisdictions_separate():
    # Two real, different companies that happen to share a name — must NOT merge.
    index = [
        _entry("Acme Corp", "United States", "2026-06-01", "us1"),
        _entry("Acme Corp", "United Kingdom", "2026-06-02", "uk1"),
    ]
    out = _collapse_index(index, limit=100)
    assert len(out) == 2, "same name, different countries must stay separate"
