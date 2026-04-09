"""Tier C / C+ scheduled corpus ingest CLI.

Companion to ingest_tier_a.py — that one is built for one-shot bulk
ingest of the static-tier sources (A/B/B+ primary research, run from a
laptop). This one is built for SCHEDULED ingest of the live-feed
sources (Tier C — TED tenders, sam.gov, ACLED, Bellingcat etc.) and
the Lusophone Africa sources (Tier C+ — CPLP, Angola, Mozambique
defence intel — the commercial moat).

Why a separate script when ingest_tier_a.py already accepts --tier C
═══════════════════════════════════════════════════════════════════
Tier C is supposed to run on a CRON-LIKE SCHEDULE (weekly, in the
common case) rather than as a manual one-off. This wrapper:

  1. Defaults --tier to C with an optional --include-cplus flag to
     also process C+ in the same run.
  2. Filters by `frequency` so a weekly cron only touches sources
     marked frequency: weekly / continuous, not the quarterly ones.
  3. Tracks last-run state in a JSON file so subsequent runs can skip
     sources that were ingested too recently. Avoids hammering remote
     servers if the cron fires twice or you re-run after a partial
     failure.
  4. Returns a non-zero exit code only if MORE THAN HALF the sources
     failed — single transient failures shouldn't fail the whole
     scheduled job.

The actual fetch + extract + POST logic is unchanged — this script
re-uses the strategy handlers from ingest_tier_a.py so we don't have
two copies of the crawl logic to maintain.

Usage
═════
    # Manual run — process Tier C, all frequencies, ignore state file
    python -m aria_service.cli.ingest_tier_c \\
        --aria-url https://aria-intel.fly.dev \\
        --token $ARIA_API_TOKEN

    # Weekly cron — only sources marked frequency: weekly|continuous,
    # skip anything ingested in the last 5 days, also do C+
    python -m aria_service.cli.ingest_tier_c \\
        --include-cplus \\
        --frequency weekly \\
        --skip-recent-days 5 \\
        --aria-url https://aria-intel.fly.dev \\
        --token $ARIA_API_TOKEN

    # Dry-run — list what WOULD be ingested
    python -m aria_service.cli.ingest_tier_c --include-cplus --dry-run

Cron example (weekly Monday 06:00 UTC):
    0 6 * * 1 /usr/bin/python -m aria_service.cli.ingest_tier_c \\
        --include-cplus --frequency weekly --skip-recent-days 5 \\
        --aria-url https://aria-intel.fly.dev \\
        --token "$ARIA_API_TOKEN" >> /var/log/aria-tier-c.log 2>&1

State file
══════════
Last-run timestamps are written to `.tier_c_state.json` in the same
directory as this script (gitignored). Each entry is keyed by source
URL and stores the ISO timestamp of the last successful ingest.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Re-use the strategy handlers + helpers from ingest_tier_a so this
# wrapper stays a thin CLI surface and the actual crawl logic lives in
# one place.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from aria_service.cli import ingest_tier_a as _ta  # noqa: E402

DEFAULT_STATE_FILE = Path(__file__).parent / ".tier_c_state.json"


# ── Frequency filtering ─────────────────────────────────────────────────

# Map a CLI --frequency flag to the set of registry frequencies it
# allows. The default 'all' lets every source through. 'weekly' lets
# weekly + continuous through (continuous is the most aggressive — it
# should always be processed when a weekly job runs). 'monthly' is
# additive on top of weekly, etc.
_FREQUENCY_GROUPS = {
    "all":        {"continuous", "weekly", "monthly", "quarterly", "once"},
    "continuous": {"continuous"},
    "weekly":     {"continuous", "weekly"},
    "monthly":    {"continuous", "weekly", "monthly"},
    "quarterly":  {"continuous", "weekly", "monthly", "quarterly"},
}


def _matches_frequency(source_frequency: str, requested: str) -> bool:
    """Return True if a source's frequency matches the requested CLI filter."""
    allowed = _FREQUENCY_GROUPS.get(requested, _FREQUENCY_GROUPS["all"])
    return (source_frequency or "once").lower() in allowed


