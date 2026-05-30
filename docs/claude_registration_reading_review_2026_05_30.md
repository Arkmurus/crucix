# Claude → ARIA — can you register on sites + read any site? (capability review, 2026-05-30)

Grounded review (3 passes) of autonomous website registration + web-reading, for DD/investigations,
"without breaking any security protocol." Ground-or-abstain: verify each before acting.

## VERDICT IN ONE LINE
- **Register on websites: NOT YET — `portal_registry.py` is a catalog + operator-handoff STUB.** It
  never submits a signup form. To make it real needs real work (below).
- **Read any site: MOSTLY — strong multi-engine reader, but restricted.** ~6 restrictions block
  legitimate reads + JS-rendering is wired unevenly + no login support.
- **Security: web-reading is SAFE today (R-F882 doc-as-DATA intact). Registration is safe ONLY
  because it's dormant** — if wired live as-is it would cross the autonomy line with no approval gate.

---

## 1. REGISTRATION — what's real vs stub (portal_registry.py, R-F1063)
- ✅ Portal CATALOG: 45 portals with url/captcha/email-verify flags.
- ❌ Submit signup form: **STUB** — the "automated" branch only does `GET /register` to probe
  reachability (`portal_registry.py:577`); there is **no `httpx.post` of form data** anywhere.
- ❌ Email verification: **MISSING** — `email_reader.py` can read IMAP but registration never imports
  it; nothing extracts/visits a confirmation link.
- ❌ CAPTCHA: deferred to operator (correct — do NOT bypass).
- ❌ Field mappings: placeholder; the function's own comments call it "a TEMPLATE."
- ❌ Orphaned: `register_for_portal` has **zero callers** (no route, task, or autonomous trigger).
- ⚠️ Credentials stored **plaintext** (`set_json`, no encryption).
- The only real thing it does against portals: read FREE no-registration APIs (USASpending).
**→ ARIA cannot complete a real website registration today.**

### To make registration REAL + SAFE (build list)
1. **Browser automation** for signup — use the existing Playwright engine (`scraper/playwright_engine.py`)
   for JS/SPA forms; plain httpx can't do modern signup forms.
2. **Real form-POST** with per-portal field schemas (replace the template).
3. **Email-verification loop** — wire `email_reader` → find confirmation email → extract link → visit it.
4. **APPROVAL GATE (required, autonomy doctrine):** registration is an OUTWARD action (creates an
   account, accepts ToS, submits data). It MUST go through `pending_actions`/operator approval and
   `safety.can_task_run` — never POST a signup unattended. Today the gate is a TODO comment, not a guard.
5. **Credential vault** — encrypt portal creds at rest (Fernet off an `ARIA_*` secret), never plaintext.
6. **Real-identity-only** — keep `aria@arkmurus.com` / `Arkmurus Group Ltd`; add an assertion that
   REJECTS non-arkmurus / fabricated identities (no synthetic personas — that's fraud/ToS-breach).
7. **ToS checkpoint** — read/record `terms_url` acceptance before registering.
8. **CAPTCHA/anti-bot → operator handoff (keep, never bypass).**
9. Gate/remove `email_reader.send_email` (unguarded SMTP, no callers) — route replies through the
   gated `email_outbound` (enable-flag + operator allowlist).

---

## 2. READING — strong but restricted (the "read any website" ask)
She reads via httpx + Lightpanda (light JS) + Playwright/Chromium (full JS) + PDF/DOCX/OCR/vision +
Wayback fallback. Document coverage is strong (PDF tables, OCR, DOCX, vision up to 300pp). BUT:

### Restrictions that BLOCK legitimate reads (relax for operator-DD, behind a flag)
- **robots.txt enforced** (`crawl_enhancements.py:489,533`; `deep_researcher.py:144`) → refuses
  Disallowed paths (many registries disallow /search, /documents). Gate behind `ARIA_RESPECT_ROBOTS=0`.
- **Social/auth blocklist** (`security.py:118-119,145-152`) → LinkedIn/X/Facebook hard-blocked at
  `validate_url`. Make an allowlist for investigative reads.
- **Script-extension block on GET** (`security.py:56,62,110`) → refuses `…/file.js/.py/.jar` even for a
  plain read. Overcautious (GET doesn't execute) — trim for reads.
- **8 KB article cap** (`researcher.py:1061`) on the chat path → drops content. Raise to 50–100 KB.
- **15 s timeout** (`researcher.py:971`) → too short for slow gov/registry portals. Raise.
- **No login/cookie support** anywhere → can't read gated registries/filings. Add cookie/session
  injection using the credential vault (this is what makes registered accounts USEFUL for DD).

### JS-rendering is wired UNEVENLY (gap)
- Chat/article path escalates to Lightpanda only — **no Playwright fallback** (`researcher.py:1026`).
- Playwright fires only on the crawl path's `primary_failed` branch (`crawl_enhancements.py:627`) —
  not on a 200-but-empty JS shell.
- CLI `fetch_url` is plain httpx, no JS at all.
→ Wire Playwright into the chat + CLI paths and trigger it on thin-200 shells.

### Also missing: native `.xlsx` reader (only `.csv`) — add openpyxl for DD spreadsheets.

### KEEP (do NOT relax — these ARE the security protocols)
- SSRF / private-IP / cloud-metadata blocks (`security.py:43-48`).
- **No CAPTCHA / anti-bot bypass** — bypassing a site's bot-defence IS breaking its security
  protocol (CFAA/CMA/ToS liability). The code deliberately reports-and-defers; keep it.
- Injection guard (R-F882): fetched HTML stays DATA, scanned + sanitised, never executed. Keep ON.
- Real-identity / truthful crawler UA. Keep.

---

## 3. THE KEY DECISION (for the operator)
"Read any website, no restrictions" + "without breaking any security protocol" are in tension on ONE
axis: **CAPTCHA / anti-bot bypass.** Everything else ("no restrictions") is a policy/config relax that
is defensible for legitimate DD — robots, social blocklist, size/timeout caps, login with REAL
credentials. But bypassing CAPTCHA/Cloudflare/anti-bot is the one thing that DOES break a target's
security protocol and carries legal liability. Recommendation: relax the politeness/policy
restrictions (flagged, operator-gated); KEEP the no-CAPTCHA-bypass + SSRF + real-identity + injection
invariants. That gives "read nearly any PUBLIC site + login-gated sites with legitimate creds,"
without breaking security protocols.

## Order (if we build this)
Reading first (higher DD value, lower risk): wire Playwright everywhere → relax robots/social/size/
timeout behind a flag → add login/cookie support + `.xlsx`. Then registration: approval-gate +
credential vault FIRST, then form-POST + email-verify + Playwright signup. R-number + capability test
(register against a sandbox/real free portal; read a JS site + a gated page) + 2-pass + BATCH each.
