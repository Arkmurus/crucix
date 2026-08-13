"""C-40 / R-F3946 — RULE ONE's Brave half was unenforced, and unmeasured.

THE RULE (operator, 2026-08-12, CLAUDE.md §17): "anthropic API calls must be only
active on DD reports, when a new DD report is been actioned, as well as for brave
API, that was the rule number one."

THE DEFECT. The Anthropic half is genuinely enforced (chain composition +
non-degrading pin). The BRAVE half was not enforced at all: `@_brave_scope`
decorated EIGHT routes in routes/aria.py — including `POST /chat`, `/explore`,
`/explore-deep` and `/research/spawn` — and `brave_is_enabled()` consulted only
a boolean contextvar, a key and a global kill-switch. No DD gate existed. Every
general chat turn that triggered a web search spent the paid Brave key.

WHY NOBODY SAW IT. `rule_one_status()` states the whole rule in its `rule` string
but measures ONLY `"anthropic" in preference_only_providers()`. So production
reported `rule_one: {breached: false}` while half the rule was being broken
continuously — a half-measure reporting a whole rule. That reading is what a
deep-diligence pass trusted and published as "RULE ONE is holding".

THE FIX IS A PURPOSE, NOT A ROUTE LIST. Removing the decorator from eight routes
would be whack-a-mole: the ninth route added next month re-opens it, silently.
The scope now carries WHY it was opened, and the policy is enforced at the single
decision point (`brave_is_enabled`). A caller that does not declare a DD purpose
does not get Brave, wherever it lives. DD already enables its own scope
(dd_orchestrator.py:14981 / :14564), so it is unaffected — the decorators were
never what DD depended on.

NOTE ON `ARIA_STUDENT_BRAVE_BUDGET=0`: it does exactly what CLAUDE.md §27e says,
and it only ever governed the student loop. After this change it is no longer the
ONLY thing standing between us and a breach.
"""
import pytest

from aria_service.intel import web_search as ws


@pytest.fixture(autouse=True)
def _brave_key_present(monkeypatch):
    """Rule One only bites when a key exists — otherwise Brave cannot serve."""
    monkeypatch.setattr(ws, "BRAVE_API_KEY", "test-key-not-real", raising=False)
    monkeypatch.setattr(ws, "_BRAVE_GLOBALLY_OFF", False, raising=False)
    ws.reset_brave_usage_counters()


# ── 1. The breach itself: a non-DD scope must not get Brave ─────────────────

def test_undeclared_scope_does_not_get_brave():
    """This is EXACTLY what `@_brave_scope` on POST /chat does today."""
    token = ws.enable_brave_for_scope(True)          # no purpose — the route shape
    try:
        assert ws.brave_is_enabled() is False, (
            "RULE ONE: Brave is for DD reports only. A scope that does not "
            "declare a DD purpose must not reach the paid key, no matter which "
            "route opened it."
        )
    finally:
        ws.reset_brave_scope(token)


@pytest.mark.parametrize("purpose", ["chat", "explore", "student", "research", ""])
def test_non_dd_purposes_are_all_refused(purpose):
    token = ws.enable_brave_for_scope(True, purpose=purpose)
    try:
        assert ws.brave_is_enabled() is False, f"purpose={purpose!r} is not DD"
    finally:
        ws.reset_brave_scope(token)


# ── 2. DD must keep working — this is the half that must NOT break ──────────

def test_dd_purpose_still_gets_brave():
    """Brave IS DD's engine (R-F3847). Confining it must not disable it."""
    token = ws.enable_brave_for_scope(True, purpose="dd")
    try:
        assert ws.brave_is_enabled() is True, (
            "DD must still reach Brave — R-F3847 makes it DD's sole engine"
        )
    finally:
        ws.reset_brave_scope(token)


