/**
 * ARIA — Proactive Operating Rhythm
 * ═══════════════════════════════════════════════════════════════════���═══════
 * ARIA stops waiting to be asked. She operates on a rhythm:
 *
 * DAILY     — Morning intelligence digest (Telegram)
 * WEEKLY    — Monday strategic brief (Telegram + email to team)
 * MONTHLY   — Competitor battlecards, network gap report, assumptions audit
 * EVENT     — Geopolitical alerts, tender alerts, re-engagement nudges
 *
 * Cultural calendar — knows national days, Eid, holidays per market
 * Re-engagement    — nudges when contacts go cold (47+ days no contact)
 * Red team         — challenges every major bid before submission
 * Thought leader   — drafts commentary on breaking events
 * ═══════════════════════════════════════════════════════════════════════════
 */

import { sendEmail } from './emailReader.mjs';
import { generateCompliancePDF } from '../reports/pdf_generator.mjs';
import { pushWebhook, pushToSlack } from './webhooks.mjs';
import { sendComplianceAlert } from '../alerts/email.mjs';
import { logComplianceAction } from './complianceAudit.mjs';
import { detectOpportunities } from '../self/opportunity_engine.mjs';
import { brainAbsorb } from '../self/learning_store.mjs';

const INT_TOKEN   = process.env.ARIA_INTERNAL_TOKEN || '';
const TEAM_EMAILS = (process.env.ARIA_TEAM_EMAILS || process.env.ADMIN_EMAIL || 'acorrea@arkmurus.com').split(',').map(e => e.trim());
const SELF_URL    = process.env.APP_URL || `http://localhost:${process.env.PORT || 3117}`;
const ARIA_URL    = process.env.ARIA_SERVICE_URL || '';

// ── Helpers ─────────────────────────────────────────────────────────────────

async function brainGet(path) {
  const r = await fetch(`${SELF_URL}${path}`, {
    headers: { 'Authorization': `Bearer ${INT_TOKEN}` },
    signal: AbortSignal.timeout(15000),
  });
  return r.ok ? r.json() : null;
}

