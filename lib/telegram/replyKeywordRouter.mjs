// lib/telegram/replyKeywordRouter.mjs
//
// Reply Keyword Router — R-F2288 / Phase 2
// ==========================================
// Routes user reply keywords to ARIA API endpoints.
//
// Keywords (from strategy doc Section 04):
//   SCREEN [name]     → Sanctions screen via ARIA API
//   [COUNTRY]         → Country brief (Angola, Mozambique, Nigeria, etc.)
//   TENDER [sector]   → Active tenders in sector
//   DEMO              → ARIA demo flow
//   PRO               → Pro upgrade info
//   [post keyword]    → Deep-dive from channelPublisher keyword registry
//
// All keywords are matched case-insensitively.

// ── Constants ──────────────────────────────────────────────────────────────────

const ARIA_SERVICE_URL = process.env.ARIA_SERVICE_URL || 'https://aria-intel.fly.dev';
const ARIA_API_TOKEN = process.env.ARIA_API_TOKEN || null;

/** Registered country keywords → full country name. */
const COUNTRY_KEYWORDS = {
  angola:     'Angola',
  mozambique: 'Mozambique',
  nigeria:    'Nigeria',
  kenya:      'Kenya',
  ethiopia:   'Ethiopia',
  brazil:     'Brazil',
  portugal:   'Portugal',
  turkey:     'Turkey',
  uae:        'United Arab Emirates',
  saudi:      'Saudi Arabia',
  ghana:      'Ghana',
  senegal:    'Senegal',
  ivory:      "Côte d'Ivoire",
  rwanda:     'Rwanda',
  tanzania:   'Tanzania',
  south_africa: 'South Africa',
  drc:        'Democratic Republic of Congo',
  angola_oil: 'Angola (Oil & Gas)',
  moz_lng:    'Mozambique (LNG)',
  // R-F2295 — strategy Section 03 country keys that were missing.
  guinea:         'Guinea',
  guinea_bissau:  'Guinea-Bissau',
  balkans:        'Balkans',
  // R-F2297 — broaden BEYOND Africa (operator 2026-07-02: "we need a broader
  // audience to sign up... keep an open mind"). Global defence/procurement markets
  // so a worldwide subscriber can query their own country. Africa stays covered
  // above; this adds Europe/NATO, Middle East/Gulf, Asia-Pacific and Latin America.
  // ── Europe / NATO ──
  poland: 'Poland', ukraine: 'Ukraine', romania: 'Romania', germany: 'Germany',
  france: 'France', uk: 'United Kingdom', britain: 'United Kingdom', italy: 'Italy',
  spain: 'Spain', greece: 'Greece', netherlands: 'Netherlands', sweden: 'Sweden',
  finland: 'Finland', norway: 'Norway', czech: 'Czech Republic', bulgaria: 'Bulgaria',
  serbia: 'Serbia', croatia: 'Croatia', nato: 'NATO', europe: 'Europe',
  // ── Middle East / Gulf ──
  qatar: 'Qatar', egypt: 'Egypt', israel: 'Israel', jordan: 'Jordan', iraq: 'Iraq',
  lebanon: 'Lebanon', kuwait: 'Kuwait', oman: 'Oman', bahrain: 'Bahrain',
  gulf: 'Gulf (GCC)', gcc: 'Gulf (GCC)', mena: 'MENA',
  // ── Asia-Pacific ──
  india: 'India', pakistan: 'Pakistan', bangladesh: 'Bangladesh', indonesia: 'Indonesia',
  philippines: 'Philippines', vietnam: 'Vietnam', korea: 'South Korea', japan: 'Japan',
  malaysia: 'Malaysia', thailand: 'Thailand', taiwan: 'Taiwan', australia: 'Australia',
  // ── Latin America ──
  mexico: 'Mexico', colombia: 'Colombia', peru: 'Peru', argentina: 'Argentina',
  chile: 'Chile', ecuador: 'Ecuador',
};

/** Command keywords → handler info. */
const COMMAND_KEYWORDS = {
  screen: { handler: 'screen', description: 'Sanctions screen an entity', requiresArg: true },
  tender: { handler: 'tender', description: 'Active tenders in sector', requiresArg: true },
  demo:   { handler: 'demo', description: 'ARIA demo flow', requiresArg: false },
  pro:    { handler: 'pro', description: 'Pro upgrade info', requiresArg: false },
  help:   { handler: 'help', description: 'Show available keywords', requiresArg: false },
  morning: { handler: 'morning', description: 'Full morning brief', requiresArg: false },
};

// ── Keyword Parsing ────────────────────────────────────────────────────────────

