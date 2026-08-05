"""Ground-truth assertions for the census classifier.

Every expectation below is independently verifiable from a manifest or an
HTML tag. A classifier that fails these is not fit to drive a deletion ladder.
"""

import json
import sys
from pathlib import Path

# CLAUDE.md §16: no hardcoded checkout path — derive from this file's location.
REPO = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
c = json.loads((REPO / "docs" / "cure" / "census.json").read_text(encoding="utf-8"))
M = c["modules"]

# (path, must_be_one_of, why)
EXPECT = [
    ("services/wa-listener/aria_wa_listener.mjs", {"DEPLOY-ENTRYPOINT"},
     "Dockerfile.wa:127 CMD [node aria_wa_listener.mjs]; live aria-wa app"),
    ("server.mjs", {"DEPLOY-ENTRYPOINT"},
     "Dockerfile.web:104 CMD [node server.mjs]; package.json start"),
    ("aria_service/main.py", {"DEPLOY-ENTRYPOINT"},
     "aria_service/Dockerfile:194 CMD [python,-m,aria_service.main] (exec form)"),
    ("public/js/app.js", {"SERVED-ASSET"},
     "loaded via <script src> in public/*.html, never imported"),
    ("aria_service/routes/aria.py", {"ACTIVE-STATIC"},
     "included by main.py include_router(aria_router)"),
    ("aria_service/intel/dd_orchestrator.py", {"ACTIVE-STATIC"},
     "supported surface: DD run end-to-end"),
    ("aria_service/intel/state_store.py", {"ACTIVE-STATIC"},
     "core store layer, imported widely"),
]

FORBID_DEAD = [
    "services/wa-listener/aria_wa_listener.mjs",
    "server.mjs",
    "aria_service/main.py",
    "public/js/app.js",
]

fails = []
for path, allowed, why in EXPECT:
    m = M.get(path)
    if m is None:
        fails.append(f"MISSING from census: {path}")
        continue
    got = m["classification"]
    status = "ok " if got in allowed else "FAIL"
    if got not in allowed:
        fails.append(f"{path}: got {got}, expected one of {sorted(allowed)} ({why})")
    print(f"  [{status}] {path:<52} -> {got}")

print()
for path in FORBID_DEAD:
    m = M.get(path)
    got = m["classification"] if m else "MISSING"
    bad = got in ("DEAD-CANDIDATE", "MISSING")
    print(f"  [{'FAIL' if bad else 'ok '}] not-dead: {path:<42} -> {got}")
    if bad:
        fails.append(f"{path} classified {got} — live code on the deletion ladder")


# ── Self-reference contamination guard ────────────────────────────────────
# The census's own reports name every dead module by path. If those reports are
# scanned as evidence, dead code is promoted to ACTIVE-STATIC and the deletion
# ladder silently empties. This actually happened: 109 -> 27 DEAD-CANDIDATE on
# the first re-run after docs/cure/ was committed.
suspicious = [
    f for f, m in M.items()
    if m["classification"] == "ACTIVE-STATIC"
    and m.get("n_importers_non_test", 0) == 0
    and m.get("n_string_hits_non_test", 0) <= 1
    and not m.get("entrypoint_refs")
    and not m.get("asset_refs")
]
print(f"  [{'FAIL' if len(suspicious) > 40 else 'ok '}] self-reference guard: "
      f"{len(suspicious)} module(s) held ACTIVE-STATIC by a single string hit")
if len(suspicious) > 40:
    fails.append(
        f"{len(suspicious)} modules are ACTIVE-STATIC on one string hit and zero "
        "importers — the sweep is probably reading the census's own output "
        "(docs/cure/). Check SELF_OUTPUT_PREFIXES in census.py."
    )
    for f in suspicious[:5]:
        print("       e.g.", f)

print()
if fails:
    print(f"VALIDATION FAILED ({len(fails)}):")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("VALIDATION PASSED — classifier agrees with all known ground truth")
