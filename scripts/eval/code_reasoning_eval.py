"""R-F2431 — code-reasoning eval harness (OBJECTIVE, verifiable, reproducible).

Measures code-reasoning on ARIA's ACTUAL task: fixing gaps in code. This is the
code analogue of the 500-Q DD eval — it produces the number a future
code-sovereign must BEAT before it can replace DeepSeek on the coder's reasoning
path (R-F1366 pins DeepSeek there today).

The score is NOT an LLM-judge opinion. Each held-out task carries a reproduce
test that FAILS on the buggy code and must PASS on the fix (R-F1685
``reproduce_fail_to_pass`` discipline), plus sibling tests that must STAY green
(no-regression). A model "resolves" a task iff its generated fix:
  1. COMPILES   (py_compile of every changed file), AND
  2. RESOLVES   (the fail_to_pass node goes FAIL -> PASS), AND
  3. NO-REGRESSION (every pass_to_pass node stays green).

Each task is run in an isolated temp dir OUTSIDE the repo so the repo's pytest
config/conftest cannot leak in — fully reproducible on any box with Python +
pytest, no aria_service/chromadb/torch imports.

Usage:
  # DeepSeek baseline (current coder LLM) — defaults pull from DEEPSEEK_API_KEY
  python scripts/eval/code_reasoning_eval.py \
    --target https://api.deepseek.com/v1 --model deepseek-chat \
    --out data/eval_reports/code_reasoning_deepseek_baseline.json

  # A candidate sovereign on a vLLM endpoint
  python scripts/eval/code_reasoning_eval.py \
    --target http://localhost:8000/v1 --model aria-code-sovereign-v0 \
    --out data/eval_reports/code_reasoning_sovereign_v0.json

  # Scorer self-check only (no model calls) — proves the gate is objective
  python scripts/eval/code_reasoning_eval.py --self-check
"""
from __future__ import annotations

import argparse
import json
import os
import py_compile
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SET = _REPO_ROOT / "data" / "eval" / "code_reasoning_heldout.jsonl"

_PYTEST_TIMEOUT = 60  # seconds per node; a stdlib fixture test is sub-second


