# Session record — 2026-07-26/27 · ARIA Vetting module, end to end

**Scope:** integrate a supplied `aria-vetting` Phase 0 engine into the platform,
retire `status.html`, make it legally coherent, and remove the autonomous
signup agent + 2captcha.

**Shipped:** 46 R-numbers (R-F3136 → R-F3204, non-contiguous — the peer agent
held others). Live on aria-intel `1a7e7ff3` and aria-web `ed341b72`;
`7d188f09` committed and awaiting deploy.

---

## What the module is now

`aria_service/vetting/` — engine vendored unmodified apart from imports, so
`rules.assess()` stays a pure function of `(case, pack, as_of)`: no I/O, no
network, no clock read. Everything else sits strictly outside it.

- **Tenancy** is in the sqlite PRIMARY KEY, so an un-scoped read is
  inexpressible. Reads fail closed to **404, never 403** — confirming a case
  exists discloses that a named person is under screening by a named employer.
- **Erasure** by per-case AES-256-GCM crypto-shredding. The evidence store is
  append-only (it is DD's tamper-evident spine); encrypting before it sees the
  bytes and destroying the key at disposal makes Art. 17 erasure effective
  without weakening that store.
- **Art. 10 gate**: no criminal-offence data without a recorded DPA 2018 Sch. 1
  condition + APD, enforced on WRITE and scoped to the case's JURISDICTION.
- **Art. 22**: decisions must name a human; adverse ones need a reason and
  four-eyes. The engine status at decision time is stored, so *whether anyone
  ever departs from the recommendation* is auditable.
- **Portal**: scoped applicant/referee links (hashed, expiring, upload-only),
  QR, email/WhatsApp share. `routes/vetting_portal.py` is a SEPARATE module
  precisely because it is unauthenticated — "is this endpoint authed?" is
  answered by which file it is in.

## BS 7858 — verified against the operator's licensed copy

- **31 days is CORRECT.** 7.7 says "no unverified periods greater than 31 days";
  the engine flags at 32+. I wrongly called this a defect first. A stricter
  house limit is a per-contract setting, not a correction to the standard.
  A test pins 31 so a future reading of "30" cannot quietly tighten it.
- Pack **v1.2.0** published as a NEW VERSION (hashes are pinned in every case
  manifest; editing 1.1.0 in place would break replay). Added: SIA licence
  expiry + public-register check (7.3.2 a)8, 7.4 c)1), the seven public-record
  elements (7.4 f), who-examined capture, and **two-or-more documentary items**
  where no direct reference exists (7.7 b) — the engine had accepted one.
- Copyright: rules and clause numbers only. **Never** store the standard's text.

## Defects found in my own shipped code

Worth recording because each is a repeatable class:

1. **Stale cached verdict** — caching `last_status` for the card view
   reintroduced a false clean: a case assessed IN_PROGRESS/0 blockers still
   showed that after a blocker was added. Fixed in `store.save()` with
   `mark_stale` defaulting **True**, so a future writer that forgets gets the
   SAFE outcome.
2. **Create was not Art. 10 gated** while PATCH and upload were — data entered
   ungated AND the case then became permanently un-editable.
3. **Detector fooled by shape** — `holds_criminal_offence_data` used bare
   `getattr`, so a dict-shaped `inputs` made the gate silently miss. Reachable
   only via statement ordering in one route, which is not a guarantee.
4. **`sendGenericEmail`/`sendRawEmail` do not exist** — optional-chaining
   fallback would have reported "sent" while sending nothing (§3b).
5. Tests appended AFTER `process.exit()` reported "pass" while never running.

## Live monitoring (5 cycles, 260 lines)

Clean. Endpoint self-check 9/9 twice, source seed `errors=0`, no tracebacks,
no restart loops. The only `error` matches were strings like `errors=0`.

## Retired this session

- **Autonomous portal signup** — all four entry points (scheduler tick, boot,
  hourly loop, manual route→410). `_portal_registration_enabled()` returns
  False unconditionally so an env var cannot restart it. Modules deleted.
- **2captcha** — solver deleted, six call sites neutralised, tests retired.
- **`portal_registry.py` deliberately KEPT**: DD imports
  `lookup_contracts_by_uei`. Deleting it to remove signup would have taken a
  working DD feature with it.

## Open next session

1. **Referee details on `CareerEntry`** (operator's last request): the share
   dialog makes the officer TYPE the referee, but the applicant already
   nominated them on the application form. Put name/email/phone/title on the
   career entry so selecting a period auto-fills the recipient; keep manual
   entry for gap referees; flag a period with no nominated referee as its own
   action. Not started.
2. Surface the request ledger + sighting controls in the UI (backend live).
3. Counsel: GB Sch. 1 condition, Anthropic transfer mechanism, DPIA sign-off,
   EU AI Act classification. **Do not market into the EU** until the gaps in
   `docs/vetting/eu_ai_act_assessment.md` close.

## Two-agent hazard — cost real time

Uncommitted `store.py`, `main.py` and two test files were **reverted mid-edit**
by the peer's reset (their `2b9036d2` landed between). `git add -A` twice tried
to sweep their in-flight R-F3200/R-F3201 into my commit. Separate worktrees
would remove the class entirely. Until then: commit early, re-verify before
building on an edit, and never `git add -A`.
