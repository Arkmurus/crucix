"""R-F783 — wedge_introspect reads + summarises R-F704 wedge stacks.

Unit-tests the parser + filename guard + summary aggregation. The
production wedge dir is /data/wedge_stacks; tests use a temp dir
via ARIA_WEDGE_TEST_DIR (not a real env var — we monkeypatch the
resolver directly so production paths are untouched).
"""
from __future__ import annotations

import os
import time

from aria_service.intel import wedge_introspect as wi


# Sample wedge content — slimmed from a real wedge_675 capture. Two
# threads: one watchdog, one main-loop on knowledge.store_fact.
_SAMPLE_WEDGE = """\
=== [R-F704] event-loop heartbeat stale by 5.51s at wall-clock 2026-05-21 08:45:45 UTC (epoch 1779353145.641) ===
Thread 0x00007fe4cbfff6c0 (most recent call first):
  File "/usr/local/lib/python3.13/threading.py", line 359 in wait
  File "/usr/local/lib/python3.13/queue.py", line 199 in get
  File "/usr/local/lib/python3.13/site-packages/anyio/_backends/_asyncio.py", line 991 in run

Thread 0x00007fe47e3016c0 (most recent call first):
  File "/app/aria_service/intel/coverage_heatmap.py", line 188 in _fact_text
  File "/app/aria_service/intel/coverage_heatmap.py", line 224 in _count_facts_for_cell_sync
  File "/app/aria_service/intel/coverage_heatmap.py", line 336 in _compute_matrix_sync

Current thread 0x00007fe4e4a696c0 (most recent call first):
  File "/app/aria_service/main.py", line 423 in _wedge_watchdog
  File "/usr/local/lib/python3.13/threading.py", line 995 in run

Thread 0x00007fe5044c9380 (most recent call first):
  File "/app/aria_service/intel/knowledge.py", line 848 in store_fact
  File "/app/aria_service/intel/brain_hook.py", line 723 in absorb
  File "/app/aria_service/routes/aria.py", line 10511 in _bg_absorb
  File "/usr/local/lib/python3.13/site-packages/starlette/background.py", line 21 in __call__
=== end stack dump ===
"""


def test_filename_guard_rejects_traversal():
    """Path-traversal guard must reject anything that isn't the
    watchdog's exact naming pattern."""
    assert wi._is_valid_wedge_name("wedge_675_1779352332.log") is True
    assert wi._is_valid_wedge_name("../../etc/passwd") is False
    assert wi._is_valid_wedge_name("/etc/passwd") is False
    assert wi._is_valid_wedge_name("wedge.log") is False
    assert wi._is_valid_wedge_name("wedge_a_b.log") is False
    assert wi._is_valid_wedge_name("") is False
    assert wi._is_valid_wedge_name("wedge_1_1.txt") is False


def test_parse_top_culprit_picks_deepest_block():
    """The parser must pick the user-code frame from the deepest
    block, skipping the watchdog block. The sample has 4 thread
    blocks; the watchdog block is excluded, the deepest user-code
    block is the _bg_absorb → store_fact one. `func` reports the
    SHALLOWEST user-code frame (= the high-level operation:
    _bg_absorb) while `leaf_func` reports the deepest (= the on-CPU
    frame: store_fact). Both are useful — see the parser docstring."""
    culprit = wi._parse_top_culprit(_SAMPLE_WEDGE)
    assert culprit, "expected a culprit from the sample wedge"
    # High-level operation (shallowest user frame in the deepest block)
    assert culprit["func"] == "_bg_absorb"
    assert culprit["file"].endswith("aria.py")
    # Leaf / on-CPU frame
    assert culprit["leaf_func"] == "store_fact"
    assert culprit["leaf_file"].endswith("knowledge.py")
    assert culprit["leaf_line"] == 848
    # The user_stack should include the full call chain
    funcs = [f["func"] for f in culprit["user_stack"]]
    assert "store_fact" in funcs
    assert "absorb" in funcs
    assert "_bg_absorb" in funcs


def test_parse_returns_empty_on_no_user_frames():
    """A wedge dump that has only stdlib / site-packages frames
    yields no culprit (defensive — wedge_watchdog stack alone
    shouldn't be reported as a culprit)."""
    only_watchdog = """\
=== [R-F704] event-loop heartbeat stale ===
Current thread 0x00007fe4e4a696c0 (most recent call first):
  File "/app/aria_service/main.py", line 423 in _wedge_watchdog
  File "/usr/local/lib/python3.13/threading.py", line 995 in run
=== end stack dump ===
"""
    assert wi._parse_top_culprit(only_watchdog) == {}


