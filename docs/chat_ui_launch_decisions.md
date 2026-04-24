# ARIA launch decisions — operator brief

**Purpose**: concrete recommendations for the 5 open decisions in
`chat_ui_scope_2026-10.md` so the operator can sign off in one pass.

Every recommendation below is a DEFAULT; easily reversible; chosen to
minimise regret under uncertainty.

## 1. Price point

**Recommendation**: **two tiers at launch — $20/mo Pro, $199/mo Professional Intelligence**.

### Pro ($20/mo)
- Matches Claude Pro / ChatGPT Plus baseline — consumers already
  compare the three.
- Caps: 200 messages/day, 20 DD runs/month, 5 MB file uploads.
- Model access: Claude Sonnet 4.6 + DeepSeek. No deep_research; no
  autonomous tasks.
- Audience: analysts, journalists, researchers, hobbyists who want
  defence-intelligence grounding without institutional sign-off.

### Professional Intelligence ($199/mo)
- Captures the **actual willingness-to-pay** in the defence
  broking / compliance market. A single DD report saves the user
  hours; $199/mo is 1-2 hours of their billable time.
- Caps: 2000 messages/day, 100 DD runs/month, 50 MB uploads, full
  autonomous research access (with $100/mo cost cap per account),
  priority queue on the chat path.
- Audience: defence-BD professionals (Arkmurus is one), compliance
  officers, export-control advisors, regional intelligence
  consultants.

### Why NOT a middle tier
- Three tiers at launch = three price points to explain, three
  configurations to maintain, and the middle one always
  underperforms both neighbours. Start with two; add a mid-tier in
  v2 if the data shows demand between them.

### Why NOT enterprise-only
- Enterprise sales cycle is 3-12 months. October launch means no
  revenue visible until Q1 2027 if enterprise-only. Two self-serve
  tiers produce revenue from day 1.

### Defer to v2
- Team workspaces (shared conversation history, role-based access).
- Custom model access (fine-tuned on client corpus).
- On-prem deployment.

## 2. Free tier limits

**Recommendation**: **10 messages/day free, signup required, no DD or autonomous access**.

### Rationale
- Free tier is a FUNNEL, not a product. 10/day is enough to test
  ARIA on a real question, not enough to replace Pro.
- **Signup required** (not anonymous) — we need the email for
  upgrade marketing and abuse control.
- **No DD runs** — each one costs ~$0.50 in LLM spend. Free users
  who run 5 DDs/day would vaporise the unit economics.
- **No autonomous tasks** — scheduled research is a paid feature.

### Hard floors
- File uploads: 3 per day, 1 MB max (prevent abuse vector).
- Message length: 4000 chars (same as Pro).
- No public API access on free.

### Anti-abuse
- Require email verification before first message (not just signup).
- Rate-limit signups per IP / device fingerprint (prevent
  multi-account farming).
- Soft-ban new accounts from known-abusive IP ranges (shared VPN
  exit nodes for the top 20 residential VPN providers).

### What NOT to do
- **Do NOT run a permanent free tier with unlimited usage** —
  predatory user behaviour will eat the cost cap.
- **Do NOT require a credit card for free tier** — that's a
  subscription with extra steps; hurts top-of-funnel conversion.

## 3. Waitlist vs open signup at launch

**Recommendation**: **waitlist + rolling invites for the first 4-8 weeks, open signup once capacity is proven**.

### Why waitlist
- ARIA has never been stress-tested beyond a handful of WA users.
  First 100 paid users could expose queue saturation, LLM provider
  rate-limit collisions, verification-gate edge cases we haven't
  seen, and RAG lookup contention.
- A waitlist lets us onboard in batches (e.g. 50 invites/day) and
  pause if metrics degrade.
- "Waitlist" is also a credibility signal — scarcity → perceived
  quality.

### Operating the waitlist
- Collect email + 1-line use case ("why do you want ARIA?") on
  signup. Use the use cases to seed documentation examples.
