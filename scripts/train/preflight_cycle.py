"""R-F3395 — refuse to start a paid training cycle on a dataset that cannot train.

WHAT WENT WRONG. Every blocker in the 2026-07-28 corpus build was knowable for
free, before any GPU existed, and every one of them was found by hand instead:
tool-call ids Mistral's template rejects; a base-model default that disagreed
with every runbook; train/eval entity leakage; three subjects that also sat in
the frozen 500-Q golden set; rows with no `subject` that collapsed into one
pseudo-entity. The last one was caught only because a split happened to be
built that day. That is luck, not a process.

WHY THE OLD PRE-FLIGHT DID NOT CATCH THEM. `dpo_v04_pod_run.sh` checks that the
corpus file is non-empty (`[ -s ]`) and prints `wc -l`. A file full of rows that
cannot render is non-empty and has a line count. Its one real check — the base
architecture signature — is genuine but runs ON THE POD, after `pip install`,
with the meter already running. This module moves every check that needs no GPU
to the free side of the spend, and adds the ones that were missing.

SKIPPED IS NOT PASS. A check that could not run (no tokenizer, no golden set)
reports SKIPPED and says why. Reporting green because a check did not execute is
how absence gets laundered into evidence — the failure class behind three
fabricated Phase-A gate passes. `--strict` (used by the pod path) makes SKIPPED
blocking, so the free local run can be informative while the paid run cannot
proceed on unproven input.

    python -m scripts.train.preflight_cycle \
        --train-file data/training/split_v1/train.jsonl \
        --eval-file  data/training/split_v1/eval.jsonl \
        --base-model mistralai/Mistral-7B-Instruct-v0.3 --strict
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from scripts.train.build_tooluse_corpus import _norm_subject, validate_trace

# The base ARIA's adapters are bound to. Recorded in activate_aria_llm_v01.sh
# and enforced by baseline_pod_run.sh — an adapter trained on anything else is
# architecturally unloadable, not merely worse.
ARIA_BASE_MODEL = "mistralai/Mistral-7B-Instruct-v0.3"

# Config signature of that base. Checking the NAME proves nothing: a fork, a
# mirror or a renamed repo all pass a string compare and then blow up at load.
MISTRAL_V03_SIGNATURE: dict[str, Any] = {
    "model_type": "mistral",
    "vocab_size": 32768,
    "num_hidden_layers": 32,
    "hidden_size": 4096,
    "intermediate_size": 14336,
}

_MAX_NAMED = 5  # how many offending rows to name before summarising


class Result:
    """One check's outcome. Starts SKIPPED: nothing is proven until it runs."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.status = "SKIPPED"
        self.detail = "did not run"

    def ok(self, detail: str) -> "Result":
        self.status, self.detail = "PASS", detail
        return self

    def fail(self, detail: str) -> "Result":
        self.status, self.detail = "FAIL", detail
        return self

    def skip(self, detail: str) -> "Result":
        self.status, self.detail = "SKIPPED", detail
        return self

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"

    def line(self) -> str:
        mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "SKIPPED": " skip "}[self.status]
        return f"[{mark}] {self.name:<14} {self.detail}"


def exit_code(checks: Sequence[Result], *, strict: bool) -> int:
    """Non-zero if anything failed — or, in strict mode, did not run."""
    if any(c.failed for c in checks):
        return 1
    if strict and any(c.status == "SKIPPED" for c in checks):
        return 2
    return 0


def _entity(row: dict) -> str:
    return _norm_subject(str(row.get("subject") or "")) or ""


