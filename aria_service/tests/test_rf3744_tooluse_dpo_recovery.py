"""R-F3744 capability tests for precise DPO evidence and recovery."""
from pathlib import Path

from scripts.train.compound_tooluse_cycle import build_generation_queue


def test_generation_queue_is_train_only_regressed_axes_and_deduplicated() -> None:
    train = [
        {"subject": "Alpha plc", "label": "tooluse_adverse", "id": 1},
        {"subject": "Alpha", "label": "tooluse_adverse", "id": 2},
        {"subject": "Alpha", "label": "tooluse_contradiction", "id": 3},
        {"subject": "Bravo", "label": "tooluse_trace", "id": 4},
    ]
    verdict = {"regressions": [
        {"label": "tooluse_adverse"}, {"label": "tooluse_contradiction"}]}

    queue = build_generation_queue(train, verdict)

    assert [row["id"] for row in queue] == [1, 3]


def test_recovery_persists_adapter_before_starting_paid_generation() -> None:
    code = (Path(__file__).resolve().parents[2] / "scripts" / "train" /
            "recover_tooluse_candidate.sh").read_text(encoding="utf-8")
    persisted = code.index('root@"$HOST":/workspace/eval/aria_tooluse_candidate_v2.tgz')
    started = code.index("echo STARTED")
    assert persisted < started
    assert "trap stop_pod EXIT" in code
    assert "pod_selfstop_watch_v04.sh" in code
    assert "adapter_config.json" in code
    assert "aria_tooluse_eval_trained_recovered.json" in code
    assert "aria_tooluse_cycle_recovered.log" in code


def test_generation_driver_requires_complete_report_from_train_queue() -> None:
    code = (Path(__file__).resolve().parents[2] / "scripts" / "train" /
            "pod_tooluse_generate.sh").read_text(encoding="utf-8")
    assert 'TRAIN_FILE="${TRAIN_FILE:-/workspace/datasets/aria_tooluse_dpo_generation.jsonl}"' in code
    assert 'not d.get("complete")' in code
    assert 'len(d.get("rows") or []) != int(d.get("total", -1))' in code
    install = code.index('log "installing pinned serving/evaluation runtime')
    serving = code.index('log "serving recovered candidate adapter')
    assert install < serving
    assert '"uvicorn"' in code[install:serving]
    assert "torch.cuda.is_available()" in code[install:serving]


def test_full_cycle_separates_sft_corpus_from_generation_queue_and_harvests_adapter() -> None:
    root = Path(__file__).resolve().parents[2]
    launch = (root / "scripts" / "train" / "tooluse_launch.sh").read_text(encoding="utf-8")
    pod = (root / "scripts" / "train" / "pod_tooluse_cycle.sh").read_text(encoding="utf-8")
    harvest = (root / "scripts" / "train" / "tooluse_harvest.sh").read_text(encoding="utf-8")

    assert 'GEN_LOCAL="${GEN_LOCAL:-$TRAIN_LOCAL}"' in launch
    assert "aria_tooluse_generation.jsonl" in launch
    assert 'GEN_LIMIT="${GEN_LIMIT:-150}"' in launch
    assert "GEN_LIMIT=$GEN_LIMIT" in launch
    assert '--eval-file "$GEN_FILE"' in pod
    assert '--train-file "$TRAIN_FILE"' in pod
    assert "aria_tooluse_candidate_adapter.tgz" in pod
    assert "aria_tooluse_candidate_latest.tgz" in harvest


def test_fresh_generation_driver_arms_watchdog_before_adapter_upload() -> None:
    code = (Path(__file__).resolve().parents[2] / "scripts" / "train" /
            "run_tooluse_generation.sh").read_text(encoding="utf-8")
    armed = code.index('grep -q ARMED')
    uploaded = code.index('log "uploading validated serving adapter')
    started = code.index('grep -q STARTED')
    assert armed < uploaded < started
    assert "trap release EXIT" in code
    assert "preflight_cycle" in code
    assert "tooluse_dpo_generation_v2.jsonl" in code
    assert "d.get(\"complete\") is not True" in code
    assert "len(d.get(\"rows\") or []) != expected" in code
    assert "awk '/\\/adapter_config.json$/" in code
    assert "harvest_diagnostics" in code
    assert "diagnostics harvested" in code
    assert "SFTP_UPLOAD=reput" in code
    assert "SFTP_UPLOAD=put" in code
    assert "test -f /workspace/aria_tooluse_candidate.tgz" in code
    assert 'timeout "$UPLOAD_SLICE" sftp' in code
    assert 'ADAPTER_SHA256=$(sha256sum "$ADAPTER_LOCAL"' in code
    assert "printf '%s  %s\\n' '$ADAPTER_SHA256' /workspace/aria_tooluse_candidate.tgz | sha256sum -c -" in code
    assert 'REMOTE_BYTES=$(TSSH' in code
    assert 'state=$STATE' in code
    assert '"$STATE" = RUNNING' in code
    state_written = code.index('echo "POD_ID=$POD_ID"')
    assert armed < state_written < uploaded
    assert "DEADLINE=$UPLOAD_DEADLINE" in code
    assert "DEADLINE=$GENERATION_DEADLINE" in code
    assert "kill \\$(cat /workspace/eval/_watchdog_pid)" in code
    assert "NOT_RUNNING" in code
    assert '"${REPORT_LOCAL}.partial"' in code
    assert 'REMOTE_ADAPTER="/workspace/checkpoints/$ARCHIVE_ADAPTER_DIR"' in code
    assert '"ADAPTER=\'$REMOTE_ADAPTER\' setsid nohup' in code
