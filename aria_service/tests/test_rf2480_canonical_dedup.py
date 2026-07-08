"""R-F2480 — a re-run must version the SAME entity, not fork a duplicate case.

Live bug: a Modirum Gespi re-run produced TWO vault cases —
  company:BR:45218484000340   (reg '45.218.484/0003-40')
  company:BR:CNPJ45218484000340 (reg 'CNPJ 45.218.484/0003-40')
because _scrub_regnum kept the redundant 'CNPJ' label. Post-fix both scrub to the
same ID → one canonical → one versioned case.
"""
from aria_service.intel.dd_versioning import _scrub_regnum, canonical_entity_id


def test_cnpj_label_stripped_to_same_id():
    assert _scrub_regnum("45.218.484/0003-40") == "45218484000340"
    assert _scrub_regnum("CNPJ 45.218.484/0003-40") == "45218484000340"
    assert _scrub_regnum("cnpj45218484000340") == "45218484000340"


def test_labeled_and_unlabeled_regnum_same_canonical():
    a = canonical_entity_id(entity_type="company", name="Modirum Gespi",
                            jurisdiction_iso2="BR", registration_number="45.218.484/0003-40")
    b = canonical_entity_id(entity_type="company", name="Modirum Gespi",
                            jurisdiction_iso2="BR", registration_number="CNPJ 45.218.484/0003-40")
    assert a == b == "company:BR:45218484000340", (a, b)


def test_legit_alpha_id_prefix_preserved():
    # UK Scotland/NI company numbers (SC/NI + 6 digits) are NOT labels — keep them.
    assert _scrub_regnum("SC123456") == "SC123456"
    assert _scrub_regnum("NI654321") == "NI654321"
    # pure numeric unchanged
    assert _scrub_regnum("12345678") == "12345678"
    # a pure-label with no digits is never emptied
    assert _scrub_regnum("CNPJ") == "CNPJ"


def test_different_companies_stay_distinct():
    a = canonical_entity_id(entity_type="company", name="Acme", jurisdiction_iso2="BR",
                            registration_number="CNPJ 11.111.111/0001-11")
    b = canonical_entity_id(entity_type="company", name="Beta", jurisdiction_iso2="BR",
                            registration_number="CNPJ 22.222.222/0001-22")
    assert a != b


if __name__ == "__main__":
    test_cnpj_label_stripped_to_same_id()
    print("PASS test_cnpj_label_stripped_to_same_id")
    test_labeled_and_unlabeled_regnum_same_canonical()
    print("PASS test_labeled_and_unlabeled_regnum_same_canonical")
    test_legit_alpha_id_prefix_preserved()
    print("PASS test_legit_alpha_id_prefix_preserved")
    test_different_companies_stay_distinct()
    print("PASS test_different_companies_stay_distinct")
    print("ALL PASS")
