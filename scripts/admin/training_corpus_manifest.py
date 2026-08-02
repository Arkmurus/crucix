"""R-F3637 — pin the training corpus, and make §24's pre-flight mechanical.

WHY THIS EXISTS
---------------
§24 attaches a condition to the operator's standing spend approval for the weekly
train/eval cycle:

    "training must be REAL — pre-flight review of the training pipeline + dataset
     quality before any paid cycle; a cycle that would train on unreviewed/contaminated
     data is cancelled, not run."

That condition had no machine. Worse, it had nothing to review AGAINST: every file in
`data/training/*.jsonl` is UNTRACKED. Nothing pins them, so:

  * a run cannot be attributed to a corpus — there is no sha to record beside a
    checkpoint, and no diff between v06 and v07;
  * an eval movement cannot be explained — you cannot tell a real gain from a
    relocated artefact (`a-fix-that-relocates-a-failure-is-not-a-fix`);
  * CONTAMINATION cannot be disproved. Phase A gate #6 pins the 500-Q golden set at
    hash a07b6af760ad7f44. If a training row is also an eval row, the score is
    measuring memorisation and gate #6 stops meaning anything — the exact false clean
    this repo exists to prevent, in the one place it would be most expensive.

The corpus is NOT committed here: it is large, and a hash manifest gives attribution
without turning the repo into a data store. What must be reproducible is the ANSWER to
"which rows produced this checkpoint", and a manifest answers it.

USAGE
-----
    python scripts/admin/training_corpus_manifest.py            # report + contamination check
    python scripts/admin/training_corpus_manifest.py --record   # write the manifest

Exit codes:  0 clean · 1 CONTAMINATION FOUND · 2 refused to record

Contamination is checked against the LIVE golden set when reachable. When it is not,
this reports UNKNOWN and refuses to record a clean bill — absent is not false
(`declared-capability-flag-drifts`). A manifest claiming "no contamination" because it
could not look is worse than no manifest.

R-F3639 — WHY THERE IS A REMOTE PATH
------------------------------------
R-F3637 shipped this reading the golden set through `eval_runner.get_golden_set()`, and
closed by saying the pre-flight should be "run on aria-intel, where the golden set is
reachable". That step could never be taken, and the reason is structural, not a config
miss: THE TWO INPUTS LIVE ON DIFFERENT MACHINES.

  * the corpus is 57 untracked files / 136 MB under `data/training/` on the DEV box. It
    is not in git, so it is not in the image CI builds from the pushed sha — aria-intel
    has never seen it;
  * the golden set is a `state_store` value on aria-intel's `/data` volume. The dev box's
    local store does not have it.

So "run it there" had no corpus and "run it here" had no eval — the gate was unrunnable
from either end, and §24's condition stayed un-mechanised while reading as shipped.

This bridges them in ONE command: when the local store cannot answer, read the golden set
over `flyctl ssh` (read-only, `mode=ro`) and compare on HASHES. Hashes are the point, not
an optimisation — the eval questions never leave the box, so proving the corpus is clean
never risks copying the eval next to the training data it must stay separate from.

Every source is named in the output and recorded in the manifest, so a later reader can
tell WHICH golden set a corpus was cleared against. Unreachable stays UNKNOWN; the remote
path adds a way to look, never a reason to assume.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPUS_DIR = ROOT / "data" / "training"
MANIFEST = ROOT / "docs" / "training_corpus_manifest.json"
DEFAULT_APP = "aria-intel"
GOLDEN_SET_KEY = "crucix:aria:eval:golden_set"
# eval_runner.FREEZE_KEY — the gate-#6 pin (count + content hash). Recorded beside the
# corpus so "cleared against 500 prompts" names WHICH 500: without it, a later reader
# cannot tell the frozen set from any other set that happened to have 500 rows.
FREEZE_KEY = "crucix:aria:eval:500q:status"


def _norm(text: str) -> str:
    """Normalise a prompt for overlap comparison.

    Deliberately aggressive: contamination that survives whitespace or case changes is
    still contamination. A stricter (exact-match) comparison would under-report, and
    under-reporting is the failure mode that matters here.
    """
    return " ".join((text or "").split()).strip().lower()


def _row_prompt(row: dict) -> str:
    """Pull the question/prompt out of a corpus row, whatever the schema calls it."""
    for key in ("question", "prompt", "instruction", "input", "query"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v
    # chat-style: first user turn
    msgs = row.get("messages")
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, str) and c.strip():
                    return c
    return ""


def _prompt_hash(text: str) -> str:
    """The comparison currency, shared by the local and remote paths.

    Both sides MUST derive this identically or the intersection silently reads empty —
    i.e. it would report a clean corpus by failing to compare. The remote probe below
    carries a copy of `_norm`/`_row_prompt`/this digest because it runs in aria-intel's
    interpreter, not ours; `_assert_probe_agrees()` pins them together.
    """
    return hashlib.sha256(_norm(text).encode()).hexdigest()[:20]


def scan_corpus() -> list[dict]:
    """SHA-256, row count and byte size for every corpus file — the pin."""
    out: list[dict] = []
    for path in sorted(CORPUS_DIR.glob("*.jsonl")):
        digest = hashlib.sha256()
        rows = 0
        bad = 0
        hashes: set[str] = set()
        with path.open("rb") as fh:
            for raw in fh:
                digest.update(raw)
                if not raw.strip():
                    continue
                rows += 1
                try:
                    p = _row_prompt(json.loads(raw))
                except Exception:
                    bad += 1          # counted, never silently dropped
                    continue
                if p:
                    hashes.add(_prompt_hash(p))
        out.append({
            "file": path.name,
            "sha256": digest.hexdigest()[:32],
            "rows": rows,
            "unparseable_rows": bad,
            "bytes": path.stat().st_size,
            "_hashes": hashes,            # stripped before writing
        })
    return out


# The remote reader. Runs in aria-intel's interpreter over `flyctl ssh`, opens the state
# DB READ-ONLY, and emits prompt HASHES only — the eval questions themselves never leave
# the box, so clearing the corpus can never place the eval beside it.
_PRELUDE_END = "# --- end shared prelude ---"
_REMOTE_PROBE = r'''
import glob, hashlib, json, sqlite3

def _norm(text):
    return " ".join((text or "").split()).strip().lower()

def _row_prompt(row):
    for key in ("question", "prompt", "instruction", "input", "query"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v
    msgs = row.get("messages")
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, str) and c.strip():
                    return c
    return ""

# --- end shared prelude ---
KEY = "__GOLDEN_KEY__"
FREEZE = "__FREEZE_KEY__"

def _read(keys):
    out = {}
    for db in sorted(glob.glob("/data/*.db")):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        except Exception:
            continue
        try:
            tables = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tables:
                cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]
                if "key" not in cols:
                    continue
                valcol = next((c for c in cols
                               if c in ("value", "val", "data", "json")), None)
                if not valcol:
                    continue
                for k in keys:
                    if k in out:
                        continue
                    try:
                        row = con.execute("SELECT %s FROM %s WHERE key=?"
                                          % (valcol, t), (k,)).fetchone()
                    except Exception:
                        continue
                    if row and row[0]:
                        out[k] = (row[0], "%s:%s.%s" % (db, t, valcol))
        finally:
            con.close()
        if len(out) == len(keys):
            break
    return out

def _decode(blob):
    raw = blob.decode() if isinstance(blob, (bytes, bytearray)) else blob
    val = json.loads(raw)
    if isinstance(val, dict) and "value" in val and not isinstance(val.get("value"), str):
        val = val["value"]
    return val

got = _read([KEY, FREEZE])

print("__BEGIN_GOLDEN__")
if KEY not in got:
    print(json.dumps({"status": "KEY_NOT_FOUND"}))
else:
    items = _decode(got[KEY][0])
    hashes = sorted({
        hashlib.sha256(_norm(_row_prompt(x)).encode()).hexdigest()[:20]
        for x in items if _row_prompt(x)
    })
    # the gate-#6 pin, when present. Absent => reported as None, never invented.
    pin = None
    if FREEZE in got:
        try:
            rec = _decode(got[FREEZE][0])
            if isinstance(rec, dict):
                pin = {"frozen": rec.get("frozen"),
                       "pinned_hash": rec.get("hash") or rec.get("pinned_hash"),
                       "pinned_count": rec.get("count") or rec.get("pinned_count"),
                       "frozen_at": rec.get("frozen_at")}
        except Exception:
            pin = None
    print(json.dumps({"status": "OK", "source": got[KEY][1],
                      "entries": len(items), "hashes": hashes, "freeze": pin}))
print("__END_GOLDEN__")
'''.replace("__GOLDEN_KEY__", GOLDEN_SET_KEY).replace("__FREEZE_KEY__", FREEZE_KEY)


def _assert_probe_agrees() -> None:
    """Fail loudly if the remote probe's hashing has drifted from ours.

    A drift here does not raise an error at compare time — it produces an EMPTY
    intersection, which prints as CONTAMINATION=NO. That is the one failure mode this
    whole tool exists to prevent, so it is checked rather than trusted.
    """
    ns: dict = {}
    if _PRELUDE_END not in _REMOTE_PROBE:
        # Without the marker the split would silently hand the WHOLE probe to exec —
        # running the state-DB reader on this box and still "passing". Refuse instead.
        raise AssertionError(f"remote probe lost its {_PRELUDE_END!r} marker")
    body = _REMOTE_PROBE.split(_PRELUDE_END)[0]
    exec(compile(body, "<remote-probe-prelude>", "exec"), ns)   # noqa: S102 — our own literal
    sample = "  The Quick\tBrown FOX  "
    mine = _prompt_hash(sample)
    theirs = hashlib.sha256(ns["_norm"](sample).encode()).hexdigest()[:20]
    if mine != theirs:
        raise AssertionError(
            f"remote probe hashing drifted from local ({theirs} != {mine}); "
            "an empty intersection would read as CONTAMINATION=NO")
    for row, want in (({"question": "q"}, "q"), ({"prompt": "p"}, "p"),
                      ({"messages": [{"role": "user", "content": "m"}]}, "m"),
                      ({"answer": "a"}, "")):
        if ns["_row_prompt"](row) != _row_prompt(row) or ns["_row_prompt"](row) != want:
            raise AssertionError(f"remote probe prompt extraction drifted on {row!r}")


def _golden_hashes_local() -> tuple[set[str] | None, str]:
    """The frozen set from THIS box's store, hashed. (None, reason) when unreadable."""
    try:
        sys.path.insert(0, str(ROOT))
        import asyncio
        from aria_service.intel import eval_runner
        golden = asyncio.run(eval_runner.get_golden_set())
    except Exception as exc:                      # noqa: BLE001 — reported, not swallowed
        return None, f"local store UNREACHABLE ({type(exc).__name__}: {exc})"
    if not golden:
        # get_golden_set() swallows store errors and returns [] (the non-strict-read
        # class in CLAUDE.md §1), so EMPTY here is indistinguishable from a failed read.
        return None, "local store returned EMPTY — not treated as 'no overlap'"
    return {_prompt_hash(_row_prompt(g)) for g in golden if _row_prompt(g)}, "local store"


