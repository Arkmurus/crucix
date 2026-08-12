"""R-F3942/R-F3943 — RULE ONE was unenforced and unobservable, and it cost the account.

OPERATOR, 2026-08-12: "anthropic API calls must be only active on DD reports, when a
new DD report is been actioned, as well as for brave API, that was the rule number
one." This restates the 2026-08-11 directive already recorded at `web_search.py:167`
("Brave (and Anthropic) are the designated tools for DD reports, and nothing else").

WHAT HAPPENED. The live secret `ARIA_PREFERENCE_ONLY_PROVIDERS` was set to the EMPTY
STRING. `fallback.py` documents that as "disables the mechanism", and the chain is
built as `[p for p in providers if p.name not in preference_only_providers()]` — so
Claude re-entered the GENERAL order and served any call whose primary was cooling,
which DeepSeek does many times a day.

MEASURED LIVE 2026-08-12, month-to-date $73.34:
    anthropic          614 calls   4,587,342 tok   $39.10   <- 53% of ALL spend
      claude-opus-4-8  540 calls   4,454,861 tok   $38.74
    dd_orchestrator      8 calls       2,117 tok   $0.04    <- the only sanctioned use
It exhausted the credit balance ("Your credit balance is too low to access the
Anthropic API", probed directly), and because DD pins Claude NON-DEGRADABLY, DD went
down. Cheap general traffic consumed the budget reserved for the paid product.

NOBODY COULD SEE IT. CLAUDE.md §17 actively instructed sessions to KEEP the empty
value ("Do not 'tidy' it back to anthropic"), estimating ~$21/mo — already a ~5x
understatement at the observed run rate. /health published the provider lists, so the
breach was technically visible, but nothing named the RULE, so a list containing
"anthropic" did not read as a violation.

These tests pin the POLICY, not one env var, and each carries a control proving it can
still fail (R-F3858).
"""
from __future__ import annotations

import pytest

from aria_service.llm import fallback, openai_compat


# ── Rule One: anthropic is confined to DD ──────────────────────────────────────

def test_the_code_default_keeps_anthropic_out_of_the_general_chain(monkeypatch):
    """THE DEFAULT IS THE GUARANTEE. With no override, Claude must be preference-only.

    The deployment had an override; the default was always right. That is why the fix
    was to UNSET the variable rather than set it — an override exists only to deviate.
    """
    monkeypatch.delenv("ARIA_PREFERENCE_ONLY_PROVIDERS", raising=False)
    assert "anthropic" in fallback.preference_only_providers()


