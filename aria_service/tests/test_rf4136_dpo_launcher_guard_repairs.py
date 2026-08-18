"""R-F4136 capability guards for current DPO launcher contracts."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_upload_watchdog_state_is_persisted_before_adapter_transfer() -> None:
    code = (ROOT / "scripts/train/run_tooluse_generation.sh").read_text(encoding="utf-8")
    armed = code.index('arm_watchdog \\\n  "POD_ID=')
    state_written = code.index("write_state upload", armed)
    uploaded = code.index('log "uploading validated serving adapter')
    assert armed < state_written < uploaded


def test_pod_dpo_runner_preserves_default_and_accepts_reviewed_accumulation() -> None:
    code = (ROOT / "scripts/train/pod_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert 'DPO_GRAD_ACCUM="${DPO_GRAD_ACCUM:-1}"' in code
    assert '--gradient-accumulation-steps "$DPO_GRAD_ACCUM"' in code
