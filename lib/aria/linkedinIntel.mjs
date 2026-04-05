/**
 * ARIA — LinkedIn Intelligence Module
 * ═══════════════════════════════════════════════════════════════════════════
 * Processes LinkedIn/Sales Navigator email alerts and extracts:
 *
 * 1. APPOINTMENT DETECTION — job changes → relationship windows
 * 2. RELATIONSHIP MAPPING — builds decision-maker maps per organisation
 * 3. COMPETITOR TRACKING — flags OEM BD teams engaging target contacts
 * 4. COMPANY GROWTH SIGNALS — hiring surges → procurement budget signals
 * 5. CONTENT INTELLIGENCE — post topics → active requirement detection
 * 6. WARM INTRO ROUTING — connection path suggestions
 *
 * Data source: Email alerts from Sales Navigator → aria@arkmurus.com
 * The emailReader.mjs feeds emails here for LinkedIn-specific processing.
 * ═══════════════════════════════════════════════════════════════════════════
 */

import { redisSet, redisGet, redisConfigured } from '../persist/store.mjs';

const INT_TOKEN = process.env.ARIA_INTERNAL_TOKEN || 'aria-internal';

// ── Relationship map store ───────────────────────────────────────────────────
// { "Angola MoD": { decision_maker: {...}, influencer: {...}, gatekeeper: {...}, champion: {...} } }
let relationshipMaps = {};
const REDIS_KEY_MAPS = 'crucix:linkedin:relationship_maps';

// ── Competitor activity log ──────────────────────────────────────────────────
let competitorActivity = [];
const REDIS_KEY_COMPETITORS = 'crucix:linkedin:competitor_activity';
const MAX_COMPETITOR_LOG = 200;

// ── Growth signals ───────────────────────────────────────────────────────────
let growthSignals = [];
const REDIS_KEY_GROWTH = 'crucix:linkedin:growth_signals';

// ── Known competitors (OEM BD teams to watch) ────────────────────────────────
const COMPETITOR_KEYWORDS = [
  'baykar', 'turkish aerospace', 'tai', 'aselsan', 'roketsan',
  'norinco', 'poly technologies', 'catic', 'avic',
  'rosoboronexport', 'rostec', 'almaz-antey',
  'elbit', 'rafael', 'iai', 'israel aerospace',
  'paramount', 'denel',
  'rheinmetall', 'leonardo', 'thales', 'bae systems',
  'embraer', 'taurus', 'avibras',
  'hanwha', 'hyundai rotem', 'korea aerospace',
];

// ── Target organisations ─────────────────────────────────────────────────────
const TARGET_ORGS = [
  { name: 'Angola MoD', keywords: ['angola', 'faa', 'simportex', 'angolan armed forces', 'ministry of defence angola'] },
  { name: 'Mozambique MoD', keywords: ['mozambique', 'fadm', 'mozambican armed forces', 'ministry of defence mozambique'] },
  { name: 'Guinea-Bissau MoD', keywords: ['guinea-bissau', 'fasb', 'guinea bissau armed forces'] },
  { name: 'Nigeria MoD', keywords: ['nigeria', 'nigerian army', 'nigerian air force', 'nigerian navy', 'dicon', 'ministry of defence nigeria'] },
  { name: 'Kenya MoD', keywords: ['kenya', 'kdf', 'kenya defence forces', 'ministry of defence kenya'] },
  { name: 'Saudi Arabia MoD', keywords: ['saudi', 'royal saudi', 'gami', 'ministry of defense saudi'] },
  { name: 'UAE MoD', keywords: ['uae', 'emirates', 'edge group', 'tawazun'] },
  { name: 'Philippines DND', keywords: ['philippines', 'philippine army', 'philippine air force', 'department of national defense'] },
  { name: 'Indonesia MoD', keywords: ['indonesia', 'tni', 'indonesian armed forces', 'ministry of defence indonesia'] },
  { name: 'Brazil MoD', keywords: ['brazil', 'brazilian army', 'brazilian navy', 'ministry of defence brazil'] },
  { name: 'Poland MoD', keywords: ['poland', 'polish armed forces', 'ministry of national defence poland'] },
  { name: 'India MoD', keywords: ['india', 'indian army', 'indian air force', 'indian navy', 'ministry of defence india'] },
];

// ── Role classification ──────────────────────────────────────────────────────
const ROLE_PATTERNS = {
  decision_maker: /minister|secretary|chief of staff|commander|director general|ceo|president|chairman/i,
  influencer: /advisor|counsellor|consultant|attache|diplomatic|ambassador/i,
  gatekeeper: /procurement|acquisition|logistics|budget|purchasing|contracts/i,
  champion: /cooperation|partnership|business development|trade|commercial/i,
};

function classifyRole(title) {
  for (const [role, pattern] of Object.entries(ROLE_PATTERNS)) {
    if (pattern.test(title)) return role;
  }
  return 'contact';
}

