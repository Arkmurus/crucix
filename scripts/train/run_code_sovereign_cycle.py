"""R-F2444 — REAL code-sovereign SFT+eval cycle (bounded, always-terminate).

Every primitive is proven: pod lifecycle (R-F2442), SSH exec + sft_train +
adapter-emit (R-F2443), serve_eval_shim serving, eval_mined_tier scoring. This
chains them into the real measured cycle:

  create pod -> ssh -> install -> SFT Qwen2.5-Coder-7B LoRA on the 212 mined
  pairs -> serve base+adapter (shim) -> eval on the 59-row frozen tier via the
  RunPod proxy -> compare resolved_rate vs the DeepSeek bar -> ALWAYS terminate.

Safety: durable pod-id record, terminate in `finally` (retry-until-gone), a hard
wall-clock deadline that force-terminates, status written every phase. Does NOT
flip the live coder (§16 activation stays a separate operator step) — it MEASURES.

Run detached:
  nohup python scripts/train/run_code_sovereign_cycle.py > data/eval_reports/_code_cycle.log 2>&1 &
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts" / "train"))
import runpod_derisk as RD
import runpod_exec_smoke as SM

_STATUS = _REPO / "data" / "eval_reports" / "code_sovereign_cycle_status.json"
_SFT_TRAIN = _REPO / "scripts" / "train" / "sft_train.py"
_SHIM = _REPO / "scripts" / "train" / "serve_eval_shim.py"
_TRAIN_FILE = _REPO / "data" / "training" / "code_sft_v1.jsonl"
_EVAL_TIER = _REPO / "data" / "eval" / "mined_code_eval_tier.jsonl"
_EVAL_SCORER = _REPO / "scripts" / "eval" / "eval_mined_tier.py"
_DEEPSEEK_BAR = _REPO / "data" / "eval_reports" / "code_reasoning_mined_deepseek.json"
_SOV_OUT = _REPO / "data" / "eval_reports" / "code_reasoning_mined_sovereign_v0.json"

_BASE = "Qwen/Qwen2.5-Coder-7B-Instruct"
_SERVED = "aria-code-sovereign-v0"
_PORT = 8888
_HARD_DEADLINE_S = 150 * 60
_ADAPTER_DIR = "/workspace/code_sovereign"


def _status(**kw):
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
    print(f"[status] { {k: v for k, v in kw.items() if k != 'tail'} }", flush=True)


def main():
    key = RD._key()
    pub = SM._PUB_KEY.read_text(encoding="utf-8").strip()
    report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "base": _BASE,
              "train_pairs": sum(1 for _ in _TRAIN_FILE.open(encoding="utf-8")),
              "eval_rows": sum(1 for _ in _EVAL_TIER.open(encoding="utf-8")),
              "created": False, "sft_done": False, "served": False,
              "sovereign_resolved_rate": None, "deepseek_bar": None,
              "beats_bar": None, "terminated": False, "cost_usd": None, "error": None}
    t0 = time.time()
    pod_id = host = port = None
    cost_hr = None
    try:
        _status(phase="0_create", **report)
        body = {"name": "aria-code-sovereign", "imageName":
                "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
                "gpuTypeIds": ["NVIDIA A40", "NVIDIA RTX A6000", "NVIDIA L40S", "NVIDIA L40"],
                "gpuCount": 1, "cloudType": "SECURE", "containerDiskInGb": 120,
                "ports": [f"{_PORT}/http", "22/tcp"], "env": {"PUBLIC_KEY": pub}}
        code, data = RD._req("POST", "/pods", key, body)
        pod_id = data.get("id")
        if not pod_id:
            report["error"] = f"create failed http {code}: {json.dumps(data)[:300]}"
            _status(phase="fail_create", **report); return
        RD._record_write(pod_id)
        cost_hr = data.get("costPerHr")
        report["created"] = True
        _status(phase="1_wait_pod", pod_id=pod_id, cost_per_hr=cost_hr)

        while time.time() - t0 < _HARD_DEADLINE_S:
            _, d = RD._req("GET", f"/pods/{pod_id}", key)
            st = str(d.get("desiredStatus") or "").upper()
            host, pm = d.get("publicIp"), (d.get("portMappings") or {})
            port = str(pm.get("22") or "") if pm else ""
            cost_hr = d.get("costPerHr") or cost_hr
            if st == "RUNNING" and host and port:
                break
            time.sleep(10)
        if not (host and port):
            report["error"] = "no SSH endpoint"; _status(phase="fail_ssh", **report); return

        for _ in range(30):
            try:
                rc, out = SM._ssh(host, port, "echo ready", 15)
                if rc == 0 and "ready" in out:
                    break
            except subprocess.TimeoutExpired:
                pass
            time.sleep(10)
        _status(phase="2_ship")
        for local, remote in [(_SFT_TRAIN, "/workspace/sft_train.py"),
                              (_SHIM, "/workspace/serve_eval_shim.py"),
                              (_TRAIN_FILE, "/workspace/code_sft_v1.jsonl")]:
            if not SM._scp(host, port, local, remote):
                report["error"] = f"scp {local.name} failed"; _status(phase="fail_scp", **report); return

        # ── SFT (detached + POLL, never a long blocking SSH that can hang) ──
        # Install BOTH train and serve deps up front (missing uvicorn/fastapi
        # crashed the shim on the prior run). Launch training with nohup and poll
        # short SSH checks for completion — a long blocking SSH drops mid-train.
        _status(phase="3_sft")
        launch = (
            "nohup bash -c '"
            "export HF_HOME=/workspace/.cache/huggingface; "
            "pip install -q --disable-pip-version-check trl==0.12.2 transformers>=4.46 "
            "peft datasets accelerate fastapi uvicorn pydantic bitsandbytes 2>&1; "
            f"python /workspace/sft_train.py --base-model {_BASE} "
            "--train-file /workspace/code_sft_v1.jsonl "
            f"--output-dir {_ADAPTER_DIR} --epochs 3 --lora-rank 32 --lora-alpha 64 "
            "--max-seq-len 4096 --batch-size 1; "
            "echo SFT_EXIT=$? > /workspace/sft.done"
            "' > /workspace/sft.log 2>&1 & echo launched")
        SM._ssh(host, port, launch, 60)
        poll = ("cat /workspace/sft.done 2>/dev/null; echo ---; "
                f"test -f {_ADAPTER_DIR}/adapter_config.json && echo HAVE_ADAPTER; "
                "echo ---; tail -3 /workspace/sft.log 2>/dev/null")
        deadline = time.time() + 75 * 60
        while time.time() < deadline:
            time.sleep(30)
            try:
                _, pout = SM._ssh(host, port, poll, 40)
            except subprocess.TimeoutExpired:
                continue
            if "SFT_EXIT=" in pout:
                report["sft_done"] = "HAVE_ADAPTER" in pout
                _status(phase="3_sft_done", sft_done=report["sft_done"], tail=pout[-1000:])
                break
        else:
            report["error"] = "SFT poll deadline"; _status(phase="fail_sft", **report); return
        if not report["sft_done"]:
            report["error"] = "SFT did not emit adapter"; _status(phase="fail_sft", **report); return

        # ── serve (deps already installed above) ──
        _status(phase="4_serve")
        SM._ssh(host, port, "pkill -f jupyter 2>/dev/null; pkill -f serve_eval_shim 2>/dev/null; sleep 2", 30)
        serve_cmd = (
            f"export HF_HOME=/workspace/.cache/huggingface; "
            f"nohup env BASE_MODEL={_BASE} ADAPTER={_ADAPTER_DIR} MODEL_NAME={_SERVED} "
            f"PORT={_PORT} python /workspace/serve_eval_shim.py "
            "> /workspace/shim.log 2>&1 & echo started")
        SM._ssh(host, port, serve_cmd, 30)
        proxy = f"https://{pod_id}-{_PORT}.proxy.runpod.net"
        # wait for the model to load + serve
        ready = False
        for _ in range(40):  # ~13 min for a 7B load
            try:
                r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                                    f"{proxy}/v1/models"], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=30)
                if r.stdout.strip() == "200":
                    ready = True; break
            except Exception:
                pass
            time.sleep(20)
        report["served"] = ready
        _status(phase="4_serve_ready", served=ready, proxy=proxy)
        if not ready:
            report["error"] = "shim never served"; _status(phase="fail_serve", **report); return

        # ── eval on the frozen tier ──
        _status(phase="5_eval")
        ev = subprocess.run(
            [sys.executable, str(_EVAL_SCORER), "--eval-set", str(_EVAL_TIER),
             "--target", f"{proxy}/v1", "--model", _SERVED, "--api-key", "none",
             "--max-tokens", "2048", "--out", str(_SOV_OUT)],
            cwd=str(_REPO), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=90 * 60)
        try:
            srep = json.loads(_SOV_OUT.read_text(encoding="utf-8"))
            report["sovereign_resolved_rate"] = srep.get("resolved_rate")
        except Exception:
            report["error"] = "eval produced no report: " + (ev.stdout or ev.stderr)[-400:]
        if _DEEPSEEK_BAR.exists():
            report["deepseek_bar"] = json.loads(_DEEPSEEK_BAR.read_text(encoding="utf-8")).get("resolved_rate")
        if report["sovereign_resolved_rate"] is not None and report["deepseek_bar"] is not None:
            report["beats_bar"] = report["sovereign_resolved_rate"] >= report["deepseek_bar"]
        _status(phase="5_eval_done", sovereign_resolved_rate=report["sovereign_resolved_rate"],
                deepseek_bar=report["deepseek_bar"], beats_bar=report["beats_bar"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        _status(phase="exception", error=report["error"])
    finally:
        if pod_id:
            report["terminated"] = RD.terminate(pod_id, key)
            if report["terminated"]:
                RD._record_clear()
        elapsed = time.time() - t0
        if cost_hr:
            report["cost_usd"] = round(elapsed / 3600 * float(cost_hr), 3)
        report["elapsed_min"] = round(elapsed / 60, 1)
        verdict = ("MEASURED — sovereign beats DeepSeek: recommend staged activation"
                   if report.get("beats_bar") else
                   "MEASURED — sovereign does NOT beat DeepSeek: no-go, keep DeepSeek on coder path"
                   if report.get("beats_bar") is False else
                   f"INCOMPLETE — {report.get('error')}")
        report["verdict"] = verdict
        _status(phase="6_done", **report)
        print("\n=== CODE-SOVEREIGN CYCLE ===")
        print(json.dumps({k: v for k, v in report.items() if k not in ("tail",)}, indent=2))
        print("VERDICT:", verdict)
        if pod_id and not report["terminated"]:
            print("!!! MANUAL CLEANUP: python scripts/train/runpod_derisk.py --cleanup")


if __name__ == "__main__":
    main()