def _describe(items: Iterable[str], total: int) -> str:
    shown = list(items)[:_MAX_NAMED]
    more = f" (+{total - len(shown)} more)" if total > len(shown) else ""
    return ", ".join(shown) + more


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def check_schema(rows: Sequence[dict]) -> Result:
    """Every row must satisfy the corpus builder's own grounding validator.

    Reusing `validate_trace` rather than re-deriving the rules means the gate
    and the builder cannot drift apart into disagreeing about what is valid.
    """
    res = Result("schema")
    bad: list[str] = []
    for i, row in enumerate(rows):
        errs = validate_trace(row)
        if errs:
            bad.append(f"row {i} ({row.get('subject') or '?'}): {errs[0]}")
    if bad:
        return res.fail(f"{len(bad)} invalid — {_describe(bad, len(bad))}")
    return res.ok(f"{len(rows)} rows valid")


def check_subjects(rows: Sequence[dict]) -> Result:
    """A row with no subject normalises to "" and joins one pseudo-entity.

    That is invisible to a split — every subject-less row lands on the same
    side and the split still reports zero overlap.
    """
    res = Result("subjects")
    missing = [i for i, r in enumerate(rows) if not str(r.get("subject") or "").strip()]
    if missing:
        return res.fail(
            f"{len(missing)} rows carry no subject (they collapse into one "
            f"entity and defeat the split): rows {_describe([str(i) for i in missing], len(missing))}"
        )
    return res.ok(f"{len(rows)} rows carry a subject")


def check_split(train: Sequence[dict], evalset: Sequence[dict]) -> Result:
    """Train and eval must not share an entity — aliases included."""
    res = Result("split")
    if not evalset:
        return res.fail("eval side is empty — the resulting score would mean nothing")
    if not train:
        return res.fail("train side is empty")
    t = {_entity(r) for r in train} - {""}
    e = {_entity(r) for r in evalset} - {""}
    both = sorted(t & e)
    if both:
        return res.fail(
            f"{len(both)} entities on BOTH sides (eval would measure memorisation): "
            f"{_describe(both, len(both))}"
        )
    return res.ok(f"{len(t)} train / {len(e)} eval entities, disjoint")


def check_contamination(train: Sequence[dict], golden: Sequence[str]) -> Result:
    """No TRAINING subject may appear in the frozen 500-Q benchmark.

    The direction is load-bearing and easy to get backwards. Contamination is
    training on what you later grade yourself against: an entity in TRAIN that
    also sits in the golden set inflates the Phase-A gate #6 score, which is
    supposed to be the honest measure. An entity in our held-out EVAL split that
    also appears in golden is not contamination at all — both are held out, the
    model never saw either, and nothing is inflated.

    Passing the eval side here would flag harmless duplication while leaving the
    only overlap that corrupts a gate completely unchecked.
    """
    res = Result("contamination")
    if not golden:
        return res.skip("no golden set supplied (--golden-set) — overlap unproven")
    # Token containment, not equality. `_norm_subject` strips corporate suffixes,
    # so "Wagner Group" becomes `wagner` while a golden entry "Wagner Group PMC"
    # keeps all three tokens — an exact compare declares them unrelated and the
    # overlap that actually matters walks straight through. Containment errs
    # toward flagging: a false flag costs one review, a miss silently invalidates
    # every number the cycle produces.
    g = [set(s.split()) for s in ({_norm_subject(x) for x in golden} - {""})]
    hits = sorted(
        e for e in {_entity(r) for r in train} - {""}
        if any((t := set(e.split())) <= gt or gt <= t for gt in g)
    )
    if hits:
        return res.fail(
            f"{len(hits)} TRAIN subjects also in the frozen golden set (gate #6 would be inflated): {_describe(hits, len(hits))}"
        )
    return res.ok(f"0 of {len(train)} train rows overlap {len(g)} golden entities")


def check_render(rows: Sequence[dict], tokenizer: Any) -> Result:
    """Every row must survive the REAL chat template.

    This is the check that would have paid for itself: `id="call_1"` renders
    fine in our JSON and raises `Tool call IDs should be alphanumeric strings
    with length 9!` inside Mistral's template — on the pod, after the install.
    """
    res = Result("render")
    if tokenizer is None:
        return res.skip("no tokenizer loaded (pass --base-model) — renderability unproven")
    bad: list[str] = []
    for i, row in enumerate(rows):
        try:
            tokenizer.apply_chat_template(row.get("messages") or [], tokenize=False)
        except Exception as exc:  # noqa: BLE001 — any template refusal is fatal
            bad.append(f"row {i} ({row.get('subject') or '?'}): {exc}")
    if bad:
        return res.fail(f"{len(bad)} unrenderable — {_describe(bad, len(bad))}")
    return res.ok(f"{len(rows)} rows render")


