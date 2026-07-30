# R-F3466 — BS 7858 single-standard vetting review

- BSI lists BS 7858:2019 as the current release. Do not invent an amendment or
  present internal ruleset revisions as different British Standards.
- Public product surface now exposes one standard only. Historical pack
  objects remain internal because manifest hashes must replay; deleting or
  mutating them would corrupt auditability.
- A boolean `interview_done` did not prove clause 7.3.4. The structural fix is
  `interview_date < offer_date`, with missing proof as an action and a failed
  sequence as a blocker.
- `COVERED_BY_STAT_DEC` was a bypass: it made timeline coverage green without
  enforcing a declaration document, top-management approval or the aggregate
  duration limit. R-F3466 adds all three controls.
- “Real time” means re-reading the authoritative assessment, document state
  and request ledger while a visible file is open. The page now refreshes
  every 30 seconds and prevents overlapping refreshes.
- A case engine cannot prove organization-level duties. The clause register
  must disclose top-management, training, outsourcing, ancillary-access and
  transfer controls as partial/operator/not-encoded until first-class records
  exist. Never turn READY_FOR_CONTROLLER_REVIEW into “certified” or
  “BS 7858 compliant”.
- Verification: 216 vetting-selected Python tests passed; 35 existing vetting
  UI tests passed; the new page capability test passed; whole-tree compileall
  passed.