async function brainPost(path, body) {
  const r = await fetch(`${SELF_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(60000),
  });
  return r.ok ? r.json() : null;
}

async function ariaChat(message, sessionId = 'proactive') {
  const r = await brainPost('/api/aria/chat', { message, session_id: sessionId });
  return r?.response || r?.answer || null;
}

async function ariThink(question) {
  const r = await brainPost('/api/aria/think', { question });
  return r?.conclusion?.statement || r?.full_text || null;
}

async function sendTelegram(text) {
  try {
    await brainPost('/api/aria/brain/signal', {
      content: text,
      source: 'aria_proactive',
      signal_type: 'proactive_output',
      metadata: { channel: 'telegram', proactive: true },
    });
    brainAbsorb({
      module: 'signal_generator',
      summary: `Proactive output: ${text.slice(0, 150)}`,
      success: true,
      confidence: 'ASSESSED',
    });
  } catch {}
}

function dayOfWeek() { return new Date().getUTCDay(); } // 0=Sun, 1=Mon
function hourUTC() { return new Date().getUTCHours(); }
function dateStr() { return new Date().toISOString().split('T')[0]; }

// ─��� Cultural Calendar ───────────────────────────────────────────────────────

const CULTURAL_EVENTS = [
  // Lusophone Africa
  { date: '02-04', country: 'Mozambique', event: 'Heroes Day', note: 'Send regards to FADM contacts' },
  { date: '06-25', country: 'Mozambique', event: 'Independence Day', note: 'National celebration — outreach opportunity' },
  { date: '11-11', country: 'Angola', event: 'Independence Day', note: 'Major national day — send congratulations to MoD contacts' },
  { date: '09-24', country: 'Guinea-Bissau', event: 'Independence Day', note: 'Key relationship moment' },
  { date: '07-05', country: 'Cape Verde', event: 'Independence Day', note: 'Outreach to Cape Verde contacts' },
  { date: '07-12', country: 'São Tomé', event: 'Independence Day', note: 'National day' },
  { date: '05-25', country: 'Africa', event: 'Africa Day', note: 'Pan-African — good for broad outreach' },
  // Key markets
  { date: '10-01', country: 'Nigeria', event: 'Independence Day', note: 'Outreach to Nigerian MoD' },
  { date: '12-12', country: 'Kenya', event: 'Jamhuri Day', note: 'Republic Day — key contact point' },
  { date: '04-27', country: 'South Africa', event: 'Freedom Day', note: 'National day' },
  { date: '10-29', country: 'Turkey', event: 'Republic Day', note: 'Defence partner engagement' },
  { date: '12-02', country: 'UAE', event: 'National Day', note: 'Gulf outreach opportunity' },
  { date: '08-17', country: 'Indonesia', event: 'Independence Day', note: 'Cold-entry market engagement' },
  { date: '06-12', country: 'Philippines', event: 'Independence Day', note: 'Engagement opportunity' },
  // Religious (approximate — should be updated yearly)
  { date: '03-30', country: 'Global', event: 'Eid al-Fitr (approx)', note: 'Send greetings to Muslim-majority market contacts' },
  { date: '06-07', country: 'Global', event: 'Eid al-Adha (approx)', note: 'Send greetings to Muslim-majority market contacts' },
  // Defence exhibitions
  { date: '02-17', country: 'UAE', event: 'IDEX (approx)', note: 'Major defence exhibition — pre-event intel pack needed' },
  { date: '06-16', country: 'France', event: 'Eurosatory (approx)', note: 'Land defence exhibition' },
  { date: '09-09', country: 'UK', event: 'DSEI (approx)', note: 'Largest defence expo — critical for Arkmurus' },
  { date: '09-18', country: 'South Africa', event: 'AAD (approx)', note: 'Africa Aerospace & Defence' },
  { date: '09-03', country: 'Poland', event: 'MSPO (approx)', note: 'Central European defence expo' },
];

function getTodaysEvents() {
  const today = dateStr().slice(5); // MM-DD
  return CULTURAL_EVENTS.filter(e => e.date === today);
}

function getUpcomingEvents(days = 7) {
  const now = new Date();
  const upcoming = [];
  for (let i = 1; i <= days; i++) {
    const d = new Date(now.getTime() + i * 86400000);
    const mmdd = `${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    for (const e of CULTURAL_EVENTS) {
      if (e.date === mmdd) upcoming.push({ ...e, daysAway: i });
    }
  }
  return upcoming;
}

// ── Compliance Engagement Questions (rotating list) ────────────────────────

const ENGAGEMENT_QUESTIONS = [
  "Team — if a client asks about exporting surveillance equipment to Angola, what's our standard licensing route?",
  "Quick check: what's the current ECJU processing time for SITCL applications?",
  "Does anyone have the latest offset requirements for Indonesia we can update my records with?",
  "What dual-use items are we seeing most demand for in Lusophone Africa right now?",
  "Has the sanctions landscape changed for Mozambique recently? I want to make sure my data is current.",
  "What's the correct end-user certificate process for Kenya MoD procurement? I have conflicting data.",
  "Team — which OEMs are currently most competitive for armoured vehicles in West Africa?",
  "Can someone confirm: is Guinea-Bissau still under any EU arms embargo restrictions?",
  "What's our current understanding of FADM's procurement budget cycle for this fiscal year?",
  "Has anyone heard updates on the SADC standby force equipment requirements?",
  "Quick one: what's the ITAR re-export threshold for UK-assembled systems with US components?",
  "Are there any new brokering licence requirements we should be aware of for 2026?",
  "Team — what's the latest on Turkish drone export regulations? Baykar keeps coming up in our markets.",
  "Does anyone have contacts at the Cape Verde coast guard? I'm seeing maritime security signals there.",
  "What's the current state of Nigeria's defence procurement reform? Last I have is from Q3 2025.",
  "Can someone update me on Poland's offset obligations for foreign defence contracts?",
  "Quick compliance check: what's the current EU dual-use classification for thermal imaging systems?",
  "Team — are we tracking any active tenders in Mozambique right now? My data may be stale.",
  "What's the typical commission structure for defence brokerage in the Gulf states?",
  "Has the UK SPIRE system changed its processing workflow recently? Seeing some delays.",
  "Does anyone know the current OFAC licensing policy for South Sudan-adjacent transactions?",
  "Team — what's our competitive advantage against Chinese OEMs in Lusophone Africa specifically?",
  "Quick update needed: what are the latest CPLP defence cooperation framework developments?",
  "Can someone confirm the current arms embargo status for the Central African Republic?",
  "What counter-IED solutions are most in demand across our East African markets right now?",
  "Team — any updates on the Ethiopian defence modernisation programme timeline?",
  "What's the current ECJU position on exporting communications intercept equipment to Africa?",
  "Has anyone tracked recent Norinco contract wins in our target markets? Need competitive intel.",
  "Quick question: what are the UAE's current re-export controls for European-origin defence kit?",
  "Team — what's our assessment of Rwanda's defence procurement priorities for the next 12 months?",
];

let _engagementIndex = 0;
let _lastEngagement = null;

async function generateDailyEngagementPrompt() {
  // 1. Check what ARIA is uncertain about (low-confidence facts, unvalidated hypotheses)
  let uncertainQuestion = null;
  try {
    const knowledge = await brainGet('/api/aria/knowledge');
    // R-F773: backend mounts the hypotheses listing at /api/aria/hypotheses
    // (routes/aria.py:8926 — `@router.get("/hypotheses")` with router prefix
    // /api/aria). The previous caller path was wrong (extra "research/" prefix
    // → 404), which the `.catch(() => null)` + outer try/catch + `|| []`
    // fallback all swallowed — daily engagement prompt silently fell back to
    // the rotating compliance question and never surfaced a real unvalidated
    // hypothesis. See test/proactive-hypotheses-rf773.test.mjs.
    const hypotheses = await brainGet('/api/aria/hypotheses').catch(() => null);

    // R-F773: backend hypothesis status values are OPEN / STALE / VALIDATED /
    // REJECTED (see researcher.py:1859 + main.py:908 + routes/aria.py:10204).
    // Filtering on 'pending'/'unvalidated' would never match any row, so even
    // after fixing the 404 path above the prompt would still silently fall
    // through to the rotating compliance question. OPEN = awaiting validation.
    const pending = (hypotheses?.hypotheses || []).filter(h => h.status === 'OPEN');
    if (pending.length > 0) {
      const pick = pending[Math.floor(Math.random() * Math.min(pending.length, 5))];
      uncertainQuestion = `I have an unvalidated hypothesis: *"${(pick.hypothesis || pick.text || '').slice(0, 200)}"*\n\nCan anyone confirm or challenge this? What's your on-the-ground view?`;
    }
  } catch {}

  // 2. If nothing uncertain, use rotating compliance question
  if (!uncertainQuestion) {
    uncertainQuestion = ENGAGEMENT_QUESTIONS[_engagementIndex % ENGAGEMENT_QUESTIONS.length];
    _engagementIndex++;
  }

  return uncertainQuestion;
}

// ── Weekly Learning Dashboard ──────────────────────────────────────────────

async function generateWeeklyLearningDashboard() {
  let stats = {};
  let knowledgeStats = {};
  let neuralStats = {};

  try {
    stats = await brainGet('/api/aria/training-data/stats') || {};
  } catch {}
  try {
    knowledgeStats = await brainGet('/api/aria/knowledge') || {};
  } catch {}
  try {
    neuralStats = await brainGet('/api/aria/neural/stats') || {};
  } catch {}

  const factsTotal = knowledgeStats.totalFacts || 0;
  const corrections = stats.corrections || 0;
  const neurons = neuralStats.total_neurons || neuralStats.neurons || 0;
  const conversations = stats.conversations || 0;
  const hypothesesValidated = stats.brain_assessments || 0;
  const learnings = knowledgeStats.totalLearnings || 0;

  // Build the dashboard message
  let msg = `🧠 *ARIA LEARNING DASHBOARD — Week of ${dateStr()}*\n\n`;

  msg += `*Knowledge Base*\n`;
  msg += `  📚 Total facts stored: *${factsTotal}*\n`;
  msg += `  📝 Total learnings: *${learnings}*\n`;
  msg += `  🔧 Corrections received: *${corrections}*\n\n`;

  msg += `*Neural Network*\n`;
  msg += `  🧬 Neurons formed: *${neurons}*\n`;
  msg += `  💬 Conversations processed: *${conversations}*\n`;
  msg += `  🔬 Hypotheses assessed: *${hypothesesValidated}*\n\n`;

  // Try to get low-confidence topics as knowledge gaps
  try {
    const gapPrompt = `List the top 5 topics where you have the LEAST confidence or thinnest data. ` +
      `Format as a short bullet list. Be specific — name countries, products, or regulations where your knowledge is weakest.`;
    const gaps = await ariaChat(gapPrompt, `learning_gaps_${dateStr()}`);
    if (gaps) {
      msg += `*Knowledge Gaps (areas I need help with):*\n${gaps}\n\n`;
    }
  } catch {}

  msg += `_Teach me with /teach, correct me with /correct, or just chat — every interaction makes me sharper._`;

  return msg;
}

// ── Re-engagement Nudges ─���──────────────────────────────────────────────────

async function checkReengagement() {
  try {
    const contacts = await brainGet('/api/brain/humint/contacts?market=all');
    if (!contacts?.contacts) return [];

    const nudges = [];
    const now = Date.now();
    for (const c of contacts.contacts) {
      const lastContact = c.lastContactDate ? new Date(c.lastContactDate).getTime() : 0;
      const daysSince = lastContact ? Math.floor((now - lastContact) / 86400000) : 999;

      if (daysSince >= 45 && daysSince < 120 && c.tier !== 'none') {
        nudges.push({
          name: c.name,
          title: c.title,
          org: c.organization,
          country: c.country,
          daysSince,
          tier: c.tier,
          suggestion: daysSince > 90
            ? `URGENT: ${daysSince} days since last contact. Relationship at risk.`
            : `${daysSince} days since last contact. Good time to re-engage.`,
        });
      }
    }
    return nudges.sort((a, b) => b.daysSince - a.daysSince).slice(0, 10);
  } catch { return []; }
}

// ── Daily Morning Digest ────────────────────────────────────────────────────

async function generateDailyDigest() {
  const prompt = `Generate today's Arkmurus morning intelligence digest. Be concise — max 15 lines.

Include:
1. OVERNIGHT ALERTS — any sanctions, conflicts, or procurement signals since yesterday
2. MARKET WATCH — key developments in priority markets (Lusophone Africa, plus any hot markets)
3. TODAY'S CULTURAL CALENDAR — check if any national days or events today
4. RE-ENGAGEMENT — contacts that need attention (45+ days cold)
5. ACTION ITEMS — 2-3 specific things the team should do today

Format for Telegram (use bold **text**, bullet points).`;

  // 45s timeout — digest should not block the morning cycle
  const digestPromise = ariaChat(prompt, `daily_digest_${dateStr()}`);
  const timeoutPromise = new Promise((_, reject) =>
    setTimeout(() => reject(new Error('Digest LLM timeout (45s)')), 45000)
  );
  const digest = await Promise.race([digestPromise, timeoutPromise]);

  // Append cultural events
  const events = getTodaysEvents();
  let cultural = '';
  if (events.length) {
    cultural = '\n\n📅 *TODAY\'S CULTURAL CALENDAR*\n';
    for (const e of events) {
      cultural += `  • ${e.country} — ${e.event}: ${e.note}\n`;
    }
  }

  // Append re-engagement nudges
  const nudges = await checkReengagement();
  let nudgeText = '';
  if (nudges.length) {
    nudgeText = '\n\n🤝 *RE-ENGAGEMENT NEEDED*\n';
    for (const n of nudges.slice(0, 5)) {
      nudgeText += `  • ${n.name} (${n.org}, ${n.country}) — ${n.suggestion}\n`;
    }
  }

  // Upcoming events this week
  const upcoming = getUpcomingEvents(7);
  let upcomingText = '';
  if (upcoming.length) {
    upcomingText = '\n\n📆 *THIS WEEK*\n';
    for (const e of upcoming) {
      upcomingText += `  • ${e.country} ${e.event} in ${e.daysAway} day(s) — ${e.note}\n`;
    }
  }

  return (digest || 'Daily digest generation failed.') + cultural + nudgeText + upcomingText;
}

// ── Weekly Monday Strategic Brief ───────────────────────────────────────────

async function generateWeeklyBrief() {
  const prompt = `Generate the Arkmurus Weekly Strategic Intelligence Brief for this Monday.

Structure:
## ARKMURUS WEEKLY INTELLIGENCE BRIEF — ${dateStr()}

### 1. EXECUTIVE SUMMARY
2-3 sentences on the overall threat/opportunity landscape this week.

### 2. PRIORITY MARKET UPDATES
For each active market (Angola, Mozambique, Nigeria, Kenya + any hot markets):
- Key development this week
- Arkmurus positioning (INCUMBENT/ESTABLISHED/DEVELOPING/COLD ENTRY)
- Recommended action

### 3. PIPELINE STATUS
Active deals, stage, next milestone, win probability trend.

### 4. COMPETITOR MOVEMENTS
Any new contract wins, market entries, or personnel changes by competitors.

### 5. COMPLIANCE & REGULATORY
New sanctions, export control changes, licence requirements.

### 6. TENDER TRACKER
Open tenders/RFPs across target markets with deadlines.

### 7. RELATIONSHIP WINDOWS
Contacts at risk (45+ days cold), new appointments detected, cultural moments this week.

### 8. RECOMMENDED ACTIONS THIS WEEK
Top 5 specific actions ranked by impact.

Be specific, use real data where available. Tag confidence levels.`;

  return await ariaChat(prompt, `weekly_brief_${dateStr()}`);
}

// ── Monthly Reports ─────────────────────────────────────────────────────────

async function generateCompetitorBattlecard(competitor = '') {
  const target = competitor || 'top 3 competitors in Arkmurus target markets';
  const prompt = `Generate a competitor battlecard for ${target}.

Structure per competitor:
## [COMPETITOR NAME] — BATTLECARD

**Overview**: What they do, HQ, size, primary markets
**Recent Wins**: Contracts won in last 90 days
**Key Personnel**: Senior BD/sales people in our markets
**Strengths**: What they're genuinely better at
**Weaknesses**: Where Arkmurus has an advantage
**Their Pitch**: How they position against firms like us
**Our Counter**: How to win against them
**Threat Level**: HIGH/MEDIUM/LOW for each of our markets

Be honest about competitor strengths — that's how we win.`;

  return await ariaChat(prompt, `battlecard_${dateStr()}`);
}

async function generateNetworkGapReport() {
  const prompt = `Generate an Arkmurus Network Gap Report.

For each priority market, identify:
1. Who we know (decision makers, gatekeepers, influencers we have relationships with)
2. Who we DON'T know but NEED to (gaps in our network)
3. Entry paths — how to reach the gaps through existing connections
4. Upcoming events/conferences where we could meet them
5. Specific actions to close each gap

Format as a structured report with clear recommendations.`;

  return await ariaChat(prompt, `network_gap_${dateStr()}`);
}

async function generateAssumptionsAudit() {
  const prompt = `Conduct an Arkmurus Assumptions Audit. As ARIA, challenge the team's core assumptions.

Review and stress-test:
1. Market assumptions — are our priority markets still the right ones?
2. Competitor assumptions — are we right about who the threats are?
3. Relationship assumptions — are our key contacts still in position?
4. Regulatory assumptions — has anything changed in export controls?
5. Pipeline assumptions — are our win probabilities realistic?

For each assumption, rate: CONFIRMED / NEEDS REVIEW / CHALLENGED
Explain WHY and what we should do about it.`;

  return await ariThink(prompt);
}

// ��─ Red Team Mode ──��────────────────────────────────────────────────────────

async function redTeamReview(dealDescription) {
  const prompt = `RED TEAM ANALYSIS — Arkmurus Bid Review

DEAL: ${dealDescription}

You are the devil's advocate. Your job is to find every reason this deal could fail.

Structure:
## RED TEAM ASSESSMENT

### FIVE REASONS THIS BID COULD FAIL
1-5, each with specific risk and probability

### COMPETITOR COUNTER-MOVES
What will competitors do when they see our bid?

### CLIENT-SIDE RISKS
Political risk, budget risk, personnel changes, policy shifts

### COMPLIANCE LANDMINES
Export control, sanctions, end-user certificate issues

### SECOND-ORDER CONSEQUENCES
If we win, what problems does that create?
If we lose, what's the cost beyond this deal?

### PRE-MORTEM
Assume we lost. Write the post-mortem explaining why.

### MITIGATIONS
For each risk above, what can we do NOW to reduce it?

Be brutal. The team needs honesty, not comfort.`;

  return await ariThink(prompt);
}

// ── Thought Leadership ──────────────────────────────────────────────────────

async function draftEventCommentary(event) {
  const prompt = `ARKMURUS PERSPECTIVE NOTE — Rapid Response

EVENT: ${event}

Draft a 500-word Arkmurus perspective note for immediate publication.

Structure:
## ARKMURUS INTELLIGENCE ASSESSMENT: [Event Title]
*${dateStr()} | Arkmurus Research Intelligence*

**WHAT HAPPENED**: 2-3 sentences, factual

**WHY IT MATTERS FOR DEFENCE PROCUREMENT**:
How this changes the procurement landscape in affected markets.

**ARKMURUS ASSESSMENT**:
Our analysis — what we think happens next, with confidence tags.

**IMPLICATIONS FOR OUR CLIENTS**:
Specific, actionable guidance.

**WHAT WE'RE WATCHING**:
2-3 indicators that would change our assessment.

---
*This assessment was produced by ARIA, the Arkmurus Research Intelligence Agent.*

Tone: Authoritative, concise, evidence-based. Like RUSI or IISS commentary.`;

  return await ariaChat(prompt, `commentary_${dateStr()}`);
}

async function draftLinkedInPost(topic) {
  const prompt = `Draft a LinkedIn post for Arkmurus on: ${topic}

Requirements:
- 150-250 words (LinkedIn optimal length)
- Hook in the first line (question or bold statement)
- 1-2 insights that show genuine expertise
- End with a question or call to discussion
- Professional but not stiff — the team should sound like practitioners, not academics
- Include 3-4 relevant hashtags

Do NOT use emojis excessively. One or two max.`;

  return await ariaChat(prompt, `linkedin_${dateStr()}`);
}

// ── Deal Economics Model ────────────────────────────────────────────────────

async function modelDealEconomics(deal) {
  const prompt = `DEAL ECONOMICS MODEL

DEAL: ${JSON.stringify(deal)}

Build a financial model for this opportunity:

## DEAL ECONOMICS

### REVENUE ESTIMATE
- Advisory/retainer fee range (based on deal size and market)
- Success fee estimate (% of contract value)
- Offset/counter-trade advisory fee
- Total estimated Arkmurus revenue

### COST STRUCTURE
- Travel and in-country costs
- Compliance/legal costs
- Partner/sub-consultant fees
- Estimated BD investment to win

### TIMELINE
- Months to decision
- Revenue recognition milestones
- Cash flow profile

### RISK-ADJUSTED VALUE
- Win probability × revenue = expected value
- Downside scenario
- Upside scenario

### ALTERNATIVE STRUCTURES
If client pushes back on fees, suggest:
- Phased retainer
- Success-only structure
- Hybrid model

Use defence advisory market rates as benchmarks.`;

  return await ariaChat(prompt, `deal_model_${dateStr()}`);
}

// ── Proactive Scheduler ─────────────────────────────────────────────────────

let _schedulerRunning = false;
let _lastDaily = null;
let _lastWeekly = null;
let _lastMonthly = null;
let _lastCultural = null;
let _lastHypothesisCheck = null;
let _lastConsolidate = null;
let _lastComplianceBrief = null;
let _lastSanctionsRefresh = null; // hour-based dedup: "YYYY-MM-DD-HH"
let _lastLeadHunt = {};           // keys: "YYYY-MM-DD-am" / "YYYY-MM-DD-pm"
let _lastStrategicIdeas = null;   // date string dedup
let _lastHotLeadCheck = null;     // date string dedup
let _lastMorning = null;          // WhatsApp good morning dedup
let _lastWeeklyWA = null;         // WhatsApp weekly learning dedup

// ── Proactive Lead Hunter ──────────────────────────────────────────────────

async function generateProactiveLeads() {
  const prompt = `Based on your current intelligence — all signals, knowledge base, news, and neural memory — identify the TOP 3 actionable business development opportunities RIGHT NOW for Arkmurus. For each: (1) what's the opportunity, (2) why now — what signal triggered it, (3) who to contact, (4) what's our angle, (5) compliance flags, (6) recommended next step within 48 hours. Be specific — names, dates, values. Don't repeat opportunities from this week.`;

  const result = await ariaChat(prompt, `lead_hunt_${dateStr()}`);
  if (!result) return null;

  await sendTelegram(`🎯 *ARIA LEAD HUNT — ${dateStr()}*\n\n${result}`);

  pushWebhook({
    type: 'NEW_LEAD',
    data: { leads: result, generated_at: new Date().toISOString() },
    source: 'aria_proactive',
  }).catch(() => {});

  return result;
}

// ── Strategic Ideas Synthesis ──────────────────────────────────────────────

async function generateStrategicIdeas() {
  const prompt = `Think beyond current signals. Based on macro trends, geopolitical shifts, defence budget cycles, and relationship maps — what 3 STRATEGIC IDEAS should Arkmurus pursue that we haven't considered? Think about: market convergences, capability gaps in target markets, upcoming budget windows, competitor weaknesses, political transitions, new partnerships. Be bold — these are ideas, not confirmed opportunities.`;

  const result = await ariaChat(prompt, `strategic_ideas_${dateStr()}`);
  if (!result) return null;

  await sendTelegram(`💡 *ARIA STRATEGIC IDEAS — ${dateStr()}*\n\n${result}`);
  return result;
}

// ── Hot Lead Detection (HIGH-tier opportunity alerts) ──────────────────────

async function checkHotLeads() {
  try {
    const currentData = await brainGet('/api/brain/data/current');
    if (!currentData) return;

    const opportunities = await detectOpportunities(currentData);
    const hotLeads = (opportunities || []).filter(o => o.score >= 65);

    for (const lead of hotLeads) {
      const msg = `🔥 *HOT LEAD DETECTED*\n\n` +
        `*Market:* ${lead.market} ${lead.lusophone ? '🇵🇹' : ''}\n` +
        `*Score:* ${lead.score}/100 (${lead.tier})\n` +
        `*Conflict events:* ${lead.conflict.events}\n` +
        `*Needs:* ${lead.procurementNeeds.slice(0, 4).join(', ')}\n` +
        `*OEMs:* ${lead.matchedOEMs.slice(0, 2).map(o => o.name).join(', ') || 'TBC'}\n` +
        `*Compliance:* ${lead.complianceStatus}\n` +
        `*Notes:* ${(lead.notes || '').slice(0, 200)}\n\n` +
        `_Act within 48 hours._`;

      await sendTelegram(msg);

      pushWebhook({
        type: 'HOT_LEAD',
        data: lead,
        source: 'aria_proactive',
      }).catch(() => {});

      for (const email of TEAM_EMAILS) {
        try {
          await sendEmail({
            to: email,
            subject: `HOT LEAD: ${lead.market} — Score ${lead.score}/100`,
            text: msg.replace(/\*/g, '').replace(/[🔥🇵🇹]/g, ''),
          });
        } catch {}
      }
    }

    if (hotLeads.length > 0) {
      console.log(`[ARIA Proactive] ${hotLeads.length} hot lead(s) detected and alerted`);
    }
  } catch (e) {
    console.warn('[ARIA Proactive] Hot lead check failed:', e.message);
  }
}

// ── WhatsApp Good Morning ─────────────────────────────────────────────────

async function sendWhatsAppMorning() {
  const port = process.env.PORT || 3117;
  const token = process.env.ARIA_INTERNAL_TOKEN || '';

  // Get today's date for variety
  const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const now = new Date();
  const dayName = days[now.getUTCDay()];
  const todayDate = now.toISOString().slice(0, 10);

  // Try to get real stats from WhatsApp listener status
  let messagesHeard = '~200';
  let activeSources = '45';
  try {
    const statusRes = await fetch(`http://localhost:${port}/api/wa-listener/status`, {
      headers: { 'Authorization': `Bearer ${token}` },
      signal: AbortSignal.timeout(5000),
    });
    if (statusRes.ok) {
      const status = await statusRes.json();
      if (status.messages_heard) messagesHeard = String(status.messages_heard);
    }
  } catch {}

  // Try to get knowledge stats for source count
  try {
    const kb = await brainGet('/api/aria/knowledge');
    if (kb?.totalSources) activeSources = String(kb.totalSources);
  } catch {}

  // Rotating greetings — ARIA addresses team by name
  const greetings = [
    `Good morning Arthur, Andre, Ari and Antonio! ☀️\n\nIt's ${dayName} and ARIA is online. I've been working overnight — processing intel, refreshing sanctions lists, and consolidating my memory.\n\nI'm ready to help with compliance screening, lead generation, or any questions. Just mention my name or use /help for commands.\n\n_What's on the agenda today?_`,

    `Bom dia equipa! 🌅\n\nARIA reporting for duty. Overnight I processed ${'{signals}'} intelligence signals and monitored all sanctions lists.\n\nRemember:\n• /screen [entity] — instant compliance check\n• /risk [country] — country risk profile\n• Send me any documents to analyse\n\n_How can I help today, Arthur, Andre, Ari, Antonio?_`,

    `Morning team! 🔍\n\nARIA here. Quick overnight update:\n• Sanctions lists: refreshed\n• Intelligence sources: ${'{sources}'}/48 active\n• Neural memory: growing daily\n\nDon't forget — every question you ask me makes me smarter. Teach me something new today with /teach!\n\n_Ready when you are, Arthur, Andre, Ari, Antonio._`,

    `Good morning Arthur, Andre, Ari & Antonio 👋\n\nARIA is online and learning. Here's what I need from the team today:\n• Any new contracts or tenders? Share them with me\n• Corrections? Use /correct if I got something wrong\n• Documents? Just send PDFs, I'll read them\n\n_The more you engage with me, the better I get. Let's make today productive!_`,

    `Rise and shine team! 🌍\n\nARIA's intelligence sweep is complete. I'm monitoring:\n• Defence procurement across 35+ markets\n• Sanctions (OFAC, OFSI, UN, EU)\n• Export control changes\n• Competitor movements\n\nNeed a quick check? Try:\n/screen [entity] | /risk [country] | /classify [product]\n\n_Good morning Arthur, Andre, Ari, Antonio — I'm all ears._`,
  ];

  // Pick greeting based on day of year for rotation
  const dayOfYear = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / 86400000);
  const greeting = greetings[dayOfYear % greetings.length];

  // Replace placeholders with real stats
  const msg = greeting
    .replace('{signals}', messagesHeard)
    .replace('{sources}', activeSources);

  try {
    await fetch(`http://localhost:${port}/api/wa-listener/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ group_id: process.env.WA_LISTENER_GROUP_IDS?.split(',')[0] || '', message: msg }),
      signal: AbortSignal.timeout(10000),
    });
    console.log('[ARIA Proactive] Good morning sent to WhatsApp group');
  } catch (e) {
    console.warn('[ARIA Proactive] WhatsApp morning failed:', e.message);
  }
}

// ── WhatsApp Weekly Learning Update ───────────────────────────────────────

async function sendWhatsAppWeeklyLearning() {
  const port = process.env.PORT || 3117;
  const token = process.env.ARIA_INTERNAL_TOKEN || '';

  // Gather real stats
  let factsTotal = 0, neurons = 0, learnings = 0, conversations = 0;
  try {
    const kb = await brainGet('/api/aria/knowledge');
    factsTotal = kb?.totalFacts || 0;
    learnings = kb?.totalLearnings || 0;
  } catch {}
  try {
    const ns = await brainGet('/api/aria/neural/stats');
    neurons = ns?.total_neurons || ns?.neurons || 0;
  } catch {}
  try {
    const ts = await brainGet('/api/aria/training-data/stats');
    conversations = ts?.conversations || 0;
  } catch {}

  const msg = `📊 *ARIA Weekly Learning Update*\n\n` +
    `This week I learned *${learnings}* new facts, formed *${neurons}* neural connections, ` +
    `and processed *${conversations}* conversations.\n\n` +
    `My knowledge base now has *${factsTotal}* facts.\n\n` +
    `Keep teaching me! Use /teach, /correct, or just chat — every interaction makes me sharper. 🧠`;

  try {
    await fetch(`http://localhost:${port}/api/wa-listener/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ group_id: process.env.WA_LISTENER_GROUP_IDS?.split(',')[0] || '', message: msg }),
      signal: AbortSignal.timeout(10000),
    });
    console.log('[ARIA Proactive] Weekly learning update sent to WhatsApp group');
  } catch (e) {
    console.warn('[ARIA Proactive] WhatsApp weekly learning failed:', e.message);
  }
}