- Invite in FIFO batches unless a batch contains an obvious
  brand-fit user (named defence analyst, compliance officer at a
  known firm) — prioritise those for feedback value.
- 7-day invite expiry. Reclaim unused invites for the next batch.

### Exit criteria for open signup
- No P0 incidents for 14 consecutive days.
- p95 chat-path latency < 8 seconds on tool-using turns, < 2s on
  non-tool.
- Verification-gate CRITICAL_UNVERIFIED rate < 5% of CRITICAL
  classifications.
- Cost per paid user < $5/mo (leaves ≥75% margin at $20/mo Pro).

### What NOT to do
- **Don't launch open to the world on day 1**. Even Anthropic ran
  a waitlist for Claude Pro for months.

## 4. Custom domain

**Recommendation**: **`aria.app` if available; `arkmurus.ai` as fallback; register both today**.

### Why `aria.app`
- Short, memorable, on-theme.
- `.app` is Google's registrar default for AI apps; HTTPS enforced
  at the TLD level.
- Decouples the product brand from the Arkmurus parent company —
  valuable if ARIA ever spins out.

### Fallback: `arkmurus.ai`
- Reinforces the Arkmurus brand.
- `.ai` carries AI positioning implicitly.
- Downside: ties the product to the parent company; potential
  confusion if Arkmurus pivots.

### What to register either way
- `.app` + `.ai` + `.com` + `.io` of the chosen root (defensive).
- Typo variants: `aira.app`, `ariia.app` (one-character neighbours
  of the real domain, reserved to prevent phishing).

### What NOT to do
- **Don't launch on `arkmurus.com/aria`** — subpath URLs look like
  an internal tool, not a consumer product. Kills conversion.

## 5. Support channel

**Recommendation**: **Email-only at launch (`support@aria.app` → operator's inbox), Intercom added at 500 paid users**.

### Launch stage: email-only
- Paid users of a brand-new product tolerate email turnaround if
  response quality is high (personal reply > chat-bot canned).
- Zero infrastructure cost. Zero SaaS dependency at launch.
- Volume will be low in the waitlist phase — email is easily
  managed by one operator.

### Scale stage: 500 paid users threshold
- At 500 paid × ~2% weekly touch rate = 10 tickets/week. Fine on
  email.
- At 2000 paid × 2% = 40 tickets/week. Email breaks down
  (threading chaos, missed follow-ups). Migrate to Intercom
  Starter ($74/mo).

### What NOT to do at launch
- **Don't build a custom in-product chat widget**. Scope creep;
  the product itself IS a chat — let users describe bugs via
  email with logs they can copy-paste.
- **Don't outsource to a support contractor**. Brand voice
  matters; early support conversations are product-development
  signal, not cost to minimise.
- **Don't use Discord / Slack community as primary support**.
  Public support channels surface edge cases to adversaries; bad
  for a defence-intelligence product's security posture.

### Templates to prepare before launch
- 8 canned replies covering: login trouble, file-upload error,
  "why did ARIA say X about entity Y" (auditability flex — show
  them the trace), rate limit hit, subscription cancellation,
  refund request, feature suggestion acknowledgement, abuse
  report follow-up.

## Summary table

| # | Decision | Recommendation | Reversibility |
|---|---|---|---|
| 1 | Price point | $20 Pro + $199 Pro-Intelligence | Can add middle tier in v2 |
| 2 | Free tier | 10 msg/day, signup required, no DD | Can liberalise if funnel is weak |
| 3 | Launch mode | Waitlist → open after 14d P0-free | Can open earlier if capacity holds |
| 4 | Domain | `aria.app` (fallback `arkmurus.ai`) | Hard to change after launch — decide first |
| 5 | Support | Email-only at launch | Add Intercom at 500 paid users |

## Ask of the operator

Sign off (or veto) each of 1-5 before the 6-week build window starts.
Decision 4 (domain) is the **only** one that needs to happen BEFORE
the build — it shapes the auth cookie domain, email templates, CORS
config. The other four can be deferred to week 4 at the latest.
