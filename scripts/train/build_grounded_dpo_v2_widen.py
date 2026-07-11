#!/usr/bin/env python3
"""build_grounded_dpo_v2_widen — assemble the v2 "widening" grounded-synthesis DPO set.

Goal (USP): widen ARIA's lead on accurate citation + calibrated coverage.
  chosen   = grounded / correctly-cited OR correctly-abstained
  rejected = confident-parametric / fabricated  OR  over-abstention

CLEAN sources only (verified 0-leak vs the frozen 500-Q eval):
  A. data/training/aria_dpo_pairs_v1_str.jsonl (466 usable; 3 [from <source>]
     template artifacts dropped) -> rendered in 3 CITATION-LABEL FORMATS
     (descriptive [Source:..]/[from ..], bracketed [S1]/[S2], numbered [1]/[2])
     to kill the R-F1964 train/eval label-format brittleness. This family is the
     anti-fabrication + abstention-ballast core (chosen mostly = correct abstain,
     rejected = confident fabrication).
  B. data/training/aria_grounded_v1.jsonl, label=="grounded" WITH a citation
     (408) -> ANSWERABLE-COVERAGE pairs: chosen = the grounded fact-stating
     answer (cited), rejected = a bare over-abstention. Raises answer-rate while
     the anti-fabrication penalty from family A holds calibration. Rendered in
     descriptive + one alternate format.

DELIBERATELY EXCLUDED (documented for the audit trail):
  - build_dpo_from_report.py mining: every report in data/eval_reports/ runs the
    defence_dd 500-Q, which is 98.2% the FROZEN gate-#6 eval -> mined prompts are
    eval-contaminated AND closed-book (wrong kind). Not a clean source. Skipped.
  - golden-seed answerable pairs: eval_golden_seed SEED_ENTRIES carry no context
    and no answerable/expected_keywords fields; the frozen eval (which does) is
    off-limits. grounded_v1 is the correct context-bearing source instead.

No paid LLM generation. Pure assembly + deterministic re-labelling.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BASE = Path("data/training/aria_dpo_pairs_v1_str.jsonl")
GROUNDED = Path("data/training/aria_grounded_v1.jsonl")

SRC_RE = re.compile(r"\[Source:\s*([^\]]+?)\s*\]")
FROM_RE = re.compile(r"\[from\s+([^\]]+?)\s*\]")

# instruction-line snippets to rewrite per format
INSTR_FROM = "cite inline as [from <source>]"
INSTR_RAG = "Cite each fact inline using its [Source: ...] label."

# A row labelled "grounded" in aria_grounded_v1 is NOT guaranteed to STATE a
# fact — many merely cite a source while abstaining ("I cannot confirm … [from
# X]"). For answerable-COVERAGE we only want rows whose answer OPENS with a
# grounded fact, so the rejected (a bare over-abstention) is genuinely worse.
# Multilingual opening-abstention markers (EN/DE/ES/PT/FR — the corpus languages):
ABST_OPEN_RE = re.compile(
    r"(cannot confirm|can not confirm|does not contain|do(es)? not (specify|provide|"
    r"mention|include|address|indicate)|don.t have|do not have|no information|not "
    r"present|not contain|unable to|insufficient|cannot answer|cannot determine|"
    r"cannot provide|i cannot|keine (information|angaben|hinweise)|enthält keine|"
    r"nicht gen.gend|no (hay|contiene|proporciona|se (puede|menciona))|não (contém|"
    r"fornece|posso|há)|pas (de|d.information|suffisant)|ne (contient|permet)|no puedo)",
    re.I,
)


def opens_with_fact(answer: str) -> bool:
    """True if the answer's opening asserts a grounded fact (not an abstention)."""
    return not ABST_OPEN_RE.search(answer.strip()[:180])


OVER_ABSTENTIONS = [
    "I don't have enough information in the provided context to answer this question.",
    "The provided context does not contain enough information for me to answer this.",
    "I'm unable to answer this based on the context provided.",
    "There isn't sufficient detail in the context to address this question.",
    "I cannot answer this from the given context.",
]


def ordered_labels(prompt: str) -> list[str]:
    """Unique [Source: LABEL] labels in first-seen order."""
    seen: list[str] = []
    for m in SRC_RE.findall(prompt):
        lab = m.strip()
        if lab and lab not in seen:
            seen.append(lab)
    return seen


def match_index(from_label: str, labels: list[str]) -> int | None:
    fl = from_label.strip()
    for i, lab in enumerate(labels):
        if fl == lab:
            return i
    for i, lab in enumerate(labels):
        if fl and (fl in lab or lab in fl):
            return i
    return None


def token(style: str, idx: int) -> str:
    n = idx + 1
    if style == "s":
        return f"[S{n}]"
    if style == "num":
        return f"[{n}]"
    raise ValueError(style)


def instr_from(style: str) -> str:
    if style == "s":
        return "cite inline as [S1], [S2], … matching the numbered source tags above"
    return "cite inline as [1], [2], … matching the numbered source tags above"


