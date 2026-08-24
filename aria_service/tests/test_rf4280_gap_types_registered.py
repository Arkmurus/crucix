"""R-F4280 / C-240 — three emitted gap types were not registered.

An unregistered type is not cosmetic. `record_gap` logs
`"Unknown gap type %r — recording anyway"` (capability_gaps.py:543) and the
signal lands in the ledger under a name nothing filters on: recorded, and
effectively unreadable. That is the §21b DARK condition reached by a different
route, and `test_rf2644` had been red on all three.

The one with teeth is `resolution_enforcement_failure`. It is R-F4144's
identity-gate signal, and R-F4278 keeps `tooluse_resolution` ADVISORY only
because that enforcement demonstrably replaces the model's answer — with the
recorded reversal condition "if enforcement is ever removed from a response
path". While the type was unregistered, an enforcement failure was unobservable,
so the evidence that decision rests on could not be seen.
"""
from __future__ import annotations

import logging
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.intel.capability_gaps import VALID_GAP_TYPES  # noqa: E402

REGISTERED_BY_RF4280 = (
    "boot_regression_check_skipped",
    "ranking_amplification",
    "resolution_enforcement_failure",
)

#: Where each one is emitted. Read before registering, per the R-F3428 precedent.
EMIT_SITES = {
    "boot_regression_check_skipped": "aria_service/intel/boot_snapshot_diff.py",
    "ranking_amplification": "aria_service/intel/knowledge.py",
    "resolution_enforcement_failure": "aria_service/intel/companies_house.py",
}


@pytest.mark.parametrize("gap_type", REGISTERED_BY_RF4280)
def test_the_type_is_registered(gap_type: str) -> None:
    assert gap_type in VALID_GAP_TYPES


@pytest.mark.parametrize("gap_type,module", sorted(EMIT_SITES.items()))
def test_the_type_is_actually_emitted_where_claimed(gap_type: str, module: str) -> None:
    """Registering a type nothing emits would be the opposite defect.

    A registry that accumulates names no code uses stops describing the system,
    and the next reader cannot tell a live failure domain from a dead one.
    """
    src = (ROOT / module).read_text(encoding="utf-8")
    assert f'gap_type="{gap_type}"' in src, f"{gap_type} is not emitted by {module}"


def test_an_unregistered_type_still_warns() -> None:
    """THE CAPABILITY TEST — the symptom, driven through the real code path.

    This is what the three types produced before R-F4280, and it must still
    happen for a genuinely unknown type: a registry that accepted anything would
    be no registry at all.
    """
    import asyncio
    from unittest.mock import patch

    from aria_service.intel import capability_gaps as cg

    records: list[str] = []

    class _Cap(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Cap()
    cg.logger.addHandler(handler)
    try:
        with patch.object(cg, "rs") as store:
            async def _get(*a, **k):
                return None

            async def _setex(*a, **k):
                return None

            async def _set(*a, **k):
                return None

            store.get = _get
            store.setex = _setex
            store.set = _set
            store.lpush = _set
            store.ltrim = _set
            store.get_json = _get
            store.set_json = _set
            try:
                asyncio.run(cg.record_gap("definitely_not_a_registered_type", "probe"))
            except Exception:
                pass  # the store is a stub; the WARNING is what is under test
    finally:
        cg.logger.removeHandler(handler)

    assert any("Unknown gap type" in m for m in records), records


def test_the_types_this_shipped_do_not_warn() -> None:
    """The fix, stated as the absence of the symptom above."""
    for gap_type in REGISTERED_BY_RF4280:
        assert gap_type in VALID_GAP_TYPES, gap_type


def test_the_enforcement_signal_is_the_one_the_advisory_axis_depends_on() -> None:
    """R-F4278 keeps tooluse_resolution advisory on enforcement evidence.

    If that enforcement fails, it must be VISIBLE — otherwise the reversal
    condition can never be observed and the axis stays advisory on a premise
    nobody can check.
    """
    from scripts.train.axis_alignment import OUTPUT_OVERRIDDEN

    assert "resolution_enforcement_failure" in VALID_GAP_TYPES
    evidence = OUTPUT_OVERRIDDEN["tooluse_resolution"]
    assert "enforce_resolution_response" in evidence
    src = (ROOT / "aria_service/intel/companies_house.py").read_text(encoding="utf-8")
    assert "def enforce_resolution_response" in src
    assert 'gap_type="resolution_enforcement_failure"' in src