/**
 * Parse a user's reply text into a keyword action.
 *
 * @param {string} text — The user's reply text.
 * @returns {{ type: string, action: string, arg: string|null, keyword: string|null }}
 *
 * Returns:
 *   { type: 'command', action: 'screen', arg: 'entity name' }
 *   { type: 'country', action: 'country_brief', arg: 'Angola' }
 *   { type: 'post_keyword', action: 'deep_dive', keyword: 'angola_oil' }
 *   { type: 'unknown', action: null }
 */
export function parseReply(text) {
  if (!text || typeof text !== 'string') {
    return { type: 'unknown', action: null, arg: null, keyword: null };
  }

  const clean = text.trim();
  const upper = clean.toUpperCase();
  const lower = clean.toLowerCase();

  // 1. Check for SCREEN command
  const screenMatch = upper.match(/^SCREEN\s+(.+)/);
  if (screenMatch) {
    return { type: 'command', action: 'screen', arg: screenMatch[1].trim(), keyword: null };
  }

  // 2. Check for TENDER command
  const tenderMatch = upper.match(/^TENDER\s+(.+)/);
  if (tenderMatch) {
    return { type: 'command', action: 'tender', arg: tenderMatch[1].trim(), keyword: null };
  }

  // 3. Check for single-word commands
  const cmdKey = lower.trim();
  if (COMMAND_KEYWORDS[cmdKey]) {
    const cmd = COMMAND_KEYWORDS[cmdKey];
    if (cmd.requiresArg) {
      return { type: 'unknown', action: null, arg: null, keyword: null, error: `${cmdKey} requires an argument. Try: ${cmdKey.toUpperCase()} [value]` };
    }
    return { type: 'command', action: cmdKey, arg: null, keyword: null };
  }

  // 4. Check for country keywords
  if (COUNTRY_KEYWORDS[lower]) {
    return { type: 'country', action: 'country_brief', arg: COUNTRY_KEYWORDS[lower], keyword: null };
  }

  // 5. Check for partial country match (e.g. "angola" matches "angola_oil")
  const countryMatch = Object.keys(COUNTRY_KEYWORDS).find(k => lower.startsWith(k) || lower.endsWith(k));
  if (countryMatch) {
    return { type: 'country', action: 'country_brief', arg: COUNTRY_KEYWORDS[countryMatch], keyword: null };
  }

  // 6. If none matched, return as potential post keyword
  return { type: 'post_keyword', action: 'deep_dive', arg: null, keyword: lower.replace(/[^a-z0-9_]/g, '') };
}

// ── Response Builders ──────────────────────────────────────────────────────────

/**
 * Build a response for a SCREEN command.
 *
 * @param {string} entityName — Entity to screen.
 * @returns {Promise<{text:string,error?:string}>}
 */