def test_dd_scope_still_restores_on_exit():
    """R-F3087 — the paid boundary must not leak into the caller's context."""
    outer = ws.enable_brave_for_scope(True, purpose="dd")
    assert ws.brave_is_enabled() is True
    inner = ws.enable_brave_for_scope(False, purpose="dd")
    assert ws.brave_is_enabled() is False
    ws.reset_brave_scope(inner)
    assert ws.brave_is_enabled() is True, "inner reset must restore the DD scope"
    ws.reset_brave_scope(outer)
    assert ws.brave_is_enabled() is False


def test_the_real_dd_call_site_declares_a_dd_purpose():
    """§3c — the fix is inert unless DD's own call sites actually declare it.

    Guards the wiring, not the helper: if someone later adds a third DD entry
    point without a purpose, DD silently loses Brave and falls back to nothing
    (R-F3847 turned the SearXNG substitution off).
    """
    import ast
    import inspect
    from aria_service.intel import dd_orchestrator

    src = inspect.getsource(dd_orchestrator)
    tree = ast.parse(src)
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", None) == "enable_brave_for_scope"
    ]
    assert calls, "dd_orchestrator must enable its own Brave scope"
    for c in calls:
        kw = {k.arg: k.value for k in c.keywords}
        assert "purpose" in kw, (
            "every dd_orchestrator enable_brave_for_scope() must declare "
            "purpose= — without it DD loses Brave entirely"
        )
        assert isinstance(kw["purpose"], ast.Constant)
        assert ws.is_dd_brave_purpose(kw["purpose"].value), (
            f"purpose={kw['purpose'].value!r} is not a DD purpose"
        )


# ── 3. The measurement: a breach must be OBSERVABLE, not asserted ───────────

def test_refusals_are_counted_so_the_gate_is_provably_alive():
    """A gate nobody can see is the guard-that-cannot-fail class (R-F3858).

    Refusals are EXPECTED and frequent (chat asks constantly), so they are
    counted, never wired as gaps — a per-refusal gap would reproduce the
    crawler flood that evicts real defects from the 500-slot ledger.
    """
    before = ws.brave_policy_status()["non_dd_scope_refused"]
    token = ws.enable_brave_for_scope(True, purpose="chat")
    try:
        ws.brave_is_enabled()
    finally:
        ws.reset_brave_scope(token)
    after = ws.brave_policy_status()["non_dd_scope_refused"]
    assert after == before + 1, "a refused non-DD request must be counted"


def test_policy_status_reports_confinement_and_grants():
    st = ws.brave_policy_status()
    for key in ("confined_to_dd", "key_present", "non_dd_scope_refused",
                "dd_grants", "non_dd_grants"):
        assert key in st, f"brave_policy_status must report {key}"
    assert st["confined_to_dd"] is True
    assert st["non_dd_grants"] == 0, (
        "a non-DD grant is a live breach — this counter is the falsifiable "
        "half, and it must be zero while the gate holds"
    )


# ── 4. rule_one_status must measure BOTH halves ────────────────────────────

def test_rule_one_status_measures_the_brave_half():
    """The half-measure is the reason the breach survived, so close it here."""
    from aria_service.llm import fallback

    st = fallback.rule_one_status()
    assert "brave_confined_to_dd" in st, (
        "rule_one_status states a two-clause rule ('anthropic ... as well as "
        "for brave API') but measured only Anthropic. A surface that reports "
        "breached=false while half the rule is broken is worse than no surface: "
        "it was believed."
    )
    assert st["brave_confined_to_dd"] is True
    assert "brave_non_dd_grants" in st
    assert st["breached"] is False


def test_a_brave_breach_sets_the_overall_breached_flag(monkeypatch):
    """The composite must FAIL when either half fails — else it is decoration."""
    from aria_service.llm import fallback

    monkeypatch.setattr(
        ws, "brave_policy_status",
        lambda: {"confined_to_dd": False, "key_present": True,
                 "non_dd_scope_refused": 0, "dd_grants": 0, "non_dd_grants": 7},
    )
    st = fallback.rule_one_status()
    assert st["brave_confined_to_dd"] is False
    assert st["breached"] is True, (
        "anthropic confined + brave breached must still read BREACHED"
    )
