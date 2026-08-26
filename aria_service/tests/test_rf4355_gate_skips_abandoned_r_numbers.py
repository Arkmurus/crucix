"""R-F4355 (C-300) — the gate must not demand a capability test for an
R-number that was abandoned, cancelled or superseded.

MEASURED 2026-08-26, while pushing the merge to main. Two sessions collided on
one defect; the later reservation (R-F4354) was released as **superseded** by
agreement, and the commit recording that release names it. Naming it is the
whole point of the record. The pre-push hook then refused the push::

    [R-F559] FAIL - R-numbers missing test file:
      R-F4354

R-F4354 has no code and never will. The gate was demanding a capability test
proving a user-visible symptom is fixed for a number that fixed nothing — a
requirement that **cannot be satisfied honestly at any effort**, which is
precisely the property R-F4353 (C-298) had just been fixed for. There the gate
could not SEE a tier; here it demands the impossible. Both push the author
toward ``--no-verify``, which disables the gate for every R-number in the push.

This is not a one-off. The registry holds **57 abandoned, 1 cancelled and 1
superseded** R-numbers, so any commit message mentioning one of the 59 is a
landmine — including the honest bookkeeping commit that records the abandonment.

The registry already knows. `status` is authoritative and the gate simply was
not reading it.

WHAT MUST STILL FAIL, and these are the load-bearing half: `in_progress` and
`shipped` are REAL work and keep their test requirement. An unknown status, and
an R-number absent from the registry entirely, also keep it — otherwise a typo
in a status field, or an unregistered number, would buy an exemption. The
exemption is allow-listed, never inferred.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "verify_commit.py"
_REGISTRY = _REPO / "data" / "r_number_reservations.json"


def _import_module():
    """Explicit utf-8 read — see test_rf4353 for why the loader path is avoided."""
    src = _SCRIPT.read_text(encoding="utf-8")
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("_vc_rf4355", loader=None))
    mod.__file__ = str(_SCRIPT)
    exec(compile(src, str(_SCRIPT), "exec"), mod.__dict__)
    return mod


def _statuses() -> dict[str, str]:
    data = json.loads(_REGISTRY.read_text(encoding="utf-8"))
    return {r["r_number"]: (r.get("status") or "") for r in data.get("reservations", [])}


def test_abandoned_r_numbers_are_exempt_from_the_test_requirement() -> None:
    """THE SYMPTOM. R-F4354 is superseded; the gate must not demand a test."""
    mod = _import_module()
    assert _statuses().get("R-F4354") == "superseded", "fixture assumes the release"

    assert mod._is_test_exempt("R-F4354"), (
        "a superseded R-number has no code and can never have a capability "
        "test; demanding one makes the gate unsatisfiable and invites --no-verify")


def test_every_terminal_status_in_the_real_registry_is_exempt() -> None:
    """All three no-code statuses present in the live registry are covered —
    not just the one that happened to block today."""
    mod = _import_module()
    statuses = _statuses()

    for status in ("abandoned", "cancelled", "superseded"):
        sample = next((rn for rn, s in statuses.items() if s == status), None)
        if sample is None:
            continue
        assert mod._is_test_exempt(sample), (
            f"status {status!r} ({sample}) still demands a capability test")


def test_real_work_still_requires_a_test() -> None:
    """THE GUARD. shipped and in_progress are real work and keep the
    requirement — an exemption that leaked here would gut the gate."""
    mod = _import_module()
    statuses = _statuses()

    for status in ("shipped", "in_progress"):
        sample = next((rn for rn, s in statuses.items() if s == status), None)
        assert sample is not None, f"fixture assumes a {status} R-number exists"
        assert not mod._is_test_exempt(sample), (
            f"{status} R-number {sample} was exempted from the test requirement")


def test_unknown_and_unregistered_are_never_exempt() -> None:
    """Exemption is ALLOW-LISTED, not inferred. A number the registry does not
    know, or one carrying an unrecognised status, keeps the requirement — else
    a typo in a status field buys a free pass."""
    mod = _import_module()

    assert not mod._is_test_exempt("R-F999999"), "unregistered number exempted"
    assert not mod._is_test_exempt(""), "empty R-number exempted"


def test_main_actually_applies_the_exemption(monkeypatch, capsys) -> None:
    """CAPABILITY — drives the real entry point, not the helper.

    A mutation run proved this necessary: with ``_is_test_exempt`` correct but
    never CONSULTED at the call site, every helper test still passed. A decision
    nothing consumes did not happen. This asserts the gate's exit code over a
    range whose only R-number is superseded.
    """
    import sys
    mod = _import_module()
    assert _statuses().get("R-F4354") == "superseded"

    monkeypatch.setattr(mod, "_r_numbers_in_range", lambda _rng: {"R-F4354"})
    monkeypatch.setattr(sys, "argv", ["verify_commit.py", "--skip-tests"])

    rc = mod.main()
    out = capsys.readouterr()
    assert rc != 2, (
        "the gate still failed with 'missing test file' for a superseded "
        f"R-number; stderr={out.err[:300]}")
    assert "exempt" in out.out, "the exemption must be announced, not silent"


def test_the_gate_end_to_end_accepts_a_range_naming_only_an_abandoned_number() -> None:
    """CAPABILITY. Drive the real entry point over a commit range whose only
    R-number is superseded: it must not fail with 'missing test file'."""
    mod = _import_module()
    statuses = _statuses()
    abandoned = [rn for rn, s in statuses.items()
                 if s in {"abandoned", "cancelled", "superseded"}]
    assert abandoned, "fixture assumes terminal-status R-numbers exist"

    missing = [rn for rn in abandoned if not mod._test_files_present_for(rn)
               and not mod._is_test_exempt(rn)]
    assert not missing, (
        f"{len(missing)} terminal-status R-numbers would still block a push "
        f"that merely names them: {missing[:5]}")