# ────────────────────────────── task IO ────────────────────────────────────
def load_tasks(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _materialize(work: Path, files: dict[str, str], task: dict) -> None:
    """Write source files + the reproduce test into ``work``."""
    for rel, content in files.items():
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    tp = work / task["test_path"]
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(task["test_content"], encoding="utf-8")


def _run_node(work: Path, node: str) -> bool:
    """Run one pytest node in ``work`` (isolated). Return True iff it PASSED."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", node, "-q", "-p", "no:cacheprovider",
             "-o", "addopts=", "--rootdir", str(work)],
            cwd=str(work),
            capture_output=True,
            text=True,
            timeout=_PYTEST_TIMEOUT,
        )
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _compiles(work: Path, files: dict[str, str]) -> bool:
    for rel in files:
        try:
            py_compile.compile(str(work / rel), doraise=True)
        except py_compile.PyCompileError:
            return False
        except Exception:
            return False
    return True


# ─────────────────────────── objective scorer ──────────────────────────────
def validate_task(task: dict) -> dict:
    """Prove the task is a genuine reproduce: on the BUGGY code the
    fail_to_pass node must FAIL and every pass_to_pass node must PASS.
    A task that does not fail on its own bug is INVALID (would be a free pass)."""
    work = Path(tempfile.mkdtemp(prefix="cre_val_"))
    try:
        _materialize(work, {task["module_path"]: task["buggy"]}, task)
        ftp_fails = not _run_node(work, task["fail_to_pass"])
        ptp_ok = all(_run_node(work, n) for n in task.get("pass_to_pass", []))
        return {"valid": bool(ftp_fails and ptp_ok),
                "fail_to_pass_fails_on_bug": ftp_fails,
                "pass_to_pass_green_on_bug": ptp_ok}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def score_fix(task: dict, fixed_files: dict[str, str]) -> dict:
    """OBJECTIVE score of a candidate fix. ``fixed_files`` maps module_path ->
    new content. Missing files fall back to the original buggy content (so a
    no-op fix scores as unresolved, never crashes)."""
    files = {task["module_path"]: fixed_files.get(task["module_path"], task["buggy"])}
    work = Path(tempfile.mkdtemp(prefix="cre_score_"))
    try:
        _materialize(work, files, task)
        compiles = _compiles(work, files)
        resolves = compiles and _run_node(work, task["fail_to_pass"])
        no_regr = compiles and all(_run_node(work, n) for n in task.get("pass_to_pass", []))
        resolved = bool(compiles and resolves and no_regr)
        return {"compiles": compiles, "resolves": resolves,
                "no_regression": no_regr, "resolved": resolved}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ───────────────────────────── model I/O ───────────────────────────────────
def build_prompt(task: dict) -> str:
    return (
        "You are ARIA's autonomous coder. Fix the bug in the file below so the "
        "failing test passes. Do NOT change the test. Return the COMPLETE "
        "corrected file, nothing else, in exactly this format:\n\n"
        "### FILE: <path>\n```python\n<full corrected file contents>\n```\n\n"
        f"GAP / TASK:\n{task['instruction']}\n\n"
        f"### FILE: {task['module_path']}\n```python\n{task['buggy']}\n```\n\n"
        f"REPRODUCE TEST ({task['test_path']}) — it currently FAILS:\n"
        f"```python\n{task['test_content']}\n```\n"
    )


def _parse_files(text: str) -> dict[str, str]:
    """Extract ``### FILE: path`` + fenced block pairs. Falls back to the first
    bare fenced code block keyed as the sole path if no header is present."""
    import re
    out: dict[str, str] = {}
    for m in re.finditer(r"### FILE:\s*(\S+)\s*\n```[a-zA-Z0-9]*\n(.*?)```", text, re.S):
        out[m.group(1).strip()] = m.group(2)
    return out


def _call_model(*, target_url: str, model: str, api_key: str | None,
                prompt: str, max_tokens: int, temperature: float) -> tuple[str, float, dict]:
    import httpx
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {"model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": temperature}
    t0 = time.time()
    with httpx.Client(timeout=180.0) as client:
        resp = client.post(f"{target_url}/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
    dt = time.time() - t0
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {}) or {}
    return content, dt, usage


# deepseek-chat published rates (USD / 1M tokens) — cost line only, §17.
_RATE_IN, _RATE_OUT = 0.27, 1.10


# ────────────────────────────── runner ─────────────────────────────────────
def run_eval(*, tasks: list[dict], target_url: str, model: str, api_key: str | None,
             max_tokens: int, temperature: float) -> dict:
    results, latencies = [], []
    tok_in = tok_out = 0
    for task in tasks:
        val = validate_task(task)
        if not val["valid"]:
            results.append({"id": task["id"], "bug_class": task["bug_class"],
                            "source_r": task.get("source_r"), "task_invalid": True,
                            "validation": val, "resolved": False})
            continue
        try:
            raw, dt, usage = _call_model(target_url=target_url, model=model, api_key=api_key,
                                         prompt=build_prompt(task),
                                         max_tokens=max_tokens, temperature=temperature)
            latencies.append(dt)
            tok_in += int(usage.get("prompt_tokens", 0) or 0)
            tok_out += int(usage.get("completion_tokens", 0) or 0)
            fixed = _parse_files(raw)
            score = score_fix(task, fixed)
            results.append({"id": task["id"], "bug_class": task["bug_class"],
                            "source_r": task.get("source_r"), "task_invalid": False,
                            "parsed_files": list(fixed.keys()), "latency_s": round(dt, 2),
                            **score})
            print(f"  [{task['id']:<32}] resolved={score['resolved']} "
                  f"compiles={score['compiles']} ({dt:.1f}s)")
        except Exception as exc:  # network / provider error — recorded, not silent
            results.append({"id": task["id"], "bug_class": task["bug_class"],
                            "source_r": task.get("source_r"), "task_invalid": False,
                            "error": f"{type(exc).__name__}: {exc}", "resolved": False})
            print(f"  [{task['id']:<32}] ERROR {type(exc).__name__}: {exc}")

    scored = [r for r in results if not r.get("task_invalid")]
    resolved = [r for r in scored if r.get("resolved")]
    compiled = [r for r in scored if r.get("compiles")]
    n = len(scored) or 1
    from collections import defaultdict
    by_class: dict[str, list[bool]] = defaultdict(list)
    by_tier: dict[str, list[bool]] = defaultdict(list)
    _tier = {t["id"]: t.get("tier", "floor") for t in tasks}
    for r in scored:
        by_class[r["bug_class"]].append(bool(r.get("resolved")))
        by_tier[_tier.get(r["id"], "floor")].append(bool(r.get("resolved")))
    return {
        "model": model, "target": target_url,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_tasks": len(tasks), "n_scored": len(scored),
        "n_invalid": len(results) - len(scored),
        "resolved_rate": round(len(resolved) / n, 4),
        "compile_rate": round(len(compiled) / n, 4),
        "resolved": len(resolved), "compiled": len(compiled),
        "by_bug_class": {k: {"resolved": sum(v), "total": len(v)} for k, v in sorted(by_class.items())},
        "by_tier": {k: {"resolved": sum(v), "total": len(v),
                        "rate": round(sum(v) / len(v), 4)} for k, v in sorted(by_tier.items())},
        "latency_p50_s": round(statistics.median(latencies), 2) if latencies else None,
        "latency_p95_s": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 2) if len(latencies) >= 2 else (round(latencies[0], 2) if latencies else None),
        "tokens_in": tok_in, "tokens_out": tok_out,
        "cost_usd": round(tok_in / 1e6 * _RATE_IN + tok_out / 1e6 * _RATE_OUT, 4),
        "results": results,
    }


def self_check(tasks: list[dict]) -> int:
    """Prove the scorer + task set are objective, WITHOUT any model:
      - every task is a genuine reproduce (fails on its own bug),
      - the GOLD fix scores resolved=True,
      - a NO-OP fix (buggy unchanged) scores resolved=False.
    Returns process exit code (0 == all invariants hold)."""
    ok = True
    for t in tasks:
        val = validate_task(t)
        gold = score_fix(t, {t["module_path"]: t["gold"]})
        noop = score_fix(t, {})  # no fixed files -> falls back to buggy
        good = val["valid"] and gold["resolved"] and not noop["resolved"]
        ok = ok and good
        print(f"  [{t['id']:<32}] valid={val['valid']} gold_resolved={gold['resolved']} "
              f"noop_resolved={noop['resolved']} -> {'OK' if good else 'BROKEN'}")
    print("SELF-CHECK:", "ALL OBJECTIVE" if ok else "BROKEN")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-set", default=str(_DEFAULT_SET))
    ap.add_argument("--target", default=os.getenv("ARIA_LLM_URL") or "https://api.deepseek.com/v1")
    ap.add_argument("--model", default=os.getenv("ARIA_CODER_LLM_MODEL") or "deepseek-chat")
    ap.add_argument("--api-key", default=os.getenv("DEEPSEEK_API_KEY") or None)
    ap.add_argument("--max-tokens", type=int, default=1600)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    tasks = load_tasks(Path(args.eval_set))
    if args.self_check:
        sys.exit(self_check(tasks))

    print(f"Running code-reasoning eval: {args.model} @ {args.target}  ({len(tasks)} tasks)")
    report = run_eval(tasks=tasks, target_url=args.target, model=args.model,
                      api_key=args.api_key, max_tokens=args.max_tokens,
                      temperature=args.temperature)
    print("\n=== SUMMARY ===")
    print(f"model={report['model']}  resolved_rate={report['resolved_rate']}  "
          f"compile_rate={report['compile_rate']}  "
          f"({report['resolved']}/{report['n_scored']} resolved)")
    print(f"by_bug_class={json.dumps(report['by_bug_class'])}")
    print(f"latency p50={report['latency_p50_s']}s p95={report['latency_p95_s']}s  "
          f"cost=${report['cost_usd']}")
    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {outp}")


if __name__ == "__main__":
    main()
