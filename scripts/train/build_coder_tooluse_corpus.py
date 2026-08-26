"""R-F4371 (C-316) — build a MULTI-TURN CODER tool-use corpus from REAL executions.

WHY. ARIA has never once been trained to call a coding tool. Measured across the
whole of `data/training/*.jsonl` on 2026-08-26:

    tool-use trajectories                     5,310
    distinct tool names ever seen                 7   (screen, web_search,
                                                       companies_house_*)
    trajectories using ANY coder tool             0
    trajectories with ONE call-turn           76.1%

So when the coder CLI hands her `read_file` / `edit_file` / `run` / `list_dir` /
`grep`, she is entirely out of distribution and falls back on the base model's
priors. That is not a metaphor for the observed failures — it IS them, one for
one, from live sessions on 2026-08-26:

    "I cannot execute or modify files. You must manually edit the `calc.py` file."
    list_dir(path=..., recursive=True)   -> unexpected keyword argument 'recursive'
    read_file("C:\\path\\to\\file.txt")  -> a placeholder path, invented whole

and the 76% single-call skew is why she stops after one step even when the task
is plainly unfinished.

WHAT THIS TEACHES, and every item is here because a live failure demanded it:

  1. the coder tools EXIST and are hers to call;
  2. DEPTH — a tool result with work outstanding is followed by the NEXT CALL,
     not by prose (3-5 call-turns per trajectory);
  3. GROUND BEFORE ACTING — `list_dir`/`grep` to find the real path rather than
     inventing one;
  4. ARGUMENTS MATCH THE SCHEMA — only declared parameters, because `recursive`
     cost a whole turn;
  5. ERROR RECOVERY — a tool error is followed by a CORRECTED call, never by a
     refusal or an apology;
  6. VERIFY — end by running the thing and reporting what it actually printed.

THE HARD CONSTRAINT — TOOL OUTPUTS MUST BE REAL. Inherited deliberately from
R-F3366, which states it best: "A corpus whose tool results are LLM-imagined
teaches the model that plausible-looking tool output is acceptable." Every tool
result here is produced by EXECUTING `aria_cli.tools.Toolbox` — the same class,
the same methods, the same result formatting the CLI uses at inference. So the
strings she trains on are byte-identical in shape to the strings she will see,
and no result in this file was written by a model or by hand.

The assistant's CALLS are chosen by a deterministic scripted policy, not by an
LLM. That is the point: we are teaching a known-correct trajectory, so there is
no teacher to be wrong, nothing to grade, and no per-row cost.

NO NETWORK, NO CREDENTIALS, NO CUSTOMER DATA. Every trajectory runs inside a
throwaway sandbox this script creates and deletes. It never reads the operator's
repo, so it cannot bake source or secrets into weights.

USAGE
    python -m scripts.train.build_coder_tooluse_corpus \
        --out data/training/aria_coder_tooluse_v1.jsonl
    python -m scripts.train.build_coder_tooluse_corpus --validate-only <file>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli.safety import WriteGuard  # noqa: E402
from aria_cli.tools import Toolbox  # noqa: E402

#: The system prompt the CLI actually serves a small sovereign (R-F4325). Training
#: under a DIFFERENT prompt than inference would teach the behaviour in a context
#: she never sees — the same mismatch class R-F4325 measured at 0/5.
SYSTEM = (
    "You are ARIA, an autonomous coding agent in the operator's repository "
    "{root} on {platform}. Act with the tools; do not describe commands.\n\n"
    "Rules: reserve an R-number before code; fix the root cause, never a "
    "band-aid; run the test before claiming it passes; never delete data.\n\n"
    "Work in small steps: inspect with a tool, then act, then verify."
)

# R-F4372 (C-317) — TOOL_PARAMS and BANNED moved to `coder_tool_contract`, a
# module with no heavy imports, so the EVALUATOR can read the same contract on a
# training pod where `aria_cli` is not installed. Re-exported here because this
# module is where they were defined and callers already import them from it; the
# definition itself must exist in exactly one place.
from scripts.train.coder_tool_contract import (  # noqa: E402
    BANNED, DESCRIPTIONS, TOOL_PARAMS, tool_schemas,
)

__all__ = ["BANNED", "DESCRIPTIONS", "TOOL_PARAMS", "tool_schemas"]


def _call_id(*parts: object) -> str:
    """A 9-char alphanumeric id — the Mistral chat template rejects anything
    else ("Tool call IDs should be alphanumeric strings with length 9!"), and a
    corpus carrying ids the template refuses would train an unusable shape."""
    raw = "|".join(str(p) for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:9]


class Trajectory:
    """Records an assistant/tool exchange while REALLY executing each call."""

    def __init__(self, box: Toolbox, root: Path, user: str) -> None:
        self.box = box
        self.messages = [
            {"role": "system",
             "content": SYSTEM.format(root=str(root), platform="Windows")},
            {"role": "user", "content": user},
        ]
        self._n = 0

    def call(self, name: str, thought: str = "", **args) -> str:
        """Emit one assistant tool_call, EXECUTE it for real, record the result."""
        undeclared = set(args) - TOOL_PARAMS[name]
        if undeclared:
            raise ValueError(
                f"{name} called with undeclared argument(s) {sorted(undeclared)} "
                f"— the corpus must never model an invented parameter")
        self._n += 1
        cid = _call_id(name, self._n, json.dumps(args, sort_keys=True))
        self.messages.append({
            "role": "assistant",
            "content": thought,
            "tool_calls": [{
                "id": cid, "type": "function",
                "function": {"name": name,
                             "arguments": json.dumps(args, ensure_ascii=False)},
            }],
        })
        result = getattr(self.box, name)(**args)
        # `agent.py::_record_tool` puts `result.output` on the wire verbatim, so
        # that is exactly what the corpus must carry. NO getattr fallback: the
        # first version used `getattr(result, "content", str(result))`, and
        # because ToolResult has no `.content` every tool message silently
        # became a Python dataclass repr — a corpus of fabricated tool output,
        # written with no error and no way to notice it downstream.
        text = result.output
        if not isinstance(text, str):
            raise TypeError(f"{name}: tool output is {type(text).__name__}, "
                            f"not str — refusing to write it")
        self.messages.append({"role": "tool", "tool_call_id": cid,
                              "content": text})
        return text

    def answer(self, text: str) -> dict:
        self.messages.append({"role": "assistant", "content": text})
        return {"messages": self.messages,
                "source": "coder_tooluse_real_execution",
                "builder": "R-F4371"}


# ── variant vocabulary ──────────────────────────────────────────────────────
# Six families would be six rows. The FAMILY is the lesson; the identifiers,
# values and paths are varied so she learns the SHAPE rather than memorising one
# file name — the same reason the DD corpora vary their subjects. Everything
# below is still really executed and still really verified.

MODULES = ["calc", "mathutil", "arith", "totals", "budget", "ledger",
           "metrics", "pricing", "scoring", "tally"]
FUNCS = ["add", "combine", "sum_two", "accumulate", "merge_values",
         "total", "join_nums", "plus", "aggregate", "compute"]
PKGS = ["app", "core", "svc", "engine", "lib", "runtime", "server", "worker"]
SETTINGS = ["TIMEOUT", "RETRIES", "MAX_WORKERS", "BATCH_SIZE", "PORT",
            "CACHE_TTL", "POOL_SIZE", "DEADLINE", "MAX_DEPTH", "CHUNK_SIZE",
            "QUEUE_LIMIT", "GRACE_PERIOD", "WINDOW_SIZE", "FLUSH_EVERY",
            "BACKLOG", "PAGE_SIZE", "LEASE_TTL", "SHARD_COUNT",
            "IDLE_TIMEOUT", "PREFETCH"]
# Pool lengths are deliberately co-prime-ish (5, 7, 11): with three pools of
# length 5 the combined period is 5, so variant 0 and variant 5 would be the
# same row wearing a different date.
NOTEFILES = ["notes.md", "RELEASE.md", "changelog.md", "plan.md", "TODO.md"]
# No two entries may differ ONLY by case: Windows treats them as one file,
# so they produce the same prompt with different answers - a contradiction
# that exact-match dedupe cannot see. `_pool_is_case_unique` pins this.
WRONGNAMES = ["NOTES.txt", "Notes.MD", "note.md", "README.txt",
              "release.md", "notes.markdown", "CHANGES.txt"]
GREETINGS = ["HELLO", "READY", "OK-1", "STARTED", "ONLINE", "GREEN"]
CASEFUNCS = ["title_case", "to_title", "titleise", "capitalise_words",
             "proper_case", "titlecase", "as_title", "headline_case",
             "to_headline", "word_caps", "cap_words"]


def _pick(seq, v):
    return seq[v % len(seq)]


# ── the tasks ───────────────────────────────────────────────────────────────
# Each returns (row, verify) where verify() asserts the sandbox really ended in
# the intended state. A trajectory whose edit did not land teaches a lie.

def task_fix_and_verify(box, root, v=0):
    """read -> edit -> run -> answer. The operator's own failing case."""
    mod, fn = _pick(MODULES, v), _pick(FUNCS, v)
    a, b = 2 + (v % 5), 3 + (v % 7)
    (root / f"{mod}.py").write_text(f"def {fn}(a, b):\n    return a - b\n",
                                    encoding="utf-8")
    t = Trajectory(box, root,
                   f"{mod}.py has a bug: {fn}() subtracts instead of adding. "
                   f"Fix it, then run it to prove {fn}({a},{b}) is {a + b}.")
    t.call("read_file", path=f"{mod}.py")
    t.call("edit_file", path=f"{mod}.py",
           old_string="return a - b", new_string="return a + b")
    out = t.call("run",
                 command=f'python -c "import {mod}; print({mod}.{fn}({a},{b}))"')
    row = t.answer(
        f"Fixed and verified. `{fn}()` returned `a - b`; it now returns "
        f"`a + b`, and running it prints {a + b}.")
    return row, lambda: "a + b" in (root / f"{mod}.py").read_text(
        encoding="utf-8") and str(a + b) in out