// ── 1. APPOINTMENT DETECTION ─────────────────────────────────────────────────
function detectAppointment(subject, body) {
  const patterns = [
    /started a new position/i,
    /new role as (.+)/i,
    /appointed as (.+)/i,
    /promoted to (.+)/i,
    /joined (.+) as (.+)/i,
    /now (?:the )?(.+) at (.+)/i,
    /job change/i,
    /career update/i,
    /new position/i,
  ];

  const text = `${subject} ${body}`;
  for (const p of patterns) {
    if (p.test(text)) {
      // Extract name, role, org from the email
      const nameMatch = text.match(/(?:^|\n)([A-Z][a-z]+ (?:[A-Z][a-z]+ )?[A-Z][a-z]+)/);
      const roleMatch = text.match(/(?:as|position of|role of|promoted to)\s+(.{5,80}?)(?:\.|,|\n|at )/i);
      const orgMatch  = text.match(/(?:at|joined|with)\s+(.{3,60}?)(?:\.|,|\n|as )/i);

      return {
        detected: true,
        name: nameMatch ? nameMatch[1].trim() : null,
        new_role: roleMatch ? roleMatch[1].trim() : null,
        organisation: orgMatch ? orgMatch[1].trim() : null,
        raw_subject: subject,
      };
    }
  }
  return { detected: false };
}

// ── 2. COMPETITOR TRACKING ───────────────────────────────────────────────────
function detectCompetitorActivity(subject, body) {
  const text = `${subject} ${body}`.toLowerCase();
  const found = [];

  for (const kw of COMPETITOR_KEYWORDS) {
    if (text.includes(kw)) {
      // Check if it mentions a target market
      for (const org of TARGET_ORGS) {
        if (org.keywords.some(k => text.includes(k))) {
          found.push({
            competitor: kw,
            target_org: org.name,
            context: subject,
            detected_at: new Date().toISOString(),
          });
        }
      }
      if (!found.length) {
        found.push({
          competitor: kw,
          target_org: 'general',
          context: subject,
          detected_at: new Date().toISOString(),
        });
      }
    }
  }
  return found;
}

// ── 3. COMPANY GROWTH SIGNALS ────────────────────────────────────────────────
function detectGrowthSignal(subject, body) {
  const text = `${subject} ${body}`.toLowerCase();
  const patterns = [
    /hiring|new jobs|job openings|recruitment/i,
    /procurement officer|procurement manager|acquisition/i,
    /budget increase|budget allocation|defence spending/i,
    /expansion|new office|new facility/i,
    /contract awarded|deal signed/i,
  ];

  for (const p of patterns) {
    if (p.test(text)) {
      for (const org of TARGET_ORGS) {
        if (org.keywords.some(k => text.includes(k))) {
          return {
            detected: true,
            organisation: org.name,
            signal_type: 'growth',
            context: subject,
            detected_at: new Date().toISOString(),
          };
        }
      }
    }
  }
  return { detected: false };
}

// ── 4. CONTENT INTELLIGENCE ──────────────────────────────────────────────────
function detectContentSignal(subject, body) {
  const text = `${subject} ${body}`.toLowerCase();
  const signals = [];

  const requirements = [
    { pattern: /uav|drone|unmanned/i, capability: 'UAV/Drone' },
    { pattern: /armoured|armored|apc|ifv|mrap/i, capability: 'Armoured Vehicles' },
    { pattern: /naval|patrol boat|frigate|corvette/i, capability: 'Naval' },
    { pattern: /ammunition|munition|calibre|cartridge/i, capability: 'Ammunition' },
    { pattern: /radar|surveillance|isr|c4isr/i, capability: 'Surveillance/ISR' },
    { pattern: /counter.?ied|mine|eod/i, capability: 'Counter-IED' },
    { pattern: /helicopter|rotary/i, capability: 'Helicopters' },
    { pattern: /cyber|electronic warfare|ew/i, capability: 'Cyber/EW' },
    { pattern: /training|simulation|exercise/i, capability: 'Training' },
    { pattern: /border|security|protection/i, capability: 'Border Security' },
  ];

  for (const req of requirements) {
    if (req.pattern.test(text)) {
      signals.push({
        capability: req.capability,
        context: subject.slice(0, 100),
      });
    }
  }
  return signals;
}

