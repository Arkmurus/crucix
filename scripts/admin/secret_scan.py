#!/usr/bin/env python3
"""R-F3720 — refuse to let a credential VALUE reach a public repo.

WHY THIS EXISTS. `Arkmurus/crucix` is a PUBLIC repo and, before this, had no
secret scanning of any kind: no gitleaks, no trufflehog, no pre-commit hook,
nothing in any of the seven workflows. Nothing stood between a pasted token and
the world.

WHY NOT JUST INSTALL GITLEAKS. The credentials this project actually handles are
bare hex/base64 strings with no vendor prefix (ARIA_API_TOKEN, ARIA_INTERNAL_TOKEN,
REPORT_SIGNING_KEY). They match no vendor signature, so an off-the-shelf scanner
would pass this repo while missing precisely the secrets it holds. Vendor formats
are still checked — they are cheap and catch a pasted third-party key — but the
load-bearing rule is the assignment heuristic below.

WHY IT MUST NOT FIRE ON DIGESTS (the calibration that matters). On 2026-08-05 a
session read the 16-hex strings in CLAUDE.md as live tokens and came one command
away from rotating both service tokens across three production apps. They were
`flyctl secrets list` DIGESTS; the real tokens are 43 chars. A scanner that
cannot tell a digest from a value manufactures exactly that emergency, and a
scanner people learn to ignore protects nothing. So:

  - a HASH/DIGEST (<=32 hex, or sitting next to the word digest/sha/hash/fingerprint)
    is NOT a finding;
  - a placeholder/example/redaction is NOT a finding;
  - a high-entropy value ASSIGNED to a secret-named variable IS.

Exit 0 = clean, 1 = findings (CI gate), 2 = could not run. Absence of evidence is
reported as failure to run, never as a pass.

Usage:
  python scripts/admin/secret_scan.py            # scan tracked files
  python scripts/admin/secret_scan.py --staged   # scan staged files only (hook)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

BASELINE = Path("docs/secret_scan_baseline.json")

# ── why a BASELINE and not a path exemption ──────────────────────────────────
# The first calibrated run returned 24 findings, every one a SYNTHETIC secret
# inside a test whose subject is redaction (test_rf2796_terminal_secret_redaction,
# test_rf1563_chat_pii_redaction, test_rf1832_sast_scan_ast_aware). Those fixtures
# must keep existing — they are how the redaction code is proven.
#
# The tempting fix is "skip tests/". That is wrong: a real key pasted into a test
# file is a real leak, and tests are exactly where a careless paste lands. So the
# accepted set is keyed by a hash OF THE VALUE. The known fixtures pass; swap one
# for a live credential in the SAME file on the SAME line and the hash changes,
# so it is a new finding and CI fails. The baseline is a reviewable artefact, not
# a hole.


def _fingerprint(path: Path, rule: str, value: str) -> str:
    h = hashlib.sha256(f"{path.as_posix()}|{rule}|{value}".encode()).hexdigest()
    return h[:20]

# ── vendor formats: unambiguous, always a finding ────────────────────────────
VENDOR = [
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "OpenAI-style secret key"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "Anthropic API key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "GitHub token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    (re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"), "Slack token"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "Google API key"),
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\."), "JWT with signature"),
]

# ── assignment heuristic: NAME = <high-entropy value> ────────────────────────
# The value MUST be a quoted LITERAL. An unquoted right-hand side is an
# expression — `const INT_TOKEN = process.env.ARIA_INTERNAL_TOKEN`,
# `tok = os.getenv("X")`, `KEY = settings.key` — which READS a secret rather
# than containing one. Matching those produced 19 false positives on the first
# run, and a scanner that cries wolf on every correct env lookup is one people
# switch off. Reading a credential from the environment is the RIGHT pattern;
# this gate exists to catch the literal that should have been an env var.
SECRETISH = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|"
    r"PRIVATE_KEY|SIGNING_KEY|CREDENTIAL|ACCESS_KEY)[A-Z0-9_]*)\b"
    r"\s*[:=]\s*(['\"])([A-Za-z0-9_\-+/=]{20,})\2"
)

# things that are safe by construction
PLACEHOLDER = re.compile(
    r"(?i)\b(example|placeholder|your[_\-]?|dummy|fake|sample|redacted|changeme|"
    r"xxx+|<[^>]+>|\.\.\.|test[_\-]?token|null|none|todo|replace[_\-]?me|"
    r"insert[_\-]?|\$\{|\$\(|%s|\{\{)"
)
DIGEST_CONTEXT = re.compile(r"(?i)\b(digest|sha\d*|hash|fingerprint|checksum|etag|"
                            r"commit|revision|build_rev|md5|blake)\b")
HEXISH = re.compile(r"^[0-9a-f]+$")

SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf", ".zip",
               ".gz", ".woff", ".woff2", ".ttf", ".lock", ".min.js", ".map",
               # R-F4297 (C-251) — model, archive and database formats. A
               # credential is not committed as a tensor, and reading 335 MB of
               # weights to discover that took this gate past its timeout.
               ".safetensors", ".tgz", ".tar", ".bin", ".pt", ".pth", ".ckpt",
               ".npy", ".npz", ".db", ".sqlite", ".sqlite3", ".7z", ".whl",
               ".so", ".dll", ".pyd", ".exe", ".mp4", ".mp3", ".wav", ".parquet"}
SKIP_PARTS = {"node_modules", ".git", "__pycache__", ".venv", "dist", "build"}


def entropy(s: str) -> float:
    if not s:
        return 0.0
    return -sum((c := s.count(ch) / len(s)) and c * math.log2(c)
                for ch in set(s))


def tracked_files(staged: bool) -> list[Path]:
    # R-F3729 — include UNTRACKED-but-not-ignored files, not just `git ls-files`.
    #
    # Tracked-only had an ordering hole that bit within an hour of shipping:
    # the gate's own test fixtures were baselined while the test file was still
    # untracked, so the scan could not see them and reported CLEAN. The commit
    # made them visible and CI went red on arrival — the precise failure the
    # baseline exists to prevent.
    #
    # A scanner that only sees what is already committed always answers one
    # commit late. The point is to catch a credential BEFORE it lands, so it
    # must look at what is about to be added. --exclude-standard honours
    # .gitignore, so scratch files and build output stay out.
    cmd = (["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
           if staged else
           ["git", "ls-files", "--cached", "--others", "--exclude-standard"])
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    files = []
    for line in out.stdout.splitlines():
        p = Path(line.strip())
        if not line.strip() or not p.exists() or not p.is_file():
            continue
        if set(p.parts) & SKIP_PARTS or p.suffix.lower() in SKIP_SUFFIX:
            continue
        if p.name.endswith(".example") or ".example." in p.name:
            continue
        files.append(p)
    return files


#: R-F4297 (C-251) — how much of a file is read to decide whether it is text.
#: DELIBERATELY 1024, matching the `text[:1024]` check this replaces, so no file
#: that used to be scanned stops being scanned. This fix changes COST, not
#: COVERAGE; widening the window would smuggle a coverage change in under a
#: performance fix.
BINARY_SNIFF_BYTES = 1024


def is_binary(path: Path) -> bool:
    """Is `path` something we should not try to read as source?

    THE DEFECT THIS FIXES was one line of ordering. The scan loop read the WHOLE
    file and then asked whether it was binary:

        text = p.read_text(encoding="utf-8", errors="replace")
        if "\x00" in text[:1024]:
            continue

    So every 335 MB `adapter_model.safetensors` was fully read and utf-8-decoded
    into a Python string, and thrown away on the next line. Across this checkout
    that is 15.6 GB and 5m45s, against a 90 s gate budget — measured 2026-08-24,
    the day both secret-gate tests appeared as new baseline failures having found
    nothing at all. A security gate that times out is not slow, it is ABSENT.

    UNREADABLE FAILS CLOSED. A file that cannot be opened returns True — "not
    scannable" — rather than False. False would send it to the reader, which
    swallows the error and moves on, and the file would then be counted among the
    scanned. Silently promoting "could not look" to "looked and it was fine" is
    the absence-reads-as-health shape CLAUDE.md §1 records three times.
    """
    if path.suffix.lower() in SKIP_SUFFIX:
        return True
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(BINARY_SNIFF_BYTES)
    except OSError:
        return True


def scan_one(path: Path) -> list[tuple[int, str, str]]:
    """Stream one file, returning [(lineno, rule, RAW value)].

    Streamed rather than materialised: a 136 MB training corpus is legitimately
    text and must still be scanned to the last line — a harvested key would sit a
    long way down — but holding it in one string is how this box ran out of
    memory. Line by line, the cost is bounded by the longest line.
    """
    if is_binary(path):
        return []
    out: list[tuple[int, str, str]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if len(line) > 4000:
                    continue
                for rule, val in scan_line(line):
                    out.append((i, rule, val))
    except OSError:
        return []
    return out


def scan_line(line: str) -> list[tuple[str, str]]:
    """Return [(rule, RAW value)] for one line. The caller masks before printing;
    the raw value is needed to fingerprint against the baseline."""
    hits = []
    for rx, label in VENDOR:
        m = rx.search(line)
        if m and not PLACEHOLDER.search(line):
            hits.append((label, m.group(0)))
    for m in SECRETISH.finditer(line):
        name, val = m.group(1), m.group(3)   # group(2) is the quote character
        if PLACEHOLDER.search(line):
            continue
        # a digest/hash is not a credential — the R-F3721 calibration
        if DIGEST_CONTEXT.search(line):
            continue
        if HEXISH.match(val) and len(val) <= 32:
            continue          # 16/32-hex bare hex reads as a digest, not a value
        if entropy(val) < 3.2:
            continue          # words, paths, versions
        hits.append((f"{name} assigned a high-entropy value", val))
    return hits


def _mask(v: str) -> str:
    return f"{v[:4]}…{v[-2:]} (len={len(v)})" if len(v) > 10 else f"<len={len(v)}>"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--update-baseline", action="store_true",
                    help="record the CURRENT findings as accepted. Review the diff.")
    args = ap.parse_args()

    accepted: dict[str, str] = {}
    if BASELINE.exists():
        try:
            accepted = json.loads(BASELINE.read_text(encoding="utf-8")).get("accepted", {})
        except Exception as e:
            print(f"[secret-scan] COULD NOT READ BASELINE {BASELINE}: {e}", file=sys.stderr)
            return 2      # an unreadable baseline is not an empty one (R-F3717 class)
    try:
        files = tracked_files(args.staged)
    except Exception as e:                       # never report "clean" on a crash
        print(f"[secret-scan] COULD NOT RUN: {e}", file=sys.stderr)
        return 2

    findings = []
    skipped = 0
    for p in files:
        # R-F4297 (C-251) — decide from a BOUNDED read, before paying for the
        # file. This check already existed; it sat one line too late, after the
        # whole file had been read and decoded.
        if is_binary(p):
            skipped += 1
            continue
        for i, rule, val in scan_one(p):
            findings.append((p, i, rule, val, _fingerprint(p, rule, val)))

    # R-F4297 — say what was NOT read. A guard whose universe quietly shrank
    # certifies over a smaller world, and a CLEAN verdict reads identically
    # either way (CLAUDE.md §1, 'certified by an absence').
    print(f"[secret-scan] scanned {len(files) - skipped} text file(s); "
          f"skipped {skipped} binary/non-source of {len(files)} in scope")

    if args.update_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({
            "_comment": ("Accepted secret-scan findings (R-F3720). Keyed by "
                         "sha256(path|rule|VALUE) — swap a fixture for a real "
                         "credential and the fingerprint changes, so it fails. "
                         "Every entry is a synthetic test fixture; review any "
                         "addition as you would a credential."),
            "accepted": {f: f"{p.as_posix()}:{i} {rule}"
                         for p, i, rule, _v, f in findings},
        }, indent=2) + "\n", encoding="utf-8")
        print(f"[secret-scan] baseline WRITTEN with {len(findings)} accepted "
              f"finding(s) -> {BASELINE}")
        return 0

    new = [f for f in findings if f[4] not in accepted]
    stale = set(accepted) - {f[4] for f in findings}
    if stale:
        print(f"[secret-scan] note: {len(stale)} baseline entr(y/ies) no longer "
              f"present — re-run --update-baseline to prune")
    if not new:
        print(f"[secret-scan] CLEAN — {len(findings)} known fixture(s) accepted, "
              f"0 new credential values")
        return 0
    print(f"[secret-scan] {len(new)} NEW FINDING(S) — a value must never be committed:")
    for p, i, rule, val, fp in new:
        print(f"  {p}:{i}: {rule} -> {_mask(val)}   [{fp}]")
    print("\nIf this is a DIGEST or a hash, label it as one on the same line "
          "(the scanner honours digest/sha/hash/fingerprint) — see R-F3721.")
    print("If it is a SYNTHETIC test fixture, run --update-baseline and commit "
          "the diff so the acceptance is reviewable.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
