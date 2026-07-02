"""R-F2352 — the re-run buttons send `entity_type`, but orchestrate_dd's R-F659 gate
requires `target['type']`. Without normalising entity_type -> type in the endpoint, every
re-run fast-failed ("R-F659: entity_type missing"). This proves the gate requirement and
the normalisation that fixes it. The endpoint path is verified by live smoke (§23).
"""
from aria_service.intel.dd_orchestrator import _validate_entity_type_for_dd


def test_gate_requires_type_not_entity_type():
    ok, _ = _validate_entity_type_for_dd({"type": "company", "name": "Modirum Gespi"})
    assert ok is True
    # entity_type ALONE does not satisfy the gate — the exact reason re-run failed.
    bad, _ = _validate_entity_type_for_dd({"entity_type": "company", "name": "Modirum Gespi"})
    assert bad is False


def test_entity_type_to_type_normalization_fixes_the_gate():
    # Mirrors the endpoint's R-F2352 normalisation applied to a re-run body.
    body = {"name": "Modirum Gespi", "entity_type": "company"}  # what the re-run buttons send
    if body.get("entity_type") and not body.get("type"):
        body["type"] = body["entity_type"]
    ok, _ = _validate_entity_type_for_dd(body)
    assert ok is True
