# R-F3235 / R-F3236 — controlled watchlist enrollment

- Root cause: `watchlist.html` accepted a free-text name, so its DD/Vetting query
  prefill was advisory rather than an enforceable provenance boundary.
- Fix: Add Entity now loads the authenticated user's DD reports and vetting
  cases, presents source Type first, then a source-filtered Entity select, and
  posts only the selected normalized record plus the chosen review cycle.
- Failure behavior: malformed/blank records are excluded; repeated DD runs for
  one canonical entity are collapsed; one unavailable source fails closed while
  the other remains usable; a value not present in the fetched candidate set is
  rejected.
- Existing Monitor buttons remain source-bound through `source` + `source_ref`
  and preselect the matching record after the destination page verifies it
  exists in the current owner-scoped list.
- Testing lesson: the blocking-dialog static test matched the prose
  `delete alert (HTTP ...)` inside a Toast string. R-F3236 strips string literals
  before checking executable source and includes positive/negative guard tests.
- Browser limitation: no in-app or Chrome browser was connected during local
  verification, so authenticated visual interaction must not be claimed from
  this session.