# ── State file ──────────────────────────────────────────────────────────

def _load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as e:
        print(f"WARNING: failed to read state file {path}: {e}", file=sys.stderr)
        return {}


def _save_state(path: Path, state: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
    except Exception as e:
        print(f"WARNING: failed to write state file {path}: {e}", file=sys.stderr)


def _is_recent(state: dict, url: str, days: int) -> bool:
    """Return True if the state file says we ingested this URL in the
    last `days` days. Used to skip sources that were processed by an
    earlier cron run within the cooldown window."""
    if days <= 0:
        return False
    iso = state.get(url, {}).get("last_ingest_at")
    if not iso:
        return False
    try:
        last = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return False
    return datetime.now(timezone.utc) - last < timedelta(days=days)


def _record_ingest(state: dict, url: str, ok_chunks: int, fail_chunks: int) -> None:
    state[url] = {
        "last_ingest_at": datetime.now(timezone.utc).isoformat(),
        "chunks_ok": ok_chunks,
        "chunks_failed": fail_chunks,
    }


# ── Main ────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m aria_service.cli.ingest_tier_c",
        description="Scheduled ingest of Tier C (live feeds) and optionally Tier C+ (Lusophone moat) corpus sources.",
    )
    p.add_argument("--include-cplus", action="store_true",
                   help="Also process Tier C+ (Lusophone Africa / CPLP) in the same run")
    p.add_argument("--cplus-only", action="store_true",
                   help="Process ONLY Tier C+ (skip Tier C). Mutually exclusive with --include-cplus.")
    p.add_argument("--frequency", default="all",
                   choices=sorted(_FREQUENCY_GROUPS.keys()),
                   help="Only process sources matching this frequency band (default all)")
    p.add_argument("--skip-recent-days", type=int, default=0,
                   help="Skip sources successfully ingested in the last N days (per state file)")
    p.add_argument("--state-file", default=str(DEFAULT_STATE_FILE),
                   help="Path to JSON state file tracking last-ingest timestamps")
    p.add_argument("--source-class", default=None,
                   help="Limit to one publisher (e.g. ACLED, Bellingcat)")
    p.add_argument("--cplp-only", action="store_true",
                   help="Only sources tagged cplp_relevant=true")
    p.add_argument("--dry-run", action="store_true",
                   help="List what would be ingested without sending")
    p.add_argument("--aria-url", default=os.getenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev"),
                   help="Base URL of the ARIA service")
    p.add_argument("--token", default=os.getenv("ARIA_API_TOKEN", "") or os.getenv("ARIA_INT_TOKEN", ""),
                   help="Bearer token sent as Authorization header (default ARIA_API_TOKEN, then ARIA_INT_TOKEN)")
    p.add_argument("--timeout", type=int, default=_ta.DEFAULT_TIMEOUT_S,
                   help="Per-request timeout in seconds")
    args = p.parse_args(argv)

    if args.include_cplus and args.cplus_only:
        print("ERROR: --include-cplus and --cplus-only are mutually exclusive", file=sys.stderr)
        return 2

    # Decide which tiers to process
    if args.cplus_only:
        tiers = ["C+"]
    elif args.include_cplus:
        tiers = ["C", "C+"]
    else:
        tiers = ["C"]

    # Load registry
    try:
        from aria_service.intel import corpus_registry
    except Exception as e:
        print(f"ERROR: cannot import corpus_registry: {e}", file=sys.stderr)
        return 2

    # Aggregate sources across requested tiers
    all_sources = []
    for tier in tiers:
        all_sources.extend(corpus_registry.get_sources(
            tier=tier,
            source_class=args.source_class,
            cplp_only=args.cplp_only,
        ))

    if not all_sources:
        print(f"ERROR: no sources match the filter (tiers={tiers}, source_class={args.source_class})", file=sys.stderr)
        return 2

    # Frequency filter
    before_freq = len(all_sources)
    sources = [s for s in all_sources if _matches_frequency(s.frequency, args.frequency)]
    if before_freq != len(sources):
        print(f"Frequency filter ({args.frequency}): {before_freq} -> {len(sources)} sources")

    # State file — drop anything ingested too recently
    state_path = Path(args.state_file)
    state = _load_state(state_path) if args.skip_recent_days > 0 else {}
    if args.skip_recent_days > 0:
        before_state = len(sources)
        sources = [s for s in sources if not _is_recent(state, s.url, args.skip_recent_days)]
        skipped_recent = before_state - len(sources)
        if skipped_recent:
            print(f"State filter (skip last {args.skip_recent_days}d): skipped {skipped_recent} recently-ingested sources")

    if not sources:
        print("Nothing to do — all sources filtered out.")
        return 0

    print(f"Tiers: {tiers}")
    print(f"Found {len(sources)} source(s) to process")
    print(f"Target: {args.aria_url}/api/aria/corpus/ingest")
    print()

    if args.dry_run:
        for i, s in enumerate(sources, 1):
            mark = " [SKIP]" if s.ingest_strategy in _ta._SKIP_STRATEGIES else ""
            print(f"  [{i}/{len(sources)}] {s.tier:3} {s.source_class:25} {s.ingest_strategy:12} freq={s.frequency:10} {s.url}{mark}")
        return 0

    # Real run — re-use ingest_tier_a's strategy handlers
    n_ok = 0
    n_fail = 0
    n_skip = 0
    n_sources_total = len(sources)
    n_sources_failed = 0
    t_start = time.time()

    for i, s in enumerate(sources, 1):
        prefix = f"[{i}/{n_sources_total}] {s.tier} {s.source_class}"
        if s.ingest_strategy in _ta._SKIP_STRATEGIES:
            print(f"{prefix}: SKIP ({s.ingest_strategy} — handled separately)")
            n_skip += 1
            continue
        handler = _ta._STRATEGY_HANDLERS.get(s.ingest_strategy)
        if not handler:
            print(f"{prefix}: SKIP (unknown strategy {s.ingest_strategy!r})")
            n_skip += 1
            continue
        print(f"{prefix}: fetching {s.url}")
        source_ok = 0
        source_fail = 0
        try:
            results = handler(s, aria_url=args.aria_url, token=args.token, timeout=args.timeout)
        except Exception as e:
            print(f"  ERROR: handler crashed: {e}")
            n_fail += 1
            source_fail += 1
            n_sources_failed += 1
            continue
        for ok, msg in results:
            if ok:
                n_ok += 1
                source_ok += 1
                print(f"  OK — {msg}")
            else:
                n_fail += 1
                source_fail += 1
                print(f"  FAIL — {msg}")
        if source_ok == 0 and source_fail > 0:
            n_sources_failed += 1

        # Update state file even on partial success — we did REACH the
        # source, the cron should not retry it tomorrow.
        if source_ok > 0 and args.skip_recent_days > 0:
            _record_ingest(state, s.url, source_ok, source_fail)

        time.sleep(_ta.INTER_REQUEST_DELAY_S)

    if args.skip_recent_days > 0 and state:
        _save_state(state_path, state)

    elapsed = time.time() - t_start
    print()
    print(f"Done. {n_ok} chunks ingested, {n_fail} failed, {n_skip} skipped, in {elapsed:.1f}s")
    print(f"Sources: {n_sources_total} total, {n_sources_failed} fully failed")

    # Cron-friendly exit code: only fail the run if MORE than half of
    # the sources had zero successful chunks. Single transient failures
    # are normal in a scheduled job hitting 20+ remote servers.
    if n_sources_total > 0 and n_sources_failed > n_sources_total / 2:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
