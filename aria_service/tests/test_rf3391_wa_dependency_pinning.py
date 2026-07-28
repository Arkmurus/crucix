"""R-F3391 — the WA listener installed `baileys: "latest"` with no lockfile, so production ran an unchosen release candidate.

THE INHERITED ITEM WAS "CRITICAL protobufjs RCE". THE INVESTIGATION DISPROVED IT.
`npm audit` on the dev tree reports protobufjs <=7.6.2 (critical, arbitrary code
execution) reachable via libsignal 2.0.1, which hard-pins protobufjs 6.8.8. But
the dev tree is not what ships. Measured inside the LIVE aria-wa container:

    baileys 7.0.0-rc13   protobufjs 7.6.5   (single copy, no nested vulnerable one)

The advisory covers <=7.6.2, so PRODUCTION IS NOT VULNERABLE. The vulnerable copy
exists only in the root dev tree, which `Dockerfile.wa` never installs.

WHAT THE INVESTIGATION FOUND INSTEAD IS WORSE, BECAUSE IT IS ONGOING.
`services/wa-listener/package.json` declared:

    "@whiskeysockets/baileys": "latest"

with NO package-lock.json, and `Dockerfile.wa` ran `npm install --omit=dev`. Three
consequences, all live:

  1. NON-REPRODUCIBLE BUILDS. Every image build resolves `latest` afresh. The same
     commit produces different software on different days, so "what is deployed"
     is not answerable from the repo.
  2. PRODUCTION RUNS A RELEASE CANDIDATE. 7.0.0-rc13 — a pre-release nobody chose,
     reviewed or approved. It arrived because it happened to be `latest` at build
     time.
  3. DEV AND PROD ARE ON DIFFERENT MAJORS. The dev tree has 6.7.23; production has
     7.0.0-rc13. Local testing of the WhatsApp path does not reflect production.

For a system whose WhatsApp tier handles customer communications, an unreviewed
dependency arriving straight into production is a supply-chain exposure in its own
right — a compromised or merely broken publish reaches users with nothing in the
way.

THE FIX IS THE SAFEST ONE AVAILABLE: pin to EXACTLY the version already proven
working in production (7.0.0-rc13), add a lockfile, and switch the image to
`npm ci`. That is a ZERO-behaviour-change fix — the same bytes that run today —
while making every future change deliberate and reviewable. Upgrading off the rc
becomes a separate, deliberate decision rather than a side effect of rebuilding.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WA_PKG = ROOT / "services" / "wa-listener" / "package.json"
WA_LOCK = ROOT / "services" / "wa-listener" / "package-lock.json"
DOCKERFILE = ROOT / "Dockerfile.wa"


def _deps() -> dict:
    return json.loads(WA_PKG.read_text(encoding="utf-8")).get("dependencies", {})


# ── no floating specifiers on the WhatsApp tier ───────────────────────────

def test_baileys_is_pinned_to_an_exact_version():
    spec = _deps().get("@whiskeysockets/baileys", "")
    assert spec, "baileys missing from the wa-listener manifest"
    assert spec != "latest", (
        "`latest` means production runs whatever npm served at build time — "
        "that is how a release candidate reached prod unreviewed"
    )
    assert spec[0].isdigit(), f"expected an exact version, got {spec!r}"


def test_no_dependency_uses_latest():
    floating = [k for k, v in _deps().items() if str(v).strip() == "latest"]
    assert not floating, f"floating `latest` specifiers on the WA tier: {floating}"


def test_pin_matches_what_is_actually_running():
    """Pinned to the version VERIFIED live in the aria-wa container, so this is a
    zero-behaviour-change fix rather than an untested upgrade."""
    assert _deps().get("@whiskeysockets/baileys") == "7.0.0-rc13"


# ── a lockfile, and a build that honours it ───────────────────────────────

def test_lockfile_exists():
    assert WA_LOCK.exists(), (
        "no lockfile: even a pinned top-level dep resolves its transitive tree "
        "freshly on every build"
    )


def test_lockfile_agrees_with_the_manifest():
    lock = json.loads(WA_LOCK.read_text(encoding="utf-8"))
    pkgs = lock.get("packages") or {}
    entry = pkgs.get("node_modules/@whiskeysockets/baileys") or {}
    assert entry.get("version") == _deps()["@whiskeysockets/baileys"], (
        f"lockfile has {entry.get('version')!r}, manifest pins "
        f"{_deps()['@whiskeysockets/baileys']!r} — npm ci would refuse"
    )


def test_dockerfile_uses_npm_ci_not_npm_install():
    src = DOCKERFILE.read_text(encoding="utf-8")
    assert "npm ci" in src, (
        "`npm install` ignores the lockfile for resolution decisions; `npm ci` "
        "installs exactly what was reviewed"
    )
    assert "cd services/wa-listener && npm install" not in src


def test_dockerfile_copies_the_lockfile():
    """npm ci fails without it — a build that cannot see the lockfile cannot
    honour it."""
    src = DOCKERFILE.read_text(encoding="utf-8")
    assert "package-lock.json" in src, "the lockfile is never COPYed into the image"


# ── the security claim, asserted against the lockfile ────────────────────

def test_locked_protobufjs_is_not_the_vulnerable_range():
    """The inherited item. Advisory covers protobufjs <=7.6.2; assert every copy
    the lockfile pins is above it, so the claim is checked rather than believed."""
    lock = json.loads(WA_LOCK.read_text(encoding="utf-8"))
    versions = [
        (path, meta.get("version"))
        for path, meta in (lock.get("packages") or {}).items()
        if path.endswith("node_modules/protobufjs") and meta.get("version")
    ]
    assert versions, "no protobufjs in the lockfile — cannot verify the advisory"

    def _tuple(v):
        return tuple(int(x) for x in v.split("-")[0].split(".")[:3])

    bad = [(p, v) for p, v in versions if _tuple(v) <= (7, 6, 2)]
    assert not bad, f"lockfile pins a vulnerable protobufjs (<=7.6.2): {bad}"