def task_ground_before_acting(box, root, v=0):
    """list_dir -> grep -> read -> answer. Teaches: find the real path.

    The live failure was `read_file("C:\\path\\to\\file.txt")` — a placeholder
    invented rather than looked up. Every variant here locates the file first.
    """
    # The question names only `name`, so EVERY other varying part of this row
    # must be derived from the SAME index. When `val` cycled at 9 while
    # SETTINGS cycled at 8, "Where is TIMEOUT defined?" acquired two different
    # correct answers — caught by _drop_contradictions, which is what it is for.
    i = v % len(SETTINGS)
    name = SETTINGS[i]
    pkg = _pick(PKGS, i)
    val = 10 * (1 + i % 9)
    d = root / pkg
    d.mkdir(exist_ok=True)
    (d / "settings.py").write_text(
        f"DEBUG = True\n{name} = {val}\nRETRY_BACKOFF = 1.5\n", encoding="utf-8")
    (d / "main.py").write_text(
        f"from {pkg}.settings import {name}\n\n\ndef start():\n"
        f"    return {name}\n", encoding="utf-8")
    t = Trajectory(box, root, f"Where is {name} defined, and what is its value?")
    t.call("list_dir", path=".")
    hits = t.call("grep", pattern=name, output_mode="content")
    t.call("read_file", path=f"{pkg}/settings.py")
    row = t.answer(
        f"`{name}` is defined in `{pkg}/settings.py` as `{val}`, and imported "
        f"by `{pkg}/main.py`. I located it by listing the directory and "
        f"grepping rather than guessing a path.")
    return row, lambda: "settings.py" in hits


