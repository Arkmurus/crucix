"""R-F2367 — the §24 eval-contamination pre-flight guard must ABORT a
contaminated cycle and PASS a clean one.

Capability: run the REAL guard script (the one wired into run_v04_dpo_cycle.sh /
run_grounded_dpo_cycle.sh) via subprocess and assert its exit code — exit 2 when a
training file's prompts overlap the frozen eval set, exit 0 when clean. This is the
structural enforcement of CLAUDE.md §24 ("a contaminated cycle is cancelled, not
run"): if this test passes, a paid cycle physically cannot train on eval questions.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "scripts" / "train" / "preflight_eval_contamination.py"


def _write_jsonl(path: Path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _run(train_files, eval_file):
    args = [sys.executable, str(GUARD)]
    for t in train_files:
        args += ["--train", str(t)]
    args += ["--eval", str(eval_file), "--max-overlap", "0.01"]
    return subprocess.run(args, capture_output=True, text=True)


def test_rf2367_guard_aborts_on_contaminated(tmp_path):
    ev = tmp_path / "eval.jsonl"
    _write_jsonl(ev, [
        {"question": "What is the FAA troop strength?"},
        {"question": "Cite Saudi Arabia's 2024 defence budget."},
        {"question": "Is entity X sanctioned by OFAC?"},
    ])
    # A DPO file whose prompts ARE the eval questions (the contamination class).
    bad = tmp_path / "dpo_bad.jsonl"
    _write_jsonl(bad, [
        {"prompt": "What is the FAA troop strength?", "chosen": "a", "rejected": "b"},
        {"prompt": "Cite Saudi Arabia's 2024 defence budget.", "chosen": "a", "rejected": "b"},
        {"prompt": "Is entity X sanctioned by OFAC?", "chosen": "a", "rejected": "b"},
    ])
    res = _run([bad], ev)
    assert res.returncode == 2, f"guard did not abort on contaminated data (rc={res.returncode})\n{res.stdout}\n{res.stderr}"
    assert "CONTAMINATED" in res.stdout
    assert "ABORT" in res.stderr


def test_rf2367_guard_passes_clean(tmp_path):
    ev = tmp_path / "eval.jsonl"
    _write_jsonl(ev, [
        {"question": "What is the FAA troop strength?"},
        {"question": "Cite Saudi Arabia's 2024 defence budget."},
    ])
    # Clean training data: different prompts + a messages-schema record.
    clean = tmp_path / "clean.jsonl"
    _write_jsonl(clean, [
        {"prompt": "Explain end-user certificate red flags.", "chosen": "a", "rejected": "b"},
        {"messages": [{"role": "user", "content": "Summarise Wassenaar dual-use controls."},
                      {"role": "assistant", "content": "..."}], "topic": "compliance"},
    ])
    res = _run([clean], ev)
    assert res.returncode == 0, f"guard wrongly failed clean data (rc={res.returncode})\n{res.stdout}\n{res.stderr}"
    assert "OK" in res.stdout


def test_rf2367_guard_fails_closed_on_empty_eval(tmp_path):
    """A missing/empty eval set must FAIL CLOSED (can't verify → refuse), not pass."""
    ev = tmp_path / "empty_eval.jsonl"
    ev.write_text("", encoding="utf-8")
    clean = tmp_path / "clean.jsonl"
    _write_jsonl(clean, [{"prompt": "anything", "chosen": "a", "rejected": "b"}])
    res = _run([clean], ev)
    assert res.returncode == 2, "guard must fail closed when the eval set yields 0 questions"