def check_length(rows: Sequence[dict], tokenizer: Any, max_seq_len: int) -> Result:
    """Rows over the budget are truncated mid-trace and trained on as stumps.

    Truncation is silent: the run completes, the loss looks normal, and the
    model has learnt to stop halfway through a tool call.
    """
    res = Result("length")
    if tokenizer is None:
        return res.skip("no tokenizer loaded (pass --base-model) — token budget unproven")
    over: list[str] = []
    longest = 0
    for i, row in enumerate(rows):
        try:
            text = tokenizer.apply_chat_template(row.get("messages") or [], tokenize=False)
            n = len(tokenizer(text)["input_ids"])
        except Exception:  # noqa: BLE001 — render check already reports this row
            continue
        longest = max(longest, n)
        if n > max_seq_len:
            over.append(f"row {i} ({row.get('subject') or '?'}): {n} tok")
    if over:
        return res.fail(
            f"{len(over)} rows exceed --max-seq-len {max_seq_len} and would be "
            f"silently truncated — {_describe(over, len(over))}"
        )
    return res.ok(f"longest {longest} tok <= {max_seq_len}")


def check_base_model(
    name: str,
    *,
    loader: Callable[[str], Any] | None = None,
    signature: str = "",
) -> Result:
    """The declared base must match a DECLARED signature.

    Fails closed: a config that cannot be read is unproven, not acceptable.

    R-F4372 (C-317) — the signature was hardcoded to Mistral-7B-Instruct-v0.3.
    That is right as a DEFAULT and wrong as a law: it made a deliberate base
    change indistinguishable from an accidental one, so the only way to train on
    a code-specialised base was to delete the guard. Now the expected signature
    is stated by the caller and still enforced. An EMPTY value falls back to the
    Mistral default rather than matching everything — a guard you can disable by
    clearing a variable is not a guard.
    """
    res = Result("base-model")
    if not name:
        return res.fail("no base model declared")
    want = MISTRAL_V03_SIGNATURE
    if str(signature).strip():
        try:
            want = json.loads(signature)
        except Exception as exc:  # noqa: BLE001
            return res.fail(f"--base-signature is not JSON ({exc}) — unproven")
        if not isinstance(want, dict) or not want:
            return res.fail("--base-signature must be a non-empty object")
    if loader is None:
        try:
            from transformers import AutoConfig  # noqa: PLC0415 — optional heavy dep

            loader = lambda m: AutoConfig.from_pretrained(m).to_dict()  # noqa: E731
        except Exception as exc:  # noqa: BLE001
            return res.skip(f"transformers unavailable ({exc}) — architecture unproven")
    try:
        cfg = loader(name)
    except Exception as exc:  # noqa: BLE001
        return res.fail(f"cannot read config for {name!r}: {exc}")
    if not isinstance(cfg, dict):
        cfg = {k: getattr(cfg, k, None) for k in MISTRAL_V03_SIGNATURE}
    bad = {
        k: (cfg.get(k), expect)
        for k, expect in want.items()
        if cfg.get(k) != expect
    }
    if bad:
        return res.fail(f"{name} does not match the declared signature "
                        f"(got, want): {bad}")
    return res.ok(
        f"{name} — {want.get('model_type')}, vocab {want.get('vocab_size')}, "
        f"{want.get('num_hidden_layers')}L, {want.get('hidden_size')}d")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _load(path: Path) -> list[dict]:
    rows: list[dict] = []
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{ln} is not valid JSON: {exc}") from exc
    return rows