def _golden_hashes_remote(app: str, timeout: int) -> tuple[set[str] | None, str]:
    """The frozen set from the live app over `flyctl ssh`. (None, reason) on any failure."""
    payload = base64.b64encode(_REMOTE_PROBE.encode()).decode()
    cmd = ["flyctl", "ssh", "console", "-a", app, "-C",
           f"python -c \"exec(__import__('base64').b64decode('{payload}'))\""]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, "flyctl not on PATH"
    except subprocess.TimeoutExpired:
        return None, f"flyctl ssh timed out after {timeout}s"
    out = proc.stdout or ""
    if "__BEGIN_GOLDEN__" not in out:
        # flyctl exits non-zero for unrelated reasons (metrics-token warnings), so the
        # marker — not the exit code — decides whether the probe actually ran.
        tail = (proc.stderr or out).strip().splitlines()[-1:] or ["no output"]
        return None, f"remote probe did not run on {app}: {tail[0][:160]}"
    body = out.split("__BEGIN_GOLDEN__", 1)[1].split("__END_GOLDEN__", 1)[0].strip()
    try:
        data = json.loads(body)
    except Exception as exc:                      # noqa: BLE001
        return None, f"remote probe output unparseable ({exc})"
    if data.get("status") != "OK":
        return None, f"remote: {data.get('status')} (key {GOLDEN_SET_KEY} not on {app})"
    hashes = set(data.get("hashes") or [])
    if not hashes:
        return None, f"remote golden set EMPTY on {app} — not treated as 'no overlap'"
    # Name WHICH set cleared the corpus. "500 entries" alone is not attribution: the pin
    # (gate #6's hash) is what distinguishes the frozen eval from any other 500 rows.
    pin = data.get("freeze") or {}
    if pin.get("frozen") and pin.get("pinned_hash"):
        pin_str = f"frozen pin {pin['pinned_hash']} count={pin.get('pinned_count')}"
    else:
        # Not a refusal: an unfrozen set still answers "did we train on it". But the
        # manifest must not imply a pin that is not there.
        pin_str = "NOT FROZEN (gate #6 pin absent — set may drift after this check)"
        print(f"  warning: golden set on {app} is {pin_str}")
    return hashes, (f"{app} live store ({data.get('entries')} entries @ "
                    f"{data.get('source')}; {pin_str})")


