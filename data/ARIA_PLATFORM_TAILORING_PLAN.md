# ARIA Platform Tailoring Plan — Multi-Tenant SaaS Architecture

## STATUS: ON HOLD (operator decision, 2026-06-10)

**Do NOT build until ALL THREE conditions met:**
1. Phase A complete (all 7 gates — #3/#5/#7 still open)
2. Sovereign ARIA-LLM well-trained + reasoning independently off rented DeepSeek (v0.2→v0.3 distillation track)
3. Self-coder reliably correct + non-destructive

**Claude's architecture corrections (saved for when hold lifts):**
- Admin = **separate process**, not route-gating on the same Node app
- **Shared store + tenant_id** row-isolation, NOT SQLite-per-tenant
- Rebrand post-MVP validation, not now

**Current lane:** Close Phase A gates + sovereign-LLM training/eval + autonomous-coding maturity.

## Current State
- **aria-web** (Node monolith): Single sign-in page → dashboard. Everything is one app.
- **aria-intel** (FastAPI brain): The LLM + DD + intelligence engine. No multi-tenant separation.
- **aria-wa** (Baileys WA listener): WhatsApp interface.
- **No customer isolation** — one login, one view, one ARIA.

## Target State: Three-Tier SaaS

```
┌─────────────────────────────────────────────────────┐
│                   PUBLIC FACING                      │
│  aria.arkmurus.com (Customer UI)                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  Chat UI (like Claude/OpenAI/DeepSeek)        │   │
│  │  • Ask questions, get DD reports              │   │
│  │  • Search history (per-user)                  │   │
│  │  • Saved searches, bookmarks                  │   │
│  │  • Billing/plan management                    │   │
│  └──────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────┤
│                  ADMIN BACKEND                       │
│  admin.arkmurus.com (Internal Ops)                   │
│  ┌──────────────────────────────────────────────┐   │
│  │  • User management (create/disable/roles)     │   │
│  │  • Usage analytics (per-user, per-company)    │   │
│  │  • Billing dashboard (Stripe)                 │   │
│  │  • Intelligence feed monitoring               │   │
│  │  • System health (current aria-web)           │   │
│  │  • Audit logs (all user actions)              │   │
│  │  • Rate limit / quota management              │   │
│  └──────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────┤
│                   ENGINE LAYER                       │
│  aria-intel (FastAPI) — multi-tenant aware           │
│  ┌──────────────────────────────────────────────┐   │
│  │  • Per-tenant state isolation (SQLite per     │   │
│  │    customer or shared with tenant_id)         │   │
│  │  • Per-tenant rate limits + quotas            │   │
│  │  • Per-tenant customisation (prompts,         │   │
│  │    knowledge base, compliance rules)          │   │
│  │  • Chat history per user per tenant           │   │
│  │  • DD report storage per tenant               │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

## Phase 1: Customer Chat UI (Weeks 1-3)

### What to build
A standalone chat interface at `chat.arkmurus.com` (or `/chat` on aria-web) that mirrors the Claude/OpenAI/DeepSeek UX:

```
┌──────────────────────────────────────────────┐
│  ARKMURUS  [New Chat]  [Settings]  [User ▼] │
├──────────────────────────────────────────────┤
│  ┌────────────────────────────────────────┐  │
│  │  User: "Run DD on Acme Corp"           │  │
│  │                                        │  │
│  │  ARIA: [DD Report for Acme Corp]       │  │
│  │  ┌────────────────────────────────┐    │  │
│  │  │ Risk: AMBER                     │    │  │
│  │  │ Sanctions: CLEAN                │    │  │
│  │  │ Jurisdiction: Delaware, US      │    │  │
│  │  │ View Full Report →              │    │  │
│  │  └────────────────────────────────┘    │  │
│  │                                        │  │
│  │  User: "Any UBO concerns?"             │  │
│  │  ARIA: [Follow-up analysis...]         │  │
│  └────────────────────────────────────────┘  │
├──────────────────────────────────────────────┤
│  [Type your question...              ] [→]   │
└──────────────────────────────────────────────┘
```

### Key features
1. **Chat history per user** — every query + response stored, searchable
2. **Session management** — continue where you left off
3. **DD report rendering** — structured cards, not raw JSON
4. **Search within history** — find past DD runs
5. **Export** — PDF/CSV of chat or specific DD report

### Technical approach
- **Frontend:** Standalone SPA (React/Vue) or enhanced HTML page with JS
- **Backend:** New FastAPI routes on aria-intel or a new lightweight service
- **Auth:** JWT-based (Stripe Customer Portal or magic-link)
- **Storage:** PostgreSQL or SQLite per tenant for chat history

## Phase 2: Admin Backend (Weeks 3-5)

### What to build
A separate admin interface at `admin.arkmurus.com`:

```
┌──────────────────────────────────────────────┐
│  ARKMURUS ADMIN    [Dashboard] [Users] [Logs]│
├──────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
│  │ Active   │ │ Revenue  │ │ DD Runs      │ │
│  │ Users:42 │ │ $12,400  │ │ Today: 156   │ │
│  └──────────┘ └──────────┘ └──────────────┘ │
│                                              │
│  Users Table:                                │
│  ┌──────┬────────┬───────┬────────┬──────┐  │
│  │ Name │ Company│ Plan  │ Usage  │Status│  │
│  ├──────┼────────┼───────┼────────┼──────┤  │
│  │ Acme │ Acme   │ Pro   │ 45%    │ ✅   │  │
│  │ Beta │ Beta   │ Free  │ 92%    │ ⚠️   │  │
│  └──────┴────────┴───────┴────────┴──────┘  │
└──────────────────────────────────────────────┘
```

### What moves from current aria-web to admin
- Intelligence feed monitoring (sanctions, CVE, procurement)
- System health dashboard
- Brain stats viewer
- Error log viewer
- User management (create, suspend, role assignment)

## Phase 3: Multi-Tenant Engine (Weeks 5-8)

### Tenant isolation model
```
┌──────────────────────────────────────────────┐
│              aria-intel                       │
│                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
│  │ Tenant A │ │ Tenant B │ │ Tenant C     │ │
│  │ (Acme)   │ │ (Beta)   │ │ (Gamma)      │ │
│  ├──────────┤ ├──────────┤ ├──────────────┤ │
│  │ • Own DB │ │ • Own DB │ │ • Own DB     │ │
│  │ • Own    │ │ • Own    │ │ • Own        │ │
│  │   rate   │ │   rate   │ │   rate       │ │
│  │   limits │ │   limits │ │   limits     │ │
│  │ • Own    │ │ • Own    │ │ • Own        │ │
│  │   prompt │ │   prompt │ │   prompt     │ │
│  │   config │ │   config │ │   config     │ │
│  └──────────┘ └──────────┘ └──────────────┘ │
│                                              │
│  ┌──────────────────────────────────────────┐│
│  │ Shared: LLM, Sanctions DB, Intelligence  ││
│  │ Feeds, Sanctions Lists, Knowledge Base   ││
│  └──────────────────────────────────────────┘│
└──────────────────────────────────────────────┘
```

### Key changes to aria-intel
1. **Add `tenant_id` to all state keys** — `crucix:tenant:{id}:chat:history`, `crucix:tenant:{id}:dd:report:*`
2. **Rate limiting per tenant** — `ARIA_TENANT_{ID}_RPM` env vars or DB-backed
3. **Custom prompt templates per tenant** — each tenant can customise ARIA's behaviour
4. **Per-tenant knowledge base** — company-specific documents, policies, counterparties
5. **API keys per tenant** — for programmatic access

## Phase 4: Billing & Plans (Weeks 8-10)

### Tier structure
| Tier | Price | DD Runs/mo | Users | Chat History | Support |
|---|---|---|---|---|---|
| Free | £0 | 10 | 1 | 7 days | Community |
| Starter | £99/mo | 100 | 3 | 30 days | Email |
| Pro | £499/mo | 1,000 | 10 | 90 days | Priority |
| Enterprise | Custom | Unlimited | Unlimited | Unlimited | Dedicated |

### Stripe integration
- Customer portal for self-service plan changes
- Usage-based billing for overages
- Invoicing for Enterprise

## Phase 5: Enterprise Features (Weeks 10-12+)

- **SSO/SAML** — Okta, Azure AD, Google Workspace
- **Audit logs** — every action logged per user per tenant
- **Custom compliance rules** — per-company sanctions lists, jurisdiction filters
- **White-label** — custom domain, branding
- **API access** — REST API for programmatic DD runs
- **Webhook notifications** — on DD completion, sanctions hits

## Architecture Decisions

### Why NOT a single shared DB
- Compliance data is sensitive — tenant isolation is a legal requirement
- One tenant's heavy usage shouldn't degrade another's performance
- GDPR right-to-deletion requires per-tenant data management

### Why SQLite per tenant (not PostgreSQL)
- Current stack is SQLite — proven, zero-maintenance
- Each tenant's data is small (chat logs + DD reports)
- Can migrate to PostgreSQL later when scale demands it
- Simpler deployment — no new infrastructure

### Why separate admin UI
- Security: admin credentials never touch customer-facing code
- Complexity: admin features (user management, billing) don't clutter the chat UI
- Audit: admin actions are logged separately from customer actions

## Current Phase A Gate Status

Per the buildout plan, we're still in **Phase A** (Honesty foundation). Before building the multi-tenant platform, we need:

| Gate | Status |
|---|---|
| #1 Composite ≥71% | ✅ Closed |
| #2 Heatmap floor ≥70% | ✅ Closed (R-F748) |
| #3 0 fly ERRORs/7d | ⏳ Ongoing |
| #4 Quarantined DDs closed | ✅ Closed |
| #5 Env vars set | ⏳ ACLED deferred |
| #6 500-Q eval frozen | ✅ Closed |
| #7 ≥4 design-partner convos | ⏳ Not started |

**Recommendation:** Start Phase 1 (Customer Chat UI) in parallel with closing remaining Phase A gates. The chat UI doesn't require Phase B — it's a frontend reorganisation of existing capabilities.

## Next Steps (Immediate)

1. **Audit current aria-web routes** — separate customer-facing from admin-facing
2. **Design the chat UI** — wireframe the Claude-like interface
3. **Build chat history storage** — new SQLite schema for per-user chat logs
4. **Create admin subdomain** — `admin.arkmurus.com` pointing to same Node app with admin-only routes
5. **Add tenant_id to aria-intel** — start with the state_store keys
6. **Set up Stripe** — products, pricing, customer portal
