# Design Partner Kit — Phase A Gate #7

**Goal:** 4 discovery conversations with prospective ARIA users (defence brokers, compliance officers, small-fund DD teams, OEM export-control specialists) **before** Phase B sovereign-LLM buildout begins. Per `platform_buildout_north_star.md`, this gate closes only when 4 conversations are logged AND have surfaced concrete pain-validations or kill-the-idea signals.

**Time budget per conversation:** 30 min discovery + 15 min note-taking = 45 min. 4 conversations × 45 min ≈ 3 hours of operator time over 2-3 weeks.

**Critical doctrine:** these are **discovery interviews**, NOT demos. You are NOT pitching ARIA. You are listening for pain patterns. The day you start describing features instead of asking questions is the day the signal turns to noise.

---

## Part 1 — Positioning Sheet (forward-or-paraphrase)

> **One sentence:** ARIA is an AI compliance + intelligence agent for defence brokers and dual-use exporters who need due diligence done in 30 minutes instead of 3 days, without missing the sanctions divergence or UBO chain that turns into a regulatory incident.
>
> **Who it's for:** people who currently run DD on counterparties by opening 8 browser tabs (OpenSanctions, Companies House, OFSI, OFAC, EU consolidated, Wayback, LinkedIn, Google news in 5 languages) and stitching the answer together by hand. Brokerage shops, mid-cap OEM export-compliance teams, fund DD desks.
>
> **What it does today:** runs the full 10-layer DD chain (sanctions, network, UBO walk, compliance, commercial coherence, counter-intel, sanctions divergence, forensic, verification, synthesis) on a name or domain in ~3 minutes. Produces a signed audit-grade PDF with citations. Refuses to fabricate. Surfaces hard counts ("3 facts confirmed, 0 unsupported, 2 contradicted, 4 require human review") instead of confident prose.
>
> **What it doesn't do:** replace legal sign-off, replace the ECJU/OFAC licence process itself, or pretend a low-confidence answer is high-confidence.
>
> **What we're asking:** 30 minutes of your time to understand your workflow, NOT to demo. If after the conversation you're curious to try it, we'll send a sandbox link.

Operator note: do NOT send the kit before the conversation. Hold the positioning until the prospect surfaces their pain — then it lands.

---

## Part 2 — 5-Question Discovery Script

### Q1 — Establish pain ground (5 min)
> *"What's the most painful 30 minutes of your week?"*

Listen for: repeated manual work, dread, audit anxiety, "I always worry I missed something."

Follow-up if they go vague: *"Walk me through the last DD you did. What were the inputs, what were the outputs, where did you waste time?"*

**Anti-signal:** if they describe a smooth, fully-tooled workflow, this prospect isn't gate-#7 material — they're already solved. Move on.

### Q2 — Quantify the cost (5 min)
> *"How long does a typical due-diligence cycle take you? And what's the worst-case version of it?"*

Listen for: hours/days, specific friction points, "we had to go back and re-do it when X happened."

Follow-up: *"If you got a clean signal in 30 minutes instead of 3 days, what would you do with the saved time?"*

**Signal you want:** specific concrete answer ("I'd take on 2 more clients", "I'd actually screen the long-tail counterparties we currently skip").

**Anti-signal:** vague "be more productive" — means the pain isn't bound yet.

### Q3 — Surface failure modes they've hit (10 min)
> *"Have you ever had a counterparty come back with a problem you didn't catch on first screen? What did you miss?"*

Listen for: real incidents — sanctions divergence between regimes, UBO chain they didn't follow, virtual-office shell they didn't catch, a fabricated company number, retroactive sanctions update they missed.

Follow-up: *"What did you change after that?"*

**This is the gold question.** Real incidents are the truth-set you'll evaluate ARIA against. If they share a specific case, you have a counterparty-level eval-question for your golden-set gate #6.

### Q4 — Test the value prop directly (5 min)
> *"If a tool ran your full DD chain in 3 minutes with citations, but refused to give you an answer when it wasn't confident, how would you actually use it in your workflow?"*

Listen for: where they'd insert it (pre-screen vs final check vs both), what would make them not trust it, what they'd want to override.

**Signal:** specific integration mental model — "I'd run it before sending to legal" or "I'd want to attach the PDF to the file."

**Anti-signal:** "I don't know" or "I'd have to think about it" — means they haven't internalised the value yet.

### Q5 — Probe the kill-the-idea signal (5 min)
> *"What's the ONE thing about an AI tool like this that would make you not use it, no matter how good the rest is?"*

Listen for: trust ceiling, audit-trail anxiety, regulator-conversation fear, sovereign-data concerns, "I need to know it's reasoning isn't black-box."

Follow-up: *"If we addressed that completely, would you actually adopt it? Or is there a deeper reason?"*

