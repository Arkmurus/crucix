# ARIA chat UI — scope to October 2026 consumer release

**Status**: scoping, not started.
**Author**: 2026-04-24 session.
**Horizon**: ~6 weeks of focused product work.

## 0. Reality check on the vision doc

`memory/product_vision_6mo_release.md` (dated 2026-04-09) claimed:
- "No streaming responses — ARIA returns full responses"
- "Angular admin panel is the only web surface"
- "No per-user conversation history"
- "No account system (bearer tokens only)"

What's actually true as of 2026-04-24:

| Claim in vision doc | Real state | Evidence |
|---|---|---|
| No streaming | ✅ **Already shipped** | `public/aria.html` consumes `/chat/stream` via SSE + token-by-token rendering. Stop + Regenerate controls added in commit `8034c69`. |
| Only Angular admin panel | ❌ **Wrong** | `public/aria.html` is a chat-first UI with sidebar, markdown rendering, file upload, think-mode toggle. The admin panel is `admin.html` / `dashboard.html` — a sibling, not the only surface. |
| No per-user history | ❌ **Wrong** | `aria_service/intel/conversation_store.py` keys conversations by `user_id` with sorted-set + metadata hash. `/api/aria/conversations` endpoint exists. |
| Bearer tokens only | ❌ **Partially wrong** | JWT auth via `Auth.requireAuth()` + `/api/aria` router `Depends(_router_auth_dep)`. Login / signup / password-reset flows exist (`signin.html`, `signup.html`, `forgot-password.html`). |

**Net**: the vision doc described a 6-LARGE-gap world. The real scope is closer to **2 MEDIUM + 3 SMALL gaps**. This changes both timeline and plan.

## 1. What actually needs to be built (6-week runway)

### Gap A: conversation history sidebar (MEDIUM, ~1 week)

**Current state**: `conversation_store` persists conversations per user; `/api/aria/conversations` lists them. But `aria.html`'s sidebar doesn't render the list — "New conversation" button exists, old ones aren't browseable.

**Build**:
- Sidebar component in `aria.html` reading `/api/aria/conversations?user_id=<self>&limit=50`
- Clicking a conversation loads its messages (`/api/aria/conversations/{sid}`) and sets `SID`
- Auto-title from the first user message (already done in `conversation_store.create_conversation`)
- Pagination + "Load older" link
- Delete / archive per conversation (DELETE route exists? check)

### Gap B: message editing / forking (MEDIUM, ~1 week)

**Current state**: once a user message is sent, it's immutable. No edit, no fork, no branch.

**Build**:
- Edit icon on user bubbles → re-opens the input with prior text; submit replaces the message + re-runs the stream
- Server-side: add a `/api/aria/chat/edit-at-turn` endpoint that truncates the session at turn N and re-runs from there
- UI: show the fork point visually (optional — Claude does this; ChatGPT does not)

### Gap C: file drag-drop + inline preview (SMALL, ~3 days)

**Current state**: file upload via hidden `<input>` triggered by paperclip button. No drag-drop. Uploaded files appear only as a user bubble; no inline PDF render.

**Build**:
- Drop zone listener on `body` highlighting on `dragover`; `drop` handler routes to existing file upload flow
- For PDFs: render first page as thumbnail in the user bubble (PDF.js already in `vendor/`? check)
- DOCX/XLSX: just show filename + size chip

### Gap D: mobile responsive polish (SMALL, ~2 days)

**Current state**: media query at `@media (max-width:640px)` in `aria.html` handles basic layout collapse. But the sidebar behaviour on mobile is untested.

**Build**:
- Sidebar: drawer pattern on mobile (hidden by default; hamburger toggle)
- Textarea: bottom-fixed on mobile (iOS soft-keyboard support)
- Sticky header with current conversation title
- Touch-safe button sizes (min 44px hit target)

### Gap E: billing + tier gating (MEDIUM, ~1.5 weeks)

**Current state**: no paid tier, no metering visible to end-user. Cost cap is engineer-facing on `/api/aria/cost/monthly`.

**Build**:
- Stripe checkout integration (subscription, $20/mo to match Claude baseline)
- Per-user rate limits in `ARIA_USER_RPM_CAP` + `ARIA_USER_DAILY_COST_USD_CAP` (env vars exist — wire to per-user enforcement)
- "Upgrade" CTA when free-tier rate limit hits
- Account settings page showing current usage + billing history

