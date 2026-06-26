# ARIA Guardian — Roadmap & Living-Organism Analysis

> Reserved: **R-F1984** · authored 2026-06-27 (night handoff) · owner: operator + Claude
> Companion docs: `memory/aria_guardian_vision_2026_06_26.md`, `memory/aria_guardian_phase0_rf1979_2026_06_26.md`
> **Binding context:** Guardian is Phase-B work proceeding under the operator's explicit, repeated Phase-A override ("do it robustly, no corner cutting", "lets push aria guardian robustly"). Law-as-boundary throughout: no impersonation of/towards non-consenting parties, no stalking, no surveillance of people who haven't consented.

---

## 0. TL;DR — pick up here tomorrow morning

**FIRST TASK: Layer 3 — make intent understanding multilingual platform-wide.** Extend the Guardian LLM interpreter (`aria_service/guardian/interpret.py`, shipped R-F1983) into a general intent layer so `routes/aria.py:_detect_tool_intent`'s English-regex routing also understands any language for ALL tools (investigate / screen / DD / research / guardian). Today a Portuguese "investiga a empresa Acme" or French "vérifie cette société" can miss the right tool. This is the real SOTA gap; Guardian was just where it first surfaced.

Then: real-phone validation of the full check-in→circle-alert chain, and the Twilio "real calls" go/no-go.

---

## 1. Where we are (shipped & live)

| Capability | R-number | State |
|---|---|---|
| Action Gateway (consent tiers · panic kill-switch · encrypted circle · tamper-evident audit · §25 outcome) | R-F1979 | LIVE |
| Dead-man's-switch check-in (two-stage: ping YOU at the deadline → escalate to circle) | R-F1979/1982 | LIVE |
| Send-as-you (confirm-gated WhatsApp message from your own linked number) | R-F1981 | LIVE |
| Panic / SOS (instant alert to whole circle) | R-F1981 | LIVE |
| Trusted circle vault (Fernet-encrypted contacts) | R-F1979 | LIVE |
| Intent comprehension **Layer 1** — deterministic fast-path + multilingual SOS words | R-F1981/1983 | LIVE |
| Intent comprehension **Layer 2** — LLM interpreter, ANY language, fail-safe | R-F1983 | LIVE (brain) |

**The keystone insight:** every Guardian capability flows through ONE hardened door — `guardian/gateway.py:execute(req, send_fn)` = classify risk → kill-switch → consent gate → deliver (injected `send_fn`) → tamper-evident audit → §25 delivery outcome → escalate-on-safety-failure. Because delivery is an **injected function**, adding a new "limb" (a phone call, a location pin, an email) is a new `send_fn`, **not** a rearchitecture. This is what makes "she can do anything" tractable instead of hand-wavy.

---

## 2. TO-DO LIST (sequenced)

### Tomorrow — Layer 3: multilingual platform-wide intent (the headline)
- [ ] **L3.1** Generalize `guardian/interpret.py` → an `intent/interpret.py` that classifies into the full tool vocabulary (`investigate`, `screen`, `dd`, `research`, `guardian.*`, `help`, `none`) with params, multilingual, JSON-out, fail-safe to `none`.
- [ ] **L3.2** Wire it as a **fallback after** `_detect_tool_intent` returns `None` (NOT a replacement) — so the fast, proven English regex stays the primary path and the LLM only fills the gap (non-English / unusual phrasing). Gate behind a flag (`ARIA_LLM_INTENT_FALLBACK=1`) + a confidence threshold; on low confidence fall through to normal chat.
- [ ] **L3.3** Reuse `intel/comprehension.py:detect_language_signal` to decide WHEN to invoke the LLM fallback (don't pay for it on obvious-English-tool hits).
- [ ] **L3.4** Capability tests: PT/ES/FR/AR phrasings for investigate/screen/dd actually route to the right tool; a normal question still → `none`; never fabricates an entity.
- [ ] **L3.5** Cost guard: it REPLACES the chat LLM call for a matched tool command (not a 2nd call); only false-positive language hits add one cheap classification. Log dropped/uncertain cases.

### Validation & hardening (carry-over)
- [ ] **V.1** Real-phone end-to-end: enrol a REAL WhatsApp circle contact → arm 1-min check-in → don't reply → confirm the self-ping AND the circle alert land on real handsets. (Needs operator's phone; last-mile never yet proven.)
- [ ] **V.2** Confirm the stage-1 self-ping renders correctly in a GROUP vs a 1:1 chat (origin-chat delivery, R-F1982).
- [ ] **V.3** R-F1971 (open): scope the pre-commit `check_wiring_present`/`check_circuit_breaker` to added lines + fix the cp1252 emoji-diff crash in `scripts/pre-commit` (currently bypassed with `--no-verify`).

### Decisions that need the operator (surface, don't silently skip — §18/§19e)
- [ ] **D.1** **Twilio Voice** — to make ARIA actually CALL a phone (the thing Baileys cannot do). ~$1/mo number + ~$0.013/min. Needs an account + `TWILIO_*` secrets. **Recommended** — a ringing phone cuts through when texts are ignored; highest safety value.
- [ ] **D.2** **Companion mobile app** — the only way to unlock real-time GPS, geofence ("when you're home"), background panic button, and on-device calling. Build effort (Flutter/React-Native thin client) — a project, not a config flip.