**Critical:** this question lets the prospect tell you the truth they're afraid to volunteer. The answer often kills a feature roadmap item — that's the discovery doing its job.

---

## Part 3 — Post-Call Note Template

Fill this in within 1 hour of each call. Don't trust your memory beyond that window.

```
DESIGN PARTNER CONVERSATION #__
═══════════════════════════════

Date / time:        2026-MM-DD HH:MM
Duration:           __ min
Channel:            (call / Zoom / in-person / WhatsApp)
Prospect role:      (broker / compliance officer / fund DD / OEM)
Prospect firm size: (1-5 / 6-20 / 21-100 / 100+ people)
Prospect region:    (UK / EU / Gulf / SEA / Africa / multi)

PAIN GROUND (Q1)
────────────────
What is their most painful repeated work?

VOLUMETRIC COST (Q2)
────────────────────
Hours per DD cycle (typical / worst case):
What would they do with reclaimed time:

REAL INCIDENTS / FAILURE MODES (Q3)
────────────────────────────────────
Have they had a "miss" incident? Describe:
What did they change after:
**Eligible for our eval golden set?  (y/n) — what's the test question?**

VALUE-PROP TEST (Q4)
────────────────────
Where in their workflow would ARIA fit:
What would make them not trust it:

KILL-THE-IDEA (Q5)
──────────────────
One thing that would make them never adopt:
Did they hint at a deeper concern:

OVERALL READ
────────────
Pain-validated (1-5):                    __ (1 = no real pain, 5 = burning hair)
Trust-likely-to-build (1-5):             __ (1 = will never trust, 5 = ready today)
Would-pay-for-it (1-5):                  __ (1 = no way, 5 = will sign now)
Would-trial-sandbox (y/n):
Follow-up sandbox sent:                  (y/n, date)

QUOTES (verbatim, use sparingly)
────────────────────────────────
"..."
"..."

ACTIONS
───────
- [ ] Send sandbox link by ____
- [ ] Add to eval golden set: (test question + expected answer)
- [ ] Re-contact in __ weeks for follow-up
- [ ] Add to (no-fit / sandbox / paid-pilot) bucket
```

---

## Part 4 — Conversation Sourcing

Prioritised list of where to find 4 prospects without burning your network:

1. **Existing Arkmurus contacts (1-2 conversations).** People you've already done DD work for — they know what pain feels like.
2. **WhatsApp defence-broker group (1 conversation).** Light-touch — "I'm building a tool to make DD faster, 30 min of your brain in exchange for early access."
3. **LinkedIn Sales Navigator (1 conversation).** Search: compliance officers at mid-cap defence OEMs (Hensoldt, Saab, Indra, RUAG, Nammo, etc.). Cold outreach with the one-sentence positioning. Expect 5-10% reply rate; send 20 to get 1.
4. **Sourcing the 4th — operator's call.** Possibilities: defence-broker association (DIVAC UK, ASD-Europe), a procurement gazette commentator on LinkedIn, or a known compliance-tech founder who'd give competitive intel.

---

## Part 5 — When This Gate Closes

Per `platform_buildout_north_star.md`, gate #7 is **closed** when ALL of:

1. **4 completed conversations**, each ≥20 min in duration
2. **All 4 post-call notes filled in** (no skipped sections)
3. **At least 2 conversations** surface specific real-world incidents (Q3) — these become new entries in `sanctions_divergence` or `counter_intel` of the eval golden set (also helps close gate #6)
4. **A summary memo** authored at end of cycle: pain-pattern themes, common kill-the-idea signal, which 2 prospects are paid-pilot candidates, which (if any) are no-fit

The gate does NOT require 4 sandbox-ready prospects. Even 4 conversations that **kill** the idea would close the gate — it's about generating real signal, not selling.

**Once closed, flip the gate criterion in `docs/aria_platform_buildout_2026_05_10.md` and `memory/platform_buildout_north_star.md` (currently 5 of 7 open → would be 4 of 7).**

---

## Part 6 — What I (ARIA / Claude) Can Help With

Things you can ask me for as you go:

- **Pre-call:** brief background on the prospect's firm (Companies House + sanctions + recent procurement awards)
- **Pre-call:** translate the positioning sheet into another language for non-English prospects
- **Post-call:** turn a specific incident the prospect described into an eval golden-set entry (closes 2 gates with one conversation)
- **Post-call:** draft a follow-up email or sandbox-link message
- **After 2+ calls:** spot the pattern across notes and flag what's repeating

Just say "Aria, do X for design partner #N" and I'll fill it in.

---

*Authored 2026-05-14 by Claude/ARIA as R-F506. Living document — operator can edit freely as the conversations surface what the script gets wrong.*