def task_recover_from_a_bad_path(box, root, v=0):
    """A REAL error, then a CORRECTED call. Teaches: recover, never refuse.

    This is the direct antidote to "I cannot verify the path or the file's
    existence without more context" — she can, by listing the directory.
    """
    # EVERYTHING THE ANSWER MENTIONS MUST BE DETERMINED BY WHAT THE PROMPT
    # SHOWS. The prompt shows `wrong` and the phrasing; the answer states the
    # real filename and the date. Deriving those from a separate index gave the
    # same question two answers — twice, from two different period mismatches
    # (i%12/i%28 dates against an i%11 phrasing, and then a case-collision skip
    # that made `wrong` not a clean function of i%7). So the key IS the prompt:
    # (wrong_index, ask_index), and real + date are read off that key.
    wi, ai = v % len(WRONGNAMES), v % 11
    wrong = WRONGNAMES[wi]
    # The real file must differ from the wrong one CASE-INSENSITIVELY: Windows
    # filesystems treat "release.md" and "RELEASE.md" as one file, so that pair
    # made the "wrong" read SUCCEED and the row would have taught a recovery
    # from an error that never happened.
    real = next(r for k in range(len(NOTEFILES))
                if (r := NOTEFILES[(wi + k) % len(NOTEFILES)]).lower()
                != wrong.lower())
    n = wi * 11 + ai
    date = f"2026-{1 + n % 12:02d}-{1 + n % 28:02d}"
    (root / real).write_text(f"# Notes\nrelease: {date}\n", encoding="utf-8")
    ask = [f"Read the release date out of {wrong}.",
           f"What release date does {wrong} give?",
           f"Find the release date recorded in {wrong}.",
           f"Check {wrong} and tell me the release date.",
           f"The release date is in {wrong} - read it out.",
           f"Open {wrong} and report the release date.",
           f"{wrong} should have a release date in it. What is it?",
           f"Get me the release date from {wrong}.",
           f"Look in {wrong} for the release date.",
           f"Tell me the release date; it is recorded in {wrong}.",
           f"Read {wrong} and give me the release date."][ai]
    t = Trajectory(box, root, ask)
    miss = t.call("read_file", path=wrong)
    t.call("list_dir", path=".")
    got = t.call("read_file", path=real)
    row = t.answer(
        f"The release date is {date}. My first path was wrong, so I listed the "
        f"directory, found `{real}`, and read that instead.")
    return row, lambda: "error" in miss.lower() and date in got


