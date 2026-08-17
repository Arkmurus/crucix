"""Balance genuine resolution preferences to the measured live branch prior.

R-F4096 keeps every observed clarification failure once and weights each of the
six observed selection failures three times.  The result is 17 clarification
and 18 selection rows: the same 17/18 decision boundary measured by R-F4089,
without inventing a rejected answer.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def balance_pairs(rows: list[dict]) -> list[dict]:
    """Return a deterministic 17/18 ask/select curriculum from genuine pairs."""
    asks = [row for row in rows if str(row.get("why") or "").startswith(
        "did not ask for clarification"
    )]
    selections = [row for row in rows if str(row.get("why") or "").startswith(
        "did not select the resolved"
    )]
    if len(asks) != 17 or len(selections) != 6:
        raise ValueError(
            f"expected measured 17 ask and 6 selection failures, got "
            f"{len(asks)} ask and {len(selections)} selection"
        )
    balanced = asks + [row for row in selections for _ in range(3)]
    if len(balanced) != 35:
        raise AssertionError("balanced resolution curriculum must contain 35 pairs")
    return balanced


def main(argv: list[str] | None = None) -> int:
    """Build the balanced JSONL artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    rows = [
        json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    balanced = balance_pairs(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in balanced),
        encoding="utf-8", newline="\n",
    )
    print(f"wrote {len(balanced)} balanced genuine pairs -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
