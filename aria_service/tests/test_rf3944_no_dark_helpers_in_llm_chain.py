"""R-F3944 — R-F3942/R-F3943 shipped two DARK public helpers, and gate A caught them.

    fallback.py:134      public sync function 'rule_one_status()'       no @fail_wire
    openai_compat.py:37  public sync function 'deepseek_backup_enabled()'  no @fail_wire

HOW IT SLIPPED. The identical class was hit earlier the same session with
`current_hour_bucket`/`rate_bucket_key` (R-F3940) and fixed by exempting them — then on
the very next change the gate was not re-run. The regression set was chosen by grepping
for "fallback|openai_compat|deepseek_backup|preference_only", and
`test_rf3560_gap_type_overrides.py` matches none of those words, so the suite that
enforces wiring coverage was never selected. `pre-commit --check-all` does not carry
gate A either, so it passed too.

THE RULE: after touching ANY module, run the wiring-gate suites BY NAME. They are not
discoverable by grepping for the module you changed.

The exemptions themselves are reasoned, not convenient:
  * `deepseek_backup_enabled` — a bool env read with a literal default, no I/O, total
    over its input. Its two neighbours in the same dict are already exempt on exactly
    that basis.
  * `rule_one_status` — it IS the §21a reporter; it emits its own wire_failure on the
    breach branch. @fail_wire would nest a failure wire inside the function whose whole
    job is to emit one.

THE REAL HAZARD THIS PINS is not the exemption, it is HOW it was added. Both entries
were MERGED into the existing "fallback.py" / "openai_compat.py" keys. A duplicate key
in a dict literal silently keeps the LAST one, so a second "fallback.py" key would have
discarded the `stream` exemption above it — and `stream` is an ASYNC GENERATOR that
must never be wrapped (§13, wrapping it breaks SSE streaming). That is the R-F3429
hazard, documented in a comment two lines below where the edit was made.
"""
from __future__ import annotations

import ast

from aria_service.intel import wiring_harness as wh


def test_the_two_helpers_are_no_longer_dark():
    """THE REGRESSION TEST — gate A must report no blocking violation for either."""
    exempt_fb = wh.HARD_EXEMPT.get("fallback.py", {})
    exempt_oc = wh.HARD_EXEMPT.get("openai_compat.py", {})

    assert "rule_one_status" in exempt_fb, (
        "rule_one_status() is a public sync function; it must be wired or exempt "
        "(R-F3944)")
    assert "deepseek_backup_enabled" in exempt_oc, (
        "deepseek_backup_enabled() is a public sync function; it must be wired or "
        "exempt (R-F3944)")
    # An exemption without a stated reason is a mute button, not a decision.
    assert exempt_fb["rule_one_status"].strip(), "exemption must carry a reason"
    assert exempt_oc["deepseek_backup_enabled"].strip(), "exemption must carry a reason"


def test_the_merge_did_not_discard_the_pre_existing_exemptions():
    """THE ACTUAL DAMAGE a duplicate key would have done (R-F3429).

    A second "fallback.py" key would silently win and drop everything above it —
    including `stream`, an ASYNC GENERATOR that must never be wrapped because doing so
    breaks SSE streaming (§13). This asserts the prior entries survived the merge.
    """
    fb = wh.HARD_EXEMPT["fallback.py"]
    for prior in ("stream", "is_configured", "provider_scope",
                  "get_preferred_provider", "get_provider_status",
                  "preference_only_providers"):
        assert prior in fb, (
            f"'{prior}' was lost from the fallback.py exemptions — a duplicate dict "
            "key silently keeps the LAST one (R-F3429/R-F3944)")

    oc = wh.HARD_EXEMPT["openai_compat.py"]
    for prior in ("is_configured", "default_deepseek_model", "backup_deepseek_model"):
        assert prior in oc, f"'{prior}' was lost from the openai_compat.py exemptions"


def test_hard_exempt_has_no_duplicate_module_keys():
    """The mechanical check, over the SOURCE — a duplicate is invisible in the parsed
    dict (the last one simply won), so the parsed object cannot reveal it. Read the
    literal."""
    src = wh.__file__
    with open(src, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    literal = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "HARD_EXEMPT":
            literal = node.value
        elif isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "HARD_EXEMPT" for t in node.targets):
            literal = node.value
    assert isinstance(literal, ast.Dict), "HARD_EXEMPT must be a dict literal"

    keys = [k.value for k in literal.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, (
        f"duplicate HARD_EXEMPT module keys {dupes} — a later duplicate silently "
        "discards the earlier entry's exemptions (R-F3429/R-F3944)")


def test_the_gate_can_still_catch_a_genuinely_dark_function():
    """R-F3858 — an exemption list that swallowed everything would make gate A green
    and meaningless. A public sync function that is NOT exempt must still be flagged."""
    fb = wh.HARD_EXEMPT["fallback.py"]
    assert "complete" not in fb, (
        "complete() is the main dispatch path and must NEVER be exempt — if it is, "
        "gate A can no longer see the function that matters most")

    invented = "a_function_nobody_exempted"
    assert invented not in fb, "precondition"
    # i.e. the exemption set is a specific, finite list — not a catch-all.
    assert len(fb) < 20, (
        "the fallback.py exemption list has grown suspiciously large; each entry "
        "removes a function from gate A's coverage")
