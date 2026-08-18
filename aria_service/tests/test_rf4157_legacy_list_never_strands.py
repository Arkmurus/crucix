"""R-F4157 (C-178) — an actively-written list stranded its legacy blob forever,
and the busier the list the more certainly it happened.

R-F1515 replaced the JSON-blob list (one row holding the whole list) with a
row-per-entry table, and shipped `_migrate_list_if_needed()` to convert legacy
keys lazily "on first read", deleting the blob afterwards. It was called by
`lpop`, `ltrim`, `llen` and `lrange` — but **only in their fallback branch**:

```python
rows = await cur.fetchall()        # SELECT ... FROM list_entries
if rows:
    return values[start:end]       # <-- RETURNS EARLY
# Fallback: check legacy JSON blob
await _migrate_list_if_needed(key) # <-- only when list_entries is EMPTY
```

`lpush` did not call it at all. So the sequence was:

1. legacy blob sits in `state` with `kind='list'`
2. one `lpush` lands -> the key now has rows in `list_entries`
3. every later read short-circuits on `if rows:`
4. the migration is now **unreachable**, and the blob is dead weight forever

**Self-selecting:** the more traffic a list gets, the sooner step 2 happens, so
the biggest lists strand first and largest.

### Measured live on aria-intel, 2026-08-18

```
state rows kind='list' with live list_entries rows :   55 keys, 19,266,104 bytes
state rows kind='list' with no live counterpart    :  914 keys,    126,068 bytes

biggest stranded:
  crucix:audit:log                      14,141,171   (50,000 live rows)
  crucix:metacog:self_assessments        1,464,433   (   500 live rows)
  crucix:metacog:codegen:proposals       1,305,664   (   100 live rows)
  crucix:news_monitor:articles             982,469   ( 1,000 live rows)
```

**19.3 MB of provably-superseded blobs** — the "one-time migration" had never
run for any actively-written list.

Migrating on the FIRST push closes the window permanently: at that moment there
are no live rows yet, which is exactly the state `_migrate_list_if_needed`
requires. Once migrated it is a no-op — one indexed lookup for a `kind='list'`
row — so steady-state cost is nil.

**Not fixed here: the 55 keys already stranded.** `crucix:audit:log` is a
tamper-evident audit chain whose blob holds entries from 2026-06-10 that may
pre-date the live rows, so merging or deleting is a data decision under §26
("never touch data stores destructively; archive with a manifest"), not
something to slip into a code fix.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "intel" / "state_store.py"


def _fn(name: str) -> ast.AST:
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name:
            return n
    raise AssertionError(f"{name} not found in state_store.py")


def _calls(node: ast.AST) -> set[str]:
    return {
        (getattr(c.func, "attr", None) or getattr(c.func, "id", None))
        for c in ast.walk(node) if isinstance(c, ast.Call)
    }


def test_lpush_migrates_before_it_writes():
    """The regression. Without this, the first push to a legacy list orphans
    its blob permanently."""
    assert "_migrate_list_if_needed" in _calls(_fn("lpush")), (
        "lpush does not migrate — an actively-written legacy list will strand "
        "its blob the moment it receives a push, and no read will ever reach "
        "the migration again")


@pytest.mark.parametrize("op", ["lpop", "ltrim", "llen", "lrange"])
def test_the_read_paths_still_migrate(op):
    """The original lazy migration must not be lost while fixing the write
    path — it is what converts a legacy list that is READ before it is
    written."""
    assert "_migrate_list_if_needed" in _calls(_fn(op))


def _migration_call(fn: ast.AST) -> ast.Call:
    for c in ast.walk(fn):
        if isinstance(c, ast.Call) and (
                getattr(c.func, "attr", None) or getattr(c.func, "id", None)
        ) == "_migrate_list_if_needed":
            return c
    raise AssertionError("no _migrate_list_if_needed call in lpush")


def _insert_linenos(fn: ast.AST) -> list[int]:
    """Line numbers of real INSERTs into list_entries.

    AST, not `str.find`. A first draft searched the source text for "INSERT"
    and matched lpush's own DOCSTRING ("single INSERT with no Python-level
    lock"), so it reported the insert as happening before the migration. A
    substring search over code is a test that reads comments.
    """
    out = []
    for c in ast.walk(fn):
        if not isinstance(c, ast.Call):
            continue
        for a in c.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                    and "INSERT" in a.value.upper() and "list_entries" in a.value:
                out.append(c.lineno)
    return sorted(out)


def test_the_migration_happens_before_the_insert():
    """Order is the whole fix. Migrating AFTER the INSERT would run against a
    key that already has live rows — the exact condition that makes
    `_migrate_list_if_needed` a no-op — so the blob would still strand."""
    fn = _fn("lpush")
    mig = _migration_call(fn).lineno
    inserts = _insert_linenos(fn)
    assert inserts, "no INSERT INTO list_entries found in lpush"
    assert mig < min(inserts), (
        f"lpush migrates at line {mig} but inserts at {min(inserts)}; by then "
        "the key has live rows and the migration is a no-op — the blob strands")


def test_the_migration_cannot_block_a_write():
    """Housekeeping must never fail a push. A list write that raises because a
    legacy blob could not be converted would turn a storage nicety into lost
    data.

    Checked structurally: the call must sit inside a `try` with a handler. A
    first draft scanned a fixed character window backwards for "try:" and failed
    on this very file, because the explanatory comment is longer than the
    window — a heuristic measuring prose again.
    """
    fn = _fn("lpush")
    target = _migration_call(fn)
    for node in ast.walk(fn):
        if isinstance(node, ast.Try) and node.handlers:
            if any(c is target for b in node.body for c in ast.walk(b)):
                return
    raise AssertionError(
        "the pre-push migration is not inside a guarded try — an exception "
        "there would propagate out of lpush and drop the write")


def test_the_migration_still_deletes_the_blob_it_converted():
    """The point of migrating is reclaiming the row. If the DELETE were dropped
    the blob would survive conversion and this fix would reclaim nothing."""
    seg = ast.get_source_segment(
        _SRC.read_text(encoding="utf-8"), _fn("_migrate_list_if_needed")) or ""
    assert "DELETE FROM state" in seg, (
        "_migrate_list_if_needed no longer removes the legacy row — migration "
        "would duplicate the data instead of moving it")
