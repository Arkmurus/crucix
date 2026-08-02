"""R-F3639 — the §24 training pre-flight must be runnable where the corpus lives.

R-F3637 built the pre-flight but wired it to a golden set that only exists on aria-intel,
while the corpus only exists on the dev box. Neither end could run it, so §24's condition
("a cycle that would train on unreviewed/contaminated data is cancelled, not run") stayed
un-mechanised. These tests drive the tool's real entry point (`main()`), not helpers.

The properties that matter here are the REFUSALS. A tool that reports CONTAMINATION=NO
because it could not look is worse than no tool, so each way of failing to look is tested
to make sure it still refuses.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "admin" / "training_corpus_manifest.py"


def _load():
    spec = importlib.util.spec_from_file_location("_rf3639_manifest", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def tool(tmp_path, monkeypatch):
    mod = _load()
    corpus = tmp_path / "training"
    corpus.mkdir()
    monkeypatch.setattr(mod, "CORPUS_DIR", corpus)
    monkeypatch.setattr(mod, "MANIFEST", tmp_path / "manifest.json")
    # never touch the real local store in a unit test
    monkeypatch.setattr(mod, "_golden_hashes_local",
                        lambda: (None, "local store UNREACHABLE (test)"))
    return mod


def _write(corpus: pathlib.Path, name: str, rows: list[dict]) -> None:
    corpus.joinpath(name).write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _run(mod, *argv: str) -> int:
    monkey_argv = ["training_corpus_manifest.py", *argv]
    old, sys.argv = sys.argv, monkey_argv
    try:
        return mod.main()
    finally:
        sys.argv = old


# ── the fix: the remote path makes the gate runnable at all ──────────────────

def test_remote_golden_set_clears_a_clean_corpus_and_records(tool, monkeypatch, capsys):
    """The whole point of R-F3639: corpus here + golden set there = one runnable check."""
    _write(tool.CORPUS_DIR, "clean.jsonl",
           [{"question": "who owns Acme Ltd", "answer": "x"},
            {"messages": [{"role": "user", "content": "sanction status of Foo"}]}])
    monkeypatch.setattr(tool, "_golden_hashes_remote",
                        lambda app, timeout: ({"deadbeef" * 2}, f"{app} live store (500 entries)"))

    assert _run(tool, "--record") == 0
    out = capsys.readouterr().out
    assert "CONTAMINATION=NO" in out
    assert "aria-intel live store" in out

    manifest = json.loads(tool.MANIFEST.read_text())
    assert manifest["contamination"] == "none"
    # which golden set cleared it must be attributable, not implied
    assert "aria-intel live store" in manifest["golden_set_source"]
    assert manifest["totals"]["rows"] == 2


def test_remote_contamination_is_caught_and_record_refused(tool, monkeypatch, capsys):
    """A training row that is also an eval row makes gate #6 measure memorisation."""
    leaked = "What is the beneficial ownership of Acme Ltd?"
    _write(tool.CORPUS_DIR, "dirty.jsonl", [{"question": leaked, "answer": "x"}])
    monkeypatch.setattr(tool, "_golden_hashes_remote",
                        lambda app, timeout: ({tool._prompt_hash(leaked)}, "live"))

    assert _run(tool, "--record") == 2          # refused
    out = capsys.readouterr().out
    assert "CONTAMINATION=YES" in out
    assert "dirty.jsonl" in out
    assert not tool.MANIFEST.exists(), "a contaminated corpus must never be pinned"


def test_reformatted_eval_row_is_still_contamination(tool, monkeypatch, capsys):
    """Under-reporting is the failure mode that matters — normalise both sides."""
    _write(tool.CORPUS_DIR, "sneaky.jsonl",
           [{"prompt": "  WHO   owns\tAcme LTD?  "}])
    monkeypatch.setattr(tool, "_golden_hashes_remote",
                        lambda app, timeout: ({tool._prompt_hash("who owns Acme Ltd?")}, "live"))

    assert _run(tool) == 1
    assert "CONTAMINATION=YES" in capsys.readouterr().out


# ── the refusals: every way of failing to look must still refuse ─────────────

@pytest.mark.parametrize("reason", [
    ("flyctl not on PATH"),
    ("flyctl ssh timed out after 300s"),
    ("remote golden set EMPTY on aria-intel — not treated as 'no overlap'"),
])
def test_unreadable_golden_set_is_unknown_and_refuses_to_record(tool, monkeypatch, capsys, reason):
    _write(tool.CORPUS_DIR, "clean.jsonl", [{"question": "q", "answer": "a"}])
    monkeypatch.setattr(tool, "_golden_hashes_remote", lambda app, timeout: (None, reason))

    assert _run(tool, "--record") == 2
    out = capsys.readouterr().out
    assert "CONTAMINATION=UNKNOWN" in out
    assert "CONTAMINATION=NO" not in out
    assert not tool.MANIFEST.exists()


def test_no_remote_flag_does_not_silently_pass(tool, capsys):
    """Disabling the remote read removes the ability to look, not the requirement to."""
    _write(tool.CORPUS_DIR, "clean.jsonl", [{"question": "q", "answer": "a"}])
    assert _run(tool, "--record", "--no-remote") == 2
    assert "CONTAMINATION=UNKNOWN" in capsys.readouterr().out