# --------------------------------------------------------------------------
# R-F4372 (C-317) — CODER-corpus checks.
#
# The checks above encode a DD corpus: every row names a SUBJECT entity, the
# split must keep entities disjoint, and `validate_trace` scores citations and
# sanctions verdicts. A coding trajectory has no subject and no sources, so on a
# coder corpus those three checks fail for reasons that say nothing about
# whether the data is fit to train on — and a gate that always fails gets
# switched off, which is how the paid path loses its only pre-spend guard.
#
# These are the SAME QUESTIONS asked of the right corpus: is every row valid by
# its own builder's rules, does the eval leak into the train split, and does the
# training data overlap the frozen benchmark.
# --------------------------------------------------------------------------

def check_coder_schema(rows: Sequence[dict]) -> Result:
    """Every call names a real tool and uses only declared parameters.

    The rules come from `coder_tool_contract`, the same module the builder and
    the evaluator read, so this gate cannot drift from what was trained — the
    reason `check_schema` reuses `validate_trace`.
    """
    res = Result("coder-schema")
    try:
        from scripts.train.coder_tool_contract import BANNED, TOOL_PARAMS
    except Exception as exc:  # noqa: BLE001
        return res.fail(f"coder contract unavailable ({exc}) — rules unproven")

    bad: list[str] = []
    for i, row in enumerate(rows):
        msgs = row.get("messages") or []
        ids = set()
        calls = 0
        for m in msgs:
            if m.get("role") == "assistant":
                low = (m.get("content") or "").lower()
                for phrase in BANNED:
                    if phrase in low:
                        bad.append(f"row {i}: teaches a refusal ({phrase!r})")
                        break
            for tc in (m.get("tool_calls") or []):
                calls += 1
                fn = tc.get("function") or {}
                name = fn.get("name")
                cid = tc.get("id") or ""
                ids.add(cid)
                if name not in TOOL_PARAMS:
                    bad.append(f"row {i}: unknown tool {name!r}")
                    continue
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:  # noqa: BLE001
                    bad.append(f"row {i}: {name} arguments are not JSON")
                    continue
                undeclared = sorted(set(args) - TOOL_PARAMS[name])
                if undeclared:
                    bad.append(f"row {i}: {name} undeclared {undeclared}")
                if len(cid) != 9 or not cid.isalnum():
                    bad.append(f"row {i}: tool id {cid!r} is not 9 alphanumeric")
        if calls < 2:
            bad.append(f"row {i}: {calls} call-turn(s) — too shallow to teach depth")
        for m in msgs:
            if m.get("role") == "tool" and m.get("tool_call_id") not in ids:
                bad.append(f"row {i}: orphan tool message")
    if bad:
        return res.fail(f"{len(bad)} problem(s) — {_describe(bad, len(bad))}")
    return res.ok(f"{len(rows)} rows valid against the coder tool contract")


def _coder_prompt(row: dict) -> str:
    for m in row.get("messages") or []:
        if m.get("role") == "user":
            return " ".join(str(m.get("content") or "").split()).lower()
    return ""


def check_coder_leakage(train: Sequence[dict], evalset: Sequence[dict]) -> Result:
    """No held-out TASK may also appear in training.

    The coder split is deliberately by FAMILY TAIL, not by entity: train and
    eval share families on purpose, because the question is whether she learned
    the SHAPE. So the leakage that matters is an identical prompt on both sides,
    which would let a memorised answer score as a learned one.
    """
    res = Result("coder-leakage")
    if not evalset:
        return res.fail("eval set is empty — nothing is held out")
    seen = {_coder_prompt(r) for r in train}
    leaked = sorted({p for r in evalset if (p := _coder_prompt(r)) and p in seen})
    if leaked:
        return res.fail(
            f"{len(leaked)} held-out prompt(s) also appear in train — "
            f"{_describe([p[:60] for p in leaked], len(leaked))}")
    return res.ok(f"{len(evalset)} held-out prompts, none seen in train")


