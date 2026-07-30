"""R-F3505 — report (and optionally clear) watchlist entries with no surviving DD.

WHY THIS EXISTS
───────────────
R-F3500 made ``delete_report`` cascade to the watchlist, so from now on deleting
a DD stops the monitoring. It does NOT retroactively clean up entities orphaned
by deletions that happened BEFORE that fix — those are still on the watchlist and
are still re-screened by the autonomous dd_monitor every 300s.

The UI is explicit about the contract this restores
(public/watchlist.html): enrollment "is deliberately closed over existing DD and
vetting cases" — "Only entities already recorded in this section can be
monitored." An entry whose report is gone is therefore something the product says
should not exist.

SAFETY POSTURE — read this before running with --apply
──────────────────────────────────────────────────────
DRY RUN IS THE DEFAULT AND CANNOT BE SKIPPED BY ACCIDENT. Deleting is opt-in via
an explicit ``--apply`` flag; without it this script only ever reads and prints.

It is OWNER-SCOPED. Removal goes through ``remove_from_watchlist`` with each
entry's own ``user_id``, so one tenant's cleanup can never remove another
tenant's entry — the IDOR-write R-F2401 closed. An entry with NO owner is
reported but never auto-removed: an unscoped delete is exactly the operation that
rule forbids, and a stale watchlist row is far cheaper than a cross-tenant
deletion.

It errs toward KEEPING. An entry is only ever proposed for removal when the
report index can be read AND the entry's subject appears nowhere in it. If the
index read fails, or looks implausibly empty, the script refuses to propose
anything rather than risk clearing a live watchlist from a bad read — the
non-strict-read clobber class in memory/nonstrict_read_clobber_defect_class.

Usage
─────
    python scripts/admin/watchlist_orphan_reconcile.py            # dry run
    python scripts/admin/watchlist_orphan_reconcile.py --apply    # after review
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def _load() -> tuple[list[dict], list[dict]]:
    """STRICT reads only.

    Caught on the very first live dry run: ``get_json`` returned [] for BOTH
    keys and the run reported "0 watchlist entries, 0 reports, 0 orphans" —
    which reads as a clean bill of health. The strict read showed the truth:

        StoreReadError: state_store: no connection (reconnect in progress)

    Nothing was empty; the store was mid-reconnect and the non-strict read
    swallowed the failure into an empty list
    (memory/nonstrict_read_clobber_defect_class). For a script whose whole job
    is deciding what to DELETE, "I could not read it" must never present as
    "there is nothing there" — the failure mode is silently proposing to clear a
    live watchlist. Fail loudly instead.
    """
    from aria_service.intel import dd_orchestrator as dd
    from aria_service.intel import redis_store as rs
    watchlist = await rs.get_json_strict(dd.WATCHLIST_KEY)
    index = await rs.get_json_strict(dd.REPORT_INDEX_KEY)
    return list(watchlist or []), list(index or [])


def _subjects(index: list[dict]) -> set[str]:
    out: set[str] = set()
    for r in index:
        if not isinstance(r, dict):
            continue
        # R-F3505 — `entity_name` is the field the report index ACTUALLY carries
        # (verified live: keys are canonical_entity_id, entity_name, entity_type,
        # jurisdiction, run_id, severity, user_id, ...). The first version of
        # this matcher looked only for subject/company_name/target_name/name/
        # entity — none of which exist — so all 24 reports contributed ZERO
        # subjects and all 8 watchlist entries looked orphaned. Running --apply
        # on that would have deleted the entire watchlist.
        #
        # Same producer/consumer field-mismatch as
        # memory/producer-consumer-no-carrier-defect: grep what the WRITER
        # emits, never assume the reader's field names.
        for key in ("entity_name", "subject", "company_name", "target_name",
                    "name", "entity"):
            v = str(r.get(key) or "").strip().lower()
            if v:
                out.add(v)
    return out


async def main(apply: bool) -> int:
    from aria_service.intel import dd_orchestrator as dd

    try:
        watchlist, index = await _load()
    except Exception as exc:
        print(f"ABORTED — could not read the live state: {type(exc).__name__}: {exc}")
        print("This is NOT 'nothing to do'. Re-run once the state store is healthy.")
        return 3
    print(f"watchlist entries : {len(watchlist)}")
    print(f"report index rows : {len(index)}")

    if watchlist and not index:
        # A watchlist with entries but an unreadable/empty index is the shape of
        # a bad read, not of a fully-orphaned watchlist. Refuse rather than
        # propose clearing everything.
        print("\nREFUSING: the report index is empty while the watchlist is not.")
        print("That is far more likely a failed read than genuine total orphaning.")
        print("Nothing proposed, nothing removed.")
        return 2

    known = _subjects(index)
    if index and not known:
        # Every report yielded no identifiable subject -> the field names are
        # wrong, not the data. Proposing removals from this would clear the
        # whole watchlist.
        print("")
        print("REFUSING: read {} reports but could not extract a single "
              "subject name from them.".format(len(index)))
        print("That is a field-name mismatch in this script, not genuine orphaning.")
        print("Nothing proposed, nothing removed.")
        return 4
    orphans, unowned = [], []
    for w in watchlist:
        if not isinstance(w, dict):
            continue
        name = str(w.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in known:
            continue
        (orphans if str(w.get("user_id") or "").strip() else unowned).append(w)

    print(f"\nORPHANED (report deleted, still monitored): {len(orphans)}")
    for w in orphans:
        print(f"  - {w.get('name')!r}  owner={w.get('user_id')!r}  "
              f"added={w.get('added_at') or w.get('created_at') or '?'}")

    if unowned:
        print(f"\nOWNER-LESS orphans — REPORTED ONLY, never auto-removed: {len(unowned)}")
        print("  An unscoped delete is the exact IDOR-write R-F2401 closed, so these")
        print("  need an explicit operator decision per entry.")
        for w in unowned:
            print(f"  - {w.get('name')!r}")

    if not apply:
        print("\nDRY RUN — nothing was changed.")
        print("Re-run with --apply to remove the OWNED orphans listed above.")
        return 0

    if not orphans:
        print("\nNothing to remove.")
        return 0

    print(f"\nAPPLYING — removing {len(orphans)} owned orphan(s)...")
    removed = failed = 0
    for w in orphans:
        name, owner = str(w.get("name")), str(w.get("user_id") or "")
        try:
            res = await dd.remove_from_watchlist(
                name, user_id=owner,
                user_email_domain=str(w.get("user_email_domain") or ""))
            # R-F3503 — ok now means "it will no longer be re-screened", so this
            # is a real check rather than a status-code reading.
            if res.get("ok") and int(res.get("removed") or 0) > 0:
                removed += 1
                print(f"  removed {name!r}")
            else:
                failed += 1
                print(f"  NOT removed {name!r}: {res.get('reason') or res}")
        except Exception as exc:
            failed += 1
            print(f"  ERROR removing {name!r}: {exc}")

    print(f"\nremoved={removed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually remove owned orphans (default: dry run only)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.apply)))
