"""R-F3843 capability tests for mixed retention + genuine DPO training."""
from pathlib import Path

import pytest

from scripts.train.build_mixed_tooluse_cycle import (
    ALL_AXES,
    RETENTION_AXES,
    TARGET_AXES,
    select_retention_rows,
    validate_dpo,
)


ROOT = Path(__file__).resolve().parents[2]


def _train() -> list[dict]:
    return [
        {"subject": f"{axis}-{i}", "label": axis,
         "messages": [{"role": "user", "content": "q"},
                      {"role": "assistant", "content": "grounded"}]}
        for axis in sorted(RETENTION_AXES) for i in range(3)
    ]


def test_retention_quotas_are_deterministic_and_protected_only() -> None:
    first = select_retention_rows(_train(), quota=2, forbidden_subjects=set())
    second = select_retention_rows(_train(), quota=2, forbidden_subjects=set())
    assert first == second
    assert len(first) == 2 * len(RETENTION_AXES)
    assert {row["label"] for row in first} == RETENTION_AXES


def test_retention_fails_closed_on_held_out_contamination() -> None:
    with pytest.raises(ValueError, match="overlaps held-out"):
        select_retention_rows(_train(), quota=2,
                              forbidden_subjects={"tooluse adverse 0"})


def test_dpo_requires_real_negative_and_failure_evidence_for_every_target_axis() -> None:
    rows = [{"subject": axis, "label": axis, "prompt": [{"role": "user", "content": "q"}],
             "chosen": "honest", "rejected": "observed failure", "why": "validator error"}
            for axis in sorted(TARGET_AXES)]
    assert set(validate_dpo(rows, set())) == TARGET_AXES
    rows[0]["rejected"] = rows[0]["chosen"]
    with pytest.raises(ValueError, match="genuine preference"):
        validate_dpo(rows, set())


def test_all_ten_axes_have_explicit_learning_or_retention_signal() -> None:
    assert TARGET_AXES.isdisjoint(RETENTION_AXES)
    assert TARGET_AXES | RETENTION_AXES == ALL_AXES
    assert len(ALL_AXES) == 10


def test_paid_runner_pins_reference_arms_watchdog_and_persists_both_phases() -> None:
    pod = (ROOT / "scripts/train/pod_tooluse_mixed.sh").read_text(encoding="utf-8")
    host = (ROOT / "scripts/train/run_tooluse_mixed.sh").read_text(encoding="utf-8")
    assert '--sft-checkpoint "$SFT_OUT"' in pod
    assert "dpo_train.py" in pod and "sft_train.py" in pod
    assert pod.count("require_watchdog") >= 3
    assert "mv \"$SFT_ARCHIVE.tmp\" \"$SFT_ARCHIVE\"" in pod
    assert "mv \"$DPO_ARCHIVE.tmp\" \"$DPO_ARCHIVE\"" in pod
    delegated = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert "pod_selfstop_watch_v04.sh" in delegated
    assert delegated.index("pod_selfstop_watch_v04.sh") < delegated.index("setsid nohup bash /workspace/pod_tooluse_dpo.sh")
    assert "complete\") is not True" in host
    assert 'SFT_SHA256="$SFT_SHA"' in host
    delegated = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert "remote retention SFT hash mismatch" in delegated
    assert 'persist_intermediate || true' in delegated