def check_coder_contamination(rows: Sequence[dict], golden: Sequence[str]) -> Result:
    """The training data must not contain a frozen-benchmark question.

    `check_contamination` compares SUBJECTS; coder rows have none, so it passes
    vacuously — a pass that proves nothing is worse than no check, because it
    reads as cleared. This compares the prompt text instead.
    """
    res = Result("coder-contamination")
    if not golden:
        return res.skip("no golden set supplied — contamination UNPROVEN")
    norm = [" ".join(str(g).split()).lower() for g in golden if str(g).strip()]
    if not norm:
        return res.skip("golden set held no questions — contamination UNPROVEN")
    hits: list[str] = []
    for i, row in enumerate(rows):
        p = _coder_prompt(row)
        if not p:
            continue
        for g in norm:
            if len(g) > 25 and (g in p or p in g):
                hits.append(f"row {i}: {p[:60]}")
                break
    if hits:
        return res.fail(f"{len(hits)} row(s) overlap the frozen set — "
                        f"{_describe(hits, len(hits))}")
    return res.ok(f"{len(rows)} rows checked against {len(norm)} frozen questions")


def _golden_subjects(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            out.append(str(obj.get("subject") or obj.get("entity") or obj.get("question") or ""))
        except json.JSONDecodeError:
            out.append(line)
    return [s for s in out if s]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train-file", required=True, type=Path)
    ap.add_argument("--eval-file", required=True, type=Path)
    ap.add_argument("--golden-set", type=Path, default=None,
                    help="frozen 500-Q set; without it the contamination check is SKIPPED, not passed")
    ap.add_argument("--base-model", default="",
                    help=f"declared base (expected {ARIA_BASE_MODEL}); enables render/length/architecture checks")
    ap.add_argument("--max-seq-len", type=int, default=4096)
    ap.add_argument("--kind", choices=("dd", "coder"), default="dd",
                    help="which corpus this is. 'dd' keeps every existing "
                         "check unchanged; 'coder' swaps the three that encode "
                         "a subject-bearing DD row.")
    ap.add_argument("--base-signature", default="",
                    help="JSON config signature the base must match. Defaults "
                         "to Mistral-7B-Instruct-v0.3, so an unset value "
                         "behaves exactly as before.")
    ap.add_argument("--strict", action="store_true",
                    help="treat SKIPPED as blocking — use on the paid path")
    args = ap.parse_args(argv)

    for p in (args.train_file, args.eval_file):
        if not p.exists():
            print(f"[ FAIL ] missing file: {p}")
            return 1

    train, evalset = _load(args.train_file), _load(args.eval_file)
    allrows = train + evalset

    tokenizer = None
    if args.base_model:
        try:
            from transformers import AutoTokenizer  # noqa: PLC0415 — optional heavy dep

            tokenizer = AutoTokenizer.from_pretrained(args.base_model)
        except Exception as exc:  # noqa: BLE001
            print(f"[ note ] tokenizer unavailable: {exc}")

    golden = _golden_subjects(args.golden_set)
    if args.kind == "coder":
        checks = [
            check_coder_schema(allrows),
            check_coder_leakage(train, evalset),
            check_coder_contamination(train, golden),
        ]
    else:
        checks = [
            check_schema(allrows),
            check_subjects(allrows),
            check_split(train, evalset),
            check_contamination(train, golden),
        ]
    checks += [
        check_render(allrows, tokenizer),
        check_length(allrows, tokenizer, args.max_seq_len),
    ]
    if args.base_model:
        checks.append(check_base_model(args.base_model,
                                       signature=args.base_signature))
    else:
        checks.append(Result("base-model").skip("not declared (--base-model) — architecture unproven"))

    print(f"pre-flight — {len(train)} train / {len(evalset)} eval rows")
    for c in checks:
        print(c.line())

    code = exit_code(checks, strict=args.strict)
    if code:
        print("\nBLOCKED: do not start a paid cycle on this dataset.")
    else:
        print("\nclear to train.")
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
