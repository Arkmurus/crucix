"""R-F2434 — git-history fix miner (SURGICAL + ROBUST).

Turns real R-F fix commits into a VERIFIED fail->pass code-training corpus, the
SWE-bench-verified way. Every kept row earns its place: the commit's own test is
run against the PARENT (buggy) source and MUST FAIL, then against the FIX source
and MUST PASS. Anything unverifiable is discarded, never padded (that is the
whole point vs the current 1-real-gold set).

SURGICAL — READ-ONLY on the main working tree:
  * Reads history ONLY via ``git show`` / ``git log`` / ``git diff-tree``.
  * NEVER checkout/reset/stash; NEVER touches the working tree or .git state.
  * All reconstruction + test execution happens in out-of-repo temp sandboxes
    (``tempfile.mkdtemp`` OUTSIDE the repo, exactly like the R-F2431 harness so
    the repo's pytest/conftest cannot leak in). Every sandbox is removed.

ROBUST:
  * Filters non-fixes: docs-only / refactor-with-no-test / multi-R batch commits
    (>1 distinct R-number in the subject — cannot attribute to one bug) / commits
    with no test file / no source file.
  * Causal isolation: a row is kept ONLY if flipping the CHANGED source files
    (parent<->fix) flips the commit's test file fail<->pass. An unrelated import
    error fails at BOTH states -> fix_did_not_pass -> discarded.
  * Resumable: a sha-keyed checkpoint; a restart continues where it stopped.
  * Rate-limited pytest subprocesses with a per-run timeout (Windows/3.14 IOCP
    flake is isolated per subprocess and treated as unverifiable, not a crash).

Usage (bounded proof-of-quality first):
  python scripts/eval/mine_git_fixes.py --limit 150 \
    --out data/training/mined_code_fixes_verified.jsonl \
    --report data/eval_reports/mine_git_fixes_report.json

  # resume / scale later
  python scripts/eval/mine_git_fixes.py --limit 2205 --resume
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_RNUM_RE = re.compile(r"R-F\d+")
_PYTEST_TIMEOUT = 90  # seconds per (state) run
_RATE_SLEEP = 0.15    # between subprocess runs


# ─────────────────────────── read-only git plumbing ────────────────────────
def _git(*args: str, allow_fail: bool = False) -> str:
    proc = subprocess.run(["git", *args], cwd=str(_REPO),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 and not allow_fail:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def list_fix_commits(limit: int) -> list[str]:
    out = _git("log", f"-{limit}", "--format=%H", "--grep=R-F", "--", "aria_service")
    return [l.strip() for l in out.splitlines() if l.strip()]


def commit_subject_body(sha: str) -> tuple[str, str]:
    subj = _git("log", "-1", "--format=%s", sha).strip()
    body = _git("log", "-1", "--format=%b", sha).strip()
    return subj, body


def changed_files(sha: str) -> list[tuple[str, str]]:
    """Return [(status, path)] for the commit vs its first parent."""
    out = _git("diff-tree", "--no-commit-id", "--name-status", "-r", "-M", sha)
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0][0], parts[-1]))  # status letter, final path
    return rows


def show_file(sha: str, path: str) -> str | None:
    """File content at a commit, or None if it does not exist there."""
    proc = subprocess.run(["git", "show", f"{sha}:{path}"], cwd=str(_REPO),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return None
    return proc.stdout


# ─────────────────────────── candidate extraction ──────────────────────────
def classify(sha: str) -> dict:
    subj, body = commit_subject_body(sha)
    r_numbers = sorted(set(_RNUM_RE.findall(subj)))
    files = changed_files(sha)
    src, tests = [], []
    for status, path in files:
        if status == "D":
            continue
        if not path.endswith(".py"):
            continue
        if not path.startswith("aria_service/"):
            continue
        if "/tests/" in path or Path(path).name.startswith("test_"):
            tests.append(path)
        else:
            src.append(path)
    return {"sha": sha, "subject": subj, "body": body, "r_numbers": r_numbers,
            "src": src, "tests": tests, "n_changed_py": len(src) + len(tests)}


def drop_reason(c: dict) -> str | None:
    if len(c["r_numbers"]) != 1:
        return "multi_or_zero_r_subject"
    if not c["tests"]:
        return "no_test_file"
    if not c["src"]:
        return "no_source_file"  # docs/refactor/test-only
    return None


# ─────────────────────────── sandbox verification ──────────────────────────
def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    # empty __init__.py up the aria_service package chain so package-style
    # imports resolve WITHOUT the real (heavy, side-effecting) package init.
    parts = Path(rel).parts
    if parts and parts[0] == "aria_service":
        acc = root
        for seg in parts[:-1]:
            acc = acc / seg
            init = acc / "__init__.py"
            if not init.exists():
                init.write_text("", encoding="utf-8")


def _run_tests(root: Path, test_paths: list[str]) -> tuple[bool, str]:
    """Run the given test files in ``root`` (isolated). Return (passed, tail)."""
    import os
    full_env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *test_paths, "-q", "-p", "no:cacheprovider",
             "-o", "addopts=", "--rootdir", str(root), "--no-header"],
            cwd=str(root), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=_PYTEST_TIMEOUT, env=full_env,
        )
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-1200:]
        return proc.returncode == 0, tail
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"


# Modules whose import triggers heavy model/vector loads — never verify a fix
# that pulls one into the closure (would load torch/chromadb = the 10-min brain
# stall). These fixes are discarded as unverifiable-here (honest), not run.
_HEAVY = ("torch", "chromadb", "sentence_transformers", "transformers",
          "onnxruntime", "vllm", "faiss")
_HEAVY_RE = re.compile(
    r"^\s*(?:import|from)\s+(" + "|".join(_HEAVY) + r")\b", re.M)
_MISSING_ARIA_RE = re.compile(r"No module named '(aria_service[\w\.]*)'")
_MISSING_ANY_RE = re.compile(r"No module named '([\w\.]+)'")
_CANNOT_IMPORT_RE = re.compile(r"cannot import name '(\w+)' from '(aria_service[\w\.]*)'")
_MAX_PULL = 45  # closure cap — a fix needing >45 unchanged modules is not isolatable


def _pull_module(sha: str, modname: str) -> tuple[str, str] | None:
    """Fetch aria_service.pkg.mod as pkg/mod.py or pkg/mod/__init__.py."""
    rel = modname.replace(".", "/")
    for cand in (rel + ".py", rel + "/__init__.py"):
        content = show_file(sha, cand)
        if content is not None:
            return cand, content
    return None


def _next_pull_candidates(tail: str) -> list[str] | None:
    """From a failing tail, the aria_service module name(s) to try pulling.
    Handles absolute (ModuleNotFound) and relative/re-export (cannot import
    name X from PKG) failures."""
    m = _MISSING_ARIA_RE.search(tail)
    if m:
        return [m.group(1)]
    ci = _CANNOT_IMPORT_RE.search(tail)
    if ci:
        name, pkg = ci.group(1), ci.group(2)
        # X may be a submodule (pkg/X.py) OR a name re-exported from pkg/__init__.
        return [f"{pkg}.{name}", pkg]
    return None


def _materialize_state(root: Path, changed: dict[str, str | None],
                       deps: dict[str, str], tests: dict[str, str]) -> None:
    for p, content in changed.items():
        if content is not None:        # None = newly-added file, absent at parent
            _write(root, p, content)
    for p, content in deps.items():
        _write(root, p, content)
    for p, content in tests.items():
        _write(root, p, content)


def _resolve_closure(sha: str, changed: dict[str, str | None], tests: dict[str, str],
                     test_rel: list[str],
                     deps_seed: dict[str, str] | None = None
                     ) -> tuple[bool, dict[str, str], str]:
    """Iteratively pull the unchanged aria_service.* modules the changed source
    + test need, until the test PASSES or an unresolvable/heavy/cap condition
    stops it. Returns (passed, dep_map, reason). ``reason`` is "ok" on pass;
    on failure "fix_error_nonimport" means the test RAN (all imports resolved)
    but failed for a genuine non-import reason — i.e. a real assertion/behaviour
    failure (the buggy reproduce), distinct from an unresolved-import stop."""
    deps: dict[str, str] = dict(deps_seed or {})
    for _ in range(_MAX_PULL):
        root = Path(tempfile.mkdtemp(prefix="mine_fix_"))
        try:
            _materialize_state(root, changed, deps, tests)
            time.sleep(_RATE_SLEEP)
            passed, tail = _run_tests(root, test_rel)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        if passed:
            return True, deps, "ok"
        cands = _next_pull_candidates(tail)
        if not cands:
            ext = _MISSING_ANY_RE.search(tail)
            if ext:
                return False, deps, f"missing_external_dep:{ext.group(1)}"
            return False, deps, "fix_error_nonimport"
        progressed = False
        for modname in cands:
            pulled = _pull_module(sha, modname)
            if not pulled:
                continue
            path, content = pulled
            if _HEAVY_RE.search(content):
                return False, deps, "too_heavy_to_verify"
            if deps.get(path) == content:
                continue  # already have this exact module — try next candidate
            deps[path] = content
            progressed = True
            break
        if not progressed:
            return False, deps, "unresolved_import"
    return False, deps, "closure_too_large"


def verify(c: dict) -> dict:
    """SWE-bench-verified gate with bounded dependency closure. Keeps ONLY when
    the commit's test FAILS on parent source and PASSES on fix source, with the
    SAME (unchanged) dependency modules present in both — causal isolation of the
    changed files."""
    sha, parent = c["sha"], c["sha"] + "^"
    fix_src: dict[str, str] = {}
    par_src: dict[str, str | None] = {}
    for p in c["src"]:
        fc = show_file(sha, p)
        if fc is None:
            return {"verified": False, "reason": "git_extract_fix_src_none"}
        fix_src[p] = fc
        par_src[p] = show_file(parent, p)  # None if newly added at fix
    tests: dict[str, str] = {}
    for p in c["tests"]:
        tc = show_file(sha, p)
        if tc is None:
            return {"verified": False, "reason": "git_extract_test_none"}
        tests[p] = tc
    test_rel = list(tests.keys())

    # 1) resolve the closure on the FIX state; it must PASS.
    fix_ok, deps, reason = _resolve_closure(sha, fix_src, tests, test_rel)
    if not fix_ok:
        return {"verified": False, "reason": reason}

    # 2) PARENT state, resolved the SAME way (seeded with the fix's deps at the
    #    parent sha, since unchanged files are identical between sha and sha^).
    #    It must FAIL for a GENUINE non-import reason (a real assertion/behaviour
    #    failure = the bug reproducing) — NOT merely an unresolved import, which
    #    would be a false attribution. This symmetry is the anti-false-positive
    #    guard (§22/§23).
    par_ok, _pdeps, par_reason = _resolve_closure(parent, par_src, tests, test_rel,
                                                  deps_seed=dict(deps))
    if par_ok:
        return {"verified": False, "reason": "parent_did_not_fail"}
    if par_reason != "fix_error_nonimport":
        # parent stopped on an import/heavy/cap issue, not on the bug itself —
        # cannot cleanly attribute the fail->pass to the change. Discard.
        return {"verified": False, "reason": f"parent_ambiguous:{par_reason}"}

    return {"verified": True, "reason": "fail_to_pass", "n_deps": len(deps),
            "par_src": par_src, "fix_src": fix_src, "test_files": tests,
            "dep_modules": list(deps.keys())}


# ─────────────────────────────── runner ────────────────────────────────────
def _instruction(c: dict) -> str:
    subj = c["subject"]
    # strip a leading "R-Fxxxx: " / "R-Fxxxx + R-Fyyy: " prefix for a cleaner task
    body = c["body"].strip()
    first_para = body.split("\n\n")[0].strip() if body else ""
    text = subj
    if first_para and first_para.lower() not in subj.lower():
        text = f"{subj}\n{first_para[:400]}"
    return f"Fix gap: {text}"


def build_row(c: dict, v: dict) -> dict:
    multi = len(c["src"]) > 1
    return {
        "instruction": _instruction(c),
        "buggy_context": {k: val for k, val in v["par_src"].items()},
        "fix": v["fix_src"],
        "verify_test": v["test_files"],
        "sha": c["sha"],
        "r_number": c["r_numbers"][0],
        "source_files": c["src"],
        "test_files": c["tests"],
        "multi_file": multi,
        "newly_added_source": [p for p, val in v["par_src"].items() if val is None],
        "dep_modules": v.get("dep_modules", []),
        "n_deps": v.get("n_deps", 0),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--out", default="data/training/mined_code_fixes_verified.jsonl")
    ap.add_argument("--report", default="data/eval_reports/mine_git_fixes_report.json")
    ap.add_argument("--checkpoint", default="data/eval/mine_checkpoint.json")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    out_p = _REPO / args.out
    rep_p = _REPO / args.report
    ckpt_p = _REPO / args.checkpoint
    out_p.parent.mkdir(parents=True, exist_ok=True)
    ckpt_p.parent.mkdir(parents=True, exist_ok=True)

    done: dict[str, str] = {}
    verified_rows: list[dict] = []
    if args.resume and ckpt_p.exists():
        ck = json.loads(ckpt_p.read_text(encoding="utf-8"))
        done = ck.get("done", {})
        if out_p.exists():
            verified_rows = [json.loads(l) for l in out_p.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"resume: {len(done)} shas already processed, {len(verified_rows)} rows kept")

    # Idempotency guard (root cause of the double-launch dup rows): never append
    # a sha already written to the corpus. `done` is authoritative for skipping;
    # this covers the race where two writers append the same sha before either
    # checkpoints. A dedup-by-sha also runs at report time below.
    written_shas: set[str] = {r["sha"] for r in verified_rows}

    commits = list_fix_commits(args.limit)
    from collections import Counter
    drops: Counter = Counter()
    scanned = candidates = verified = 0

    for i, sha in enumerate(commits, 1):
        if sha in done:
            if done[sha] == "verified":
                pass  # already counted via verified_rows load
            continue
        scanned += 1
        try:
            c = classify(sha)
        except Exception as exc:
            done[sha] = "classify_error"; drops["classify_error"] += 1
            continue
        reason = drop_reason(c)
        if reason:
            done[sha] = reason; drops[reason] += 1
            continue
        candidates += 1
        try:
            v = verify(c)
        except Exception as exc:
            done[sha] = "verify_exception"; drops[f"verify_exception:{type(exc).__name__}"] += 1
            print(f"  [{i}/{len(commits)}] {sha[:8]} {c['r_numbers'][0]:<9} EXC {type(exc).__name__}")
            continue
        if v["verified"]:
            verified += 1
            row = build_row(c, v)
            verified_rows.append(row)
            if sha not in written_shas:      # idempotent append (dup guard)
                with out_p.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                written_shas.add(sha)
            done[sha] = "verified"
            print(f"  [{i}/{len(commits)}] {sha[:8]} {c['r_numbers'][0]:<9} VERIFIED "
                  f"(src={len(c['src'])} multi={row['multi_file']})")
        else:
            done[sha] = v["reason"]; drops[v["reason"]] += 1
            print(f"  [{i}/{len(commits)}] {sha[:8]} {c['r_numbers'][0]:<9} drop:{v['reason']}")
        # checkpoint every commit (cheap, resumable)
        ckpt_p.write_text(json.dumps({"done": done, "ts": time.time()}, indent=0), encoding="utf-8")

    # Final dedup-by-sha rewrite (safe: this process is done appending). Removes
    # any duplicate rows from an earlier concurrent-writer race; keeps first seen.
    if out_p.exists():
        seen: set[str] = set()
        deduped: list[dict] = []
        for r in (json.loads(l) for l in out_p.read_text(encoding="utf-8").splitlines() if l.strip()):
            if r["sha"] in seen:
                continue
            seen.add(r["sha"])
            deduped.append(r)
        out_p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in deduped), encoding="utf-8")
        verified_rows = deduped

    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "limit": args.limit, "commits_seen": len(commits),
        "scanned_this_run": scanned, "candidates": candidates, "verified_this_run": verified,
        "total_verified_rows": len(verified_rows),
        "yield_rate_candidates": round(verified / candidates, 4) if candidates else 0.0,
        "yield_rate_scanned": round(verified / scanned, 4) if scanned else 0.0,
        "multi_file_verified": sum(1 for r in verified_rows if r.get("multi_file")),
        "drop_reasons": dict(drops),
    }
    rep_p.parent.mkdir(parents=True, exist_ok=True)
    rep_p.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n=== MINE REPORT ===")
    print(json.dumps(report, indent=2))
    print(f"corpus -> {out_p}")


if __name__ == "__main__":
    main()
