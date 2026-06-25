"""R-F1936 — CODE-GAP FUEL SOURCES for ARIA's local coder.

The existing auto-miner (StaticAnalysisExtractor) is a firehose of ~8800 mostly
cosmetic nits (89% "missing return type", dominated by test files) — useless as
"build infrastructure" fuel. This module produces TWO high-signal sources of
real code gaps the coder can fix, both grounded in ARIA's code RAG:

  1. failing_test_gaps  — REAL bugs from failing tests. The failing test IS the
     reproduce, so these are GOLD-ABLE (FAIL-on-unfixed -> PASS-on-fixed). Each
     failing test is mapped to its source module via the test's IMPORTS (not a
     name heuristic) so the coder never gerrymanders an unrelated module
     (the R-F1686 risk). Reads the pytest lastfailed cache (run pytest first).

  2. reliability_gaps   — CURATED reliability issues (try-without/bare-except in
     core, NON-test modules) distilled from StaticAnalysisExtractor — dropping
     the cosmetic long-function/missing-return/repeated-block noise. Not
     gold-able (no reproduce) but real; the coder stages them for review.

Both are RAG-grounded: each gap carries query_codebase_context(module) snippets
in evidence["rag_context"], and the coder additionally grounds at fix time
(R-F1934). Use from the local coder runner (`aria_local_coder.py --scan`).

CLI: python scripts/admin/code_gap_fuel.py --source both --limit 10
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_LASTFAILED = REPO / ".pytest_cache" / "v" / "cache" / "lastfailed"
_CORE_DIRS = ("aria_service/intel", "aria_service/autonomous", "aria_service/routes",
              "aria_service/learning", "aria_service/crawler", "aria_service/llm")


def _is_test_path(p: str) -> bool:
    p = p.replace("\\", "/").lower()
    return "/tests/" in p or "test_" in Path(p).name


def _module_under_test(test_file: Path) -> str | None:
    """Find the aria_service module a test exercises, via its imports (robust —
    no name-heuristic gerrymandering). Returns a repo-relative .py path or None."""
    try:
        tree = ast.parse(test_file.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    candidates: list[str] = []
    for node in ast.walk(tree):
        mod = None
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
        elif isinstance(node, ast.Import):
            for n in node.names:
                if n.name.startswith("aria_service."):
                    candidates.append(n.name)
            continue
        if mod and mod.startswith("aria_service."):
            candidates.append(mod)
    for dotted in candidates:
        rel = dotted.replace(".", "/") + ".py"
        if _is_test_path(rel):
            continue
        if (REPO / rel).is_file():
            return rel
        # package import (aria_service.intel) -> skip dir; need a file
    return None


def failing_test_gaps(limit: int = 25) -> list:
    """REAL, gold-able code gaps from the pytest lastfailed cache."""
    from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity
    if not _LASTFAILED.exists():
        return []
    try:
        lastfailed = json.loads(_LASTFAILED.read_text(encoding="utf-8"))
    except Exception:
        return []
    by_file: dict[str, list[str]] = {}
    for node_id, failed in (lastfailed or {}).items():
        if failed is not True:
            continue
        f = node_id.split("::", 1)[0]
        by_file.setdefault(f, []).append(node_id)
    gaps = []
    for test_file, node_ids in by_file.items():
        tf = REPO / test_file
        if not tf.is_file():
            continue
        module = _module_under_test(tf)
        if module is None:  # can't map reliably -> skip (no gerrymander)
            continue
        gaps.append(Gap(
            gap_id=f"failtest_{Path(test_file).stem[:24]}",
            gap_type=GapType.MODULE_BUG,
            severity=GapSeverity.HIGH,
            title=f"Failing test {Path(test_file).name} ({len(node_ids)}) -> {module}",
            description=(
                f"{len(node_ids)} failing test(s) in {test_file} exercise {module}. "
                f"First: {node_ids[0]}. The failing test reproduces the bug "
                f"(FAIL-on-unfixed -> PASS-on-fixed = gold-able)."),
            module=module,
            evidence={"test_file": test_file, "failing_tests": node_ids[:20],
                      "first_failing_test": node_ids[0].split("::")[-1],
                      "source": "pytest_lastfailed", "gold_able": True},
        ))
        if len(gaps) >= limit:
            break
    return gaps


async def reliability_gaps(limit: int = 15) -> list:
    """CURATED reliability gaps (try/bare-except in core, non-test modules)."""
    from aria_service.autonomous.gap_detector import StaticAnalysisExtractor
    from aria_service.intel import redis_store as rs
    from datetime import datetime, timezone, timedelta
    ex = StaticAnalysisExtractor(rs)
    raw = await ex.extract(datetime.now(timezone.utc) - timedelta(seconds=60))
    seen, out = set(), []
    for g in raw:
        title = (g.title or "").lower()
        if "except" not in title:          # keep only the swallowed-error class
            continue
        if _is_test_path(g.module):         # core modules only
            continue
        if not any(g.module.replace("\\", "/").startswith(d) for d in _CORE_DIRS):
            continue
        key = (g.module, title[:40])
        if key in seen:
            continue
        seen.add(key)
        g.evidence = dict(g.evidence or {}); g.evidence["source"] = "reliability_curated"
        out.append(g)
        if len(out) >= limit:
            break
    return out


async def rag_enrich(gaps: list) -> list:
    """Attach code-RAG codebase-structure context to each gap (off-loop)."""
    try:
        from aria_service.intel import coding_rag_indexer as crag
    except Exception:
        return gaps
    for g in gaps:
        try:
            ctx = await asyncio.to_thread(crag.query_codebase_context, g.module, 2)
            snips = [str((r or {}).get("content", ""))[:300] for r in (ctx or [])[:2] if r]
            if snips:
                g.evidence = dict(g.evidence or {})
                g.evidence["rag_context"] = snips
        except Exception:
            pass
    return gaps


async def gather(source: str = "both", limit: int = 20, enrich: bool = True) -> list:
    gaps = []
    if source in ("both", "test"):
        gaps += failing_test_gaps(limit=limit)
    if source in ("both", "reliability"):
        gaps += await reliability_gaps(limit=limit)
    if enrich:
        gaps = await rag_enrich(gaps)
    return gaps


def main() -> int:
    ap = argparse.ArgumentParser(description="Code-gap fuel sources for the local coder")
    ap.add_argument("--source", choices=["both", "test", "reliability"], default="both")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--no-enrich", action="store_true")
    a = ap.parse_args()
    gaps = asyncio.run(gather(a.source, a.limit, enrich=not a.no_enrich))
    print(f"[fuel] {len(gaps)} code gap(s) from source={a.source}:")
    for g in gaps:
        rc = "RAG" if (g.evidence or {}).get("rag_context") else "—"
        gold = "GOLD-ABLE" if (g.evidence or {}).get("gold_able") else "stage"
        print(f"  [{g.gap_type:11}] {gold:9} {rc} {g.module:42} {str(g.title)[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
