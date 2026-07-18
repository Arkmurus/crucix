// lib/telegram/channelScheduler.mjs
//
// Telegram Channel Scheduler — R-F2288 / Phase 1
// ================================================
// Autonomous daily posting at configured times.
//
// ACTUAL PRODUCTION SCHEDULE (London time) — R-F2544 / R-F2716:
//   07:00 — Golden Intel, best Grade A else HOLD for corroboration
//   17:00 — Golden Intel, Grade A else labelled Grade B else "no qualifying intel"
// The five editorial lanes below (Case File / Know Your Rights / Country Read /
// Opportunity Signal) are RETIRED (R-F2544 — Golden Intel is the only public lane);
// their crons are unconditional skips in channelServerHooks.mjs. `POSTING_SCHEDULE`
// is retained only for the getCurrentSlot/getSchedulerState2 admin surfaces. The
// live posting is the two Golden slots wired in server.mjs (`for (const _hour of
// [7, 17])` → handleMorningSignalCron). Do not re-read this as five active slots.

// ── Constants ──────────────────────────────────────────────────────────────────

/** Daily posting schedule: hour → { type, days, label } */
const POSTING_SCHEDULE = {
  7:  { type: 'morning_signal', days: [0,1,2,3,4,5,6], label: '📰 Morning Signal', duration: 'daily' },
  9:  { type: 'case_file',      days: [1,3,5],          label: '📁 Case File',     duration: 'mon_wed_fri' },
  12: { type: 'know_your_rights', days: [2,4],          label: '🛡️  Know Your Rights', duration: 'tue_thu' },
  15: { type: 'country_read',   days: [1,4],            label: '🌐 Country Read',  duration: 'mon_thu' },
  18: { type: 'opportunity',    days: [2,5],            label: '💡 Opportunity Signal', duration: 'tue_fri' },
};

/** Content type → emoji prefix for posts. */
const TYPE_EMOJI = {
  morning_signal:     '📰',
  case_file:          '📁',
  know_your_rights:   '🛡️',
  country_read:       '🌐',
  opportunity:        '💡',
  breaking_alert:     '🚨',
};

// ── State ──────────────────────────────────────────────────────────────────────

/** Track which posts have been made today: { 'YYYY-MM-DD': Set<type> } */
const _postedToday = new Map();

/** Get today's date key. */
function _todayKey() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/London' });
}

// ── Schedule Query ─────────────────────────────────────────────────────────────

/**
 * Get the current schedule slot based on London time.
 *
 * @returns {{ slot: object|null, nextSlot: object|null, postedToday: string[] }}
 */
export function getCurrentSlot() {
  const now = new Date();
  const londonHour = parseInt(now.toLocaleString('en-GB', { timeZone: 'Europe/London', hour: '2-digit', hour12: false }));
  const londonDay = now.toLocaleDateString('en-GB', { timeZone: 'Europe/London', weekday: 'short' });
  const dayMap = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 0 };
  const dayNum = dayMap[londonDay] ?? now.getDay();

  const today = _todayKey();
  const posted = _postedToday.get(today) || new Set();

  // Find current slot (exact hour match)
  const currentSlot = POSTING_SCHEDULE[londonHour];
  const isScheduledToday = currentSlot && currentSlot.days.includes(dayNum);
  const alreadyPosted = posted.has(currentSlot?.type);

  // Find next slot
  const slots = Object.entries(POSTING_SCHEDULE)
    .map(([h, s]) => ({ hour: parseInt(h), ...s }))
    .filter(s => s.days.includes(dayNum))
    .sort((a, b) => a.hour - b.hour);

  const nextSlot = slots.find(s => s.hour > londonHour && !posted.has(s.type)) || null;

  return {
    slot: isScheduledToday && !alreadyPosted ? { hour: londonHour, ...currentSlot } : null,
    nextSlot: nextSlot ? { hour: nextSlot.hour, type: nextSlot.type, label: nextSlot.label } : null,
    postedToday: [...posted],
    allTodaySlots: slots.map(s => ({ hour: s.hour, type: s.type, label: s.label, posted: posted.has(s.type) })),
  };
}

/**
 * Mark a post type as done for today.
 *
 * @param {string} type — Post type from POSTING_SCHEDULE.
 */
