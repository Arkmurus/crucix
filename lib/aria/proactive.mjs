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

const INT_TOKEN   = process.env.ARIA_INTERNAL_TOKEN || 'aria-internal';
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
    await brainPost('/api/brain/signal', {
      content: text,
      source: 'aria_proactive',
      signal_type: 'proactive_output',
      metadata: { channel: 'telegram', proactive: true },
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

  const digest = await ariaChat(prompt, `daily_digest_${dateStr()}`);

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
    } catch (e) {
      console.warn('[ARIA Proactive] Weekly brief failed:', e.message);
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
  console.log('[ARIA Proactive] Schedule: Daily 7am UTC | Weekly Mon 8am UTC | Monthly 1st 9am UTC');
}
