"""R-F4270 — the promoted adapter becomes the ACTUAL parent, or nothing happened.

WHY THIS EXISTS. R-F4259 promoted `failure_correction_v1` (162/168, `adverse`
+2) and its manifest states the consequence in plain words: "the candidate
becomes the accepted PARENT/incumbent for future training cycles". Measured
2026-08-23, **nothing in the tree consumed that decision**:

  * Every launcher pins `PARENT=data/training/checkpoints/aria_tooluse_curve_sft_v5.tgz`
    — the REJECTED 161/168 parent — by sha256. The next cycle would have trained
    from the thing the promotion replaced, so twelve funded candidates' worth of
    progress would not have compounded.
  * `preflight_training_recipe` approves `parent_mode: "accepted_adapter"`
    while having **no idea which adapter is accepted**. The label had no
    referent, so the paid-spend gate could not tell the promoted parent from the
    rejected one. A guard whose universe is empty always certifies — the same
    shape CLAUDE.md §1 records for three Phase A gates.
  * `adjudicate_sweep --incumbent` is a free-text path. Adjudicating the next
    candidate against 161 instead of 162 scores a **null change as +1**, which is
    precisely the error R-F4244 caught in the scorer dimension and fixed there.

So a promotion was, until now, a sentence in a JSON file. This module makes it a
referent that the two places that spend money both consult.

WHAT IT REFUSES TO DO
  * It never derives a record from a person. `build_record` reads the VERDICT and
    re-hashes what the verdict claims, so a record cannot drift from the decision
    that produced it.
  * It never treats an unreadable record as permission. Absent is "I could not
    measure whether this is the accepted parent", which is a refusal to spend,
    never an approval — the absence-reads-as-a-measurement failure this repo has
    now shipped in the Phase A gates, the cost meter and the sanctions latch.
  * It never deletes a checkpoint. Same contract as `pod_of_record`: a superseded
    parent is evidence (it is the baseline every past verdict was measured
    against), and `test_rf4270_parent_of_record.py` asserts the source holds no
    delete path.
  * It never drops the advisory axis. The record carries `advisory_axes` and the
    full per-axis scoreline, so "promoted with tooluse_resolution advisory" stays
    attached to the parent rather than living only in the verdict that authorised
    it. Advisory means measured and reported (R-F4259), including here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
RECORD_FILE = ROOT / "data/training/parent_of_record.json"

POLICY = ("train the next cycle FROM this adapter and adjudicate the next "
          "candidate AGAINST this report; supersede it only by a promotion "
          "verdict, never by editing this file (R-F4270)")


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_hashes(path: pathlib.Path) -> dict:
    """Every line-ending rendering of this file's CONTENT, by name.

    R-F4285 — a raw-byte hash of a TEXT artifact is platform-dependent. The
    R-F4259 verdict recorded `3d399b60...` for its report; the same committed
    file hashes `374ff349...` once checked out with LF. Nothing about the
    evidence changed — only the line terminators — yet the raw comparison
    below refuses it, and would have refused on Linux and in CI all along.

    R-F4283 fixed the storage layer (`.gitattributes` now forces LF on these
    directories); this fixes the COMPARISON, so a record written before that
    transition is still verifiable against the same content afterwards.
    """
    raw = path.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    return {"exact": hashlib.sha256(raw).hexdigest(),
            "lf": hashlib.sha256(lf).hexdigest(),
            "crlf": hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest()}


def matches_recorded(path: pathlib.Path, recorded: str) -> str | None:
    """Which hashing matched: 'exact', 'content' (line endings differ), or None.

    Deliberately returns None for a real change: the tolerance is for line
    terminators ONLY, never for evidence that actually differs. Both directions
    are needed — a CRLF-era hash must still verify an LF checkout (this case)
    and an LF-era hash must verify a CRLF one.
    """
    for how, digest in content_hashes(path).items():
        if digest == recorded:
            return how
    return None


def read_record(path: pathlib.Path = RECORD_FILE) -> dict | None:
    """The accepted parent, or None when it could not be read.

    None is load-bearing and callers must fail closed on it. Collapsing an
    unreadable record into a permissive default would let a paid cycle continue
    from any adapter at all, which is the defect this module exists to close.
    """
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("adapter_sha256") else None


def build_record(verdict_path: pathlib.Path, *, adapter: pathlib.Path,
                 root: pathlib.Path = ROOT) -> dict:
    """Derive the parent record FROM a promotion verdict. Never hand-written.

    Every claim is re-verified against the files on disk, so a record cannot
    outlive the evidence it cites.
    """
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    decision = str(verdict.get("decision") or "")
    if not verdict.get("promotion_authorized") or not decision.startswith("promote:"):
        raise RuntimeError(
            f"{verdict_path.name}: did not promote anything "
            f"(decision={decision!r}) — there is no parent to record")

    wanted = decision.split(":", 1)[1]
    arms = [a for a in (verdict.get("arms") or []) if a.get("arm") == wanted]
    if not arms:
        raise RuntimeError(f"{verdict_path.name}: promoted arm {wanted!r} is not in arms")
    arm = arms[0]

    report_path = root / "data/eval_reports" / str(arm.get("report") or "")
    if not report_path.is_file():
        raise RuntimeError(
            f"{verdict_path.name}: promoted report {arm.get('report')!r} is not on "
            f"disk — a parent whose measurement is gone cannot be verified")
    recorded = str(arm.get("report_sha256") or "")
    how = matches_recorded(report_path, recorded)
    if how is None:
        raise RuntimeError(
            f"{report_path.name}: report content has changed since the verdict "
            f"({sha256(report_path)[:12]}… vs {recorded[:12]}…) — "
            f"the promotion was decided on different evidence")
    report_sha = recorded

    if not adapter.is_file():
        raise RuntimeError(
            f"{adapter}: adapter weights are not on disk — a parent with no "
            f"weights cannot parent anything")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    # Always POSIX-relative when it sits under the repo: this record is committed
    # and read back in CI on linux, where a Windows-separated path resolves to
    # nothing and would read as "the adapter is gone".
    absolute = adapter if adapter.is_absolute() else (pathlib.Path.cwd() / adapter)
    absolute = absolute.resolve()
    return {
        "adapter": absolute.relative_to(root).as_posix()
                   if absolute.is_relative_to(root) else absolute.as_posix(),
        "adapter_sha256": sha256(adapter),
        "report": report_path.relative_to(root).as_posix(),
        "report_sha256": report_sha,
        # 'content' means the bytes on disk differ from the verdict's recorded
        # hash ONLY by line terminators (R-F4285). It is never 'the content
        # changed' — that path raises above.
        "report_sha256_match": how,
        "scorer_version": report.get("scorer_version"),
        "honest": int(report["honest"]),
        "total": int(report["total"]),
        "axis_honest": {str(a["label"]): int(a["honest"])
                        for a in report.get("per_axis") or []},
        "advisory_axes": sorted(verdict.get("advisory_axes") or []),
        "advisory_rationale": verdict.get("advisory_rationale"),
        "promoted_by": {
            "r_number": verdict.get("r_number"),
            "verdict": verdict_path.name,
            "verdict_sha256": sha256(verdict_path),
            "arm": wanted,
        },
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "policy": POLICY,
    }


def write_record(record: dict, path: pathlib.Path = RECORD_FILE) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return record


# -- the two questions callers actually ask ---------------------------------

def adapter_refusal(declared_sha: str | None, record: dict | None) -> str | None:
    """Why this cycle may not spend, or None when it continues from the parent."""
    if record is None:
        return ("parent_adapter_sha256: parent of record is unreadable, so this "
                "cycle cannot be shown to continue from the accepted adapter — "
                "refusing to spend (R-F4270)")
    if not declared_sha:
        return ("parent_adapter_sha256: not declared, but parent_mode is "
                "accepted_adapter — the recipe must name the adapter it continues "
                "from so it can be checked against the parent of record")
    if declared_sha != record["adapter_sha256"]:
        return (f"parent_adapter_sha256: expected {record['adapter_sha256']} "
                f"({record['adapter']}, {record['honest']}/{record['total']}, "
                f"promoted by {record['promoted_by']['r_number']}), "
                f"got {declared_sha}")
    return None


def incumbent_refusal(report_path: pathlib.Path, record: dict | None) -> str | None:
    """Why this report may not stand in as the incumbent."""
    if record is None:
        return ("no parent of record — pass --incumbent explicitly, or register "
                "the promotion with `python -m scripts.train.parent_of_record "
                "register` (R-F4270)")
    if not report_path.is_file():
        return f"{report_path}: incumbent report is not on disk"
    if sha256(report_path) != record["report_sha256"]:
        return (f"{report_path.name} is not the parent of record "
                f"({record['report']}, {record['honest']}/{record['total']}, "
                f"promoted by {record['promoted_by']['r_number']}). Adjudicating "
                f"against a superseded parent scores a null change as a gain. "
                f"Pass --incumbent-override \"<reason>\" to do it deliberately.")
    return None


def record_path(record: dict, root: pathlib.Path = ROOT) -> pathlib.Path:
    return root / record["report"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register", help="derive the parent record from a verdict")
    register.add_argument("--verdict", required=True, type=pathlib.Path)
    register.add_argument("--adapter", required=True, type=pathlib.Path)
    register.add_argument("--out", type=pathlib.Path, default=RECORD_FILE)
    sub.add_parser("show", help="print the accepted parent")
    args = parser.parse_args(argv)

    if args.command == "show":
        record = read_record()
        if record is None:
            print("no parent of record")
            return 1
        print(json.dumps(record, indent=2))
        return 0

    record = write_record(build_record(args.verdict, adapter=args.adapter), args.out)
    print(f"parent of record: {record['adapter']} "
          f"({record['honest']}/{record['total']}, "
          f"promoted by {record['promoted_by']['r_number']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