export function markPosted(type) {
  const today = _todayKey();
  if (!_postedToday.has(today)) _postedToday.set(today, new Set());
  _postedToday.get(today).add(type);
}

/**
 * Check if a post type has already been made today.
 *
 * @param {string} type — Post type.
 * @returns {boolean}
 */
export function hasPostedToday(type) {
  const today = _todayKey();
  return (_postedToday.get(today) || new Set()).has(type);
}

/**
 * Get the full schedule for today.
 *
 * @returns {Array<{hour:number,type:string,label:string,posted:boolean}>}
 */
export function getTodaySchedule() {
  const now = new Date();
  const londonDay = now.toLocaleDateString('en-GB', { timeZone: 'Europe/London', weekday: 'short' });
  const dayMap = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 0 };
  const dayNum = dayMap[londonDay] ?? now.getDay();
  const today = _todayKey();
  const posted = _postedToday.get(today) || new Set();

  return Object.entries(POSTING_SCHEDULE)
    .map(([h, s]) => ({ hour: parseInt(h), ...s }))
    .filter(s => s.days.includes(dayNum))
    .map(s => ({ hour: s.hour, type: s.type, label: s.label, posted: posted.has(s.type) }));
}

/**
 * Get scheduler state (for monitoring).
 *
 * @returns {object}
 */
export function getSchedulerState() {
  const slot = getCurrentSlot();
  return {
    currentSlot: slot.slot,
    nextSlot: slot.nextSlot,
    postedToday: slot.postedToday,
    todaySchedule: slot.allTodaySlots,
  };
}

/**
 * Reset scheduler state (for testing).
 */
export function _resetSchedulerState() {
  _postedToday.clear();
}

// ── Content Templates ──────────────────────────────────────────────────────────

/**
 * Build a Case File post template.
 *
 * @param {object} data
 * @param {string} data.title — Case title.
 * @param {string} data.scenario — The scenario description.
 * @param {string} data.outcome — What happened.
 * @param {string} data.lesson — Key lesson.
 * @param {string} data.country — Country.
 * @param {string} data.sector — Sector.
 * @returns {string} Formatted post.
 */
export function buildCaseFile(data) {
  const { title, scenario, outcome, lesson, country, sector } = data;
  return `📁 *CASE FILE: ${_escapeMarkdown(title)}*\n`
    + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`
    + `*The Scenario*\n${_escapeMarkdown(scenario)}\n\n`
    + `*The Outcome*\n${_escapeMarkdown(outcome)}\n\n`
    + `*The Lesson*\n${_escapeMarkdown(lesson)}\n\n`
    + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n`
    + `📍 ${country || 'Global'} · ${sector || 'Cross-sector'}\n`
    + `🤖 *ARIA Intelligence* — Case File\n`
    + `💬 Reply with \`${_generateKeyword(title)}\` for full DD analysis`;
}

/**
 * Build a Know Your Rights post template.
 *
 * @param {object} data
 * @param {string} data.title — Right/regulation title.
 * @param {string} data.what — What it is.
 * @param {string} data.why — Why it matters.
 * @param {string} data.how — How to apply it.
 * @param {string} data.country — Jurisdiction.
 * @returns {string} Formatted post.
 */
export function buildKnowYourRights(data) {
  const { title, what, why, how, country } = data;
  return `🛡️ *KNOW YOUR RIGHTS: ${_escapeMarkdown(title)}*\n`
    + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`
    + `*What It Is*\n${_escapeMarkdown(what)}\n\n`
    + `*Why It Matters*\n${_escapeMarkdown(why)}\n\n`
    + `*How to Apply*\n${_escapeMarkdown(how)}\n\n`
    + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n`
    + `📍 ${country || 'Multi-jurisdiction'}\n`
    + `🤖 *ARIA Intelligence* — Know Your Rights\n`
    + `💬 Reply with \`${_generateKeyword(title)}\` for full analysis`;
}

/**
 * Build a Country Read post template.
 *
 * @param {object} data
 * @param {string} data.country — Country name.
 * @param {string} data.overview — Strategic overview.
 * @param {string} data.risks — Key risks.
 * @param {string} data.opportunities — Key opportunities.
 * @param {string} data.outlook — Outlook.
 * @returns {string} Formatted post.
 */
