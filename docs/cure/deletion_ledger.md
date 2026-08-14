# Deletion ledger

Cure Protocol Phase 4. **Nothing on this page has been deleted.** Entries are
candidates with their evidence; each records what is proven and, more importantly,
what is not.

`CLAUDE.md` §26 is binding: the Phase 0.3 runtime overlay has not run, so every
module in the census carries `proof_runtime: UNKNOWN`. The three-proof rule
(static + runtime + test) therefore cannot be satisfied for anything, and the
correct state for every candidate is DORMANT. A candidate is recorded here so the
evidence is accumulated in one place and the eventual deletion is a decision with
a paper trail, not a judgement call made at the moment of the `rm`.

Format per entry: what it is, why it is a candidate, the three proofs and their
current state, the quarantine step that would come first, and the risk of acting
early.

---

## D-01 · unreferenced vendor bundles under `public/` (C-91, R-F4014)

**What.** Front-end libraries served by `express.static` that no served page
loads. Found by the 2026-08-14 aria-web surface audit, which mapped every
`<script src>` across all 32 pages against the files on disk.

| File | Size | Version | Referenced by |
|---|---|---|---|
| `public/vendor/jquery.min.js` | 89,492 B | jQuery 3.5.0 | nothing |
| `public/pelican/assets/js/jquery-2.1.1.js` | 84,245 B | jQuery 2.1.1 (2014) | nothing |
| `public/vendor/html5shiv.js` | 10,331 B | — | nothing |
| `public/vendor/respond.js` | — | — | nothing |
| `public/vendor/isotope.pkgd.min.js` | — | — | nothing |
| `public/vendor/jquery.waypoints.min.js` | — | — | nothing |
| `public/vendor/jquery.counterup.min.js` | — | — | nothing |
| `public/vendor/fancybox/`, `aos-next/`, `slick/`* | — | — | see note |
| `public/pelican/assets/js/jquery.validate.min.js` | 23,087 B | — | nothing |
| `public/pelican/assets/js/validator.js` | 12,232 B | — | nothing |

\* `slick` IS referenced — by `aria-brain.html`. It is listed only so a future
sweep does not re-derive that and assume the whole directory is dead.

**Why a candidate.** Two of these are jQuery builds with published XSS and
prototype-pollution advisories (2.1.1 is from 2014; 3.5.0 predates 3.5.1's fix).
They are not exploitable as shipped — a vulnerable library that no page loads
executes nothing — but they are downloadable by anyone, they will be flagged by
any scanner or customer security review, and they are pure attack surface with no
compensating value. Verified live 2026-08-14:

    GET /vendor/jquery.min.js               -> 200  89,492 b
    GET /pelican/assets/js/jquery-2.1.1.js  -> 200  84,245 b
    GET /vendor/html5shiv.js                -> 200  10,331 b

**Proofs.**

* `proof_static` — **HELD.** No `<script src>` in any of the 32 served HTML files
  references them; 12 of 12 vendor libraries checked, only `jquery-3.7.1`
  (index.html) and `slick` (aria-brain.html) are referenced.
* `proof_runtime` — **UNKNOWN.** Phase 0.3 has not run. Static analysis cannot see
  a runtime `document.createElement('script')` injection, and the pelican template
  these came with is exactly the kind of bundle that does that. Until request logs
  or the runtime overlay show zero fetches, absence of a reference is not absence
  of use — the distinction this whole protocol exists to keep.
* `proof_test` — **NOT ESTABLISHED.** No test asserts these files are unused, so
  nothing would fail if a page started loading one tomorrow.

**Quarantine step, when the proofs are complete.** Do not `rm`. Serve a 404 for
the paths first, by adding them to a static-deny list, and watch for fallout over
a full traffic cycle. A 404 is reversible in one line; a deletion is a restore
from git plus a deploy, and the failure it causes is a blank page for a customer
rather than a log line for us.

**Risk of acting early.** Low blast radius, non-zero. If a page does load one of
these at runtime the symptom is a silently broken widget, not an error — which is
the worst shape of failure to introduce while a census is still being built.

**Interim mitigation available now:** none applied. A static-deny rule was
considered and deliberately not shipped with the audit fixes, because it is a
deletion-ladder step and belongs to the ladder's sequence, not to a defect fix.
