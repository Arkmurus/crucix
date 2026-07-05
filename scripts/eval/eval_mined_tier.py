"""R-F2434 — scorer + baseline for the mined (multi-file, real-repo) eval tier.

The R-F2431 harness measures isolated single-file bugs and DeepSeek SATURATES it
(16/16). This tier is the discriminating instrument: real ARIA fixes mined from
git history (data/eval/mined_code_eval_tier.jsonl), each a multi-file change with
its real reproduce test. Scoring reuses the mine_git_fixes sandbox exactly —
objective, no LLM judge: the model's fix RESOLVES iff, with the unchanged
dependency modules re-pulled at the commit and the buggy files replaced by the
model's output, the commit's own test PASSES.

Self-check (no model): every row's GOLD fix must RESOLVE and its BUGGY state must
FAIL — proving the tier is a genuine reproduce set from the STORED corpus content
(independent of the miner's live run).

Usage:
  python scripts/eval/eval_mined_tier.py --self-check
  python scripts/eval/eval_mined_tier.py --target https://api.deepseek.com/v1 \
    --model deepseek-chat --out data/eval_reports/code_reasoning_mined_deepseek.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts" / "eval"))
import mine_git_fixes as M  # reuse the exact sandbox verification machinery

_DEFAULT_SET = _REPO / "data" / "eval" / "mined_code_eval_tier.jsonl"
_RATE_IN, _RATE_OUT = 0.27, 1.10


def load_rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _pull_deps(row: dict) -> dict[str, str]:
    """Re-pull the exact unchanged dependency modules recorded at mine time."""
    deps: dict[str, str] = {}
    for p in row.get("dep_modules", []):
        content = M.show_file(row["sha"], p)
        if content is not None:
            deps[p] = content
    return deps


def _run_state(row: dict, source_files: dict[str, str | None], deps: dict[str, str]) -> bool:
    """Materialize source_files + deps + the verify test, run it, return passed."""
    root = Path(tempfile.mkdtemp(prefix="mined_eval_"))
    try:
        M._materialize_state(root, source_files, deps, row["verify_test"])
        time.sleep(0.05)
        passed, _ = M._run_tests(root, list(row["verify_test"].keys()))
        return passed
    finally:
        shutil.rmtree(root, ignore_errors=True)


def score_windows(row: dict, fixed_windows: dict[int, str],
                  deps: dict[str, str] | None = None) -> dict:
    """OBJECTIVE score: apply each returned window edit over the buggy source
    (exact-match replace of the original region), run the commit's real test.
    A window whose original text isn't found = failed edit (region left buggy),
    so an incomplete/wrong answer fails, never crashes."""
    if deps is None:
        deps = _pull_deps(row)
    applied: dict[str, str | None] = dict(row["buggy_context"])
    wins = row.get("_windows", {})
    n_applied = 0
    for idx, new_text in fixed_windows.items():
        if idx not in wins:
            continue
        path, orig = wins[idx]
        cur = applied.get(path)
        if cur is None or orig not in cur:
            continue
        applied[path] = cur.replace(orig, new_text, 1)
        n_applied += 1
    resolves = _run_state(row, applied, deps)
    return {"resolved": bool(resolves), "windows_total": len(wins),
            "windows_applied": n_applied}


def self_check(rows: list[dict]) -> int:
    """Prove BOTH the corpus and the localized-edit INTERFACE are objective:
      - gold FILES resolve, buggy FILES fail (genuine reproduce), and
      - the oracle WINDOW edits (perfect model) also resolve — so a correct
        localized answer is scorable (the interface itself isn't the blocker)."""
    ok = True
    for r in rows:
        deps = _pull_deps(r)
        gold = _run_state(r, r["fix"], deps)
        buggy = _run_state(r, r["buggy_context"], deps)
        build_prompt(r)  # populate _windows / _oracle
        oracle = score_windows(r, dict(r.get("_oracle", {})), deps)
        good = gold and not buggy and oracle["resolved"]
        ok = ok and good
        print(f"  [{r['r_number']:<9} {r['sha'][:8]}] gold_pass={gold} buggy_pass={buggy} "
              f"oracle_window_pass={oracle['resolved']} "
              f"({oracle['windows_applied']}/{oracle['windows_total']} win) deps={len(deps)} "
              f"-> {'OK' if good else 'BROKEN'}")
    print("SELF-CHECK:", "ALL OBJECTIVE" if ok else "BROKEN")
    return 0 if ok else 1


import difflib


def aligned_windows(buggy: str, gold: str, ctx: int = 12) -> list[tuple[str, str]]:
    """Oracle-localized edit windows: (buggy_region, gold_region) pairs for each
    changed line-range, expanded by ``ctx`` lines into surrounding equal context
    and merged. The model is shown buggy_region (real modules are 10-26k lines —
    whole-file emit is infeasible + off-task); gold_region is the oracle answer
    used only to self-check the interface. A pure reasoning measure."""
    b = buggy.splitlines(keepends=True)
    g = gold.splitlines(keepends=True)
    sm = difflib.SequenceMatcher(a=b, b=g, autojunk=False)
    opcodes = sm.get_opcodes()
    # buggy-line -> gold-line map over equal blocks (exact within context)
    bmap: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for k in range(i2 - i1 + 1):
                bmap[i1 + k] = j1 + k
    ranges: list[tuple[int, int, int, int]] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue
        bs, be = max(0, i1 - ctx), min(len(b), i2 + ctx)
        gs, ge = max(0, j1 - ctx), min(len(g), j2 + ctx)
        ranges.append((bs, be, gs, ge))
    if not ranges:
        return []
    ranges.sort()
    merged = [list(ranges[0])]
    for bs, be, gs, ge in ranges[1:]:
        if bs <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], be)
            merged[-1][3] = max(merged[-1][3], ge)
        else:
            merged.append([bs, be, gs, ge])
    return [("".join(b[bs:be]), "".join(g[gs:ge])) for bs, be, gs, ge in merged]


