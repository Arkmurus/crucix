# R-F3301..R-F3315 — landing page: styling, public access, hero and footer

Operator-driven session on the aria-web public surface. Three deploys, all
verified live by content probe rather than by release number.

## Shipped

| R | What | Commit |
|---|---|---|
| R-F3301 | Access-request form styled (it inherited nothing) | `b81afaab` |
| R-F3302 | Hero copy, free-account note removed, em dashes cleared | `b81afaab` |
| R-F3303 | Model card section ids + orphaned section 12 fixed | `b81afaab` |
| R-F3304 | Privacy/Terms moved to a white, landing-matching canvas | `b81afaab` |
| R-F3305 | `style.css` unclosed `@media` closed | `b81afaab` |
| R-F3308 | Footer inherited spacing zeroed | `ba9a434c` |
| R-F3309 | Design-partner entry point on the landing nav | `ba9a434c` |
| R-F3310 | Auth palette centralised in `public/css/auth.css` | `ba9a434c` |
| R-F3311 | Public pages no longer bounce anonymous visitors to sign-in | `ba9a434c` |
| R-F3312 | New hero photo, built to the old hero's exact box | `8e550e6d` |
| R-F3313 | Footer rebuilt as a single-line flex bar | `8e550e6d` |
| R-F3315 | `docs/asset_licences.md` records the hero's provenance | `818c6185` |

Live: **aria-web version 412, `build_rev=8e550e6d`**. R-F3315 is docs-only and
correctly needs no redeploy.

## The two that were not what they looked like

- **R-F3311 was not a styling bug.** "Read the model card" led to a login wall.
  `model-card.html` is public and says so in a comment, but calls
  `Sidebar.init` → `Auth.me` → `API.get('/api/auth/me')`, and `API.get` treated
  every 401 as a session expiry → `Auth.logout()` → `/signin.html`. Anonymous
  `GET /api/auth/me` returns 401 (probed live). Fixed at the choke point: a 401
  is an expiry only when a token was sent. An expired token still logs out, and
  that control is asserted separately so the fix cannot become a blanket opt-out.
  `app.js?v=8` → `v=9` across 24 pages, or returning visitors keep the old file.
- **R-F3301's form inherited nothing.** `class="subscribe-form lead-form"`, but
  no stylesheet defines `.subscribe-form` and every Pelican input/button rule is
  scoped under a `.form` ancestor the element lacks. Underneath it, R-F3305:
  `@media (min-width: 240px)` at `style.css:243` was never closed, so ~1,700
  rules were that at-rule's body. It rendered fine, which is why it survived.

## Corrections to my own work

- **R-F3308 fixed the footer symptom, not the cause.** The wrap came from
  `col-md-4` rigid third-widths, and R-F3302 (mine) had widened the labels until
  they no longer fit. R-F3313 replaced the grid so wrapping is structurally
  impossible rather than unlikely at widths I happened to check.
- **`landing_claim_truth.mjs` pinned exact hero copy** the operator asked to
  rewrite. Converted to the property (hero must name source, confidence, limit).

## Verification approach

Guards assert properties, not wording. The style guard resolves declarations
through the real ancestor chain (a grep for class names would have passed
throughout) and is itself verified against the known-broken stylesheet. The
R-F3311 guard executes `app.js` in a `node:vm` and asserts on navigation. The
hero guard reads intrinsic dimensions from the PNG IHDR and JPEG start-of-frame,
because `img-fluid` means the file's aspect ratio decides the layout. Each batch
was run against a clean `git worktree` at HEAD and judged on failure-set diff:
no new failures in any of the three.

## Notes for the next session

- **CLAUDE.md §20 names `memory/operator_time_tracker.md` for session close. That
  file has never existed** (no git history). The live convention is
  `memory/session_record_<date>_<r-numbers>.md`. §20 should be corrected.
- `public/about/*.html` are now on the R-F3278 dash guard; the inventory check
  reads that directory, so a new legal page cannot go unenforced silently.
- Open, operator-owned: privacy.html says "Arkmurus Limited", terms.html says
  "ARIA Intelligence Limited". Left alone — a legal-entity decision.
- The purchased hero original is **not** in the repo. Regenerating the crop needs
  it; the parameters are in `docs/asset_licences.md`.