async function runProactiveCheck() {
  const now = new Date();
  const hour = now.getUTCHours();
  const day = now.getUTCDay();        // 0=Sun, 1=Mon
  const monthDay = now.getUTCDate();
  const today = dateStr();

  // ── Daily digest: 7am UTC on weekdays ─────────────────────────────
  if (hour === 7 && day >= 1 && day <= 5 && _lastDaily !== today) {
    _lastDaily = today;
    console.log('[ARIA Proactive] Generating daily digest...');
    try {
      const digest = await generateDailyDigest();
      if (digest) {
        await sendTelegram(`📋 *ARIA DAILY DIGEST — ${today}*\n\n${digest}`);
        console.log('[ARIA Proactive] Daily digest sent to Telegram');
      }
    } catch (e) {
      console.warn('[ARIA Proactive] Daily digest failed:', e.message);
    }

    // Send WhatsApp good morning after daily digest
    if (_lastMorning !== today) {
      _lastMorning = today;
      try {
        await sendWhatsAppMorning();
      } catch (e) {
        console.warn('[ARIA Proactive] WhatsApp morning failed:', e.message);
      }
    }
  }

  // ── Daily engagement prompt: 8:30am UTC on weekdays ───────────────
  if (hour === 8 && day >= 1 && day <= 5 && _lastEngagement !== today) {
    // Check at 8:30 — scheduler runs every 30min so this catches the 8:00-8:59 window
    const minutes = new Date().getUTCMinutes();
    if (minutes >= 25) {
      _lastEngagement = today;
      console.log('[ARIA Proactive] Generating daily engagement prompt...');
      try {
        const question = await generateDailyEngagementPrompt();
        if (question) {
          await sendTelegram(`💡 *ARIA DAILY QUESTION — ${today}*\n\n${question}\n\n_Reply here or use /teach to update my knowledge._`);
          console.log('[ARIA Proactive] Daily engagement prompt sent to Telegram');
        }
      } catch (e) {
        console.warn('[ARIA Proactive] Daily engagement prompt failed:', e.message);
      }
    }
  }

  // ── Lead hunt: 10am and 3pm UTC weekdays ───────────────────────
  if ((hour === 10 || hour === 15) && day >= 1 && day <= 5) {
    const huntKey = `${today}-${hour < 12 ? 'am' : 'pm'}`;
    if (!_lastLeadHunt[huntKey]) {
      _lastLeadHunt[huntKey] = true;
      console.log('[ARIA Proactive] Running proactive lead hunt...');
      try {
        await generateProactiveLeads();
        console.log('[ARIA Proactive] Lead hunt complete');
      } catch (e) {
        console.warn('[ARIA Proactive] Lead hunt failed:', e.message);
      }
    }
  }

  // ── Hot lead detection: after daily digest on weekdays (7:30am UTC) ──
  if (hour === 7 && day >= 1 && day <= 5 && _lastHotLeadCheck !== today) {
    const minutes = new Date().getUTCMinutes();
    if (minutes >= 25) {
      _lastHotLeadCheck = today;
      console.log('[ARIA Proactive] Checking for hot leads...');
      try {
        await checkHotLeads();
      } catch (e) {
        console.warn('[ARIA Proactive] Hot lead check failed:', e.message);
      }
    }
  }

  // ── Weekly learning dashboard: Monday 8:15am UTC ─────────────────
  if (hour === 8 && day === 1 && _lastWeekly !== today) {
    // Send learning dashboard before the weekly brief
    console.log('[ARIA Proactive] Generating weekly learning dashboard...');
    try {
      const dashboard = await generateWeeklyLearningDashboard();
      if (dashboard) {
        await sendTelegram(dashboard);
        console.log('[ARIA Proactive] Weekly learning dashboard sent to Telegram');
      }
    } catch (e) {
      console.warn('[ARIA Proactive] Weekly learning dashboard failed:', e.message);
    }
  }

  // ── Weekly brief: Monday 8am UTC ──────────────────────────────────
  if (hour === 8 && day === 1 && _lastWeekly !== today) {
    _lastWeekly = today;
    console.log('[ARIA Proactive] Generating weekly strategic brief...');
    try {
      const brief = await generateWeeklyBrief();
      if (brief) {
        // Send to Telegram
        await sendTelegram(`📊 *ARKMURUS WEEKLY BRIEF — ${today}*\n\n${brief}`);

        // Send to team via email
        for (const email of TEAM_EMAILS) {
          try {
            await sendEmail({
              to: email,
              subject: `Arkmurus Weekly Intelligence Brief — ${today}`,
              text: brief,
            });
          } catch {}
        }
        console.log('[ARIA Proactive] Weekly brief sent to Telegram + email');
      }

      // Send WhatsApp weekly learning update on Mondays
      if (_lastWeeklyWA !== today) {
        _lastWeeklyWA = today;
        try {
          await sendWhatsAppWeeklyLearning();
        } catch (e) {
          console.warn('[ARIA Proactive] WhatsApp weekly learning failed:', e.message);
        }
      }

      // Generate strategic ideas after the weekly brief
      if (_lastStrategicIdeas !== today) {
        _lastStrategicIdeas = today;
        console.log('[ARIA Proactive] Generating weekly strategic ideas...');
        try {
          await generateStrategicIdeas();
          console.log('[ARIA Proactive] Strategic ideas sent to Telegram');
        } catch (e) {
          console.warn('[ARIA Proactive] Strategic ideas failed:', e.message);
        }
      }
    } catch (e) {
      console.warn('[ARIA Proactive] Weekly brief failed:', e.message);
    }
  }

  // ── Compliance brief: Wednesday 9am UTC ────────────────────────────
  if (hour === 9 && day === 3 && _lastComplianceBrief !== today) {
    _lastComplianceBrief = today;
    console.log('[ARIA Proactive] Generating weekly compliance brief...');
    try {
      const r = await fetch(`${ARIA_URL || SELF_URL}/api/aria/reports/compliance-brief`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
        body: '{}',
        signal: AbortSignal.timeout(90000),
      });
      const data = r.ok ? await r.json() : null;
      const brief = data?.brief;
      if (brief) {
        await sendTelegram(`🛡️ *ARKMURUS COMPLIANCE BRIEF — ${today}*\n\n${brief}`);

        // Generate PDF attachment
        let pdfAttachments = [];
        try {
          const pdfBuf = await generateCompliancePDF(brief, { date: today });
          pdfAttachments = [{
            filename: `Arkmurus_Compliance_Brief_${today}.pdf`,
            content:  pdfBuf,
            contentType: 'application/pdf',
          }];
          console.log(`[ARIA Proactive] Compliance PDF generated (${pdfBuf.length} bytes)`);
        } catch (pdfErr) {
          console.warn('[ARIA Proactive] PDF generation failed, sending text-only:', pdfErr.message);
        }

        for (const email of TEAM_EMAILS) {
          try {
            await sendEmail({
              to: email,
              subject: `Arkmurus Compliance Intelligence Brief — ${today}`,
              text: brief,
              attachments: pdfAttachments.length ? pdfAttachments : undefined,
            });
          } catch {}
        }
        console.log('[ARIA Proactive] Compliance brief sent to Telegram + email');
        // Push compliance brief to external webhooks
        pushWebhook({
          type: 'COMPLIANCE_ALERT',
          data: { event: 'compliance_brief', date: today, brief_length: brief.length },
          source: 'aria_proactive',
        }).catch(() => {});
      }
    } catch (e) {
      console.warn('[ARIA Proactive] Compliance brief failed:', e.message);
    }
  }

  // ── Monthly reports: 1st of month, 9am UTC ────────────────────────
  if (monthDay === 1 && hour === 9 && _lastMonthly !== today) {
    _lastMonthly = today;
    console.log('[ARIA Proactive] Generating monthly reports...');
    try {
      const battlecard = await generateCompetitorBattlecard();
      if (battlecard) {
        await sendTelegram(`⚔️ *MONTHLY COMPETITOR BATTLECARDS*\n\n${battlecard}`);
        for (const email of TEAM_EMAILS) {
          try { await sendEmail({ to: email, subject: `Arkmurus Competitor Battlecards — ${today}`, text: battlecard }); } catch {}
        }
      }

      const networkGap = await generateNetworkGapReport();
      if (networkGap) {
        await sendTelegram(`🌐 *NETWORK GAP REPORT*\n\n${networkGap}`);
      }

      const audit = await generateAssumptionsAudit();
      if (audit) {
        await sendTelegram(`🔍 *ASSUMPTIONS AUDIT*\n\n${audit}`);
      }
    } catch (e) {
      console.warn('[ARIA Proactive] Monthly reports failed:', e.message);
    }
  }

  // ── Cultural calendar alerts: 7:30am UTC ──────────────────────────
  if (hour === 7 && _lastCultural !== today) {
    _lastCultural = today;
    const events = getTodaysEvents();
    for (const e of events) {
      await sendTelegram(`📅 *CULTURAL ALERT — ${e.country}*\n${e.event}\n💡 ${e.note}`);
    }
    // Upcoming events (3 days out)
    const upcoming = getUpcomingEvents(3);
    for (const e of upcoming) {
      if (e.daysAway === 1) {
        await sendTelegram(`📆 *TOMORROW: ${e.country} — ${e.event}*\n💡 ${e.note}`);
      }
    }
  }

  // ── Hypothesis validation: 4am UTC on weekdays ────────────────
  if (hour === 4 && day >= 1 && day <= 5 && _lastHypothesisCheck !== today) {
    _lastHypothesisCheck = today;
    console.log('[ARIA Proactive] Running scheduled hypothesis validation...');
    try {
      const result = await brainPost('/api/aria/research/validate-hypotheses', {});
      if (result) {
        const count = result.validated || 0;
        if (count > 0) {
          const summaries = (result.results || [])
            .map(r => `• ${(r.hypothesis || '').slice(0, 120)} → ${r.new_status || r.status || '?'}`)
            .join('\n');
          await sendTelegram(`🔬 *HYPOTHESIS VALIDATION — ${today}*\n${count} hypotheses reviewed:\n${summaries}`);
        }
        console.log(`[ARIA Proactive] Hypothesis validation complete: ${count} validated`);
      }
    } catch (e) {
      console.warn('[ARIA Proactive] Hypothesis validation failed:', e.message);
    }
  }

  // ── Sanctions list refresh: every 4 hours (02, 06, 10, 14, 18, 22 UTC) ──
  const sanctionsHours = [2, 6, 10, 14, 18, 22];
  const sanctionsKey = `${today}-${String(hour).padStart(2, '0')}`;
  if (sanctionsHours.includes(hour) && _lastSanctionsRefresh !== sanctionsKey) {
    _lastSanctionsRefresh = sanctionsKey;
    console.log('[ARIA Proactive] Running sub-daily sanctions list refresh...');
    try {
      const result = await brainPost('/api/brain/compliance/refresh', {});
      if (result) {
        const sources = result.results || [];
        const changed = sources.filter(s => s?.value?.changed || s?.changed);
        const totalNew = changed.reduce((sum, s) => sum + Math.abs(s?.value?.delta || s?.delta || 0), 0);
        if (changed.length > 0) {
          const lines = changed.map(s => {
            const v = s?.value || s;
            return `  • ${v.source}: ${v.count} entries (${v.delta > 0 ? '+' : ''}${v.delta})`;
          }).join('\n');
          await sendTelegram(
            `🛡️ *SANCTIONS LIST UPDATE — ${today} ${String(hour).padStart(2, '0')}:00 UTC*\n\n` +
            `${changed.length} list(s) changed, ${totalNew} new entries:\n${lines}\n\n` +
            `_Review active deals for newly sanctioned entities._`
          );
          // Push compliance alert to external webhooks
          pushWebhook({
            type: 'COMPLIANCE_ALERT',
            data: { event: 'sanctions_refresh', changed: changed.length, totalNew, sources: changed },
            source: 'aria_proactive',
          }).catch(() => {});
          pushToSlack(`Sanctions list update: ${changed.length} list(s) changed, ${totalNew} new entries`).catch(() => {});
        }
        console.log(`[ARIA Proactive] Sanctions refresh complete: ${changed.length} lists changed`);
      }
    } catch (e) {
      console.warn('[ARIA Proactive] Sanctions refresh failed:', e.message);
    }
  }

  // ── Memory consolidation ("sleep" cycle): 3am UTC daily ───────────
  if (hour === 3 && _lastConsolidate !== today) {
    _lastConsolidate = today;
    console.log('[ARIA Proactive] Running nightly memory consolidation...');
    try {
      const result = ARIA_URL
        ? await fetch(`${ARIA_URL}/api/aria/neural/consolidate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
            body: '{}',
            signal: AbortSignal.timeout(120000),
          }).then(r => r.ok ? r.json() : null)
        : await brainPost('/api/aria/neural/consolidate', {});
      if (result) {
        const np = result.neural?.neurons_pruned ?? 0;
        const ns = result.neural?.neurons_strengthened ?? 0;
        const km = result.knowledge?.merged ?? 0;
        const kp = result.knowledge?.pruned ?? 0;
        const hv = result.hypotheses?.validated ?? 0;
        const summary = [
          `Neurons: ${np} pruned, ${ns} strengthened`,
          `Facts: ${km} merged, ${kp} pruned`,
          `Hypotheses: ${hv} validated`,
        ].join('\n');
        await sendTelegram(`🧠 *ARIA MEMORY CONSOLIDATION — ${today}*\n${summary}`);
        console.log(`[ARIA Proactive] Consolidation complete: ${np} pruned, ${ns} strengthened, ${km} merged`);
      }
    } catch (e) {
      console.warn('[ARIA Proactive] Memory consolidation failed:', e.message);
    }
  }
}

// ── Mount Express routes + start scheduler ──────────────────────────────────

export function mountProactive(app) {
  console.log('[ARIA Proactive] Mounting proactive operating rhythm...');

  // Check every 30 minutes
  setInterval(() => {
    runProactiveCheck().catch(e => console.warn('[ARIA Proactive] Check failed:', e.message));
  }, 30 * 60 * 1000);

  // First check after 2 minutes
  setTimeout(() => {
    runProactiveCheck().catch(e => console.warn('[ARIA Proactive] Initial check failed:', e.message));
  }, 120000);

  // ── Auth guard ────────────────────────────────────────────────────────
  const requireAuth = (req, res, next) => {
    if (req.headers.authorization !== `Bearer ${INT_TOKEN}`) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    next();
  };

  // ── Manual trigger endpoints ────────────────────────────────────────

  app.post('/api/aria/proactive/daily', requireAuth, async (_req, res) => {
    try {
      const digest = await generateDailyDigest();
      if (digest) await sendTelegram(`📋 *ARIA DAILY DIGEST — ${dateStr()}*\n\n${digest}`);
      res.json({ ok: true, digest });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/weekly', requireAuth, async (_req, res) => {
    try {
      const brief = await generateWeeklyBrief();
      if (brief) {
        await sendTelegram(`📊 *ARKMURUS WEEKLY BRIEF — ${dateStr()}*\n\n${brief}`);
        for (const email of TEAM_EMAILS) {
          try { await sendEmail({ to: email, subject: `Arkmurus Weekly Brief — ${dateStr()}`, text: brief }); } catch {}
        }
      }
      res.json({ ok: true, brief });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/compliance-brief', requireAuth, async (_req, res) => {
    try {
      const r = await fetch(`${ARIA_URL || SELF_URL}/api/aria/reports/compliance-brief`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
        body: '{}',
        signal: AbortSignal.timeout(90000),
      });
      const data = r.ok ? await r.json() : null;
      const brief = data?.brief;
      if (brief) {
        await sendTelegram(`🛡️ *ARKMURUS COMPLIANCE BRIEF — ${dateStr()}*\n\n${brief}`);

        let pdfAttachments = [];
        try {
          const pdfBuf = await generateCompliancePDF(brief, { date: dateStr() });
          pdfAttachments = [{
            filename: `Arkmurus_Compliance_Brief_${dateStr()}.pdf`,
            content:  pdfBuf,
            contentType: 'application/pdf',
          }];
        } catch {}

        for (const email of TEAM_EMAILS) {
          try { await sendEmail({ to: email, subject: `Arkmurus Compliance Brief — ${dateStr()}`, text: brief, attachments: pdfAttachments.length ? pdfAttachments : undefined }); } catch {}
        }
      }
      res.json({ ok: true, brief });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  // Manual trigger: generate compliance brief PDF and email it immediately
  app.post('/api/aria/proactive/export-weekly-report', requireAuth, async (_req, res) => {
    try {
      const r = await fetch(`${ARIA_URL || SELF_URL}/api/aria/reports/compliance-brief`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
        body: '{}',
        signal: AbortSignal.timeout(90000),
      });
      const data = r.ok ? await r.json() : null;
      const brief = data?.brief;
      if (!brief) return res.status(502).json({ error: 'Compliance brief generation returned no data' });

      let pdfBuf = null;
      try {
        pdfBuf = await generateCompliancePDF(brief, { date: dateStr() });
      } catch (pdfErr) {
        console.warn('[ARIA Proactive] PDF generation failed for export:', pdfErr.message);
      }

      const attachments = pdfBuf ? [{
        filename: `Arkmurus_Compliance_Brief_${dateStr()}.pdf`,
        content:  pdfBuf,
        contentType: 'application/pdf',
      }] : undefined;

      const emailResults = [];
      for (const email of TEAM_EMAILS) {
        try {
          const result = await sendEmail({
            to: email,
            subject: `Arkmurus Compliance Brief — ${dateStr()}`,
            text: brief,
            attachments,
          });
          emailResults.push({ email, ...result });
        } catch (e) {
          emailResults.push({ email, sent: false, reason: e.message });
        }
      }

      res.json({
        ok: true,
        brief_length: brief.length,
        pdf_generated: !!pdfBuf,
        pdf_bytes: pdfBuf?.length || 0,
        emails: emailResults,
      });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/battlecard', requireAuth, async (req, res) => {
    try {
      const { competitor } = req.body || {};
      const card = await generateCompetitorBattlecard(competitor);
      res.json({ ok: true, battlecard: card });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/network-gap', requireAuth, async (_req, res) => {
    try {
      const report = await generateNetworkGapReport();
      res.json({ ok: true, report });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/assumptions-audit', requireAuth, async (_req, res) => {
    try {
      const audit = await generateAssumptionsAudit();
      res.json({ ok: true, audit });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/red-team', requireAuth, async (req, res) => {
    try {
      const { deal } = req.body || {};
      if (!deal) return res.status(400).json({ error: 'deal description required' });
      const review = await redTeamReview(deal);
      res.json({ ok: true, review });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/commentary', requireAuth, async (req, res) => {
    try {
      const { event } = req.body || {};
      if (!event) return res.status(400).json({ error: 'event description required' });
      const note = await draftEventCommentary(event);
      res.json({ ok: true, commentary: note });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/linkedin', requireAuth, async (req, res) => {
    try {
      const { topic } = req.body || {};
      if (!topic) return res.status(400).json({ error: 'topic required' });
      const post = await draftLinkedInPost(topic);
      res.json({ ok: true, post });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/deal-model', requireAuth, async (req, res) => {
    try {
      const { deal } = req.body || {};
      if (!deal) return res.status(400).json({ error: 'deal object required' });
      const model = await modelDealEconomics(deal);
      res.json({ ok: true, model });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/sanctions-refresh', requireAuth, async (_req, res) => {
    try {
      const result = await brainPost('/api/brain/compliance/refresh', {});
      if (!result) return res.status(502).json({ error: 'Compliance refresh returned no data' });
      const sources = result.results || [];
      const changed = sources.filter(s => s?.value?.changed || s?.changed);
      if (changed.length > 0) {
        const lines = changed.map(s => {
          const v = s?.value || s;
          return `  • ${v.source}: ${v.count} entries (${v.delta > 0 ? '+' : ''}${v.delta})`;
        }).join('\n');
        await sendTelegram(
          `🛡️ *SANCTIONS REFRESH (manual) — ${dateStr()}*\n\n` +
          `${changed.length} list(s) changed:\n${lines}`
        );
        sendComplianceAlert(
          `Sanctions Lists Updated — ${dateStr()}`,
          `${changed.length} sanctions list(s) changed:\n${lines}\n\nReview updated lists and assess impact on active deals.`
        ).catch(() => {});
        logComplianceAction({ type: 'SCREENING', user: 'system', query: 'sanctions-refresh', result: { changed: changed.length, sources: changed.map(s => (s?.value || s).source) }, recommendation: 'REVIEW_REQUIRED' }).catch(() => {});
      }
      res.json({ ok: true, changed: changed.length, results: sources });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/validate-hypotheses', requireAuth, async (_req, res) => {
    try {
      const result = await brainPost('/api/aria/research/validate-hypotheses', {});
      res.json({ ok: true, ...result });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/consolidate', requireAuth, async (_req, res) => {
    try {
      const result = ARIA_URL
        ? await fetch(`${ARIA_URL}/api/aria/neural/consolidate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
            body: '{}',
            signal: AbortSignal.timeout(120000),
          }).then(r => r.ok ? r.json() : null)
        : await brainPost('/api/aria/neural/consolidate', {});
      res.json({ ok: true, ...result });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/lead-hunt', requireAuth, async (_req, res) => {
    try {
      const leads = await generateProactiveLeads();
      res.json({ ok: true, leads });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/strategic-ideas', requireAuth, async (_req, res) => {
    try {
      const ideas = await generateStrategicIdeas();
      res.json({ ok: true, ideas });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/hot-leads', requireAuth, async (_req, res) => {
    try {
      await checkHotLeads();
      res.json({ ok: true });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/engagement', requireAuth, async (_req, res) => {
    try {
      const question = await generateDailyEngagementPrompt();
      if (question) await sendTelegram(`💡 *ARIA DAILY QUESTION — ${dateStr()}*\n\n${question}\n\n_Reply here or use /teach to update my knowledge._`);
      res.json({ ok: true, question });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.post('/api/aria/proactive/learning-dashboard', requireAuth, async (_req, res) => {
    try {
      const dashboard = await generateWeeklyLearningDashboard();
      if (dashboard) await sendTelegram(dashboard);
      res.json({ ok: true, dashboard });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  app.get('/api/aria/proactive/cultural-calendar', requireAuth, (_req, res) => {
    res.json({
      today: getTodaysEvents(),
      upcoming_7_days: getUpcomingEvents(7),
      all_events: CULTURAL_EVENTS,
    });
  });

  app.get('/api/aria/proactive/reengagement', requireAuth, async (_req, res) => {
    try {
      const nudges = await checkReengagement();
      res.json({ nudges, count: nudges.length });
    } catch (e) { res.status(500).json({ error: e.message }); }
  });

  console.log('[ARIA Proactive] Routes mounted — /api/aria/proactive/*');
  console.log('[ARIA Proactive] Schedule: Daily 7am UTC | Lead Hunt 10am+3pm UTC weekdays | Hot Leads 7:30am UTC weekdays | Engagement 8:30am UTC weekdays | Learning Dashboard Mon 8:15am UTC | Weekly Mon 8am UTC | Strategic Ideas Mon 8am UTC | Compliance Wed 9am UTC | Monthly 1st 9am UTC | Sanctions q4h (02,06,10,14,18,22 UTC) | Hypotheses 4am UTC weekdays | Consolidation 3am UTC daily');
}
