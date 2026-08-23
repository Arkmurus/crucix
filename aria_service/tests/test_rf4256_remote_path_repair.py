"""R-F4256 — Git Bash rewrote a REMOTE path before the harvester saw it.

Measured 2026-08-23 on the live R-F4249 harvest: `--report /workspace/eval/x.json=...`
reached Python as `C:/Program Files/Git/workspace/eval/x.json=...`. MSYS rewrites
any argument that looks like a POSIX path, and the harvest then failed on a path
that exists on no machine — AFTER resuming the pod, so the mistake cost a start.

AGENTS.md anti-hallucination law 14: Windows is not Linux.
"""
from __future__ import annotations

import pytest

from scripts.train.harvest_cycle import repair_remote_path


class TestTheMeasuredMangling:
    def test_the_exact_string_git_bash_produced(self):
        mangled = "C:/Program Files/Git/workspace/eval/aria_tooluse_dpo_eval.json"
        assert repair_remote_path(mangled) == "/workspace/eval/aria_tooluse_dpo_eval.json"

    def test_a_clean_remote_path_is_untouched(self):
        clean = "/workspace/eval/aria_tooluse_dpo_eval.json"
        assert repair_remote_path(clean) == clean

    @pytest.mark.parametrize("prefix", [
        "C:/Program Files/Git", "C:/msys64", "/c/Program Files/Git", "D:/git"])
    def test_any_local_prefix_is_stripped(self, prefix):
        assert repair_remote_path(f"{prefix}/workspace/eval/x.json") \
            == "/workspace/eval/x.json"


class TestItDoesNotOverreach:
    def test_a_path_outside_workspace_is_left_alone(self):
        """Only /workspace is a known remote root; inventing repairs for other
        paths would silently rewrite something a caller meant."""
        other = "/some/other/path.json"
        assert repair_remote_path(other) == other

    def test_a_bare_workspace_root_at_position_zero_is_untouched(self):
        assert repair_remote_path("/workspace/") == "/workspace/"

    def test_only_the_first_occurrence_roots_the_path(self):
        assert repair_remote_path("C:/git/workspace/eval/workspace/x.json") \
            == "/workspace/eval/workspace/x.json"


class TestTheHarvestCliAppliesIt:
    def test_the_cli_repairs_before_building_the_report_list(self):
        import pathlib
        source = pathlib.Path(
            __file__).resolve().parents[2] / "scripts/train/harvest_cycle.py"
        text = source.read_text(encoding="utf-8")
        assert "repair_remote_path(remote)" in text, (
            "the repair must be applied at the CLI boundary, where the mangling "
            "happens — not left as an unused helper"
        )