def test_an_empty_override_is_reported_as_a_breach_not_passed_silently(monkeypatch):
    """THE REGRESSION TEST. Empty string = mechanism disabled = Claude in the general
    chain. That is a policy change with a bill attached and it must SAY SO."""
    monkeypatch.setattr(fallback, "_RULE_ONE_BREACH_ANNOUNCED", False, raising=False)
    monkeypatch.setenv("ARIA_PREFERENCE_ONLY_PROVIDERS", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    st = fallback.rule_one_status()

    assert st["breached"] is True
    assert st["anthropic_confined_to_dd"] is False
    assert "anthropic" not in st["preference_only_providers"]


def test_the_healthy_default_is_not_reported_as_a_breach(monkeypatch):
    """THE CONTROL (R-F3858). A guard that always fires is not a guard."""
    monkeypatch.setattr(fallback, "_RULE_ONE_BREACH_ANNOUNCED", False, raising=False)
    monkeypatch.delenv("ARIA_PREFERENCE_ONLY_PROVIDERS", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    st = fallback.rule_one_status()

    assert st["breached"] is False
    assert st["anthropic_confined_to_dd"] is True


def test_no_anthropic_key_is_not_a_breach(monkeypatch):
    """Without a key Claude cannot serve at all, so crying breach would be a guard
    firing on nothing — and a noisy guard is one people learn to ignore."""
    monkeypatch.setattr(fallback, "_RULE_ONE_BREACH_ANNOUNCED", False, raising=False)
    monkeypatch.setenv("ARIA_PREFERENCE_ONLY_PROVIDERS", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert fallback.rule_one_status()["breached"] is False


def test_a_breach_reaches_the_brain_exactly_once(monkeypatch):
    """§21a — the breach must be actionable without anyone thinking to poll a health
    field, but announced ONCE per process, not on every health poll."""
    import aria_service.intel.engine_wiring as ew

    seen: list[dict] = []
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: seen.append(kw))
    monkeypatch.setattr(fallback, "_RULE_ONE_BREACH_ANNOUNCED", False, raising=False)
    monkeypatch.setenv("ARIA_PREFERENCE_ONLY_PROVIDERS", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    fallback.rule_one_status()
    fallback.rule_one_status()
    fallback.rule_one_status()

    assert len(seen) == 1, f"expected one signal, got {len(seen)}"
    assert seen[0]["module"] == "fallback"
    assert "RULE ONE" in seen[0]["detail"]


def test_the_chain_actually_excludes_a_preference_only_provider():
    """The mechanism itself, not just the flag: the general order is built by
    filtering on this set, so the two must not drift apart."""
    from aria_service.tests._source_probe import module_code

    src = module_code(fallback)
    assert "not in _pref_only" in src, (
        "the general chain must still be composed by EXCLUDING preference-only "
        "providers — if this filter goes, Rule One goes with it (R-F3942)")


# ── R-F3943: the deepseek backup slot ──────────────────────────────────────────

def test_the_deepseek_backup_slot_is_off_by_default(monkeypatch):
    """OPERATOR 2026-08-12: "just remove deepseek back up, we do not need a backup".

    It was never redundancy — same key, same account (`verification_gate._vendor_of`
    exists because of exactly that) — and it cost ~3x the primary per token
    ($0.572/M vs $0.193/M measured) across 1,584 calls.
    """
    monkeypatch.delenv("ARIA_DEEPSEEK_BACKUP_ENABLED", raising=False)
    assert openai_compat.deepseek_backup_enabled() is False
    assert openai_compat.backup_deepseek_model() == "", (
        "a disabled backup must resolve to no model, which is what drops it from "
        "the chain in create_fallback_chain (R-F3943)")


def test_the_backup_can_still_be_re_enabled_deliberately(monkeypatch):
    """THE CONTROL. Removing it must not mean deleting the capability — a model
    retirement (R-F3035) is still a real event."""
    monkeypatch.setenv("ARIA_DEEPSEEK_BACKUP_ENABLED", "1")
    monkeypatch.delenv("ARIA_DEEPSEEK_BACKUP_MODEL", raising=False)

    assert openai_compat.deepseek_backup_enabled() is True
    assert openai_compat.backup_deepseek_model() == "deepseek-v4-pro"


@pytest.mark.parametrize("val", ["0", "false", "no", "off", "", "  ", "yes-ish", "2"])
def test_only_explicit_truthy_words_enable_paid_backup_traffic(monkeypatch, val):
    """A typo must not silently restore paid traffic — the inverse of
    `_dd_brave_only`'s default-ON reasoning, because here the safe default is the
    one that does NOT spend."""
    monkeypatch.setenv("ARIA_DEEPSEEK_BACKUP_ENABLED", val)
    assert openai_compat.deepseek_backup_enabled() is False


def test_an_empty_backup_model_var_could_not_previously_disable_it(monkeypatch):
    """THE ROOT DEFECT, pinned. `os.getenv(...) or "deepseek-v4-pro"` treats an EMPTY
    value as unset, so `ARIA_DEEPSEEK_BACKUP_MODEL=""` still returned the hardcoded
    id. There was no off-switch at all; the only way to drop the slot was to set the
    backup model EQUAL to the primary and rely on a `!=` test — a coincidence, not a
    switch. With the enable flag on, that old behaviour is preserved exactly.
    """
    monkeypatch.setenv("ARIA_DEEPSEEK_BACKUP_ENABLED", "1")
    monkeypatch.setenv("ARIA_DEEPSEEK_BACKUP_MODEL", "")
    assert openai_compat.backup_deepseek_model() == "deepseek-v4-pro"