def test_list_and_read_roundtrip(tmp_path, monkeypatch):
    """Write two synthetic wedge files into a temp dir, monkeypatch
    the resolver, and confirm list + read return them in mtime-desc
    order with parsed culprits."""
    monkeypatch.setattr(wi, "_resolve_wedge_dir", lambda: str(tmp_path))

    f1 = tmp_path / "wedge_100_1700000000.log"
    f2 = tmp_path / "wedge_200_1700000100.log"
    f1.write_text(_SAMPLE_WEDGE, encoding="utf-8")
    f2.write_text(_SAMPLE_WEDGE, encoding="utf-8")
    # Force mtime ordering — f2 newer than f1.
    os.utime(f1, (1700000000, 1700000000))
    os.utime(f2, (1700000100, 1700000100))

    listing = wi.list_recent_wedges(limit=10)
    assert listing["total_files"] == 2
    assert [i["name"] for i in listing["items"]] == [
        "wedge_200_1700000100.log",
        "wedge_100_1700000000.log",
    ]
    assert "mtime_iso" in listing["items"][0]

    rec = wi.read_wedge("wedge_200_1700000100.log")
    assert rec["name"] == "wedge_200_1700000100.log"
    assert "store_fact" in rec["content"]
    # func is the shallowest user-code frame in the deepest block —
    # for the absorb chain that's _bg_absorb (see _parse_top_culprit
    # docstring). leaf_func surfaces the on-CPU frame (store_fact).
    assert rec["top_culprit"]["func"] == "_bg_absorb"
    assert rec["top_culprit"]["leaf_func"] == "store_fact"
    assert rec["truncated"] is False


def test_read_rejects_invalid_name(tmp_path, monkeypatch):
    """read_wedge must guard against path traversal — the resolver
    points to tmp_path but `../foo` should still be refused before
    any file lookup."""
    monkeypatch.setattr(wi, "_resolve_wedge_dir", lambda: str(tmp_path))
    assert wi.read_wedge("../etc/passwd") == {}
    assert wi.read_wedge("wedge_675.log") == {}
    assert wi.read_wedge("missing_wedge_1_1.log") == {}


def test_read_clamps_max_bytes(tmp_path, monkeypatch):
    """max_bytes must be clamped to [1, _MAX_BYTES_HARD_CAP]. A caller
    passing max_bytes=10**9 should not be able to force a huge
    allocation; max_bytes=-1 must NOT degrade to file.read(-1) which
    would read the whole file regardless of size."""
    monkeypatch.setattr(wi, "_resolve_wedge_dir", lambda: str(tmp_path))
    p = tmp_path / "wedge_1_1.log"
    big_content = "x" * 2_000_000  # 2 MB
    p.write_text(big_content, encoding="utf-8")

    # Sane default reads ≤ 200_000 bytes, file is 2 MB so truncated.
    rec = wi.read_wedge("wedge_1_1.log")
    assert len(rec["content"]) <= 200_000
    assert rec["truncated"] is True

    # Huge requested max → clamped to the hard cap.
    rec_huge = wi.read_wedge("wedge_1_1.log", max_bytes=10**9)
    assert len(rec_huge["content"]) <= wi._MAX_BYTES_HARD_CAP
    assert rec_huge["truncated"] is True

    # Negative max → clamped up to 1, NOT interpreted as -1 ("all").
    rec_neg = wi.read_wedge("wedge_1_1.log", max_bytes=-1)
    assert len(rec_neg["content"]) == 1
    assert rec_neg["truncated"] is True

    # Garbage type → falls back to default 200_000.
    rec_garbage = wi.read_wedge("wedge_1_1.log", max_bytes="lots")  # type: ignore[arg-type]
    assert len(rec_garbage["content"]) <= 200_000


def test_summarise_groups_by_culprit(tmp_path, monkeypatch):
    """summarise_wedges should aggregate by (func, file) and rank
    by count desc, then last_seen desc."""
    monkeypatch.setattr(wi, "_resolve_wedge_dir", lambda: str(tmp_path))

    # 3 files with the same culprit (store_fact) and 1 with a
    # different one (_compute_matrix_sync). store_fact should win.
    now = int(time.time())
    for i, suffix in enumerate(("a", "b", "c")):
        p = tmp_path / f"wedge_{i+1}_{now - i}.log"
        p.write_text(_SAMPLE_WEDGE, encoding="utf-8")
        os.utime(p, (now - i, now - i))

    other_wedge = _SAMPLE_WEDGE.replace(
        "Thread 0x00007fe5044c9380",
        "Thread 0x00007fe5044c9999",
    )
    # Make the "other" wedge's deepest block be the heatmap, not
    # store_fact, by trimming store_fact's block to be shallower.
    other_wedge = """\
=== [R-F704] event-loop heartbeat stale ===
Thread 0x00007fe47e301aaa (most recent call first):
  File "/app/aria_service/intel/coverage_heatmap.py", line 188 in _fact_text
  File "/app/aria_service/intel/coverage_heatmap.py", line 224 in _count_facts_for_cell_sync
  File "/app/aria_service/intel/coverage_heatmap.py", line 336 in _compute_matrix_sync
  File "/usr/local/lib/python3.13/concurrent/futures/thread.py", line 59 in run

Current thread 0x00007fe4e4a696c0 (most recent call first):
  File "/app/aria_service/main.py", line 423 in _wedge_watchdog
=== end stack dump ===
"""
    p = tmp_path / f"wedge_99_{now - 10}.log"
    p.write_text(other_wedge, encoding="utf-8")
    os.utime(p, (now - 10, now - 10))

    summary = wi.summarise_wedges(hours=24, max_files=100)
    assert summary["files_scanned"] == 4
    # `func` is the SHALLOWEST user-code frame (high-level op). For
    # the 3 store_fact-chain wedges that's `_bg_absorb` (the starlette
    # BackgroundTask entry into the absorb chain). For the heatmap
    # wedge that's `_compute_matrix_sync`. 3 vs 1, so _bg_absorb wins.
    assert summary["top_culprit"]["func"] == "_bg_absorb"
    assert summary["top_culprit"]["count"] == 3
    # The other culprit should also appear with count=1
    funcs = [c["func"] for c in summary["culprits"]]
    assert "_compute_matrix_sync" in funcs