// ── Process LinkedIn email ───────────────────────────────────────────────────
export async function processLinkedInEmail(subject, from, body) {
  const results = {
    appointments: [],
    competitors: [],
    growth: [],
    content: [],
    relationship_updates: [],
  };

  // 1. Appointment detection
  const appt = detectAppointment(subject, body);
  if (appt.detected) {
    results.appointments.push(appt);

    // Auto-create relationship window
    if (appt.name && appt.new_role) {
      await sendTelegramAlert(
        `🟢 *APPOINTMENT DETECTED*\n` +
        `*${appt.name}*\n` +
        `New role: ${appt.new_role}\n` +
        `${appt.organisation ? `Org: ${appt.organisation}\n` : ''}` +
        `_Source: LinkedIn — act within 90 days_`
      );

      // Add to contacts via brain
      await feedSignal(
        `[LinkedIn Appointment] ${appt.name} — ${appt.new_role}${appt.organisation ? ' at ' + appt.organisation : ''}`,
        'linkedin_appointment'
      );
    }
  }

  // 2. Competitor activity
  const competitors = detectCompetitorActivity(subject, body);
  if (competitors.length) {
    results.competitors = competitors;
    competitorActivity.push(...competitors);
    if (competitorActivity.length > MAX_COMPETITOR_LOG) {
      competitorActivity = competitorActivity.slice(-MAX_COMPETITOR_LOG);
    }

    for (const c of competitors) {
      if (c.target_org !== 'general') {
        await sendTelegramAlert(
          `⚠️ *COMPETITOR ALERT*\n` +
          `*${c.competitor.toUpperCase()}* active in *${c.target_org}*\n` +
          `${c.context.slice(0, 120)}\n` +
          `_Source: LinkedIn — review competitive positioning_`
        );
      }
    }

    await feedSignal(
      `[LinkedIn Competitor] ${competitors.map(c => `${c.competitor} → ${c.target_org}`).join('; ')}`,
      'linkedin_competitor'
    );
  }

  // 3. Growth signals
  const growth = detectGrowthSignal(subject, body);
  if (growth.detected) {
    results.growth.push(growth);
    growthSignals.push(growth);

    await feedSignal(
      `[LinkedIn Growth] ${growth.organisation}: ${growth.context}`,
      'linkedin_growth'
    );
  }

  // 4. Content intelligence
  const content = detectContentSignal(subject, body);
  if (content.length) {
    results.content = content;

    await feedSignal(
      `[LinkedIn Content] Capabilities mentioned: ${content.map(c => c.capability).join(', ')} — ${subject.slice(0, 80)}`,
      'linkedin_content'
    );
  }

  // 5. Relationship mapping — update maps based on any person mentioned
  const orgMatch = TARGET_ORGS.find(o => o.keywords.some(k => body.toLowerCase().includes(k)));
  if (orgMatch && appt.name) {
    const role = classifyRole(appt.new_role || '');
    if (!relationshipMaps[orgMatch.name]) {
      relationshipMaps[orgMatch.name] = {};
    }
    relationshipMaps[orgMatch.name][role] = {
      name: appt.name,
      title: appt.new_role,
      detected_at: new Date().toISOString(),
      source: 'linkedin',
    };
    results.relationship_updates.push({ org: orgMatch.name, role, person: appt.name });

    // Persist to Redis
    if (redisConfigured()) {
      await redisSet(REDIS_KEY_MAPS, relationshipMaps).catch(() => {});
    }
  }

  return results;
}

// ── Helpers ──────────────────────────────────────────────────────────────────
async function feedSignal(content, signalType) {
  const port = process.env.PORT || 3117;
  try {
    await fetch(`http://localhost:${port}/api/brain/signal`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${INT_TOKEN}` },
      body: JSON.stringify({ content, source: 'linkedin', signal_type: signalType, metadata: { channel: 'linkedin', timestamp: new Date().toISOString() } }),
      signal: AbortSignal.timeout(5000),
    });
  } catch {}
}

async function sendTelegramAlert(text) {
  const port = process.env.PORT || 3117;
  const chatId = process.env.TELEGRAM_CHAT_ID;
  const token  = process.env.TELEGRAM_BOT_TOKEN;
  if (!chatId || !token) return;
  try {
    await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text: text.slice(0, 4096), parse_mode: 'Markdown', disable_web_page_preview: true }),
      signal: AbortSignal.timeout(5000),
    });
  } catch {}
}

// ── Init — restore from Redis ────────────────────────────────────────────────
export async function initLinkedInIntel() {
  if (!redisConfigured()) return;
  try {
    const maps = await redisGet(REDIS_KEY_MAPS);
    if (maps) relationshipMaps = maps;
    const comps = await redisGet(REDIS_KEY_COMPETITORS);
    if (comps) competitorActivity = comps;
    const growth = await redisGet(REDIS_KEY_GROWTH);
    if (growth) growthSignals = growth;
    console.log(`[LinkedIn Intel] Loaded: ${Object.keys(relationshipMaps).length} org maps, ${competitorActivity.length} competitor events, ${growthSignals.length} growth signals`);
  } catch {}
}

// ── API routes ───────────────────────────────────────────────────────────────
export function mountLinkedInRoutes(app) {
  app.get('/api/linkedin/maps', (_req, res) => {
    res.json({ maps: relationshipMaps, count: Object.keys(relationshipMaps).length });
  });

  app.get('/api/linkedin/competitors', (_req, res) => {
    res.json({ activity: competitorActivity.slice(-50), count: competitorActivity.length });
  });

  app.get('/api/linkedin/growth', (_req, res) => {
    res.json({ signals: growthSignals.slice(-50), count: growthSignals.length });
  });

  app.get('/api/linkedin/status', (_req, res) => {
    res.json({
      relationship_maps: Object.keys(relationshipMaps).length,
      competitor_events: competitorActivity.length,
      growth_signals: growthSignals.length,
      target_orgs: TARGET_ORGS.map(o => o.name),
      tracked_competitors: COMPETITOR_KEYWORDS.length,
    });
  });

  console.log('[LinkedIn Intel] Routes mounted — /api/linkedin/*');
}
