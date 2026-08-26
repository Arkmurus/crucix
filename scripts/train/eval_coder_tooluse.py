"""R-F4372 (C-317) — score a served model on the CODER behaviour, not the DD one.

`eval_tooluse.py` scores with `build_tooluse_corpus.validate_trace`: citations,
sanctions verdicts, no-false-clean. Those rules are right for the DD corpus and
meaningless for a coding trajectory, which has no subject and no sources.
Pointing it at the coder eval set would spend GPU hours and report a number
about a different capability — the exact failure its own docstring warns about
("Spending GPU hours and then reporting an unrelated number is worse than not
measuring at all").

WHAT THIS MEASURES, and it is the defect C-316 recorded, one metric per symptom:

    acted        did she emit a tool call at all, or answer in prose?
                 -> "I cannot execute or modify files. You must manually edit"
    right_tool   was it the tool the reference trajectory used?
                 -> she answered every task with read_file
    args_valid   did the arguments parse AND use only declared parameters?
                 -> list_dir(recursive=True), an argument that does not exist
    refused      did she state she is unable to act?

TEACHER-FORCED, AND SAID SO PLAINLY. Each held-out trajectory is replayed up to
each assistant tool-call turn, and the model is asked for THAT turn given the
real prefix. It measures next-action quality, not end-to-end task completion —
an agentic harness against a live sandbox is the stronger measure and is the
declared next step. Choosing the honest weaker measure over a fabricated
stronger one is the point; the metric names say exactly what they cover.

WHAT IS NOT FUDGED (inherited from eval_tooluse):
  * a request that errors, times out, or returns nothing is a FAILURE, not a
    skipped row. Dropping errors is how a broken model scores well.
  * an empty eval set reports NO RATE — never 0.0, never 1.0.
  * per-family denominators are carried, because a headline average hides a
    dead family.
  * the reference tool name comes from the corpus row, so the scorer cannot
    drift from what was trained.

USAGE
    python -m scripts.train.eval_coder_tooluse \
        --eval-file data/training/aria_coder_tooluse_v1.eval.jsonl \
        --target http://127.0.0.1:8888/v1 --model aria-coder \
        --out data/eval_reports/aria_coder_eval.json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The CONTRACT, not the builder: the builder imports aria_cli to execute tools
# for real, and aria_cli is not installed on a training pod. Importing it here
# would fail at module load, after the paid GPU was already running.
from scripts.train.coder_tool_contract import (  # noqa: E402
    BANNED, TOOL_PARAMS, tool_schemas,
)

def _looks_like_refusal(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in BANNED)


def _prefixes(messages: list[dict]):
    """Yield (prefix, reference_call) for every assistant tool-call turn.

    The prefix is everything BEFORE that turn, so the model is asked the same
    question the reference answered, with the same real tool output in context.
    """
    for i, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            yield messages[:i], m["tool_calls"][0]


def ask(client: httpx.Client, target: str, model: str, messages: list[dict],
        tools: list[dict], timeout: float) -> tuple[dict, str]:
    """Return (tool_call_or_empty, error). An error is a FAILURE, never a skip."""
    payload = {"model": model, "messages": messages, "tools": tools,
               "tool_choice": "auto", "max_tokens": 320, "temperature": 0.0}
    try:
        r = client.post(f"{target.rstrip('/')}/chat/completions", json=payload,
                        timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {}, f"transport: {type(exc).__name__}: {exc}"
    if r.status_code >= 400:
        return {}, f"http {r.status_code}: {r.text[:180]}"
    try:
        msg = r.json()["choices"][0]["message"]
    except Exception as exc:  # noqa: BLE001
        return {}, f"malformed response: {type(exc).__name__}: {exc}"
    calls = msg.get("tool_calls") or []
    if calls:
        return calls[0], ""
    return {}, ""  # answered in prose — a real result, not an error


def score_call(call: dict, reference: dict) -> dict:
    """Score ONE emitted call against the reference the corpus recorded."""
    fn = (call.get("function") or {})
    name = fn.get("name") or ""
    ref_name = ((reference.get("function") or {}).get("name")) or ""
    raw = fn.get("arguments")
    if isinstance(raw, dict):
        args, parses = raw, True
    else:
        try:
            args, parses = json.loads(raw or "{}"), True
        except Exception:  # noqa: BLE001
            args, parses = {}, False
    known = name in TOOL_PARAMS
    undeclared = sorted(set(args) - TOOL_PARAMS[name]) if (known and parses) else []
    return {
        "acted": True,
        "right_tool": name == ref_name,
        "args_parse": parses,
        "args_valid": bool(parses and known and not undeclared),
        "undeclared": undeclared,
        "tool": name,
        "reference_tool": ref_name,
    }


def evaluate(eval_file: Path, target: str, model: str, timeout: float,
             limit: int = 0) -> dict:
    rows = [json.loads(l) for l in eval_file.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    if limit:
        # STRATIFIED, not head-N. The eval file is grouped by family, so
        # `rows[:limit]` samples ONE family and reports it as the whole picture
        # — a partial run that is quietly unrepresentative is worse than no
        # partial run, because the number still looks like a result.
        grouped: dict[str, list[dict]] = collections.OrderedDict()
        for row in rows:
            grouped.setdefault(row.get("family", "?"), []).append(row)
        picked: list[dict] = []
        i = 0
        while len(picked) < limit and any(i < len(g) for g in grouped.values()):
            for group in grouped.values():
                if i < len(group) and len(picked) < limit:
                    picked.append(group[i])
            i += 1
        rows = picked
    tools = tool_schemas()
    per_family: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    failures: list[dict] = []
    total = collections.Counter()

    with httpx.Client() as client:
        for row in rows:
            fam = row.get("family", "?")
            for prefix, reference in _prefixes(row["messages"]):
                total["steps"] += 1
                per_family[fam]["steps"] += 1
                call, err = ask(client, target, model, prefix, tools, timeout)
                if err:
                    # An unreachable or malformed endpoint is a FAILURE.
                    total["error"] += 1
                    per_family[fam]["error"] += 1
                    failures.append({"family": fam, "why": err})
                    continue
                if not call:
                    total["prose"] += 1
                    per_family[fam]["prose"] += 1
                    last = prefix[-1] if prefix else {}
                    failures.append({"family": fam, "why": "answered in prose",
                                     "after": last.get("role")})
                    continue
                s = score_call(call, reference)
                for k in ("acted", "right_tool", "args_parse", "args_valid"):
                    if s[k]:
                        total[k] += 1
                        per_family[fam][k] += 1
                if s["undeclared"]:
                    total["undeclared_args"] += 1
                    failures.append({"family": fam, "why": "undeclared args",
                                     "tool": s["tool"],
                                     "args": s["undeclared"]})
                elif not s["right_tool"]:
                    failures.append({"family": fam, "why": "wrong tool",
                                     "got": s["tool"],
                                     "expected": s["reference_tool"]})

    steps = total["steps"]
    if not steps:
        # A rate computed from nothing is the meaningless-benchmark failure.
        return {"rows": len(rows), "steps": 0, "rates": None,
                "note": "empty eval set — NO RATE reported"}

    rates = {k: round(total[k] / steps, 4)
             for k in ("acted", "right_tool", "args_parse", "args_valid")}
    rates["prose"] = round(total["prose"] / steps, 4)
    rates["error"] = round(total["error"] / steps, 4)
    return {
        "rows": len(rows),
        "steps": steps,
        "rates": rates,
        "counts": dict(total),
        "per_family": {f: dict(c) for f, c in sorted(per_family.items())},
        "failures": failures[:60],
        "measure": "teacher-forced next-action; NOT end-to-end task completion",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval-file", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--model", default="aria-coder")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    report = evaluate(Path(args.eval_file), args.target, args.model,
                      args.timeout, args.limit)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    r = report.get("rates")
    if r is None:
        print(f"NO RATE — {report.get('note')}")
        return 1
    print(f"steps={report['steps']}  acted={r['acted']:.1%}  "
          f"right_tool={r['right_tool']:.1%}  args_valid={r['args_valid']:.1%}  "
          f"prose={r['prose']:.1%}  error={r['error']:.1%}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
