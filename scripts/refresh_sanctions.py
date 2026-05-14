"""R-F526 — refresh the canonical sanctions cache.

Pulls the latest data from each source's authoritative URL, parses
it into the canonical SQLite store, and writes a refresh-log row
per source. Idempotent: each source's rows are atomically replaced
under transaction.

Usage
═════
  Manual:
    python -m scripts.refresh_sanctions               # all sources
    python -m scripts.refresh_sanctions ofac_sdn      # one source
  Autonomous task:
    aria_service/autonomous/tasks.yaml entry
    REFRESH-SANCTIONS-CANONICAL invokes this module daily 06:00 UTC.
  HTTP endpoint:
    POST /api/aria/admin/sanctions/refresh — wraps this script for
    operator-triggered refreshes.

Local-files mode
════════════════
The operator can place pre-downloaded files in
/data/sanctions_raw/ (or $ARIA_SANCTIONS_RAW_DIR/) — the refresh
script will pick those up if present and skip the HTTP fetch.
Useful when network egress to Treasury / EU is blocked.

  /data/sanctions_raw/sdn_enhanced.xml
  /data/sanctions_raw/eu_full_sanctions_list.csv
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
import urllib.request

logger = logging.getLogger("aria.refresh_sanctions")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


# ─────────────────────────────────────────────────────────────────
# Per-source bindings (URL + local-filename + loader function)
# ─────────────────────────────────────────────────────────────────

SOURCES: dict[str, dict] = {
    "ofac_sdn": {
        "url": "https://www.treasury.gov/ofac/downloads/sdn_enhanced.xml",
        "local_name": "sdn_enhanced.xml",
        "loader_module": "aria_service.intel.sanctions_canonical.ofac_sdn",
    },
    "eu_consolidated": {
        "url": (
            "https://webgate.ec.europa.eu/fsd/fsf/public/files/"
            "csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
        ),
        "local_name": "eu_full_sanctions_list.csv",
        "loader_module": "aria_service.intel.sanctions_canonical.eu_consolidated",
    },
}


def _raw_dir() -> str:
    """Where to look for pre-downloaded raw files (and where to
    cache downloaded ones)."""
    p = os.environ.get("ARIA_SANCTIONS_RAW_DIR", "").strip()
    if p:
        return p
    if os.path.isdir("/data"):
        d = "/data/sanctions_raw"
    else:
        d = os.path.join(tempfile.gettempdir(), "aria_sanctions_raw")
    os.makedirs(d, exist_ok=True)
    return d


def _download(url: str, dest_path: str, timeout: int = 120) -> bool:
    """Stream-download URL → dest_path. Returns True on success.
    Atomic via .tmp + rename so a half-finished download never
    poisons the parser."""
    tmp = dest_path + ".tmp"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "ARIA/1.0 (compliance refresh; +arkmurus.com)",
                "Accept": "*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
        os.replace(tmp, dest_path)
        logger.info("[refresh] downloaded %s → %s (%d bytes)",
                    url, dest_path, os.path.getsize(dest_path))
        return True
    except Exception as e:
        logger.error("[refresh] download failed for %s: %s", url, e)
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


def refresh_source(source_id: str, *, force_download: bool = False) -> dict:
    """End-to-end refresh for one source.

    Returns a result dict with success flag, rows_loaded, and path
    info. The store's refresh_log is also written by the loader."""
    if source_id not in SOURCES:
        return {"success": False, "error": f"unknown source: {source_id}"}

    spec = SOURCES[source_id]
    local_path = os.path.join(_raw_dir(), spec["local_name"])

    started = time.time()
    have_local = os.path.exists(local_path) and os.path.getsize(local_path) > 0

    if force_download or not have_local:
        ok = _download(spec["url"], local_path)
        if not ok and not have_local:
            return {
                "success": False,
                "source": source_id,
                "error": "download failed and no cached file present",
                "duration_s": round(time.time() - started, 2),
            }

    # Late-import the loader so the script remains importable in
    # environments where aria_service deps aren't installed.
    mod_name = spec["loader_module"]
    try:
        mod = __import__(mod_name, fromlist=["load_from_file"])
        rows_loaded = mod.load_from_file(local_path)
    except Exception as e:
        logger.exception("[refresh] loader failed for %s", source_id)
        return {
            "success": False,
            "source": source_id,
            "error": f"{type(e).__name__}: {e}",
            "duration_s": round(time.time() - started, 2),
        }

    return {
        "success": True,
        "source": source_id,
        "rows_loaded": rows_loaded,
        "local_path": local_path,
        "duration_s": round(time.time() - started, 2),
    }


def refresh_all(*, force_download: bool = False) -> dict:
    """Refresh every configured source. Per-source errors do not
    block the others — partial success is reported per-key."""
    out: dict[str, dict] = {}
    for source_id in SOURCES:
        out[source_id] = refresh_source(source_id, force_download=force_download)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh canonical sanctions cache")
    parser.add_argument("source", nargs="?", default="",
                        help="single source id (default: refresh all)")
    parser.add_argument("--force-download", action="store_true",
                        help="re-download even if a cached file exists")
    args = parser.parse_args(argv)

    if args.source:
        result = refresh_source(args.source, force_download=args.force_download)
        print(result)
        return 0 if result.get("success") else 2

    results = refresh_all(force_download=args.force_download)
    any_failure = False
    for src, r in results.items():
        print(f"  {src}: {r}")
        if not r.get("success"):
            any_failure = True
    return 0 if not any_failure else 2


if __name__ == "__main__":
    sys.exit(main())
