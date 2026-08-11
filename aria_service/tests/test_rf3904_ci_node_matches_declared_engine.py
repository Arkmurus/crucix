"""R-F3904 — CI pinned Node 20, below the project's declared minimum, and ran ZERO tests.

`package.json` declares `"engines": {"node": ">=22"}` and both production images are
`node:22-slim`. ci.yml's web-security job pinned `node-version: "20"`, so CI tested
on a runtime the project does not support.

The consequence was not a subtle incompatibility. `npm test` is
`node --test ... "test/**/*.test.mjs"`, and Node 20's test runner DOES NOT EXPAND
GLOBS (Node 22+ does), so it reported

    Could not find '/home/runner/work/crucix/crucix/test/**/*.test.mjs'

emitted no TAP summary, and the suite gate refused with "could not parse TAP totals"
on 8+ consecutive commits — both agents' work, the entire Node tier ungated — while
the identical command passed locally with 1833 tests.

IT TOOK R-F3903 TO FIND IT. The gate had been refusing correctly and SILENTLY for
days; one run after it was taught to print what it received, the cause was a single
unmistakable line. That is the whole argument for making guards explain themselves.
"""
from __future__ import annotations

import json
import re

from aria_service.tests._source_probe import repo_path


def _declared_minimum() -> int:
    engines = json.loads(repo_path("package.json").read_text(encoding="utf-8"))["engines"]
    return int(re.search(r"(\d+)", engines["node"]).group(1))


def test_every_ci_node_version_meets_the_declared_engine():
    """A workflow testing below `engines.node` is testing something production never
    runs — and here it silently ran nothing at all."""
    minimum = _declared_minimum()
    offenders = []
    for wf in sorted(repo_path(".github/workflows").glob("*.yml")):
        for m in re.finditer(r"node-version:\s*['\"]?(\d+)", wf.read_text(encoding="utf-8")):
            if int(m.group(1)) < minimum:
                offenders.append(f"{wf.name}: node {m.group(1)} < engines>={minimum}")
    assert not offenders, (
        f"CI would test below the declared engine: {offenders}. Node 20 does not "
        f"expand the test glob, so `npm test` runs ZERO tests and the suite gate "
        f"cannot parse a TAP summary (R-F3904).")


def test_the_test_script_still_relies_on_glob_expansion():
    """The reason the version floor is load-bearing. If npm test ever stops using a
    glob, this test should be revisited rather than silently kept."""
    pkg = json.loads(repo_path("package.json").read_text(encoding="utf-8"))
    assert "**" in pkg["scripts"]["test"], (
        "npm test no longer uses a glob — the Node>=22 floor was justified by "
        "runner-side glob expansion; re-derive it before relaxing anything")


def test_the_declared_engine_matches_the_production_image():
    """A floor nobody ships against is a floor that will drift."""
    minimum = _declared_minimum()
    for df in ("Dockerfile.web", "Dockerfile.wa"):
        text = repo_path(df).read_text(encoding="utf-8")
        versions = [int(v) for v in re.findall(r"FROM node:(\d+)", text)]
        assert versions, f"{df} pins no node version"
        assert all(v >= minimum for v in versions), (
            f"{df} runs node {versions} below engines>={minimum}")
