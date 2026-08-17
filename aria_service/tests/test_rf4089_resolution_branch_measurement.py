"""R-F4089 guards for generation-only measurement on fresh resolution branches."""
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_launcher_pins_evidence_parent_and_generation_only_path() -> None:
    queue = ROOT / "data/training/aria_tooluse_resolution_branch_expansion_v1.jsonl"
    launcher = (
        ROOT / "scripts/train/run_tooluse_resolution_branch_expansion_generation.sh"
    ).read_text(encoding="utf-8")

    assert hashlib.sha256(queue.read_bytes()).hexdigest() in launcher
    assert "223ba1dc99d0e65aafdfa3f5190d57e0e8dfdd4013f9fab5be3994af63384998" in launcher
    assert "ARIA_POD_CREATE_API=graphql" in launcher
    assert "ARIA_MAX_GPU_HOURLY_USD=1.60" in launcher
    assert "exec bash scripts/train/run_tooluse_generation.sh" in launcher
    assert "run_tooluse_dpo.sh" not in launcher
    assert "run_tooluse_sft" not in launcher