---

## 3. FULL ANALYSIS — Guardian as a living organism

The operator's framing: *"her living organism, she can do everything and anything."* Mapped to a real, buildable anatomy. Each system below is honest about **what exists now** vs **what needs a channel/decision**.

### 3.1 The nervous system — the Action Gateway (DONE)
One typed door for every real-world act. Risk classes {`NOTIFY_ME`, `SEND_AS_USER`, `ALERT_CIRCLE`, `SHARE_LOCATION`, `EMERGENCY`} → consent tiers {`AUTO`, `CONFIRM`, `CIRCLE_ONLY`, `PRE_AUTHORIZED`}. Everything new plugs in here, so safety/audit/proprioception are **structural, not per-feature**. *Extend by adding risk classes + send_fns, never by bypassing the gateway.*

### 3.2 The brain — comprehension (Layers 1–3)
- **L1 reflex (done):** instant deterministic match for the clearest, most urgent commands (panic/all-clear/stop) — works even if the brain is slow/down. Multilingual SOS words live.
- **L2 cortex (done):** LLM reads ANY language, extracts structured intent, fails safe.
- **L3 association cortex (next):** the SAME interpreter generalised to ALL of ARIA's tools → truly language-agnostic understanding platform-wide.
- **Future L4 — dialogue/clarify:** on high-stakes ambiguity, ask ONE specific question (reuse `intel/comprehension.py` clarification mode + `pending_actions`) instead of guessing.

### 3.3 The limbs — output channels (the "she can DO things" part)
| Limb | Action | Status |
|---|---|---|
| WhatsApp text | send-as-you, alerts, check-in pings | **LIVE** |
| WhatsApp **location pin** | `SHARE_LOCATION` via Baileys `locationMessage` | buildable now (needs a "places" store + incoming-pin capture) |
| WhatsApp media | voice-note alert, image | buildable now |
| **Telephone call (voice)** | ring + TTS safety message, or connect | **needs Twilio (D.1)** — Baileys CANNOT place calls |
| Email | escalation / report delivery | buildable (existing mail path) |
| Companion app | on-device call, alarm, background panic | **needs the app (D.2)** |

**Honest hard limit:** Baileys (the WhatsApp library) can receive/detect calls but **cannot place** voice/video calls. "She's an AI so she can do anything" is true of her *reasoning*; the *channel* still needs a phone line. Twilio gives her a real voice; the gateway makes it a drop-in `send_fn`.

### 3.4 The senses — proprioception (§25)
She must KNOW the outcome of every act (`delivered_real_answer | timeout_fallback | error | send_failed`). The gateway already wires guardian outcomes to the brain + a queryable surface, and a failed EMERGENCY **escalates** (records a gap → self-heal). Extend: a per-user "Guardian status" view — active check-ins, last delivery per channel, circle health.

### 3.5 The memory — who & where she protects
- **Circle** (done): encrypted trusted contacts.
- **Places** (next): named locations ("home", "work") for location-share + future geofence; coords captured from a shared WhatsApp pin (no server GPS).
- **Preferences/patterns** (future): usual routes, typical check-in times, "who to call first" escalation order.

### 3.6 Autonomy — self-healing & escalation ladders
- Escalation ladder: alert circle in priority order with delays; if contact 1 doesn't ack, try 2, then call (Twilio), then …
- Self-heal: an undelivered safety action is a first-class gap the autonomous coder/heal loop acts on.
- Scheduled/recurring check-ins ("every night at 22:00 confirm I'm home").

### 3.7 The conscience — safety & ethics (non-negotiable)
Consent-tiered by design; panic kill-switch ("ARIA stop"); law-as-boundary (no impersonation toward non-consenting parties, no covert tracking of people who didn't opt in); tamper-evident audit answers "what did ARIA do as me?". **A capability that can't pass the gateway's consent/audit doesn't ship.**

---

## 4. Flagship experiences (what "wonders" looks like, concretely)
1. **Dead-man's-switch walk-home** (LIVE): "check on me in 20 min" → pings you → escalates to circle if silent.
2. **Panic phrase** (LIVE): a word → instant SOS to your circle.
3. **Trip companion** (next): "watch me to the station" = periodic check-ins + share-location to a chosen contact.
4. **Voice escalation** (needs D.1): if texts go unanswered, ARIA *calls* the circle and speaks the alert.
5. **Geofence "I'm home"** (needs D.2): auto-disarm when you reach home; auto-alert if you don't by an expected time.

---

## 5. Phasing
- **Phase 1 (done):** gateway · check-in · send-as-you · panic · circle · multilingual L1/L2.
- **Phase 2 (next sprint):** Layer 3 intent · location sharing + places · escalation ladder · Guardian status surface · real-phone validation.
- **Phase 3 (operator-gated):** Twilio voice calls (D.1) · scheduled/recurring check-ins.
- **Phase 4 (project):** companion app → real GPS/geofence/on-device panic/calling.

---

## 6. One-line resume for tomorrow
> Start at **§2 → Layer 3** (generalise `guardian/interpret.py` into a multilingual fallback behind `_detect_tool_intent`, flag-gated, tested on PT/ES/FR). Then real-phone validation. Surface the Twilio decision (D.1) when the operator's ready to give ARIA a real voice.
