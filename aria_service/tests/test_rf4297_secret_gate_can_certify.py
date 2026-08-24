"""R-F4297 / C-251 — the secret gate stopped being able to certify.

MEASURED 2026-08-24. `test_rf3720_secret_scan_gate::test_the_repo_itself_is_clean`
and `test_rf4117_secret_baseline_is_not_a_hole::test_the_repo_passes_its_own_secret_gate`
both appeared as NEW failures in the freshly recorded §16 baseline. Neither had
found a credential:

    subprocess.TimeoutExpired: Command '[... secret_scan.py]' timed out after 90 seconds

Run without a limit it takes **5m45s**. It is not finding anything in that time —
it is READING. `git ls-files --cached --others --exclude-standard` puts **15.6 GB
across 5,902 files** in scope on this checkout: 12.5 GB under `data/` (training
corpora, a 311 MB checkpoint tarball) and 3.05 GB under `.scratch/` (nine LoRA
`adapter_model.safetensors` at 335.6 MB each).

THE DEFECT IS ONE LINE OF ORDERING, and the guard it needs already existed:

    text = p.read_text(encoding="utf-8", errors="replace")   # the WHOLE file
    if "\x00" in text[:1024]:
        continue                                             # ...then skip it

Every 335 MB binary was fully read and utf-8-decoded into a Python string, and
discarded on the very next line. The binary check was correct and was simply
placed after the cost it exists to avoid. (It is also why the findings printed
mojibake — bytes rendered through `errors="replace"`.)

WHY IT MATTERS. A security gate that times out is not a slow gate, it is an
ABSENT one. And it is SELF-WORSENING in the C-95 shape: every training cycle adds
GB, so the gate gets slower until it stops certifying — the more work the repo
does, the less it is protected. §7 forbids eviction, so the data only grows.

THE FIX CHANGES COST, NOT COVERAGE — that is the property these tests pin. The
sniff threshold stays at 1024 bytes, matching the old check exactly, so no file
that used to be scanned stops being scanned. What changes is that the decision is
made from a bounded read instead of from the whole file.

AND IT MUST STILL BE ABLE TO FAIL (R-F3858). A cheaper scanner that stopped
finding things would be worse than the timeout, because a timeout is at least
loud. The planted-credential cases below are pinned as hard as the cost ones.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "_secret_scan_rf4297", ROOT / "scripts/admin/secret_scan.py")
scan = importlib.util.module_from_spec(_SPEC)
sys.modules["_secret_scan_rf4297"] = scan
_SPEC.loader.exec_module(scan)


# A synthetic credential, COMPOSED AT RUNTIME rather than written as a literal.
# Planting the literal makes this file itself a scanner finding, and the only
# way to green that is a permanent entry in the accepted-secrets baseline — a
# forever-exception bought in order to test a scanner. Every baseline entry has
# to be reviewed as though it were real, so the cheapest one is the one never
# added. R-F4297 (C-251).
_FAKE = "sk-" + "live-" + "9f3Kd82mQx7ZpLw4" + "TnVb6RcYh1JgEa5U"


# ── the bounded decision ───────────────────────────────────────────────────

def test_a_binary_file_is_recognised_without_reading_it_all(tmp_path) -> None:
    p = tmp_path / "adapter_model.safetensors"
    p.write_bytes(b"\x00\x01\x02" + b"\xff" * 4096)
    assert scan.is_binary(p) is True


def test_a_text_file_is_not_binary(tmp_path) -> None:
    p = tmp_path / "a.py"
    p.write_text("x = 1\n" * 500, encoding="utf-8")
    assert scan.is_binary(p) is False


def test_the_sniff_is_bounded_and_matches_the_old_threshold() -> None:
    """1024 bytes, exactly as `text[:1024]` checked — so coverage is unchanged."""
    assert scan.BINARY_SNIFF_BYTES == 1024


def test_a_nul_past_the_sniff_window_is_still_TEXT(tmp_path) -> None:
    """The old check looked at the first 1024 only. Widening the window here would
    silently stop scanning files that used to be scanned — a coverage change
    smuggled in under a performance fix."""
    p = tmp_path / "corpus.jsonl"
    p.write_bytes(b"a" * 4096 + b"\x00" + b"b" * 100)
    assert scan.is_binary(p) is False


# ── the cost, measured ─────────────────────────────────────────────────────

def test_a_large_binary_costs_almost_nothing(tmp_path) -> None:
    """THE CAPABILITY TEST. This is what took 5m45s across the real repo."""
    p = tmp_path / "big.safetensors"
    p.write_bytes(b"\x00" * (24 * 1024 * 1024))       # 24 MB
    t0 = time.perf_counter()
    assert scan.scan_one(p) == []
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"24 MB binary took {elapsed:.1f}s — it is still being read whole"


def test_known_binary_suffixes_are_skipped_before_opening() -> None:
    for suffix in (".safetensors", ".tgz", ".bin", ".pt", ".ckpt", ".tar"):
        assert suffix in scan.SKIP_SUFFIX, f"{suffix} still reaches the reader"


# ── it must still FIND things — cheaper is worthless if it is blind ────────

def test_a_planted_credential_in_a_text_file_is_STILL_FOUND(tmp_path) -> None:
    p = tmp_path / "config.py"
    p.write_text(f'ARIA_API_TOKEN = "{_FAKE}"\n',
                 encoding="utf-8")
    hits = scan.scan_one(p)
    assert hits, "the scanner no longer detects a credential in plain source"


def test_a_credential_deep_in_a_LARGE_text_file_is_found(tmp_path) -> None:
    """Streaming must not truncate. A corpus is exactly where a harvested key
    would sit, and it would sit a long way down."""
    p = tmp_path / "corpus.jsonl"
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        for _ in range(60_000):
            fh.write('{"text": "ordinary harmless training row"}\n')
        fh.write(f'ARIA_API_TOKEN = "{_FAKE}"\n')
    hits = scan.scan_one(p)
    assert hits, "a credential at the end of a large text file was missed"


def test_an_unreadable_file_is_not_reported_clean(tmp_path) -> None:
    """A file the scanner cannot open must not be silently counted as scanned."""
    missing = tmp_path / "gone.py"
    assert scan.scan_one(missing) == []          # no crash
    assert scan.is_binary(missing) is True, (
        "an unreadable file must fail CLOSED — treated as not-scannable, "
        "never as scanned-and-clean")


# ── silence is not certification ───────────────────────────────────────────

def test_the_scan_reports_what_it_SKIPPED() -> None:
    """A guard whose universe shrank without saying so certifies over a smaller
    world — the §1 'certified by an absence' shape. `main` must print the skip
    count so a CLEAN verdict is auditable."""
    src = (ROOT / "scripts/admin/secret_scan.py").read_text(encoding="utf-8")
    assert "skipped" in src.lower(), "the scan never reports what it did not read"


def test_the_whole_file_read_is_gone_from_the_scan_loop() -> None:
    """AST, not grep.

    The first version of this test searched the source TEXT for the offending
    call and went red on the fixed tree — because `is_binary`'s docstring QUOTES
    the defect it fixes. A guard that cannot tell executable code from prose
    describing it is the line-heuristic fault R-F3858 already had to remove once.
    Walk the tree instead, and ignore docstrings by construction.
    """
    import ast
    src = (ROOT / "scripts/admin/secret_scan.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    reads = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "read_text"
             and not (isinstance(n.func.value, ast.Name)
                      and n.func.value.id == "BASELINE")]
    assert not reads, (
        "main() reads a whole file again — the scan loop must go through "
        "scan_one(), which streams. That ordering IS the defect.")