### Gap F: public API + docs (MEDIUM, ~1 week)

**Current state**: `/api/aria/*` is JWT-protected internal API. No public surface, no docs site, no SDK.

**Build**:
- `/api/v1/chat` proxy that accepts an API key (not JWT) and routes to `/api/aria/chat/stream`
- Per-key rate limiting (Redis-backed)
- Docs site: `docs.aria.app` with OpenAPI-generated reference. Can be a static site.
- Optional: thin Python + JS SDK. Most users will hit the HTTP API directly.

## 2. Sequencing

| Week | Focus | Exit |
|---|---|---|
| 1 | Gap A (conversation history sidebar) | User can browse and resume prior conversations |
| 2 | Gap C + Gap D (file drag-drop + mobile polish) | Upload by drag; phone users see a clean UI |
| 3 | Gap B (edit / fork) | Users can retry with modified context without starting over |
| 4 | Gap E (Stripe + tier gating) — part 1 | Checkout flow works; subscribed users see the paid badge |
| 5 | Gap E part 2 + Gap F part 1 | Per-user rate limits enforced; `/api/v1/chat` live with API keys |
| 6 | Gap F part 2 + launch polish | Docs site live; landing page copy reviewed; waitlist-to-live cutover |

## 3. What's explicitly NOT in scope

- **Voice I/O** (stretch goal per vision doc). Claude doesn't have it yet in stable. Defer to post-launch.
- **Image generation** (DALL-E analog). ARIA is defence-intelligence positioned; image-gen doesn't fit the brand.
- **Native mobile apps**. Progressive Web App is sufficient for v1. iOS/Android native is a post-launch investment decision.
- **Artifact-style live HTML preview** (Claude Artifacts). Nice-to-have; not release-blocking.
- **Team workspaces**. Single-user tier only at launch. Team tier is a follow-on SKU.

## 4. Infrastructure implications

- **Dedicated chat-path resource pool**: the vision doc flagged this as non-negotiable. Current fly.io config runs interactive + background on the same machine. Real risk if autonomous engine + spider fire during a user interaction. Action: separate fly app or dedicated worker pool for background tasks before launch.
- **Per-user usage metering surface**: already instrumented via `cost_tracker.feature()` — needs a user_id dimension added to the record_call payload. Small change.
- **Rate-limit Redis keys**: per-user (`crucix:rate:user:{id}`) instead of global. Schema design in Week 4.

## 5. Key open decisions (need operator sign-off)

1. **Price point**: $20/mo matches Claude Pro. Is that right for a defence-intelligence positioning? Some premium tier at $50–$100/mo that unlocks autonomous research?
2. **Free tier limits**: 10 messages/day? 50? More if you share the product?
3. **Waitlist or open signup at launch?** Waitlist gives us room to debug capacity; open signup is braver.
4. **Custom domain**: `aria.app`? `arkmurus.ai`? Need to register and point DNS.
5. **Support channel**: email-only? Intercom? ChatGPT-style in-product?

## 6. Risks

- **Capacity under load**: never been stress-tested beyond a handful of WA users. First 100 paying users could expose queue saturation.
- **Prompt-injection attacks**: constitutional discipline is strong, but public-facing gives adversaries room to probe. Adversarial audit at 90.9% (1 HIGH fail pending) — track toward 100% before public launch.
- **LLM cost blowouts**: $409/mo projection at current (internal-only) burn rate. Public tier without hard per-user caps is an unboundable cost. Gap E is as much cost protection as revenue.
- **Legal/ToS missing**: vision doc flagged this as SMALL but real. Privacy policy + ToS need legal review before the first paying customer.

## 7. One-line verdict

The vision doc's 6-LARGE-gap framing was pessimistic. What exists is a chat-first UI with streaming, per-user history, and JWT auth already shipped — the remaining work is conversation-sidebar rendering, editing/forking, file UX polish, Stripe billing, and a public API surface. All are MEDIUM-at-most tasks and all fit inside 6 weeks with focused execution. **The product-release risk is capacity / cost / legal, not frontend scope.**
