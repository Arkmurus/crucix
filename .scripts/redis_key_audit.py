"""One-shot Redis key audit. Walks every .py file under aria_service/,
extracts string literals passed to redis_store writer / reader functions,
normalises `:{var}` placeholders, and reports keys that are read but never
written -- the silent-zero pattern that bit autonomy_surface drafts panel.

Limitations:
- Only catches LITERAL strings (incl. f-strings before braces) at the call
  site. Constants like `_K_PENDING = "..."` need one extra hop -- if the
  read uses the constant and the write uses the same constant, both pass
  the audit naturally because the constant evaluates to the same literal.
- Doesn't handle dynamically-built keys.
- Slight false-positive risk for legitimate writers in non-Python tools
  (Node.js sweep, etc.) that we don't scan.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "aria_service"

WRITER_FUNCS = {
    "set", "set_json", "setex", "setnx",
    "lpush", "rpush", "lset",
    "incr", "incrby", "incrbyfloat", "decr",
    "hset", "hincrby", "hincrbyfloat",
    "sadd", "zadd",
    "expire",  # not a write, but proves key existence; include for symmetry
}
READER_FUNCS = {
    "get", "get_json",
    "lrange", "llen", "lindex",
    "hget", "hgetall", "hlen",
    "smembers", "scard",
    "zrange", "zrevrange", "zrangebyscore", "zrevrangebyscore", "zcard", "zscore", "zrank",
    "exists", "ttl", "type",
    "keys",  # also a reader (pattern scan)
}

# Capture: rs.func("...") OR _rs.func("...") OR redis_store.func("...")
CALL_RX = re.compile(
    r"\b(?:rs|_rs|redis_store|client|_client)\.([a-z_]+)\s*\(\s*(?:f?\"([^\"]+)\"|f?'([^']+)')",
    re.MULTILINE,
)
# Module-level constants:  NAME = "literal"
CONST_ASSIGN_RX = re.compile(
    r"^([A-Z_][A-Z0-9_]*)\s*=\s*(?:f?\"([^\"]+)\"|f?'([^']+)')",
    re.MULTILINE,
)
# Local variables:  key = "literal"  OR  key = SOME_CONST.format(...)
LOCAL_KEY_LITERAL_RX = re.compile(
    r"\b([a-z_][a-z0-9_]*)\s*=\s*f?[\"']([^\"']+)[\"']",
)
LOCAL_KEY_FROMCONST_RX = re.compile(
    r"\b([a-z_][a-z0-9_]*)\s*=\s*([A-Z_][A-Z0-9_]*)\.format\(",
)
# func(_K_NAME) / func(KEY_NAME) / func(local_var)
CALL_VAR_RX = re.compile(
    r"\b(?:rs|_rs|redis_store|client|_client)\.([a-z_]+)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\b",
)


def normalise(key: str) -> str:
    """Collapse f-string interpolation placeholders to `*` so different
    runtime values map to the same canonical key."""
    return re.sub(r"\{[^}]*\}", "*", key)


def main() -> None:
    written: dict[str, set[str]] = defaultdict(set)
    read: dict[str, set[str]] = defaultdict(set)

    for py in ROOT.rglob("*.py"):
        rel = py.relative_to(ROOT.parent).as_posix()
        if "/tests/" in rel or "__pycache__" in rel:
            continue
        try:
            src = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Resolve module-level constants  NAME -> "literal"
        consts: dict[str, str] = {}
        for m in CONST_ASSIGN_RX.finditer(src):
            name = m.group(1)
            value = m.group(2) or m.group(3) or ""
            if value.startswith(("crucix:", "aria:")):
                consts[name] = value

        # Resolve local variables.  This is intentionally file-wide rather
        # than scope-aware -- catches the common pattern
        #   def foo():
        #       key = "crucix:foo:bar"
        #       await rs.set(key, ...)
        # plus
        #   key = _CONSTANT.format(x=...)
        local_vars: dict[str, str] = {}
        for m in LOCAL_KEY_LITERAL_RX.finditer(src):
            name = m.group(1)
            value = m.group(2)
            if value.startswith(("crucix:", "aria:")):
                local_vars[name] = value
        for m in LOCAL_KEY_FROMCONST_RX.finditer(src):
            name = m.group(1)
            const_name = m.group(2)
            if const_name in consts:
                local_vars[name] = consts[const_name]

        # Direct literal calls
        for m in CALL_RX.finditer(src):
            func = m.group(1)
            literal = m.group(2) or m.group(3) or ""
            if not (literal.startswith("crucix:") or literal.startswith("aria:")):
                continue
            key = normalise(literal)
            if func in WRITER_FUNCS:
                written[key].add(rel)
            elif func in READER_FUNCS:
                read[key].add(rel)

        # Constant + local-variable calls.  Look up consts first (uppercase
        # convention), fall back to local_vars.
        for m in CALL_VAR_RX.finditer(src):
            func = m.group(1)
            varname = m.group(2)
            literal = consts.get(varname) or local_vars.get(varname)
            if not literal:
                continue
            key = normalise(literal)
            if func in WRITER_FUNCS:
                written[key].add(rel)
            elif func in READER_FUNCS:
                read[key].add(rel)

    only_read = sorted(set(read.keys()) - set(written.keys()))
    only_written = sorted(set(written.keys()) - set(read.keys()))

    print(f"# Redis key audit — {len(written) + len(read)} unique keys mapped\n")
    print(f"## Read but NEVER written ({len(only_read)} silent-zero candidates)\n")
    for k in only_read:
        readers = sorted(read[k])
        print(f"- `{k}`")
        for r in readers:
            print(f"    read  {r}")
    print(f"\n## Written but NEVER read ({len(only_written)} dead-write candidates)\n")
    for k in only_written:
        writers = sorted(written[k])
        print(f"- `{k}`")
        for w in writers:
            print(f"    write {w}")


if __name__ == "__main__":
    main()
