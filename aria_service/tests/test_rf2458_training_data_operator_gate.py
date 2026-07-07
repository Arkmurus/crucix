"""R-F2458 — /training-data/library-export + /export dump up to 5000 Q&A tuples
aggregated across ALL tenants' chats with no user scoping. They must be
operator-token only (the shared service token held by aria-web / aria-wa must not
reach them). The gate is membership in _OPERATOR_ONLY_RE, which require_aria_token
enforces (operator token required for matching paths).
"""
from __future__ import annotations

from aria_service.routes.aria import _OPERATOR_ONLY_RE


def test_training_data_exports_are_operator_only():
    assert _OPERATOR_ONLY_RE.search("/api/aria/training-data/library-export"), \
        "training-data/library-export must be operator-gated"
    assert _OPERATOR_ONLY_RE.search("/api/aria/training-data/export"), \
        "training-data/export must be operator-gated"


def test_normal_reads_not_over_gated():
    # Guard against the regex accidentally gating ordinary tenant reads.
    assert not _OPERATOR_ONLY_RE.search("/api/aria/dd/report/abc123")
    assert not _OPERATOR_ONLY_RE.search("/api/aria/dd/vault/search")
    assert not _OPERATOR_ONLY_RE.search("/api/aria/health")


if __name__ == "__main__":
    test_training_data_exports_are_operator_only()
    print("PASS test_training_data_exports_are_operator_only")
    test_normal_reads_not_over_gated()
    print("PASS test_normal_reads_not_over_gated")
    print("ALL PASS")
