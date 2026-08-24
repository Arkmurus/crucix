"""R-F3394 — split the tool-use corpus by ENTITY, so eval measures learning not memorisation.

WHY NOT BY ROW. The corpus is 222 rows over 116 distinct subjects: Rolls-Royce
Holdings plc appears 5 times, Sberbank 4, Rosneft 4. A random row split puts the
same company in train AND eval, so the score reports how well the model memorised
Rolls-Royce — not whether it can screen a company it has never seen. Spending GPU
hours to produce that number is worse than not measuring, because it looks like
evidence.

ALIASES ARE THE SILENT CASE. "Rolls-Royce", "Rolls-Royce Holdings plc" and
"ROLLS-ROYCE HOLDINGS PLC" are one company. A split keyed on the raw string puts
them on opposite sides and leaks while LOOKING clean. Grouping therefore uses the
same `_norm_subject` normalisation the contamination guard uses, so the two agree
by construction rather than by coincidence.

DETERMINISTIC, AND STABLE UNDER INPUT ORDER. The assignment is a hash of the
normalised entity, not a shuffle. Re-running a capture — which returns rows in
whatever order the registry felt like — must not reshuffle the split, or
yesterday's eval set silently becomes today's training data and every number
after that is contaminated.

STRATIFIED BY LABEL. Six axes (single-hop, multi-hop, challenge, resolution,
news, unavailable). A naive entity split can put every `news` trace in train,
leaving eval unable to say anything about that capability — a blind spot that
looks like a passing grade. Entities are bucketed per label so both sides carry
every axis.

    python -m scripts.train.split_corpus --out-dir data/training/split_v1
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from scripts.train.build_tooluse_corpus import _norm_subject


def _entity_of(row: dict) -> str:
    """The normalised company/person a trace is about."""
    raw = row.get("subject") or ""
    if not raw:
        msgs = row.get("messages") or []
        raw = (msgs[1].get("content", "") if len(msgs) > 1 else "")[:60]
    return _norm_subject(raw) or "unknown"


def _bucket(entity: str, eval_fraction: float) -> bool:
    """True when this entity belongs to the eval side.

    A hash, not a shuffle: the same entity always lands on the same side no
    matter what order the rows arrive in.
    """
    h = hashlib.sha1(entity.encode("utf-8"), usedforsecurity=False).hexdigest()
    return (int(h[:8], 16) % 10_000) < int(eval_fraction * 10_000)


def golden_entities(path: Path | None) -> set[str]:
    """Normalised subjects appearing in the frozen 500-Q benchmark.

    Read as TOKEN SETS by the caller: `_norm_subject` strips corporate suffixes,
    so a golden entry and a corpus subject for one company routinely normalise
    to different strings ("wagner" vs "wagner group pmc") and an equality
    compare declares them unrelated.
    """
    if path is None or not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            raw = str(obj.get("subject") or obj.get("entity") or obj.get("question") or "")
        except json.JSONDecodeError:
            raw = line
        if norm := _norm_subject(raw):
            out.add(norm)
    return out


def _touches_golden(entity: str, golden: set[str]) -> bool:
    t = set(entity.split())
    return bool(t) and any(t <= set(g.split()) or set(g.split()) <= t for g in golden)


def split_by_entity(
    rows: Iterable[dict],
    eval_fraction: float = 0.2,
    golden: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split rows into (train, eval) so no entity appears on both sides.

    `golden` — subjects from the frozen 500-Q benchmark. Any entity that also
    appears there is FORCED to the eval side and can never enter training.
    Training on an entity you are later graded on inflates Phase-A gate #6, the
    gate whose whole purpose is to be the honest measure; the live corpus had
    exactly one such entity (Almaz-Antey, whose golden question is "Run Layer 1
    on 'PJSC Almaz-Antey'"). Forcing rather than dropping keeps the rows useful
    — they still measure — and makes the protection structural, so it holds for
    every subject captured from here on instead of depending on someone
    remembering to look.

    Raises when a split is impossible (no rows, or a single entity), because an
    empty eval set that silently returns is how a meaningless benchmark gets
    reported as a real one.
    """
    rows = list(rows)
    if not rows:
        raise ValueError("cannot split an empty corpus")
    if not 0.0 < eval_fraction < 1.0:
        raise ValueError(f"eval_fraction must be in (0,1), got {eval_fraction}")

    entities = {_entity_of(r) for r in rows}
    if len(entities) < 2:
        raise ValueError(
            f"cannot hold out an entity: the corpus covers only {len(entities)} "
            f"distinct subject(s). Capture more subjects before splitting."
        )

    # Stratify: bucket entities WITHIN each label so every axis appears on both
    # sides. Assignment stays a pure function of the entity name.
    by_label: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        by_label[str(r.get("label") or "unlabelled")].add(_entity_of(r))

    gold = golden or set()
    forced = {e for e in entities if _touches_golden(e, gold)}

    eval_entities: set[str] = set(forced)
    for _label, ents in by_label.items():
        ordered = sorted(ents)                       # deterministic
        chosen = {e for e in ordered if _bucket(e, eval_fraction)} | (ents & forced)
        # Never let a label vanish from either side when it has >1 entity.
        if not chosen and len(ordered) > 1:
            chosen = {ordered[0]}
        if len(chosen) == len(ordered) and len(ordered) > 1:
            # Keep a train side — but never by demoting a golden-forced entity
            # back into training, which is the one thing this must not do.
            for cand in reversed(ordered):
                if cand not in forced:
                    chosen.discard(cand)
                    break
        eval_entities |= chosen

    train = [r for r in rows if _entity_of(r) not in eval_entities]
    ev = [r for r in rows if _entity_of(r) in eval_entities]
    return train, ev


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="data/training/aria_tooluse_*.jsonl")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--eval-fraction", type=float, default=0.2)
    ap.add_argument("--golden-set", type=Path,
                    default=Path("data/eval_frozen/aria_eval_500q.jsonl"),
                    help="frozen 500-Q set; its entities are forced out of training")
    a = ap.parse_args()

    rows: list[dict] = []
    for p in sorted(glob.glob(a.glob)):
        rows += [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines()
                 if l.strip()]
    gold = golden_entities(a.golden_set)
    if not gold:
        print(f"WARNING: no golden set read from {a.golden_set} — "
              f"golden entities are NOT being kept out of training")
    train, ev = split_by_entity(rows, eval_fraction=a.eval_fraction, golden=gold)

    a.out_dir.mkdir(parents=True, exist_ok=True)
    for name, part in (("train.jsonl", train), ("eval.jsonl", ev)):
        (a.out_dir / name).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in part) + "\n",
            encoding="utf-8", newline="\n")

    tr_e = {_entity_of(r) for r in train}
    ev_e = {_entity_of(r) for r in ev}
    assert not (tr_e & ev_e), f"LEAK: {sorted(tr_e & ev_e)[:5]}"
    leaked = sorted(e for e in tr_e if _touches_golden(e, gold))
    assert not leaked, f"GOLDEN CONTAMINATION in train: {leaked[:5]}"
    print(f"train {len(train)} rows / {len(tr_e)} entities")
    print(f"eval  {len(ev)} rows / {len(ev_e)} entities")
    print(f"entity overlap: {len(tr_e & ev_e)} (must be 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
