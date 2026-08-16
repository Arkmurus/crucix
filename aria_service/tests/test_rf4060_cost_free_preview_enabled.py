"""R-F4060 (C-122) — the cost-free preview was off, so its own gate could never open.

`HOURLY-COST-FREE-LEARN` shipped `enabled: false` under the R-F567 doctrine that
every NEW task ships off and the operator flips it on explicitly. The flip never
happened, and the task's own description says why it exists:

    "Surfaces what would change if writes were enabled — feed to mem0 +
     dashboard so the operator can sanity-check before flipping the write env."

With the task off, the preview never runs, so **there is nothing to
sanity-check**. The approval gate cannot open because the evidence it requires is
never produced — the same structural shape as the Phase A gates §1 records as
"certified by an absence", and as R-F2689's evidence gate that C-107 had to feed.

WHY THIS IS THE RIGHT DEFAULT FOR THIS PRODUCT. The four loops are exactly the
moat CLAUDE.md describes — golden data plus ARIA's own verification, at no
vendor cost (§6 mirrors-Claude, §15 pay-once-remember-forever):

  1. mastery_decay          — surfaces stale-high topics (feeds gate #2 honesty)
  2. mistake_replay         — re-checks the ledger against the live constitution
  3. cross_source_corroborate — 2+ independent Tier-1a sources per claim
  4. distill_qa             — verified, ≥2-citation chats -> eval-set candidates

All four are deterministic, zero-LLM and zero paid API, so running the preview
hourly costs nothing but produces the evidence the operator needs.

WRITES STAY GATED, AND FOR A CONCRETE REASON — not caution theatre. `distill_qa`
seeds the 500-Q eval set, and Phase A gate #6 passes only while the live set
still matches the pinned content hash (`a07b6af760ad7f44`, count 500). A commit
would DRIFT that pin and **re-open a closed Phase A gate**. Flipping
`ARIA_COST_FREE_LEARN_WRITE` therefore requires a deliberate re-pin, which is an
operator decision, not a side effect of enabling a preview.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


_TASKS_YAML = Path(__file__).resolve().parents[1] / "autonomous" / "tasks.yaml"


@pytest.fixture(scope="module")
def task():
    import yaml
    with _TASKS_YAML.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return next(t for t in data["tasks"] if t["id"] == "HOURLY-COST-FREE-LEARN")


def test_the_preview_task_is_enabled(task):
    """Otherwise its own 'sanity-check before writes' gate can never open."""
    assert task["enabled"] is True, (
        "the cost-free preview is off, so it produces no evidence — and the "
        "write gate it exists to inform can therefore never be opened"
    )


def test_the_preview_remains_zero_cost(task):
    """'Cost-free' must stay literally zero — this is what makes it safe to run."""
    assert float(task["cost_cap_usd"]) == 0.0
    assert int(task["timeout_seconds"]) > 0, "an unbounded task is not safe to enable"


def test_writes_are_still_gated_by_env():
    """Enabling the PREVIEW must not enable WRITES.

    `distill_qa` seeds the 500-Q eval set; a commit would drift the frozen
    gate-#6 pin and re-open a closed Phase A gate.
    """
    from aria_service.intel import cost_free_learning as cfl

    env_val = os.environ.get("ARIA_COST_FREE_LEARN_WRITE", "").strip()
    assert env_val != "1", (
        "the write env is set in the test environment — this test cannot then "
        "prove the default is safe"
    )
    assert cfl._write_enabled() is False, (
        "writes are enabled by default — enabling the preview must never imply "
        "enabling commits to the eval set"
    )


def test_write_gate_is_exact_match_not_truthy():
    """A loose check would let 'false'/'0'/'no' enable writes."""
    from aria_service.intel import cost_free_learning as cfl

    for val, expected in (("1", True), ("0", False), ("true", False),
                          ("yes", False), ("", False)):
        os.environ["ARIA_COST_FREE_LEARN_WRITE"] = val
        try:
            assert cfl._write_enabled() is expected, (
                f"ARIA_COST_FREE_LEARN_WRITE={val!r} -> {cfl._write_enabled()}, "
                f"expected {expected}; the write gate must be an exact '1'"
            )
        finally:
            os.environ.pop("ARIA_COST_FREE_LEARN_WRITE", None)
