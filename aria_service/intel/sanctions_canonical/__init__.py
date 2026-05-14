"""
ARIA Canonical Sanctions Cache — R-F526.

Local, daily-refreshed cache of authoritative sanctions lists. Built
to eliminate the false-positive HARD STOP class observed on
2026-05-14 Swisscraft Aviation DD, where dd_orchestrate's fuzzy
matcher hit "Michele Zagaria" (Casalesi Camorra clan boss, Italy)
against a Swiss aviation entity with zero geographic, corporate, or
thematic overlap, and issued a HARD STOP that would have killed a
legitimate deal.

The fix shape inherits from R-F518's entity-overlap gate
(reasoning_library): a NAME HIT alone is never sufficient — the
match must also overlap on jurisdiction OR address-country OR
explicit alias confirmation before being promoted to a HARD STOP
verdict.

Architecture
════════════
  - Per-source parsers (ofac_sdn, eu_consolidated, ...) load
    upstream files into the canonical store.
  - store.py is the SQLite schema + write/read helpers.
  - lookup.py is the unified `check_sanctions(name, ...)` API.

Sources
═══════
  OFAC SDN:        https://www.treasury.gov/ofac/downloads/sdn_enhanced.xml
  EU Consolidated: https://webgate.ec.europa.eu/fsd/fsf/public/files/
                   csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw
  (UK OFSI + UN SC + others added in follow-up R-numbers.)

Refresh policy: daily 06:00 UTC via scripts/refresh_sanctions.py +
autonomous task. Each parse overwrites that source's rows
atomically (DELETE-then-INSERT in a transaction).
"""
from .lookup import check_sanctions, get_cache_status

__all__ = ["check_sanctions", "get_cache_status"]
