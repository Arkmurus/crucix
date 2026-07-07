"""R-F2387 — DD vault delete reports true row removal only."""
from __future__ import annotations

import tempfile

from aria_service.intel.dd_vault import DDVault


def test_dd_vault_delete_case_returns_false_for_missing_case():
    vault = DDVault(db_path=tempfile.mktemp(suffix=".db"))

    assert vault.delete_case("company:missing") is False


def test_dd_vault_delete_case_returns_true_for_existing_case():
    vault = DDVault(db_path=tempfile.mktemp(suffix=".db"))
    vault.record_case(
        canonical_entity_id="company:acme:GB",
        entity_name="Acme",
        entity_type="company",
        latest_report_id="dd_1",
    )

    assert vault.delete_case("company:acme:GB") is True
    assert vault.get_case("company:acme:GB") is None