def instr_rag(style: str) -> str:
    if style == "s":
        return "Cite each fact inline using its [S1]/[S2] tag."
    return "Cite each fact inline using its [1]/[2] tag."


def render_variant(prompt: str, chosen: str, style: str) -> tuple[str, str] | None:
    """Return (prompt', chosen') in the requested style, or None if the chosen has
    a citation that cannot be mapped to a context source (so we don't emit a
    format-mixed pair)."""
    if style == "desc":
        return prompt, chosen
    # Rewrite the instruction lines FIRST — the RAG line carries a literal
    # "[Source: ...]" placeholder that must NOT be counted as a real source
    # (otherwise real sources would start numbering at 2).
    new_prompt = prompt.replace(INSTR_FROM, instr_from(style))
    new_prompt = new_prompt.replace(INSTR_RAG, instr_rag(style))
    labels = ordered_labels(new_prompt)
    if not labels:
        return None  # nothing to relabel; skip alt format
    # rewrite prompt: every [Source: LABEL] -> token
    def repl_src(m: re.Match) -> str:
        lab = m.group(1).strip()
        try:
            i = labels.index(lab)
        except ValueError:
            # unseen (shouldn't happen) -> keep first token
            i = 0
        return token(style, i)

    new_prompt = SRC_RE.sub(repl_src, new_prompt)

    # rewrite chosen citations
    froms = FROM_RE.findall(chosen)
    new_chosen = chosen
    for f in froms:
        i = match_index(f, labels)
        if i is None:
            return None  # unmappable citation -> skip this variant
        new_chosen = new_chosen.replace(f"[from {f}]", token(style, i))
    return new_prompt, new_chosen


def build_base(styles: list[str]) -> list[dict]:
    out = []
    rows = [json.loads(l) for l in BASE.read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        prompt, chosen, rejected = r["prompt"], r["chosen"], r["rejected"]
        # drop unfilled template artifacts
        if "[from <source>]" in chosen:
            continue
        for style in styles:
            v = render_variant(prompt, chosen, style)
            if v is None:
                continue
            p, c = v
            out.append({
                "prompt": p, "chosen": c, "rejected": rejected,
                "meta": {"family": "base_varied", "style": style,
                         "kind": "anti_fabrication_abstention_ballast"},
            })
    return out


def build_answerable(alt_styles: list[str]) -> list[dict]:
    out = []
    rows = [json.loads(l) for l in GROUNDED.read_text(encoding="utf-8").splitlines() if l.strip()]
    i_rot = 0
    for r in rows:
        if r.get("label") != "grounded":
            continue
        msgs = r["messages"]
        prompt = next((m["content"] for m in msgs if m["role"] == "user"), "")
        chosen = next((m["content"] for m in msgs if m["role"] == "assistant"), "")
        if not prompt or not chosen:
            continue
        if "[from " not in chosen and "[Source:" not in chosen:
            continue  # require a citation in the fact-stating answer
        if not opens_with_fact(chosen):
            continue  # abstention-opener -> not answerable-coverage; skip
        rejected = OVER_ABSTENTIONS[i_rot % len(OVER_ABSTENTIONS)]
        # descriptive
        out.append({
            "prompt": prompt, "chosen": chosen, "rejected": rejected,
            "meta": {"family": "answerable_coverage", "style": "desc",
                     "kind": "anti_over_abstention"},
        })
        # one alternate format (rotate s / num)
        if alt_styles:
            style = alt_styles[i_rot % len(alt_styles)]
            v = render_variant(prompt, chosen, style)
            if v is not None:
                p, c = v
                out.append({
                    "prompt": p, "chosen": c, "rejected": rejected,
                    "meta": {"family": "answerable_coverage", "style": style,
                             "kind": "anti_over_abstention"},
                })
        i_rot += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path,
                    default=Path("data/training/aria_grounded_dpo_v2_widen.jsonl"))
    args = ap.parse_args()

    rows = []
    rows += build_base(["desc", "s", "num"])
    rows += build_answerable(["s", "num"])

    # dedup exact (prompt, chosen, rejected)
    seen = set()
    deduped = []
    dropped_dup = 0
    dropped_bad = 0
    for r in rows:
        key = (r["prompt"], r["chosen"], r["rejected"])
        if key in seen:
            dropped_dup += 1
            continue
        if not r["prompt"].strip() or not r["chosen"].strip() or not r["rejected"].strip():
            dropped_bad += 1
            continue
        if r["chosen"].strip() == r["rejected"].strip():
            dropped_bad += 1
            continue
        seen.add(key)
        deduped.append(r)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for r in deduped:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    from collections import Counter
    fam = Counter(r["meta"]["family"] for r in deduped)
    style = Counter(r["meta"]["style"] for r in deduped)
    kind = Counter(r["meta"]["kind"] for r in deduped)
    print(f"WROTE {len(deduped)} rows -> {args.out}")
    print(f"  dropped exact-dups: {dropped_dup}  dropped invalid: {dropped_bad}")
    print(f"  by family: {dict(fam)}")
    print(f"  by citation-style: {dict(style)}")
    print(f"  by kind: {dict(kind)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
