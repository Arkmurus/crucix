# R-F3578 — ARIA WA root surgery: Claude review brief

Status: **LOCAL REVIEW TREE — NOT COMMITTED, PUSHED, DEPLOYED OR SHIP-MARKED**

## Root map

ARIA presently has four WhatsApp-adjacent paths:

1. `services/wa-listener/aria_wa_listener.mjs` — canonical Fly `aria-wa`
   Baileys service, including the per-user QR account API and inbound pipeline.
2. `server.mjs` — authenticated web-to-`aria-wa` proxy and browser identity
   boundary.
3. `lib/whatsapp/waListener.mjs` — disabled-by-default embedded legacy listener.
4. `lib/whatsapp/ariaWhatsApp.mjs` — deprecated Twilio webhook which deliberately
   rejects requests. It is not an official Meta Business Platform implementation.

Guardian/personal-safety functions live separately in `aria_service/guardian/`
and are reached from the linked listener through authenticated brain routes.

## Implemented in this review tree

- Shared, pure governance policy in `lib/whatsapp/waGovernance.mjs`.
- Official channel represented as the recommended mode and exposed only when
  `ARIA_WHATSAPP_OFFICIAL_NUMBER` is configured.
- Advanced linked mode requires all risk acceptances, a limited scope and live
  TOTP step-up authentication.
- Consent expires after 30 days; QR creation is refused at both the web proxy and
  listener service boundaries.
- One-device-per-user enforcement remains in the listener.
- Each inbound linked-device event is checked against current grant state and
  scope. The initial UI safely enables only forwarded/explicitly tagged content;
  broader chat/media/group scopes remain visibly disabled pending their selector.
- Pause synchronously updates the live listener. Revoke calls Baileys `logout()`
  and invalidates the linked session.
- Ownerless primary Baileys processing is disabled by default and requires the
  explicit `WA_PRIMARY_LINKED_ENABLED=1` experimental flag.
- Raw message text, phone/chat identifiers and names were removed from the rolling
  operational store and Redis persistence. Redis now holds metadata events for 24h.
- Content-preview log lines were removed. Failure signals no longer include the
  user's message or sender identifier.
- Raw extracted-document disk caching is off by default; ephemeral in-memory
  document context defaults to two hours.
- `Dockerfile.wa` copies the shared policy module, preventing a boot-time missing
  import.

## Explicitly not claimed complete

- The official Meta WhatsApp Business Platform gateway does not yet exist. The
  old Twilio route is still deprecated. Gate A cannot be approved on current code.
- Sender binding, assurance levels 0–3 and per-action role/dual-control checks for
  the official channel are not implemented.
- Chat selection after linking is not implemented; broader scopes are therefore
  disabled rather than falsely presented as working.
- Transaction-object extraction, three confirmation lanes, evidence-axis storage,
  WhatsApp-to-DD architectural separation and outbound exact-hash approval require
  further cross-service work.
- A DPIA, ROPA, DPA, Article 14 workflow and counsel approval are governance
  deliverables, not code changes in this tree.
- No live WhatsApp socket, Meta webhook, Fly deployment or production log probe was
  performed. This is intentionally a local review tree.

## Verification evidence

- `node --check` passed for the policy module, listener and `server.mjs`.
- R-F3578 unit/capability/wiring tests: 9 passing.
- Final relevant WA regression batch: 68/68 passed. During the first run, two
  structural matchers exposed inconsistent assumptions about where inbound
  liveness was marked; the real handler now marks it at entry and both watchdog
  contracts pass.
- A later full HTML/page run was blocked by Windows runner error 1312 before the
  process spawned. Do not treat those checks as passed.

## Claude sign-off questions

1. Is fail-closed synchronous web-to-listener consent propagation the correct
   availability/safety trade-off for pause, revoke and first consent?
2. Should revocation additionally delete the per-account auth directory after
   successful `logout()`, or should forensic/session metadata remain until an
   explicit account deletion?
3. Confirm the initial `forwarded_or_tagged`-only pilot scope before broader chat
   selectors are built.
4. Review whether the official Meta gateway should replace the deprecated router
   at `/api/whatsapp` or receive a new versioned path for a controlled migration.
5. Reject sign-off if the official-channel Gate A scope is expected in this same
   R-number; it is accurately recorded as open above.
