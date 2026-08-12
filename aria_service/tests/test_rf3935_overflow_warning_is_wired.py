r"""R-F3935 — the infinite-memory overflow warning was DARK.

§7 is binding: "No TTL on knowledge. No oldest-first prune. No eviction. Overflow →
cold storage, never delete." R-F239 correctly removed the truncation that violated
it, and left a soft warn threshold as the ONLY automated protection.

That warning did `logger.warning` and nothing else — while R-F239's own comment
promises "the operator gets a brain_hook absorb prompting offload to cold storage".
The code did not do what its own comment said.

§21a is explicit that a console log is DARK, not wired. So at 1M facts ARIA would
emit one line per 100 writes into fly logs nobody reads, RSS would keep climbing, and
the operator would never be told — §19e's stated worst outcome, "a blocker the
operator has to find himself".

MEASURED CONTEXT (2026-08-12, via /api/aria/memory/health): 499,812 facts resident,
topic_index 499,812, content_index 472,221 — about half the 1,000,000 warn threshold,
so this has never fired and its darkness had never been noticed.

WHAT DELIBERATELY STAYS MANUAL: the offload itself. §7 makes it an operator action,
and an automatic offload invented here would be exactly the deletion-adjacent
behaviour §7 exists to prevent (R-F173, reversed by R-F238). What must not be manual
is the NOTICE.
"""
from __future__ import annotations

import pytest

from aria_service.intel import knowledge as k
from aria_service.tests._source_probe import module_source


def _code_only(src: str) -> str:
    """Source with comment lines removed.

    Every assertion here MUST read code, not prose: R-F239's comment quotes the
    removed truncation (`db["facts"] = db["facts"][:MAX_FACTS]`) verbatim, so a
    plain substring scan flags the explanation as the offence. That is the R-F3888
    defect, which blocked a real commit earlier today.
    """
    return "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))


def _warn_branch(src: str) -> str:
    """The executable warn branch — located by its `if`, not by any mention."""
    code = _code_only(src).splitlines()
    for i, ln in enumerate(code):
        if ln.strip().startswith("if _fact_count > WARN_FACTS"):
            return "\n".join(code[i:i + 60])
    raise AssertionError("warn branch not found")


def test_the_warning_reaches_the_brain_not_just_the_log():
    """THE DEFECT. A logger.warning is not wiring (§21a)."""
    warn = _warn_branch(module_source(k))
    assert "record_gap" in warn, (
        "the overflow warning must record a capability gap — a console log is DARK "
        "and the operator never sees it (R-F3935)")
    assert "wire_failure" in warn, "it must also reach the brain's failure sink"


def test_the_gap_says_what_to_do_and_what_not_to_do():
    """An alert that does not name the remedy gets 'fixed' by raising the threshold,
    which is the one response §7 forbids."""
    warn = _warn_branch(module_source(k))
    assert "cold" in warn.lower(), "the gap must name cold-storage offload"
    assert "never instead of it" in warn, (
        "the gap must say explicitly that raising the threshold is NOT the fix")


def test_the_signal_is_throttled_with_the_log():
    """A standing overflow must not flood the gap ledger — an alert that fires
    continuously is one that gets muted."""
    warn = _warn_branch(module_source(k))
    throttle_at = warn.find("_kb_warn_throttle % 100 == 1")
    gap_at = warn.find("record_gap")
    assert throttle_at != -1, "the throttle must remain"
    assert gap_at > throttle_at, (
        "the gap must be emitted INSIDE the throttle, not on every write")


@pytest.mark.asyncio
async def test_emitting_the_gap_never_breaks_a_fact_write(monkeypatch):
    """Observability must never break the write it observes — a knowledge store
    that refuses facts because its alarm failed is far worse than a missed alarm."""
    from aria_service.intel import capability_gaps as cg

    async def _boom(*a, **kw):
        raise RuntimeError("gap ledger down")

    monkeypatch.setattr(cg, "record_gap", _boom)
    monkeypatch.setattr(k, "WARN_FACTS", 0)      # force the branch

    res = await k.store_fact(
        topic="rf3935_probe",
        content=("a fact comfortably longer than the fifty character minimum "
                 "enforced by R-F1526 so it is not rejected"),
        source="test",
        skip_rag_ingest=True,
        skip_semantic_index=True,
    )
    assert res.get("action") in ("created", "updated", "superseded",
                                "duplicate_skipped"), res


def test_the_policy_itself_is_unchanged():
    """§7 guard: this fix must not have introduced truncation or eviction."""
    src = _code_only(module_source(k))
    assert 'db["facts"] = db["facts"][:MAX_FACTS]' not in src, (
        "truncation must never return — R-F239 removed it as a §7 violation")
    assert k.MAX_FACTS >= 100_000_000, "MAX_* remain warn sentinels, not caps"


def test_the_helper_can_actually_fail():
    """R-F3858 — prove the comment-stripper does not simply blank everything, or a
    green result here would mean nothing."""
    stripped = _code_only("# a comment\nreal_code = 1\n  # indented comment\nx = 2")
    assert "real_code = 1" in stripped
    assert "a comment" not in stripped
