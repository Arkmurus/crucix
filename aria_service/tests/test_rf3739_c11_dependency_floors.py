"""R-F3739 — CAPABILITY: C-11 floors that make the vulnerable ranges unsatisfiable.

Same shape as R-F3738 (undici), applied to the two remaining C-11 highs whose
fixes needed no major bump:

  ip-address        HIGH, vulnerable <=10.3.0, installed 10.2.0, fixed 10.4.0
    GHSA: Address4 decodes leading-zero octets as DECIMAL while resolvers decode
          them as OCTAL -> SSRF and trust-boundary bypass
    GHSA: a CIDR suffix suppresses special-use classification -> SSRF bypass
    GHSA: misclassification of IPv4-mapped / NAT64 IPv6 -> SSRF bypass
  socket.io-parser  HIGH, vulnerable 4.0.0-4.2.6, installed 4.2.6, fixed 4.2.7
    GHSA: zero-attachment memory exhaustion

All three ip-address advisories are SSRF / trust-boundary bypasses, which is the
worst class for THIS codebase specifically: ARIA fetches attacker-influenced URLs
on the research and DD paths, so an allow/deny decision that disagrees with the
resolver is directly reachable rather than theoretical.

WHY FLOORS. Documented in full in test_rf3738_undici_floor.py: a lockfile is
regenerable by anyone and only records what was resolved ONCE, whereas a declared
floor makes the vulnerable range unsatisfiable by construction. Lowering a floor
is a deliberate act and should fail CI.

Run: python -m pytest aria_service/tests/test_rf3739_c11_dependency_floors.py -v
"""
from __future__ import annotations

import json
import re

import pytest

from ._source_probe import repo_path

#: package -> (declared floor must be >= this, highest version still vulnerable)
FLOORS = {
    "ip-address": ((10, 4, 0), "<=10.3.0"),
    "socket.io-parser": ((4, 2, 7), "4.0.0-4.2.6"),
    "undici": ((7, 29, 0), "7.0.0-7.28.0"),
    "body-parser": ((2, 3, 0), "2.0.0-2.2.2"),      # R-F3740
}


def _parse(spec: str) -> tuple[int, int, int]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", spec)
    assert m, f"cannot parse a version out of {spec!r}"
    return tuple(int(g) for g in m.groups())  # type: ignore[return-value]


def _pkg() -> dict:
    return json.loads(repo_path("package.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(FLOORS))
def test_the_override_floor_excludes_the_vulnerable_range(name: str):
    floor_min, vuln = FLOORS[name]
    spec = (_pkg().get("overrides") or {}).get(name)
    assert spec, (
        f"the {name} override disappeared — it is what controls transitive "
        f"resolution, and {vuln} carries HIGH advisories"
    )
    assert _parse(spec) >= floor_min, (
        f"{name} floor is {spec!r}; {vuln} is vulnerable. Do not lower below "
        f"{'.'.join(map(str, floor_min))}."
    )


@pytest.mark.parametrize("name", sorted(FLOORS))
def test_the_lockfile_resolved_a_fixed_version(name: str):
    """The floor is the rule; this is the evidence it took effect."""
    floor_min, _ = FLOORS[name]
    lock = json.loads(repo_path("package-lock.json").read_text(encoding="utf-8"))
    found = [
        v.get("version")
        for k, v in (lock.get("packages") or {}).items()
        if k.endswith(f"node_modules/{name}") and v.get("version")
    ]
    assert found, f"no {name} entry in the lockfile"
    for got in found:
        assert _parse(got) >= floor_min, (
            f"lockfile resolved {name} {got}, inside the vulnerable range — run "
            f"`npm install --package-lock-only` after changing the floor"
        )


def test_no_c11_high_regressed_into_a_direct_dependency():
    """A direct dependency invalidates the 'transitive, minor bump' risk call."""
    deps = _pkg().get("dependencies") or {}
    # socket.io IS declared and is the legitimate parent of socket.io-parser;
    # the parser itself must not become direct.
    for name in ("ip-address", "socket.io-parser", "undici"):
        assert name not in deps, (
            f"{name} became a DIRECT dependency — re-assess the upgrade's blast "
            f"radius instead of relying on 'transitive, no usage'"
        )


# ── R-F3740 — the body-parser CVE is only reachable VIA an invalid limit ──────
#
# body-parser <2.3.0 "silently disables size enforcement when the limit value is
# invalid". Measured 2026-08-05: every limit this tree passes is well-formed
# ('500kb', '1mb', '50kb'), so the exploit path was NOT live and the floor bump
# is defence in depth.
#
# The durable risk is a future TYPO, and MEASURED against the real parser it is
# worse than "the limit is ignored". `node -e "bytes.parse(...)"`, 2026-08-05:
#
#   '500kb'   -> 512000     well-formed
#   '500 kb'  -> 512000     ALSO well-formed — bytes allows the space
#   '5ooKB'   -> 5          a FIVE-BYTE cap, from an o-for-0 typo
#   '1mb;'    -> 1          one byte
#   '1 000kb' -> 1          one byte
#   'abc'     -> null       this is the null case body-parser <2.3.0 mishandles
#   ''        -> null
#
# So there are TWO failure classes, not one: a null makes body-parser <2.3.0
# silently disable enforcement (the CVE), while a partially-parseable typo
# silently yields a 1- or 5-byte cap, which the CVE does not cover and no version
# bump fixes. Both are silent, which is what makes them dangerous.
#
# This guard is therefore deliberately STRICTER than `bytes`: it flags anything
# not cleanly well-formed, catching the silently-tiny class as well as the
# silently-disabled one. Same principle as everything else fixed today — a
# control that fails quietly is worse than one that fails loudly.

_LIMIT_RE = re.compile(r"limit:\s*['\"]([^'\"]+)['\"]")
#: Stricter than `bytes` on purpose (see above): digits, optional decimal, one
#: optional space, optional unit — and nothing else. `bytes` would silently
#: accept several of the shapes this rejects, at a 1-byte cap.
_VALID_LIMIT = re.compile(r"^\d+(?:\.\d+)?\s?(?:b|kb|mb|gb|tb|pb)?$", re.I)


def _body_limit_sites() -> list[tuple[str, int, str]]:
    import subprocess
    out = subprocess.run(
        ["git", "grep", "-n", "-E", r"express\.(json|urlencoded)\(\{[^}]*limit",
         "--", "*.mjs", "*.js"],
        cwd=str(repo_path(".")), capture_output=True, text=True,
    ).stdout
    sites = []
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        m = _LIMIT_RE.search(parts[2])
        if m:
            sites.append((parts[0], int(parts[1]), m.group(1)))
    return sites


def test_every_configured_body_limit_is_parseable():
    """A malformed limit silently removes the body-size cap — see above."""
    sites = _body_limit_sites()
    assert sites, (
        "found no express.json({limit: ...}) sites — if the body-size caps were "
        "removed or refactored, this guard needs re-pointing, not deleting"
    )
    bad = [(f, ln, v) for f, ln, v in sites if not _VALID_LIMIT.match(v.strip())]
    assert not bad, (
        "these body limits are not parseable, so size enforcement is SILENTLY "
        f"disabled at each: {bad}"
    )
