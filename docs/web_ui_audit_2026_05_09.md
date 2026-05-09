# Web/UI Quality Audit — 2026-05-09
**17 HTML pages · 6,105 LOC · headline grade: B+ (production-grade with two structural gaps)**

This audit reviews every public-facing HTML page in `public/` for
layout consistency, accessibility, responsive behaviour, broken
features, and polish appropriate to a "first-class product" target.

The headline finding: ARIA's UI is **production-grade for the surface
that exists today**. The structural gap is **the absence of UI for
the 22+ new endpoints shipped in this session** (DD pipeline tools,
learning progress, coverage heatmap, prompt-injection grading, etc.).
The endpoints work; customers and operators can't currently reach them.

---

## 1. What's verified working

| Check | Status | Notes |
|---|---|---|
| DOCTYPE on every page | ✅ 17/17 | HTML5 throughout |
| `<title>` on every page | ✅ 17/17 | Descriptive, brand-prefixed |
| Charset declared | ✅ 17/17 | UTF-8 |
| Viewport meta | ✅ 16/17 | All except `index.html` (a 11-line redirect — acceptable) |
| Favicon link | ✅ 16/17 | Same exception |
| No console.log left in production | ✅ Clean | No dev-leftover noise |
| No TODO / FIXME / XXX markers | ✅ Clean | |
| Shared header/sidebar shell | ✅ 10/17 | Consistent operator surfaces |
| Standalone public pages (intentional) | ✅ 3 | `index.html`, `model-card.html`, `status.html` |
| Mobile-first CSS in chat | ✅ aria.html | dvh + safe-area-inset on iOS |
| Auth flow with 2FA | ✅ signin.html | Pre-token / 6-digit code / back button |
| Loading states | ✅ 8/17 | Half the surfaces have explicit loading UI |
| Real signin / signup / forgot-password | ✅ All three | Functional flows |

**Visual polish on the customer-facing pages (signin / signup / aria
chat / account / dd-reports / sources / status / model-card)** is
solid — gradient accents, consistent typography (Gordita), iconography
via Bootstrap Icons, dark theme throughout, A/B layout for auth pages
that switches to single-column at <900px.

---

## 2. Concrete fixes needed (priority order)

### 🔴 P0 — Auth forms missing `<form>` elements

**Affected**: `signin.html`, `signup.html`, `forgot-password.html`.

**Problem**: All three auth pages use `<div>` + button-onclick instead
of a real `<form>` element. Consequences:
- Password managers (Bitwarden, 1Password, browser built-ins)
  don't reliably detect the form context for auto-fill
