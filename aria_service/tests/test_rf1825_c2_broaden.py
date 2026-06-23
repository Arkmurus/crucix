"""R-F1825 — C2-broaden: route user/discovery-controlled URL fetchers through safe_get.

The SAST review (R-F1824 guard) surfaced 7 SSRF candidates beyond the 2 fixed in C2.
Triage confirmed 4 fetch genuinely attacker-influenceable URLs (crawl/research/citation);
the other 3 (registry_adapters, portal_registry, web_integrity_agent) fetch constant/
config hosts (not SSRF — verified by reading). The 4 now go through url_safety.safe_get
(validates url + every redirect hop; follow_redirects off). safe_get itself is
capability-tested in R-F1814; this asserts the wiring + that the SSRF guard is clean.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
FETCHERS = ["web_crawler.py", "crawl_enhancements.py", "deep_researcher.py", "citation_audit.py"]


def test_user_url_fetchers_use_safe_get():
    for f in FETCHERS:
        s = (REPO / "aria_service/intel" / f).read_text(encoding="utf-8")
        assert "url_safety" in s and "safe_get(" in s, f"{f} not routed through safe_get"


def test_ssrf_guard_clean_on_fixed_fetchers():
    sys.path.insert(0, str(REPO / "scripts"))
    from pre_commit_checks import check_ssrf_fetch_boundary
    files = [REPO / "aria_service/intel" / f for f in FETCHERS]
    assert check_ssrf_fetch_boundary(files) == []
