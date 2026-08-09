"""R-F3432 — build DPO preference pairs from the model's OWN fabrications.

WHY. Two SFT changes each RELOCATED the same failure instead of removing it. The
model first cited the tool's name (`[from company_house_officers]`); after being
taught the register form it invented register identifiers
(`[from companies_house:officer_role_company_secretary_status]`) and began citing
ARIA's own memory (`[from memory:documents]`). Overall honesty went 0.917 ->
0.881, and a fabricated citation that LOOKS well-formed is more dangerous than
one that is obviously a tool name.

SFT can only show what to imitate — every example is a positive. It has no way to
say "this specific plausible-looking thing you produced is wrong", which is
exactly the signal needed when the failure mode is a well-formed fabrication.
A preference pair carries that.

THE REJECTED SIDE IS REAL. These pairs use the model's ACTUAL generations, so
the negative is the fabrication it genuinely produces. A synthesised bad answer
would teach it to avoid something it was never going to say.

THE CONTAMINATION TRAP IS THE WHOLE RISK. Every failure observed so far came
from the HELD-OUT split. Training on those would train on the eval set and
destroy the only measure of whether any of this works — invisibly, because the
score would improve. So:

  * `eval_entities` is REQUIRED and an empty set is refused. An empty blocklist
    means UNCHECKED, never "nothing to avoid".
  * a pair whose entity appears in the eval split raises, rather than being
    quietly filtered. A silent filter would let a mistake in the caller produce
    a smaller, still-contaminated file.
  * matching uses the same normalisation the split itself uses, so an alias
    cannot slip past.

Generations must therefore be collected over the TRAIN split, not the eval split.

    python -m scripts.train.build_tooluse_dpo \
        --report data/eval_reports/aria_tooluse_train_generations.json \
        --corpus data/training/split_v1/train.jsonl \
        --eval-file data/training/split_v1/eval.jsonl \
        --out data/training/aria_tooluse_dpo_v1.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.train.build_tooluse_corpus import _norm_subject, validate_trace
from scripts.train.eval_tooluse import score_one

_norm = _norm_subject


class EvalContamination(RuntimeError):
    """A preference pair was sourced from a held-out entity."""


def _prompt_of(trace: dict) -> list[dict]:
    """Everything up to, but excluding, the reference answer."""
    return list(trace.get("messages") or [])[:-1]


def _reference_of(trace: dict) -> str:
    msgs = trace.get("messages") or []
    return str((msgs[-1] if msgs else {}).get("content") or "")


def build_pairs(
    report: dict,
    corpus: list[dict],
    *,
    eval_entities: set[str],
    validate_chosen: bool = False,
) -> list[dict]:
    """One pair per FAILED generation, chosen = the corpus reference.

    `eval_entities` holds NORMALISED keys (`_norm_subject`), because that is what
    the split itself groups by — passing raw strings would match nothing and the
    guard would pass while checking nothing. Raises EvalContamination if any
    failing row belongs to a held-out entity.
    """
    if not eval_entities:
        raise ValueError(
            "eval_entities is empty — that means UNCHECKED, not 'nothing to avoid'. "
            "Pass the held-out split's entities so contamination can be refused."
        )

    by_task: dict[tuple[str, str], dict] = {}
    for t in corpus:
        key = _norm(str(t.get("subject") or ""))
        label = str(t.get("label") or "")
        if key and label:
            by_task.setdefault((key, label), t)

    pairs: list[dict] = []
    for row in (report.get("rows") or []):
        if row.get("honest"):
            continue                       # nothing to prefer; it was already right
        subject = str(row.get("subject") or "")
        key = _norm(subject)
        if key and key in eval_entities:
            raise EvalContamination(
                f"refusing to build a preference pair from {subject!r}: it is in the "
                f"HELD-OUT split. Generations for DPO must come from the TRAIN split, "
                f"or the eval set is trained on and the only honest measure is lost."
            )
        label = str(row.get("label") or "")
        trace = by_task.get((key, label))
        if trace is None:
            print(f"  skip {subject} [{label}]: no matching entity+axis corpus "
                  f"reference to prefer (would have to invent the 'chosen' side)",
                  file=sys.stderr)
            continue
        rescored = score_one(trace, row.get("answer"))
        if rescored.get("honest"):
            continue  # stale report failure corrected by the current validator
        rejected = str(row.get("answer") or "")
        chosen = _reference_of(trace)
        if not rejected.strip() or not chosen.strip():
            continue
        if rejected.strip() == chosen.strip():
            continue                       # no difference to learn from
        if validate_chosen:
            probe = dict(trace)
            probe["messages"] = _prompt_of(trace) + [
                {"role": "assistant", "content": chosen}]
            if validate_trace(probe):
                print(f"  skip {subject}: the corpus reference does not itself "
                      f"validate — it cannot be the preferred answer", file=sys.stderr)
                continue
        pairs.append({
            "prompt": _prompt_of(trace),
            "chosen": chosen,
            "rejected": rejected,
            "subject": subject,
            "label": row.get("label"),
            "why": (rescored.get("errors") or [""])[0],
        })
    return pairs


def write_pairs(pairs: list[dict], out: Path) -> int:
    """Write one JSON object per line. Refuses to write nothing."""
    if not pairs:
        raise ValueError(
            "no pairs to write — an empty preference set trains nothing while "
            "looking like the step ran"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pairs),
        encoding="utf-8", newline="\n",
    )
    return len(pairs)


def _load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", required=True, type=Path,
                    help="eval-format report of generations over the TRAIN split")
    ap.add_argument("--corpus", required=True, type=Path, help="the train split")
    ap.add_argument("--eval-file", required=True, type=Path,
                    help="the held-out split; its entities are refused as pair sources")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--validate-chosen", action="store_true", default=True)
    a = ap.parse_args(argv)

    report = json.loads(a.report.read_text(encoding="utf-8"))
    corpus = _load_jsonl(a.corpus)
    held = {_norm(str(r.get("subject") or "")) for r in _load_jsonl(a.eval_file)} - {""}

    pairs = build_pairs(report, corpus, eval_entities=held,
                        validate_chosen=a.validate_chosen)
    n = write_pairs(pairs, a.out)

    by_label: dict[str, int] = {}
    for p in pairs:
        by_label[str(p.get("label"))] = by_label.get(str(p.get("label")), 0) + 1
    print(f"wrote {n} preference pairs -> {a.out}")
    for lab, c in sorted(by_label.items(), key=lambda kv: -kv[1]):
        print(f"  {lab:<32}{c:>4}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
