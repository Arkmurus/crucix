"""R-F4206 capability gates for observed conditional-skip integrity gaps."""

import asyncio
from unittest.mock import patch

from aria_service.intel import symbolic_reasoner
from aria_service.tests import test_rf3912_c34_precommit_hook_fails_closed as hook_tests


def test_default_symbolic_reasoning_emits_to_the_brain():
    """The formerly skipped real emission path must reach its brain sink."""
    absorbed: list[dict] = []

    async def fake_absorb(**kwargs):
        absorbed.append(kwargs)

    async def run():
        with patch("aria_service.intel.brain_hook.absorb", side_effect=fake_absorb):
            symbolic_reasoner.reason(
                "Explain quantum gardening policy in an unknown lunar market"
            )
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert len(absorbed) == 1
    assert absorbed[0]["gap_type"] == "no_symbolic_rule"
    assert absorbed[0]["success"] is False


def test_windows_git_shell_is_resolved_for_real_hook_capability_tests():
    """A Git-for-Windows shell must activate rather than accidentally skip hooks."""
    assert hook_tests.HOOK.exists()
    assert hook_tests.SH is not None
    assert hook_tests._HOOK_UNAVAILABLE is False