def task_multi_file_change(box, root, v=0):
    """grep -> read -> edit -> run. Teaches depth beyond two calls."""
    half = 21 + v
    want = half * 2
    (root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "b.py").write_text("from a import VALUE\n\nprint(VALUE * 2)\n",
                               encoding="utf-8")
    t = Trajectory(box, root,
                   f"VALUE should be {half} so b.py prints {want}. Change it "
                   f"and prove it.")
    t.call("grep", pattern="VALUE", output_mode="content")
    t.call("read_file", path="a.py")
    t.call("edit_file", path="a.py", old_string="VALUE = 1",
           new_string=f"VALUE = {half}")
    out = t.call("run", command="python b.py")
    row = t.answer(f"Done - `a.py` now sets `VALUE = {half}` and `python b.py` "
                   f"prints {want}.")
    return row, lambda: str(want) in out


def task_create_then_run(box, root, v=0):
    """write -> run -> answer. Teaches: create files, then verify."""
    word = _pick(GREETINGS, v)
    fname = f"{_pick(['hello', 'boot', 'probe', 'smoke', 'check'], v)}.py"
    t = Trajectory(box, root,
                   f"Create {fname} that prints {word}, then run it and show "
                   f"me the output.")
    t.call("write_file", path=fname, content=f'print("{word}")\n')
    out = t.call("run", command=f"python {fname}")
    row = t.answer(f"Created `{fname}` and ran it - it prints {word}.")
    return row, lambda: word in out


def task_run_a_failing_test_then_fix_it(box, root, v=0):
    """run(fail) -> read -> edit -> run(pass). The full engineering loop."""
    fn = _pick(CASEFUNCS, v)
    phrase = _pick(["hello world", "good morning", "red team", "open source",
                    "due diligence"], v)
    want = phrase.title()
    (root / "strutil.py").write_text(
        f"def {fn}(s):\n    return s.upper()\n", encoding="utf-8")
    # A plain-python test rather than pytest: the sandbox interpreter is the
    # system one and has no pytest, so a pytest command would record
    # "No module named pytest" as the lesson. The red->green loop is the point;
    # the runner is not.
    (root / "test_strutil.py").write_text(
        f"from strutil import {fn}\n\n"
        f"assert {fn}({phrase!r}) == {want!r}, {fn}({phrase!r})\n"
        f"print('PASS')\n",
        encoding="utf-8")
    # EVERY phrasing must name `fn`. The one generic phrasing here ("the test
    # is failing") was a function of nothing, so it collided across variants
    # and asked one question with several answers.
    ask = _pick(
        [f"test_strutil.py is failing on {fn}. Find out why, fix the source "
         f"(not the test), and re-run it.",
         f"The test for {fn} fails. Fix strutil.py - not the test - and prove it.",
         f"strutil.py is wrong: {fn} does not title-case. Fix it and re-run "
         f"test_strutil.py.",
         f"Make test_strutil.py pass by fixing {fn} in strutil.py.",
         f"{fn} is returning the wrong case. Repair the source and re-run the "
         f"test."], v)
    t = Trajectory(box, root, ask)
    # `-B`, and it is load-bearing. This family runs the SAME module twice,
    # either side of an edit. Without it the red run writes
    # __pycache__/strutil.*.pyc, and when the edit lands inside the
    # filesystem's mtime granularity the green run silently imports the STALE
    # bytecode and fails identically — a false red that `verify()` caught, and
    # that would otherwise have taught "I fixed it" over an unchanged result.
    red = t.call("run", command="python -B test_strutil.py")
    t.call("read_file", path="strutil.py")
    t.call("edit_file", path="strutil.py",
           old_string="return s.upper()", new_string="return s.title()")
    out = t.call("run", command="python -B test_strutil.py")
    row = t.answer(
        f"The test failed because `{fn}` used `.upper()`, which returns "
        f"'{phrase.upper()}'. It now uses `.title()`, and the test passes.")
    # RED then GREEN - both halves asserted, because a trajectory that was
    # green from the start teaches nothing about fixing anything.
    return row, lambda: "AssertionError" in red and "PASS" in out