export function buildCountryRead(data) {
  const { country, overview, risks, opportunities, outlook } = data;
  return `🌐 *COUNTRY READ: ${_escapeMarkdown(country)}*\n`
    + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`
    + `*Strategic Overview*\n${_escapeMarkdown(overview)}\n\n`
    + `*Key Risks*\n${_escapeMarkdown(risks)}\n\n`
    + `*Key Opportunities*\n${_escapeMarkdown(opportunities)}\n\n`
    + `*Outlook*\n${_escapeMarkdown(outlook)}\n\n`
    + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n`
    + `🤖 *ARIA Intelligence* — Country Read\n`
    + `💬 Reply with \`${_generateKeyword(country)}\` for deep dive`;
}

/**
 * Build a Morning Signal post.
 *
 * @param {object} data
 * @param {string} data.date — Date string.
 * @param {Array<{title:string,text:string}>} data.signals — Intel signals.
 * @returns {string} Formatted post.
 */
export function buildMorningSignal(data) {
  const { date, signals = [] } = data;
  let msg = `📰 *MORNING SIGNAL*\n${date || new Date().toLocaleDateString('en-GB', { timeZone: 'Europe/London', day: 'numeric', month: 'long', year: 'numeric' })}\n`;
  msg += `━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

  for (const s of signals.slice(0, 5)) {
    msg += `▸ *${_escapeMarkdown(s.title)}*\n${_escapeMarkdown((s.text || '').substring(0, 200))}\n\n`;
  }

  msg += `━━━━━━━━━━━━━━━━━━━━━━━━━━\n`;
  msg += `🤖 *ARIA Intelligence* · imaria.io\n`;
  // R-F2317 — discovery loop: a forward ask (the strategy's #1 organic lever) + the
  // proven free-consult CTA on every daily post.
  msg += `💬 Screen any counterparty free — reply \`SCREEN [company]\` or DM @ArkmurusIntelBot\n`;
  msg += `📢 Useful? Forward to a colleague who runs due diligence.`;
  return msg;
}

/**
 * Build the pinned welcome post for the channel.
 *
 * @returns {string} Welcome post text.
 */
export function buildWelcomePost() {
  // R-F2317 — honest cadence: only the Daily Signal + Breaking actually fire (the
  // Case File/KYR/Country Read/Opportunity crons were disabled, R-F2306). Promise
  // only what we deliver, and global (not Africa-only).
  return `🤖 *Welcome to ARIA Intelligence*\n`
    + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`
    + `Autonomous defence due-diligence & compliance intelligence — global markets.\n\n`
    + `*What You Get*\n`
    + `📰 *Daily Signal* — one curated, sourced brief each morning (sanctions, procurement, market events)\n`
    + `🚨 *Breaking Alerts* — genuinely material developments, in real time\n\n`
    + `*Consult ARIA — free, private*\n`
    + `\`SCREEN [company]\` — live sanctions & watchlist check (OFAC · UK OFSI · EU · UN · OpenSanctions)\n`
    + `\`[COUNTRY]\` — defence-market brief (e.g. \`NIGERIA\`, \`POLAND\`, \`UAE\`, \`INDIA\`)\n`
    + `\`PRO\` — full DD reports (ownership, adverse media, financials) in under 10 min\n\n`
    + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n`
    + `🤖 *ARIA Intelligence* · imaria.io`;
}

// ── Internal Helpers ───────────────────────────────────────────────────────────

function _escapeMarkdown(text) {
  if (!text) return '';
  return String(text)
    .replace(/_/g, '\\_')
    .replace(/\*/g, '\\*')
    .replace(/`/g, '\\`')
    .replace(/\[/g, '\\[')
    .replace(/\]/g, '\\]');
}

function _generateKeyword(text) {
  if (!text) return 'analyze';
  const words = String(text)
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, '')
    .split(/\s+/)
    .filter(w => w.length > 2 && !['the', 'and', 'for', 'are', 'was', 'had', 'has', 'but', 'not', 'all', 'can'].includes(w));
  return words.slice(0, 2).join('_') || 'analyze';
}
