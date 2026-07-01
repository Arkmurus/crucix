"""R-F2243 — the coder RAG read/record endpoints must NOT be operator-tier.

The CLI (`aria`) queries the live coding RAG with the INTERNAL token (R-F2161) and
records fixes/failures with it (R-F2162). A regression put `coder/` wholesale into
_OPERATOR_ONLY_RE, so `/coder/rag/query` returned 403 to the CLI → the CLI fell back
to the in-process RAG, which cold-loads the embedder for 100s+ on the operator's
machine = "aria not responding" at launch. The fix exempts `coder/rag/` while
keeping the coder CONTROL endpoints operator-only.
"""
from __future__ import annotations

from aria_service.routes.aria import _OPERATOR_ONLY_RE


def test_coder_rag_read_and_record_are_not_operator_only():
    # the CLI's internal token must be accepted on these (the launch fix)
    assert _OPERATOR_ONLY_RE.search("/api/aria/coder/rag/query") is None
    assert _OPERATOR_ONLY_RE.search("/api/aria/coder/rag/record") is None


def test_coder_control_endpoints_stay_operator_only():
    for p in ("/api/aria/coder/gaps", "/api/aria/coder/fix", "/api/aria/coder/deploy"):
        assert _OPERATOR_ONLY_RE.search(p) is not None, f"{p} must stay operator-tier"


def test_other_control_plane_unchanged():
    for p in ("/api/aria/autonomous/start", "/api/aria/cost/set-cap",
              "/api/aria/self/deploy", "/api/aria/admin/purge"):
        assert _OPERATOR_ONLY_RE.search(p) is not None
    # ordinary reads are never operator-tier
    assert _OPERATOR_ONLY_RE.search("/api/aria/chat") is None
