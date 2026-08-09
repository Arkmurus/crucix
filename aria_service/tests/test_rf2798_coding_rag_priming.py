"""R-F2798 — the CLAUDE.md §20 coding-RAG priming step must actually work.

THE DEFECT (reproduced by execution)
────────────────────────────────────
`query_constitutional_constraints()` is a BINDING session-open step (CLAUDE.md
§20): before writing code, query the coding RAG for the constitutional rules that
constrain the change. On this machine it did nothing at all — the process died
with a Windows access violation and the shell saw a clean exit with no output, so
the step silently no-opped. R-F2623 had already fixed a *different* silent failure
in this same step (an `asyncio.run` TypeError); this was the second.

ROOT CAUSE
──────────
`rag_store._get_client()` emitted a diagnostic log line that called `.count()` on
the documents, facts and cold collections. R-F1911 had already established
(rag_store.py:1488) that `.count()` on those collections is an O(collection-size)
NATIVE scan — ~38s cold over ~215K chunks — and memoised it behind a TTL cache for
`/health` for exactly that reason. This call site was missed, so every RAG client
init paid a full native scan just to print a number. On a collection whose native
scan faults, that scan also took the whole process down, and a native access
violation CANNOT be caught by a Python try/except — so no error handling around it
would ever have helped.

Isolated per-collection (each in its own process):
    coding_constitutional 31 · coding_fixes 4 · coding_failures 7
    coding_structure 23 · aria_facts 1704 · aria_documents_cold 0
    aria_documents  → SEGFAULT

THE FIX
───────
Do not run an O(n) native scan inside a log line on the init path. Counts are
slowly-changing diagnostics and are already exposed — cached and single-flight —
through `get_stats()`. This removes the ~38s cold cost from every client init as
well as the crash, and it is the same structural argument R-F1911 made.

These tests drive the REAL priming call in a SUBPROCESS, because a segfault would
otherwise take the test runner down with it — and asserting on the child's exit
code is the only honest way to prove "it did not crash".

WHICH TEST ACTUALLY LOCKS THE REGRESSION OUT — read this before trusting the suite
──────────────────────────────────────────────────────────────────────────────────
Only `test_rag_client_init_does_not_scan_collections` fails against the pre-fix
code. The two runtime tests pass either way, because conftest (R-F1534) points
ARIA_RAG_PATH at an empty temp store and an EMPTY collection counts fine — the
fault needs the production store's ~215K-chunk `aria_documents`. Reproducing it
hermetically would mean shipping a corrupt collection fixture, which is not worth
it. So the structural guard is the load-bearing one; the runtime tests document
the contract and catch the OTHER silent-failure modes (crash-with-exit-0, and
returning zero rules).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# R-F3795 — priming queries the chromadb-backed coding RAG. Without chromadb the
# store reports DEGRADED and returns zero rules, which is correct behaviour for a
# machine with no wheel (§16) — not the silent no-op this test hunts.
from ._env_probe import requires_module

ROOT = Path(__file__).resolve().parents[2]

# Drives the exact call CLAUDE.md §20 tells an agent to run at session open.
#
# It SEEDS first, on purpose. conftest.py:58 (R-F1534) points ARIA_RAG_PATH at a
# temp dir so tests never touch the production chromadb store — correct hygiene,
# and the child inherits it, so the store it opens is legitimately EMPTY. Asserting
# against production contents here would be both non-hermetic and a false gate
# (it would pass or fail on data this test does not own). Seeding the real rules
# through the real indexer keeps it hermetic while still exercising the whole
# index → query path.
_PRIMING_SNIPPET = (
    "from aria_service.intel.coding_rag_indexer import "
    "sync_constitutional_rules, query_constitutional_constraints as q; "
    "sync_constitutional_rules(); "
    "r = q('modifying dd_schema adverse media', top_k=5); "
    "print('RULES', len(r)); "
    "print('FIRST', (r[0]['rule'][:60] if r else ''))"
)


def _run_priming(timeout: int = 300) -> subprocess.CompletedProcess:
    # -X faulthandler is LOAD-BEARING: without it a Windows access violation kills
    # the interpreter with returncode 0 and no output, so an exit-code assertion
    # passes on a process that actually crashed. That is exactly how this defect
    # hid for two R-numbers, and the first draft of this test fell for it too.
    return subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", _PRIMING_SNIPPET],
        cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
    )


@pytest.mark.slow
def test_priming_does_not_crash_the_process():
    """The binding §20 step must not die with a native access violation."""
    proc = _run_priming()
    assert "access violation" not in (proc.stderr or "").lower(), (
        f"the priming step crashed natively:\n{proc.stderr[-800:]}"
    )
    # Reaching the final print is the only proof the process survived: a native
    # fault exits 0, so returncode alone proves nothing.
    assert "RULES" in proc.stdout, (
        f"the child died before completing (rc={proc.returncode})\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-800:]}"
    )
    assert proc.returncode == 0, (
        f"priming exited {proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr[-800:]}"
    )


@pytest.mark.slow
@requires_module("chromadb")
def test_priming_returns_actual_constitutional_rules():
    """Not crashing is not enough — it has to RETURN the rules.

    A step that runs clean and yields nothing is the same silent no-op in a
    different costume, which is precisely how this went unnoticed twice.
    """
    proc = _run_priming()
    assert "RULES" in proc.stdout, f"no result line; stderr={proc.stderr[-500:]}"
    count = int(proc.stdout.split("RULES", 1)[1].split()[0])
    assert count > 0, "priming returned ZERO rules — the step is still a silent no-op"
    assert "FIRST" in proc.stdout
    first = proc.stdout.split("FIRST", 1)[1].strip()
    assert len(first) > 5, f"rule text looks empty: {first!r}"


@pytest.mark.slow
def test_rag_client_init_does_not_scan_collections():
    """Regression guard for the root cause.

    `.count()` on documents/facts is an O(collection-size) native scan (R-F1911,
    rag_store.py:1488). It must not be called on the client-init path — not for a
    log line, not for anything. If it creeps back, init pays ~38s again and a
    faulting collection can once more kill the process.
    """
    src = (ROOT / "aria_service" / "intel" / "rag_store.py").read_text(encoding="utf-8")
    start = src.index("def _get_client")
    # Bound the search to the function body: the next top-level def/async def.
    rest = src[start + 10:]
    nxt = rest.find("\ndef ")
    nxt_async = rest.find("\nasync def ")
    if nxt_async != -1 and (nxt == -1 or nxt_async < nxt):
        nxt = nxt_async
    body = rest[:nxt] if nxt != -1 else rest

    for expensive in ("local_docs.count()", "local_facts.count()", "local_cold.count()"):
        assert expensive not in body, (
            f"_get_client calls {expensive} — that is an O(n) native scan on the "
            "init path (R-F1911). Report counts via the cached get_stats() instead."
        )