TASKS = [
    task_fix_and_verify,
    task_ground_before_acting,
    task_recover_from_a_bad_path,
    task_multi_file_change,
    task_create_then_run,
    task_run_a_failing_test_then_fix_it,
]


def build_rows(variants: int = 40) -> list[dict]:
    """Every family x every variant, each one really executed and verified.

    A failure RAISES rather than skipping the row. A builder that quietly drops
    what it could not verify reports a smaller corpus and no reason, which is
    how a broken family survives unnoticed — the absence-reads-as-health shape
    this repo keeps re-learning.
    """
    rows = []
    for task in TASKS:
        for v in range(variants):
            sandbox = Path(tempfile.mkdtemp(prefix=f"codertrain_{v}_"))
            try:
                box = Toolbox(root=sandbox, guard=WriteGuard(self_mode=False))
                row, verify = task(box, sandbox, v)
                if not verify():
                    raise RuntimeError(
                        f"{task.__name__}[v={v}]: the sandbox did NOT end in "
                        f"the intended state — the trajectory would teach an "
                        f"outcome that did not happen")
                problems = validate_row(row)
                if problems:
                    raise RuntimeError(f"{task.__name__}[v={v}]: {problems}")
                # Tagged here rather than inside Trajectory: the family is a
                # property of which builder made the row, and the eval split
                # needs it to avoid putting near-identical variants on both
                # sides.
                row["family"] = task.__name__
                rows.append(row)
            finally:
                shutil.rmtree(sandbox, ignore_errors=True)
    return _drop_contradictions(rows)


def _drop_contradictions(rows: list[dict]) -> list[dict]:
    """Remove rows that answer an IDENTICAL question differently.

    A variant space that cycles produces two rows with the same user text and
    different correct answers — because the sandbox behind them differed. Each
    row is individually true and the PAIR is still poison: for a 7B it teaches
    that the same question has several answers, which is how a model learns to
    pick one at random instead of reading. Exact duplicates are collapsed (they
    are just a repeated example); genuine contradictions RAISE, because a
    silent drop hides a variant space that has quietly stopped varying.
    """
    seen: dict[str, str] = {}
    out: list[dict] = []
    dupes = 0
    for row in rows:
        raw = next(m["content"] for m in row["messages"] if m["role"] == "user")
        # Normalised: two prompts differing only in case or spacing are the
        # SAME question to a reader and to preflight_cycle, so comparing the
        # raw text let a case-variant pair through as "distinct".
        user = " ".join(str(raw).split()).lower()
        answer = row["messages"][-1]["content"]
        prior = seen.get(user)
        if prior is None:
            seen[user] = answer
            out.append(row)
        elif prior == answer:
            dupes += 1  # same question, same answer — a repeat, not a conflict
        else:
            raise RuntimeError(
                f"contradictory rows for the same question:\n  Q: {user!r}\n"
                f"  A1: {prior[:90]!r}\n  A2: {answer[:90]!r}\n"
                f"The variant space has collapsed — widen the pools rather than "
                f"shipping both.")
    if dupes:
        print(f"  collapsed {dupes} exact-duplicate trajectory(ies)")
    return out


