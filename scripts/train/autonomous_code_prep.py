"""R-F2440 — autonomous, unattended prep-to-READY orchestrator for the
code-sovereign cycle. Runs detached (operator away, full-autonomy grant) and
drives everything that is SAFE + FREE to completion, then stages the paid GPU
cycle with the exact go/no-go bar computed.

Phases (each wrapped so one failure never strands the run):
  1. WAIT for the git-fix mine to finish (poll the process; hard 14h cap).
  2. DEDUP the verified corpus by sha (the running miner used pre-dedup code).
  3. RE-SPLIT 80/20 (prefer multi-file for the eval tier).
  4. PRE-FLIGHT + build the SFT corpus (prepare_code_sft) — §24 readiness gate.
  5. RE-BASELINE DeepSeek on the refreshed eval tier -> the activation bar.
  6. WRITE a status/go-no-go JSON the operator reads when back.

It deliberately does NOT provision a GPU or flip the live coder: an unattended,
never-tested pod orchestration risks a runaway paid pod (§19e) and activating an
unproven 7B on the live path on thin eval evidence is not robust. It gets the
cycle to one-command-launch-ready with the measured bar in hand.

Run detached:
  nohup python scripts/train/autonomous_code_prep.py > data/eval_reports/_auto_code_prep.log 2>&1 &
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_STATUS = _REPO / "data" / "eval_reports" / "code_sovereign_status.json"
_CORPUS = _REPO / "data" / "training" / "mined_code_fixes_verified.jsonl"
_TRAIN = _REPO / "data" / "training" / "mined_code_fixes_train.jsonl"
_EVAL = _REPO / "data" / "eval" / "mined_code_eval_tier.jsonl"
_CKPT = _REPO / "data" / "eval" / "mine_checkpoint.json"
_MAX_WAIT_S = 14 * 3600
_POLL_S = 60


def _status(**kw) -> None:
    prev = {}
    if _STATUS.exists():
        try:
            prev = json.loads(_STATUS.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    prev.update(kw)
    prev["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _STATUS.parent.mkdir(parents=True, exist_ok=True)
    _STATUS.write_text(json.dumps(prev, indent=2), encoding="utf-8", newline="\n")
    print(f"[status] {kw}")


def _mine_running() -> bool:
    """True if a mine_git_fixes.py process is alive (Windows tasklist/wmic)."""
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "CommandLine"],
            capture_output=True, text=True, timeout=30).stdout
        return "mine_git_fixes" in out
    except Exception:
        # fall back to POSIX ps
        try:
            out = subprocess.run(["ps", "-W"], capture_output=True, text=True, timeout=30).stdout
            return "mine_git_fixes" in out
        except Exception:
            return False


def _processed() -> int:
    try:
        return len(json.loads(_CKPT.read_text(encoding="utf-8")).get("done", {}))
    except Exception:
        return 0


def wait_for_mine() -> None:
    t0 = time.time()
    last, stagnant = -1, 0
    while time.time() - t0 < _MAX_WAIT_S:
        running = _mine_running()
        p = _processed()
        _status(phase="1_wait_mine", mine_running=running, mine_processed=p)
        if not running:
            # give the final report a moment, then proceed
            time.sleep(5)
            return
        stagnant = stagnant + 1 if p == last else 0
        last = p
        # if the process is gone-but-detectable-as-stuck: 40 min no progress -> proceed
        if stagnant * _POLL_S > 2400:
            _status(phase="1_wait_mine", note="no progress 40m — proceeding")
            return
        time.sleep(_POLL_S)
    _status(phase="1_wait_mine", note="hit 14h cap — proceeding with what exists")


def merge_shards() -> int:
    """Merge the base corpus + all shard corpora into _CORPUS, dedup by sha.
    Safe to call once every shard worker has exited."""
    seen, out = set(), []
    sources = [_CORPUS] + sorted(_CORPUS.parent.glob("mined_code_fixes_verified.shard*.jsonl"))
    for src in sources:
        if not src.exists():
            continue
        for l in src.read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            r = json.loads(l)
            if r["sha"] in seen:
                continue
            seen.add(r["sha"])
            out.append(r)
    _CORPUS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8", newline="\n")
    return len(out)


def dedup_corpus() -> int:
    return merge_shards()


def resplit() -> tuple[int, int]:
    rows = [json.loads(l) for l in _CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]
    n_eval = max(1, round(len(rows) * 0.2))
    multi = sorted([r for r in rows if r["multi_file"]], key=lambda r: r["sha"])
    single = sorted([r for r in rows if not r["multi_file"]], key=lambda r: r["sha"])
    eval_rows = (multi + single)[:n_eval]
    eval_shas = {r["sha"] for r in eval_rows}
    train_rows = [r for r in rows if r["sha"] not in eval_shas]
    _TRAIN.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in train_rows), encoding="utf-8", newline="\n")
    _EVAL.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in eval_rows), encoding="utf-8", newline="\n")
    return len(train_rows), len(eval_rows)


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> None:
    _status(phase="0_start", note="autonomous code-sovereign prep started")
    # 1
    wait_for_mine()
    # 2
    try:
        n = dedup_corpus()
        _status(phase="2_dedup", verified_unique=n)
    except Exception as e:
        _status(phase="2_dedup", error=str(e))
    # 3
    try:
        tr, ev = resplit()
        _status(phase="3_split", train_rows=tr, eval_rows=ev)
    except Exception as e:
        _status(phase="3_split", error=str(e))
    # 4 preflight + SFT corpus
    sft_ready, pairs = False, 0
    try:
        rc, out = _run([sys.executable, "scripts/train/prepare_code_sft.py",
                        "--out", "data/training/code_sft_v1.jsonl"])
        for line in out.splitlines():
            if line.startswith("SFT pairs written:"):
                pairs = int("".join(c for c in line.split(":")[1] if c.isdigit()) or "0")
            if line.startswith("SFT-READY") and "True" in line:
                sft_ready = True
        _status(phase="4_preflight", sft_pairs=pairs, sft_ready=sft_ready, preflight_tail=out[-600:])
    except Exception as e:
        _status(phase="4_preflight", error=str(e))
    # 5 DeepSeek bar on refreshed eval tier
    ds_rate = None
    try:
        import os
        env = os.environ
        # load .env DEEPSEEK_API_KEY if present
        envf = _REPO / ".env"
        if envf.exists() and not env.get("DEEPSEEK_API_KEY"):
            for l in envf.read_text(encoding="utf-8").splitlines():
                if l.startswith("DEEPSEEK_API_KEY="):
                    os.environ["DEEPSEEK_API_KEY"] = l.split("=", 1)[1].strip()
        rc, out = _run([sys.executable, "scripts/eval/eval_mined_tier.py",
                        "--eval-set", "data/eval/mined_code_eval_tier.jsonl",
                        "--target", "https://api.deepseek.com/v1", "--model", "deepseek-chat",
                        "--out", "data/eval_reports/code_reasoning_mined_deepseek.json"])
        rep = json.loads((_REPO / "data/eval_reports/code_reasoning_mined_deepseek.json").read_text(encoding="utf-8"))
        ds_rate = rep.get("resolved_rate")
        _status(phase="5_deepseek_bar", deepseek_resolved_rate=ds_rate, deepseek_n=rep.get("n_tasks"))
    except Exception as e:
        _status(phase="5_deepseek_bar", error=str(e))
    # 6 go/no-go
    go = bool(sft_ready)
    _status(
        phase="6_ready" if go else "6_blocked",
        SFT_READY=sft_ready, sft_pairs=pairs,
        deepseek_bar=ds_rate,
        activation_gate=f"sovereign must BEAT deepseek resolved_rate={ds_rate} on the frozen eval tier",
        next_action=(
            "LAUNCH: bash scripts/train/launch_code_cycle.sh (SFT-READY) -> run pod runbook; "
            "activate live coder ONLY if sovereign beats the bar"
            if go else
            "HOLD: corpus not SFT-READY (thin); do not spend a paid cycle (§24)"),
        note="autonomous prep complete",
    )
    print("=== AUTONOMOUS PREP COMPLETE ===")
    print(_STATUS.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