def build_prompt(row: dict) -> str:
    windows: list[tuple[int, str, str, str]] = []  # (idx, path, buggy_text, gold_text)
    for p in row["source_files"]:
        buggy = row["buggy_context"].get(p)
        gold = row["fix"].get(p)
        if buggy is None or gold is None:
            continue
        for bw, gw in aligned_windows(buggy, gold):
            windows.append((len(windows) + 1, p, bw, gw))
    win_block = "\n".join(
        f"### WINDOW {i} file: {p}\n```python\n{bw}\n```" for i, p, bw, gw in windows)
    test_block = "\n".join(
        f"### TEST: {p}\n```python\n{c}\n```" for p, c in row["verify_test"].items())
    prompt = (
        "You are ARIA's autonomous coder. Below are the exact code REGION(S) that "
        "must be edited to make the failing test pass, taken verbatim from real "
        "(very large) source files. For EACH numbered window, return the FULL "
        "corrected version of that same region, in order, in this exact format:\n\n"
        "### FIXED <n>\n```python\n<corrected region>\n```\n\n"
        "Preserve everything you are not changing; only fix the bug. Do NOT edit "
        "the test.\n\n"
        f"GAP / TASK:\n{row['instruction']}\n\n"
        f"REGION(S) TO FIX:\n{win_block}\n\n"
        f"REPRODUCE TEST (currently FAILS — do not edit it):\n{test_block}\n"
    )
    # stash the windows on the row for scoring (idx -> (path, buggy text, gold text))
    row["_windows"] = {i: (p, bw) for i, p, bw, gw in windows}
    row["_oracle"] = {i: gw for i, p, bw, gw in windows}
    return prompt


_FIXED_RE = re.compile(r"### FIXED\s*(\d+)\s*\n```[a-zA-Z0-9]*\n(.*?)```", re.S)


def parse_fixed(text: str) -> dict[int, str]:
    return {int(m.group(1)): m.group(2) for m in _FIXED_RE.finditer(text)}


def _call_model(target_url, model, api_key, prompt, max_tokens, temperature):
    import httpx
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": temperature}
    t0 = time.time()
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(f"{target_url}/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"], time.time() - t0, (data.get("usage") or {})


def run_eval(rows, *, target_url, model, api_key, max_tokens, temperature):
    results, lat = [], []
    tin = tout = 0
    for r in rows:
        deps = _pull_deps(r)
        try:
            prompt = build_prompt(r)  # also populates r["_windows"]
            raw, dt, usage = _call_model(target_url, model, api_key, prompt,
                                         max_tokens, temperature)
            lat.append(dt)
            tin += int(usage.get("prompt_tokens", 0) or 0)
            tout += int(usage.get("completion_tokens", 0) or 0)
            fw = parse_fixed(raw)
            sc = score_windows(r, fw, deps)
            results.append({"r_number": r["r_number"], "sha": r["sha"],
                            "multi_file": r["multi_file"], "n_source": len(r["source_files"]),
                            "latency_s": round(dt, 2), **sc})
            print(f"  [{r['r_number']:<9}] resolved={sc['resolved']} "
                  f"(windows {sc['windows_applied']}/{sc['windows_total']} applied) {dt:.1f}s")
        except Exception as exc:
            results.append({"r_number": r["r_number"], "sha": r["sha"],
                            "error": f"{type(exc).__name__}: {exc}", "resolved": False})
            print(f"  [{r['r_number']:<9}] ERROR {type(exc).__name__}: {exc}")
    n = len(rows) or 1
    resolved = sum(1 for x in results if x.get("resolved"))
    return {
        "model": model, "target": target_url, "tier": "mined_multi_file",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_tasks": len(rows), "resolved": resolved,
        "resolved_rate": round(resolved / n, 4),
        "latency_p50_s": round(statistics.median(lat), 2) if lat else None,
        "tokens_in": tin, "tokens_out": tout,
        "cost_usd": round(tin / 1e6 * _RATE_IN + tout / 1e6 * _RATE_OUT, 4),
        "results": results,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-set", default=str(_DEFAULT_SET))
    ap.add_argument("--target", default=os.getenv("ARIA_LLM_URL") or "https://api.deepseek.com/v1")
    ap.add_argument("--model", default=os.getenv("ARIA_CODER_LLM_MODEL") or "deepseek-chat")
    ap.add_argument("--api-key", default=os.getenv("DEEPSEEK_API_KEY") or None)
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    rows = load_rows(Path(args.eval_set))
    if args.self_check:
        sys.exit(self_check(rows))

    print(f"Mined-tier eval: {args.model} @ {args.target} ({len(rows)} multi-file tasks)")
    rep = run_eval(rows, target_url=args.target, model=args.model, api_key=args.api_key,
                   max_tokens=args.max_tokens, temperature=args.temperature)
    print("\n=== SUMMARY ===")
    print(f"model={rep['model']} resolved_rate={rep['resolved_rate']} "
          f"({rep['resolved']}/{rep['n_tasks']})  p50={rep['latency_p50_s']}s cost=${rep['cost_usd']}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
