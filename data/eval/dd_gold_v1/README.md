# DD gold set v1

This directory contains release-gating due-diligence fixtures.  It deliberately
separates deterministic frozen replays from live source smoke tests:

- `frozen` cases pin observations and exact expected findings.  They may gate a
  release.
- `synthetic_negative_control` cases exercise deterministic absence and status
  handling without making claims about a real person or company.
- `blocked_missing_frozen_evidence` cases document a required regression case
  that cannot honestly be treated as gold until its source observations and
  adjudicated expected findings have been supplied.

Every expected finding must contain a rationale and one or more repository-local
evidence references.  A URL alone is not a frozen observation.  Live smoke tests
belong in a separate suite and check response shape and source drift, not exact
historical findings.