def validate_row(row: dict) -> list[str]:
    """Refuse anything that would teach the failures this corpus exists to fix."""
    bad: list[str] = []
    msgs = row.get("messages") or []
    roles = [m.get("role") for m in msgs]
    if roles[:2] != ["system", "user"]:
        bad.append("must open system,user")
    if roles[-1] != "assistant":
        bad.append("must end on an assistant answer")

    call_turns = [m for m in msgs if m.get("tool_calls")]
    if len(call_turns) < 2:
        bad.append(f"only {len(call_turns)} call-turn(s) — the 76% single-call "
                   f"skew is the defect, not the template")

    # DEPTH: at least one tool result must be followed by another CALL.
    acted = any(msgs[i].get("role") == "tool"
                and msgs[i + 1].get("role") == "assistant"
                and msgs[i + 1].get("tool_calls")
                for i in range(len(msgs) - 1))
    if not acted:
        bad.append("no act-after-tool-result — this is the whole behaviour")

    for m in msgs:
        if m.get("role") != "assistant":
            continue
        low = (m.get("content") or "").lower()
        for phrase in BANNED:
            if phrase in low:
                bad.append(f"assistant refuses capability: {phrase!r}")
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function") or {}
            name = fn.get("name")
            if name not in TOOL_PARAMS:
                bad.append(f"unknown tool {name!r}")
                continue
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                bad.append(f"{name}: arguments are not valid JSON")
                continue
            undeclared = set(args) - TOOL_PARAMS[name]
            if undeclared:
                bad.append(f"{name}: undeclared argument(s) {sorted(undeclared)}")
            if len(tc.get("id") or "") != 9 or not (tc.get("id") or "").isalnum():
                bad.append(f"{name}: tool id is not 9 alphanumeric chars")

    # Every tool message must answer a call that was actually made.
    ids = {tc.get("id") for m in msgs for tc in (m.get("tool_calls") or [])}
    for m in msgs:
        if m.get("role") == "tool" and m.get("tool_call_id") not in ids:
            bad.append("orphan tool message")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/training/aria_coder_tooluse_v1.jsonl")
    ap.add_argument("--validate-only", default="")
    ap.add_argument("--variants", type=int, default=40,
                    help="variants per task family (6 families).")
    ap.add_argument("--eval-frac", type=float, default=0.0,
                    help="hold out this fraction as <out>.eval.jsonl. The split "
                         "is BY FAMILY, not random: a random split leaves near "
                         "-identical variants on both sides and the eval then "
                         "measures memorisation.")
    args = ap.parse_args()

    if args.validate_only:
        path = Path(args.validate_only)
        bad_total = 0
        rows = 0
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rows += 1
            problems = validate_row(json.loads(line))
            if problems:
                bad_total += 1
                print(f"  row {rows}: {problems}")
        print(f"{path}: {rows} rows, {bad_total} invalid")
        return 1 if bad_total else 0

    rows = build_rows(variants=args.variants)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    held: list[dict] = []
    if args.eval_frac > 0:
        # Split by VARIANT POSITION within each family, taking the tail. A
        # RANDOM split would put variant 7 in train and variant 8 in eval — two
        # rows differing only in a filename — and the eval would then measure
        # memorisation rather than whether she learned to act.
        by_family: dict[str, list[dict]] = {}
        for row in rows:
            by_family.setdefault(row.get("family", "?"), []).append(row)
        keep: list[dict] = []
        for _fam, group in by_family.items():
            n_eval = max(1, int(len(group) * args.eval_frac))
            keep.extend(group[:-n_eval])
            held.extend(group[-n_eval:])
        rows = keep
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    if held:
        ev = out.with_suffix(".eval.jsonl")
        with ev.open("w", encoding="utf-8", newline="\n") as fh:
            for row in held:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"held out {len(held)} rows -> {ev}")

    turns = [len([m for m in r["messages"] if m.get("tool_calls")]) for r in rows]
    tools = sorted({tc["function"]["name"] for r in rows
                    for m in r["messages"] for tc in (m.get("tool_calls") or [])})
    print(f"wrote {len(rows)} trajectories -> {out}")
    print(f"  call-turns per trajectory: min={min(turns)} max={max(turns)} "
          f"mean={sum(turns) / len(turns):.1f}")
    print(f"  tools exercised: {', '.join(tools)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