export async function handleScreen(entityName) {
  if (!entityName) {
    return { text: '⚠️ Please provide an entity name. Example: `SCREEN John Doe` or `SCREEN Acme Corp`' };
  }

  // Try ARIA API first
  if (ARIA_API_TOKEN) {
    try {
      const res = await fetch(`${ARIA_SERVICE_URL}/api/aria/screen`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${ARIA_API_TOKEN}`,
        },
        body: JSON.stringify({ query: entityName }),
        signal: AbortSignal.timeout(30000),
      });

      if (res.ok) {
        const data = await res.json();
        return { text: formatScreenResult(entityName, data) };
      }
    } catch (err) {
      console.warn('[ReplyKeyword] ARIA API screen failed:', err.message);
    }
  }

  // Fallback: return a formatted placeholder
  return {
    text: `🔍 *Screening: ${_escapeMarkdown(entityName)}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`
      + `I'm running a sanctions and adverse media screen on *${_escapeMarkdown(entityName)}*.\n\n`
      + `This is a Pro feature. Reply \`PRO\` for upgrade info.\n\n`
      + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n🤖 *ARIA Intelligence*`,
  };
}

/**
 * Build a response for a country brief.
 *
 * @param {string} country — Country name.
 * @returns {Promise<{text:string,error?:string}>}
 */
export async function handleCountryBrief(country) {
  if (!country) {
    return { text: 'Please specify a country. Available: Angola, Mozambique, Nigeria, Kenya, UAE, Turkey, and more.' };
  }

  // Try ARIA API
  if (ARIA_API_TOKEN) {
    try {
      const res = await fetch(`${ARIA_SERVICE_URL}/api/aria/country/${encodeURIComponent(country)}`, {
        headers: { 'Authorization': `Bearer ${ARIA_API_TOKEN}` },
        signal: AbortSignal.timeout(30000),
      });

      if (res.ok) {
        const data = await res.json();
        return { text: formatCountryBrief(country, data) };
      }
    } catch (err) {
      console.warn('[ReplyKeyword] ARIA API country brief failed:', err.message);
    }
  }

  // Fallback
  return {
    text: `🌐 *Country Brief: ${_escapeMarkdown(country)}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`
      + `I'm generating a strategic overview for *${_escapeMarkdown(country)}*.\n\n`
      + `This is a Pro feature. Reply \`PRO\` for upgrade info.\n\n`
      + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n🤖 *ARIA Intelligence*`,
  };
}

/**
 * Build a response for a TENDER command.
 *
 * @param {string} sector — Sector to search.
 * @returns {Promise<{text:string,error?:string}>}
 */
export async function handleTender(sector) {
  if (!sector) {
    return { text: '⚠️ Please specify a sector. Example: `TENDER Defence` or `TENDER Oil & Gas`' };
  }

  return {
    text: `🔍 *Active Tenders: ${_escapeMarkdown(sector)}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`
      + `I'm searching for active procurement tenders in *${_escapeMarkdown(sector)}*.\n\n`
      + `This is a Pro feature. Reply \`PRO\` for upgrade info.\n\n`
      + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n🤖 *ARIA Intelligence*`,
  };
}

/**
 * Build a DEMO response.
 *
 * @returns {{text:string}}
 */
export function handleDemo() {
  return {
    text: `🎯 *ARIA Intelligence Demo*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`
      + `Try these commands right now:\n\n`
      + `\`SCREEN [name]\` — Sanctions screen any entity\n`
      + `\`ANGOLA\` — Country intelligence brief\n`
      + `\`TENDER Defence\` — Active defence tenders\n`
      + `\`PRO\` — Upgrade to Pro Intel\n\n`
      + `Or reply to any post with its keyword for a deep dive.\n\n`
      + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n🤖 *ARIA Intelligence*`,
  };
}

/**
 * Build a PRO upgrade response.
 *
 * @returns {{text:string}}
 */
export function handlePro() {
  return {
    text: `💎 *ARIA Intelligence Pro*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`
      + `*£199/month* — Full intelligence platform\n\n`
      + `*What You Get:*\n`
      + `• Unlimited sanctions screening\n`
      + `• Country intelligence briefs on demand\n`
      + `• Procurement tender alerts\n`
      + `• Full DD reports (PDF)\n`
      + `• WhatsApp integration\n`
      + `• Priority support\n\n`
      + `👉 *DM @arkmurus to upgrade*\n\n`
      + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n🤖 *ARIA Intelligence*`,
  };
}

/**
 * Build a HELP response.
 *
 * @returns {{text:string}}
 */
export function handleHelp() {
  return {
    text: `🤖 *ARIA Intelligence — Available Commands*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`
      + `*Reply Keywords:*\n`
      + `\`SCREEN [name]\` — Sanctions screen an entity\n`
      + `\`[COUNTRY]\` — Country brief (e.g. ANGOLA, MOZAMBIQUE)\n`
      + `\`TENDER [sector]\` — Active tenders\n`
      + `\`DEMO\` — See ARIA in action\n`
      + `\`PRO\` — Upgrade info\n`
      + `\`HELP\` — This message\n\n`
      + `*Post Keywords:*\n`
      + `Reply to any post with its keyword for a deep dive.\n\n`
      + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n🤖 *ARIA Intelligence*`,
  };
}

// ── Formatting Helpers ─────────────────────────────────────────────────────────

function formatScreenResult(entityName, data) {
  let msg = `🔍 *Screen Result: ${_escapeMarkdown(entityName)}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

  if (data.matches && data.matches.length > 0) {
    msg += `⚠️ *${data.matches.length} match(es) found*\n\n`;
    for (const m of data.matches.slice(0, 5)) {
      msg += `• ${_escapeMarkdown(m.name || m.entity || 'Unknown')}`;
      if (m.list) msg += ` (${_escapeMarkdown(m.list)})`;
      msg += '\n';
    }
  } else {
    msg += `✅ *No direct matches found*\n`;
    msg += `*Recommendation:* Monitor for future designations.\n`;
  }

  msg += `\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n🤖 *ARIA Intelligence* — Screen`;
  return msg;
}

function formatCountryBrief(country, data) {
  let msg = `🌐 *Country Brief: ${_escapeMarkdown(country)}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

  if (data.overview) msg += `*Overview*\n${_escapeMarkdown(data.overview.substring(0, 300))}\n\n`;
  if (data.risks) msg += `*Risks*\n${_escapeMarkdown(data.risks.substring(0, 300))}\n\n`;
  if (data.opportunities) msg += `*Opportunities*\n${_escapeMarkdown(data.opportunities.substring(0, 300))}\n\n`;

  msg += `━━━━━━━━━━━━━━━━━━━━━━━━━━\n🤖 *ARIA Intelligence* — Country Brief`;
  return msg;
}

function _escapeMarkdown(text) {
  if (!text) return '';
  return String(text)
    .replace(/_/g, '\\_')
    .replace(/\*/g, '\\*')
    .replace(/`/g, '\\`')
    .replace(/\[/g, '\\[')
    .replace(/\]/g, '\\]');
}
