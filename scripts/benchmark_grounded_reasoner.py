#!/usr/bin/env python3
"""R-F1070 — Latency benchmark for the grounded reasoner.

Measures p50/p95/p99 latency of the grounded reasoner pipeline.
Run this after any change to the reasoner to detect regressions.

Usage:
    python scripts/benchmark_grounded_reasoner.py [--samples N]

Requires: a running ARIA intel service with ARIA_GROUNDED_REASONER=1.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Test queries that exercise the full pipeline
TEST_QUERIES = [
    "What is the Wassenaar Arrangement?",
    "What is an end-user certificate?",
    "What is the ITAR?",
    "What is the difference between OFAC and BIS?",
    "What is a CAGE code?",
]


async def benchmark(samples: int = 5) -> dict:
    """Run the grounded reasoner benchmark.

    Args:
        samples: Number of samples per query (default 5).

    Returns:
        Dict with p50, p95, p99 latency in seconds.
    """
    # Import here so the script can be run standalone
    from aria_service.intel.grounded_reasoner import reason

    latencies = []

    for query in TEST_QUERIES:
        for _ in range(samples):
            start = time.monotonic()
            try:
                result = await asyncio.wait_for(reason(query), timeout=60.0)
                elapsed = time.monotonic() - start
                latencies.append(elapsed)
                status = "OK" if result and not result.abstained else "ABSTAIN"
                print(f"  {query[:50]:50s} {elapsed:6.2f}s [{status}]")
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - start
                latencies.append(elapsed)
                print(f"  {query[:50]:50s} {elapsed:6.2f}s [TIMEOUT]")
            except Exception as e:
                print(f"  {query[:50]:50s} ERROR: {e}")

    if not latencies:
        return {"error": "No samples collected"}

    sorted_lats = sorted(latencies)
    n = len(sorted_lats)
    p50 = sorted_lats[int(n * 0.50)]
    p95 = sorted_lats[int(n * 0.95)]
    p99 = sorted_lats[int(n * 0.99)]

    result = {
        "samples": n,
        "p50_seconds": round(p50, 2),
        "p95_seconds": round(p95, 2),
        "p99_seconds": round(p99, 2),
        "mean_seconds": round(statistics.mean(latencies), 2),
        "min_seconds": round(min(latencies), 2),
        "max_seconds": round(max(latencies), 2),
        "queries": TEST_QUERIES,
        "samples_per_query": samples,
    }

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Benchmark grounded reasoner latency")
    parser.add_argument("--samples", type=int, default=3, help="Samples per query")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    print(f"Grounded Reasoner Benchmark — {len(TEST_QUERIES)} queries x {args.samples} samples")
    print("=" * 70)

    result = asyncio.run(benchmark(samples=args.samples))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print()
        print("Results:")
        print(f"  Samples: {result.get('samples', 0)}")
        print(f"  p50:     {result.get('p50_seconds', 'N/A')}s")
        print(f"  p95:     {result.get('p95_seconds', 'N/A')}s")
        print(f"  p99:     {result.get('p99_seconds', 'N/A')}s")
        print(f"  Mean:    {result.get('mean_seconds', 'N/A')}s")
        print(f"  Min:     {result.get('min_seconds', 'N/A')}s")
        print(f"  Max:     {result.get('max_seconds', 'N/A')}s")

        # Compare against baseline (23s from Claude's findings)
        baseline = 23.0
        p50 = result.get("p50_seconds", 0)
        if p50:
            if p50 <= baseline * 1.2:
                print(f"\n✅ p50 ({p50}s) within 20% of baseline ({baseline}s)")
            else:
                print(f"\n⚠️  p50 ({p50}s) exceeds baseline ({baseline}s) by {(p50/baseline - 1)*100:.0f}%")


if __name__ == "__main__":
    main()