def test_remote_probe_marker_absent_is_a_failure_not_a_clean_read(tool, monkeypatch):
    """flyctl warns on stderr and can exit non-zero while the probe DID run, and can
    also produce output while it did NOT. The marker decides, not the exit code."""
    class _Proc:
        stdout = "Connecting to fdaa:...\nWarning: Metrics token unavailable\n"
        stderr = "Error: The handle is invalid."
        returncode = 1

    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: _Proc())
    hashes, why = tool._golden_hashes_remote("aria-intel", 5)
    assert hashes is None
    assert "did not run" in why


def _probe_proc(payload: dict):
    class _Proc:
        stdout = f"noise\n__BEGIN_GOLDEN__\n{json.dumps(payload)}\n__END_GOLDEN__\n"
        stderr = ""
        returncode = 0
    return _Proc()


def test_frozen_pin_is_recorded_so_the_golden_set_is_attributable(tool, monkeypatch):
    """'500 entries' does not identify a set — gate #6's pin does."""
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: _probe_proc(
        {"status": "OK", "source": "/data/aria_state.db:state.value", "entries": 500,
         "hashes": ["a" * 20], "freeze": {"frozen": True, "pinned_hash": "a07b6af760ad7f44",
                                          "pinned_count": 500}}))
    hashes, why = tool._golden_hashes_remote("aria-intel", 5)
    assert hashes == {"a" * 20}
    assert "a07b6af760ad7f44" in why, "the manifest must name WHICH golden set cleared the corpus"


def test_unfrozen_golden_set_is_flagged_not_silently_claimed_as_pinned(tool, monkeypatch, capsys):
    """An unfrozen set can still answer 'did we train on it' — but must not imply a pin."""
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: _probe_proc(
        {"status": "OK", "source": "s", "entries": 500, "hashes": ["b" * 20], "freeze": None}))
    hashes, why = tool._golden_hashes_remote("aria-intel", 5)
    assert hashes == {"b" * 20}
    assert "NOT FROZEN" in why
    assert "NOT FROZEN" in capsys.readouterr().out


def test_remote_key_not_found_is_unknown_not_empty(tool, monkeypatch):
    class _Proc:
        stdout = ('__BEGIN_GOLDEN__\n{"status": "KEY_NOT_FOUND"}\n__END_GOLDEN__\n')
        stderr = ""
        returncode = 0

    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: _Proc())
    hashes, why = tool._golden_hashes_remote("aria-intel", 5)
    assert hashes is None, "an absent key must not read as an empty (clean) golden set"
    assert "KEY_NOT_FOUND" in why


def test_local_empty_store_is_not_treated_as_no_overlap():
    """get_golden_set() swallows store errors and returns [] — empty must mean UNKNOWN."""
    mod = _load()
    import aria_service.intel.eval_runner as er

    async def _empty():
        return []

    orig, er.get_golden_set = er.get_golden_set, _empty
    try:
        hashes, why = mod._golden_hashes_local()
    finally:
        er.get_golden_set = orig
    assert hashes is None
    assert "EMPTY" in why


# ── the drift guard: a mismatch here would print CONTAMINATION=NO ────────────

def test_remote_probe_hashing_matches_local():
    """If the two sides hash differently the intersection is empty — which prints as
    'no contamination'. That silent false clean is exactly what this tool prevents."""
    mod = _load()
    mod._assert_probe_agrees()          # raises on drift

    ns: dict = {}
    exec(compile(mod._REMOTE_PROBE.split("KEY = ")[0], "<probe>", "exec"), ns)
    for sample in ("Who owns Acme?", "  MIXED   Case\tText ", "unicode — dash"):
        import hashlib
        assert mod._prompt_hash(sample) == \
            hashlib.sha256(ns["_norm"](sample).encode()).hexdigest()[:20]


def test_drift_guard_refuses_rather_than_running_the_probe_locally(tool, monkeypatch, capsys):
    """Losing the prelude marker must refuse, not exec the whole probe on this box."""
    monkeypatch.setattr(tool, "_REMOTE_PROBE", "def _norm(t): return t\n")   # marker gone
    _write(tool.CORPUS_DIR, "clean.jsonl", [{"question": "q"}])
    assert _run(tool, "--record") == 2, "a broken instrument must refuse, not report clean"
    out = capsys.readouterr().out
    assert "refusing to run" in out
    assert "CONTAMINATION=NO" not in out


def test_remote_probe_is_read_only():
    """It runs against production's live state volume — it must not be able to write."""
    mod = _load()
    probe = mod._REMOTE_PROBE
    assert "mode=ro" in probe, "the state DB must be opened read-only"
    for forbidden in ("INSERT", "UPDATE ", "DELETE", "DROP", "os.remove", "open("):
        assert forbidden not in probe, f"remote probe must not contain {forbidden!r}"


def test_unparseable_rows_are_counted_not_skipped(tool, monkeypatch, capsys):
    tool.CORPUS_DIR.joinpath("mixed.jsonl").write_text(
        '{"question": "ok"}\nnot json at all\n', encoding="utf-8")
    monkeypatch.setattr(tool, "_golden_hashes_remote", lambda app, timeout: (set(), "live"))
    # an EMPTY-but-readable golden set is still unreadable per the tool's own rule,
    # so drive the parse counting through a non-empty one
    monkeypatch.setattr(tool, "_golden_hashes_remote", lambda app, timeout: ({"z" * 20}, "live"))
    _run(tool)
    assert "UNPARSEABLE" in capsys.readouterr().out