def test_rf784_skips_module_and_main_py_entry_frames():
    """R-F784: when the deepest block's shallowest user frame is
    `<module>` at main.py (the uvicorn entry point), walk deeper to
    find the actionable handler/work frame. Live evidence from
    /admin/wedge-stacks/summary 2026-05-21 16:11 UTC: 8 of 20
    wedges reported `<module>` as top culprit because the request
    chain's user-code edges were entry-point at the bottom +
    handler in the middle. Pre-R-F784 the parser returned the
    bottom (`<module>`); post-R-F784 it returns the middle
    (`some_handler`)."""
    stack_with_module = """\
=== [R-F704] event-loop heartbeat stale ===
Thread 0x00007fe5044c9380 (most recent call first):
  File "/app/aria_service/intel/rag_store.py", line 158 in __call__
  File "/app/aria_service/routes/aria.py", line 18069 in autonomy_surface_ep
  File "/usr/local/lib/python3.13/site-packages/starlette/routing.py", line 276 in handle
  File "/usr/local/lib/python3.13/site-packages/uvicorn/server.py", line 75 in run
  File "/app/aria_service/main.py", line 1907 in <module>

Current thread 0x00007fe4e4a696c0 (most recent call first):
  File "/app/aria_service/main.py", line 423 in _wedge_watchdog
=== end stack dump ===
"""
    culprit = wi._parse_top_culprit(stack_with_module)
    assert culprit
    # Pre-R-F784 this would have been "<module>" (the shallowest
    # user frame). Post-R-F784 we walk past `<module>` (noise) and
    # past `main.py:autonomy_surface` (none in this fixture) and
    # stop at `autonomy_surface_ep` — the actual handler.
    assert culprit["func"] == "autonomy_surface_ep"
    assert culprit["file"].endswith("aria.py")
    # Leaf is unaffected — still the deepest on-CPU frame.
    assert culprit["leaf_func"] == "__call__"
    assert culprit["leaf_file"].endswith("rag_store.py")


def test_rf784_falls_back_to_module_for_pure_boot_wedges():
    """R-F784: when EVERY user frame is noise (a pure boot-time
    wedge where the only user code on the stack is main.py:<module>),
    the picker falls back to the shallowest frame — preserves the
    diagnostic signal that "the boot path is wedging" rather than
    silently returning nothing."""
    boot_only = """\
=== [R-F704] event-loop heartbeat stale ===
Thread 0x00007fe5044c9380 (most recent call first):
  File "/usr/local/lib/python3.13/runpy.py", line 88 in _run_code
  File "/app/aria_service/main.py", line 1907 in <module>

Current thread 0x00007fe4e4a696c0 (most recent call first):
  File "/app/aria_service/main.py", line 423 in _wedge_watchdog
=== end stack dump ===
"""
    culprit = wi._parse_top_culprit(boot_only)
    assert culprit
    assert culprit["func"] == "<module>"
    assert culprit["file"].endswith("main.py")


def test_resolver_falls_back_when_data_missing(tmp_path, monkeypatch):
    """When /data is not present (dev/CI), resolver returns the
    repo-local fallback path. Verify the function returns a valid
    string and doesn't raise."""
    # Force the /data branch to fail. We can't easily monkeypatch
    # os.path.isdir for just one call, so we patch os.access instead
    # — the resolver requires BOTH isdir AND access(W_OK).
    monkeypatch.setattr(os, "access", lambda p, mode: False)
    out = wi._resolve_wedge_dir()
    assert isinstance(out, str)
    assert out.endswith("wedge_stacks")