- Browsers don't show the "save password" prompt cleanly
- No "submit on Enter" by default — has to be re-implemented in JS
  (which signin.html does, but it's fragile)
- Accessibility tools (screen readers, password fillers) miss
  the semantic context

**Fix**: Wrap the input groups in `<form id="signin-form" onsubmit="doLogin(event); return false;">` and change
the button to `type="submit"`. Five-line change per page.

**Effort**: ~10 min per page = 30 min total.

### 🟠 P1 — UI surface for the 22 new endpoints (the structural gap)

**Affected**: every operator-side dashboard.

**Problem**: This session shipped 22 endpoints (R-F66..R-F84 + R-F88..R-F90) covering:
- Sanctions divergence cross-list (R-F68)
- RCA / PEP relatives screening (R-F76)
- FATF typology library + matcher (R-F72)
- TBML detection (R-F73)
- Crypto wallet sanctions screening (R-F74)
- Economic substance scoring (R-F77)
- Benford's Law forensic check (R-F70)
- DOJ FCPA enforcement scan (R-F69 — autonomous; report visible)
- Citation verification audit (R-F78)
- Structured ACH explainability (R-F71)
- Provenance chain + cascade-invalidate (R-F75)
- Prompt-injection adversarial grading (R-F80)
- Public API query-pattern monitoring (R-F83)
- Counter-intelligence corpus-poisoning scan (R-F84)
- Output harvester stats (R-F67)
- Learning progress + coverage heatmap + priorities (R-F88/F89/F90)
- Sources health (R-F58 — already wired)
- Tier-router diagnostic (R-F87a)

UI surface count per page:
```
account.html        0      bd-intelligence    0
admin.html          0      dashboard.html     0
aria-brain.html     1      dd-reports.html    0
aria.html           0      explorer.html      0
sources.html        0      status.html        0
+ 7 more            0
```

**1 of 22 endpoints surfaced** (`/learning/stats` text-only link in
aria-brain.html). The other 21 are invisible to operators.

**Fix**: Add a **DD Pipeline Tools** panel to `dd-reports.html` (the
natural home — it's already the DD-related operator UI). Each tool
gets a tile: name, one-line description, a "Run" button that opens an
inline form, and a results pane. Tiles for the 12 endpoint groups
above. Plus a **Learning** panel on `aria-brain.html` with three new
sub-sections: freshness (R-F88), coverage heatmap (R-F89), continuous-
update priorities (R-F90).

**Effort**: 4-6 hours for a clean, polished implementation. Could be
shipped in this session as a lighter MVP (3 hours) — see §4.

### 🟡 P2 — Polish gaps

| Issue | Impact | Effort |
|---|---|---|
| `index.html` redirect shows blank-white for ~50ms | Minor first-touch flicker | 5 min — add a centred logo + spinner |
| 8 of 17 pages have explicit loading states | Inconsistent — empty states look broken on slow connections | 30 min for skeleton screens on the 9 missing pages |
| No formal error boundary in chat (`aria.html`) | Network errors swallow into console; user sees stuck "thinking" | 30 min — add an inline error chip with retry action |
| `model-card.html` doesn't link from anywhere visible | Public-facing page is orphaned | 1 line in main nav |
| `status.html` doesn't link from main nav either | Same | 1 line in main nav |
| Privacy + Terms drafts (R-F50) live at `/about/` not linked from auth pages | GDPR / consumer-protection requirement at signup | 2 lines added to `signup.html` footer |
| `forgot-password.html` doesn't have a "back to sign-in" link | User dead-ends if they remember their password mid-flow | 1 line |
| No "what's new" / changelog surface | Customers can't see ARIA's updates | New page (deferred) |

### 🟢 P3 — Accessibility & polish (no-regret long tail)

- No `aria-label` on icon-only buttons (e.g. `convos-toggle`, `send-btn`) — screen readers announce them as "button" with no context
- No `aria-live` regions for the toast-style status messages
- No keyboard navigation cues on the conversation list (arrow-key navigation between conversations)
- No print stylesheet (audit-grade reports get a PDF, but `dd-reports.html` itself doesn't print cleanly)
- Tab order isn't audited end-to-end

---

## 3. Per-page brief

| Page | Lines | Quality | Notes |
|---|---|---|---|
| `index.html` | 11 | ✅ Functional | Plain redirect; could add a centred spinner for visual continuity |
| `signin.html` | 247 | 🟢 Solid | A/B layout, 2FA flow, error display, autocomplete attrs. Missing `<form>` element |
| `signup.html` | 477 | 🟢 Solid | Multi-step flow with sector capture (R-F48b), tier selection. Missing `<form>` element |
| `forgot-password.html` | 242 | 🟢 OK | Standard flow. Missing back-to-signin link |
| `aria.html` | 1107 | 🟢 Excellent | Conversation sidebar (R-F38), file upload, deep-think mode, chat history, mobile-optimised dvh + safe-area-inset. **Best page in the codebase** |
| `dashboard.html` | 259 | 🟢 OK | Operator dashboard. Could surface tier-router state + harvest stats |
| `aria-brain.html` | 996 | 🟢 Solid | The 16-panel brain dashboard. Has links to 1 of the new endpoints |
| `account.html` | 504 | 🟢 Solid | Billing tier + Stripe checkout flow + R-F42 API keys panel. Good |
| `dd-reports.html` | 342 | 🟢 OK | DD report library (R-F52). **Natural home for the new DD pipeline UI** |
| `sources.html` | 325 | 🟢 OK | Sources health + adversarial dashboard (R-F57+R-F58). Could add R-F80 prompt-injection results |
| `status.html` | 223 | 🟢 OK | Public status page (R-F47). Standalone; intentional |
| `model-card.html` | 239 | 🟢 OK | Public model card (R-F46). Standalone; orphaned from nav |
| `bd-intelligence.html` | 315 | 🟢 OK | Internal BD pipeline view |
| `admin.html` | 288 | 🟢 OK | Admin user mgmt + audit + push test |
| `explorer.html` | 99 | 🟡 Sparse | Internal tool; minimal styling |
| `opportunities.html` | 81 | 🟡 Sparse | Internal tool; minimal styling |
| `about/privacy.html` | 188 | 🟢 Draft (R-F50) | Banner says DRAFT pending counsel — correct |
| `about/terms.html` | 162 | 🟢 Draft (R-F50) | Same |

---

## 4. What I'm shipping now to close the structural gap

The P0 form fix and the highest-leverage P1 surface are quick wins. Three
fixes in this session:

1. **Wrap auth forms in `<form>`** (signin / signup / forgot-password) —
   password manager support + Enter-to-submit is the right behaviour
2. **Polish `index.html`** — add a centred logo + spinner for visual
   continuity during the 50ms redirect
3. **Add a DD Pipeline Tools tile grid to `dd-reports.html`** — surfaces
   the 12 new analytical endpoints as one-click tools with inline
   forms and results panes

For the operator-facing surfacing of R-F88/F89/F90 (learning progress
heatmap), a deeper redesign of `aria-brain.html` is warranted — that's
better as its own session because it's a multi-panel rebuild.

**What will be left for next session** (deliberate scope cut):
- Skeleton screens on the 9 pages missing them (P2)
- aria-label on icon-only buttons (P3 accessibility)
- aria-live regions (P3)
- Main-nav links to model-card.html + status.html (P2)
- Privacy/Terms link in signup footer (P2 — but pre-launch is non-negotiable)
- Print stylesheets (P3)
- "What's new" changelog page (deferred)
- Deeper aria-brain.html redesign with R-F88/F89/F90 panels

---

## 5. Honest one-line verdict

ARIA's UI is production-grade for what's currently surfaced. The
22 new endpoints shipped today need a UI surface for them to count
as customer-facing capability — without it, the engineering work
exists in the API only. This session's fixes close 3 of the 8
remaining gaps; the other 5 are explicitly logged here for the next
session.

---

*Generated 2026-05-09 EOD. Companion to `recommendations_complete_2026_05_09.md`
and `aria_independence_roadmap.md`.*