def _golden_hashes(app: str, *, allow_remote: bool, timeout: int) -> tuple[set[str] | None, str]:
    """Prompt hashes for the frozen set, or (None, reason) when it cannot be read.

    None is NOT an empty set. Returning empty on failure would report "no overlap"
    for a check that never ran.
    """
    hashes, why = _golden_hashes_local()
    if hashes is not None:
        return hashes, why
    print(f"  {why}")
    if not allow_remote:
        why = "remote read disabled (--no-remote)"
        print(f"  {why}")
        return None, why
    print(f"  falling back to the live golden set on {app} (read-only, hashes only)")
    hashes, why = _golden_hashes_remote(app, timeout)
    if hashes is None:
        print(f"  {why}")
    return hashes, why


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true", help="write docs/training_corpus_manifest.json")
    ap.add_argument("--app", default=DEFAULT_APP,
                    help=f"fly app holding the golden set (default {DEFAULT_APP})")
    ap.add_argument("--no-remote", action="store_true",
                    help="do not fall back to reading the golden set over flyctl ssh")
    ap.add_argument("--remote-timeout", type=int, default=300,
                    help="seconds to allow the remote read (default 300)")
    args = ap.parse_args()

    try:
        _assert_probe_agrees()
    except AssertionError as exc:
        # Exit 2 (refused), not 1: 1 means "contamination found", and a broken
        # instrument has not found anything — it has failed to look.
        print(f"refusing to run: {exc}")
        return 2

    if not CORPUS_DIR.is_dir():
        print(f"no corpus at {CORPUS_DIR}")
        return 2

    files = scan_corpus()
    total_rows = sum(f["rows"] for f in files)
    total_bad = sum(f["unparseable_rows"] for f in files)
    print(f"{len(files)} corpus files · {total_rows:,} rows · "
          f"{sum(f['bytes'] for f in files) / 1e6:.1f} MB"
          + (f" · {total_bad} UNPARSEABLE rows" if total_bad else ""))

    golden, golden_source = _golden_hashes(
        args.app, allow_remote=not args.no_remote, timeout=args.remote_timeout)

    contaminated: list[dict] = []
    if golden is None:
        print("CONTAMINATION=UNKNOWN — the golden set could not be read.")
    else:
        print(f"  golden set: {len(golden)} distinct prompts (via {golden_source})")
        for f in files:
            hits = f["_hashes"] & golden
            if hits:
                contaminated.append({"file": f["file"], "overlapping_rows": len(hits),
                                     "example_prompt_hashes": sorted(hits)[:3]})
        if contaminated:
            print(f"\nCONTAMINATION=YES — {len(contaminated)} file(s) share prompts with the "
                  f"frozen 500-Q eval. Training on the eval measures memorisation and "
                  f"makes Phase A gate #6 meaningless:")
            for c in contaminated:
                print(f"  ! {c['file']}: {c['overlapping_rows']} overlapping row(s)")
        else:
            print("CONTAMINATION=NO — no corpus prompt appears in the frozen eval set.")

    for f in files:
        f.pop("_hashes", None)

    if args.record:
        if golden is None:
            print("\nrefusing --record: contamination is UNKNOWN. A manifest that omits the "
                  "check reads as a clean bill of health for a check that never ran.")
            return 2
        if contaminated:
            print("\nrefusing --record: pinning a contaminated corpus would make the "
                  "contamination look reviewed and accepted.")
            return 2
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                cwd=ROOT, capture_output=True, text=True).stdout.strip()
        MANIFEST.write_text(json.dumps({
            "recorded_at_commit": commit,
            "note": ("The corpus itself is untracked by design (size). This manifest is the "
                     "PIN: a training run records these sha256s so a checkpoint can be "
                     "attributed to exact inputs."),
            "golden_set_prompts": len(golden),
            # R-F3639 — WHICH golden set cleared this corpus. Without it a later reader
            # cannot tell a check against the frozen 500-Q set from one against a stale
            # local copy, and "contamination: none" would be unattributable.
            "golden_set_source": golden_source,
            "contamination": "none",
            "totals": {"files": len(files), "rows": total_rows,
                       "unparseable_rows": total_bad},
            "files": files,
        }, indent=1) + "\n", encoding="utf-8", newline="\n")
        # R-F3639: relative_to() RAISES for a path outside ROOT, and it ran AFTER the
        # write — so a cosmetic display line could abort a run whose manifest was already
        # on disk, reporting failure for work that succeeded.
        try:
            shown = MANIFEST.relative_to(ROOT)
        except ValueError:
            shown = MANIFEST
        print(f"recorded -> {shown}")
        return 0

    return 1 if contaminated else 0


if __name__ == "__main__":
    raise SystemExit(main())
