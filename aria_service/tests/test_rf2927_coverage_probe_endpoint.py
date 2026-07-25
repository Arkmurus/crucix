"""R-F2927 — the coverage probe must run IN the server process.

registry_coverage persists to the state store, which is owned by the running server
process. A side-car `python -m scripts.probe_registry_liveness` cannot reach it —
verified live on aria-intel 2026-07-23:

    _load()          -> None   ("state_store: no connection (reconnect in progress)")
    record_outcome() -> False

record_outcome then SKIPS the write rather than clobbering the durable key. That is the
CORRECT behaviour (a transient read failure must never wipe state), and it is exactly
why the standalone sweep probed all 27 adapters, printed 27 results, and left the vault
reading 0 live / 27 unproven. The observations were never refused by the registries;
they were refused by the store, because the sweep ran in the wrong process.

GB is the control: the one row that DID go live was recorded in-process by a real DD
run through the R-F2918 branch.
"""
from __future__ import annotations

import re
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[1] / "routes" / "aria.py"
SRC = ROUTES.read_text(encoding="utf-8", errors="ignore")


def _endpoint_body() -> str:
    start = SRC.find("async def registry_coverage_probe_ep")
    assert start > -1, "the in-process coverage probe endpoint is missing"
    end = SRC.find("class RagSearchRequest", start)
    return SRC[start:end if end > start else start + 6000]


def test_rf2927_probe_endpoint_exists_and_is_token_gated():
    """It performs REAL outbound lookups, unlike the read-only GET, so it must not be
    open."""
    assert '@router.post("/registry/coverage/probe"' in SRC
    idx = SRC.find('@router.post("/registry/coverage/probe"')
    decorator = SRC[idx:idx + 200]
    assert "Depends(require_aria_token)" in decorator, "the probe endpoint is not token-gated"


def test_rf2927_probe_never_writes_liveness_directly():
    """Liveness must stay a CONSEQUENCE of a real lookup.

    The endpoint may call lookup_entity and read coverage back, but it must never call
    record_outcome itself — that would let a probe assert liveness rather than earn it.
    """
    body = _endpoint_body()
    assert "lookup_entity(" in body, "the probe does not actually exercise the adapters"
    # Match a real INVOCATION, not the docstring that explains why side-car writes were
    # refused (it quotes "record_outcome() -> False"). A bare substring check would flag
    # the rationale that documents the rule — the same false positive the DownloadFile
    # ban hit, where prose naming a pattern tripped the test banning the pattern.
    assert "rc.record_outcome(" not in body, (
        "the probe writes coverage directly — liveness must be recorded by the normal "
        "lookup path, never asserted by the prober"
    )
    assert "await record_outcome(" not in body


def test_rf2927_stub_and_gleif_are_not_reported_as_live():
    """The two ways a probe can overstate coverage, both handled explicitly."""
    body = _endpoint_body()
    assert 'startswith("gleif")' in body, "a GLEIF fallback would be counted as the national registry"
    assert 'endswith("_stub")' in body, "a stub would be counted as a registry read"
    assert '"fallback_gleif"' in body
    assert '"stub"' in body


def test_rf2927_no_fabricated_identifiers():
    """Only identifiers VERIFIED against the live adapter may appear.

    A guessed registration number produces a confident wrong verdict — the exact
    failure this surface exists to prevent. Two are verified:
      BR 33000167000101 -> brazil_cnpj / PETROLEO BRASILEIRO S A PETROBRAS
      SK 31322832       -> slovakia_orsr
    """
    body = _endpoint_body()
    ids = set(re.findall(r'"(\d{6,20})"', body))
    assert ids <= {"33000167000101", "31322832", "520035874"}, (
        f"unverified registration number(s) in the probe table: {ids}"
    )


def test_rf2927_name_only_miss_is_reported_as_inconclusive():
    """Several adapters require an identifier and return None without one. Calling that
    a verdict on the adapter is how BR was wrongly reported as dead."""
    body = _endpoint_body()
    assert "inconclusive" in body, (
        "a name-only miss is reported as a plain failure — it must say it is "
        "inconclusive for identifier-based adapters"
    )


def test_rf2927_probe_does_not_run_on_a_schedule():
    """It makes real outbound calls to government registries. It must be operator- or
    caller-triggered only — never a boot hook or a cron."""
    for marker in ("registry_coverage_probe_ep", "/registry/coverage/probe"):
        for scheduler in ("add_job", "cron", "@repeat_every", "create_task("):
            window_hits = [
                m.start() for m in re.finditer(re.escape(marker), SRC)
            ]
            for pos in window_hits:
                near = SRC[max(0, pos - 400):pos + 400]
                assert scheduler not in near, (
                    f"the probe endpoint appears wired to {scheduler} — it performs real "
                    "outbound registry calls and must stay caller-triggered"
                )
