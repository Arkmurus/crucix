// lib/telegram/channelServerHooks.mjs
//
// Channel Server Hooks — R-F2288
// ===============================
// All the server.mjs wiring for the channel publisher, scheduler,
// media engine, interactive engine, and reply keyword router.
//
// Imported by server.mjs to keep the protected file clean.
// This module exports init functions that server.mjs calls.

import { curateSignals, formatChannelPost, formatDailyBrief, canPostNow, recordPost, getSchedulerState, publishSignal, isBreakingSignal } from './channelPublisher.mjs';
import { getCurrentSlot, markPosted, getTodaySchedule, getSchedulerState as getSchedulerState2, buildCaseFile, buildKnowYourRights, buildCountryRead, buildMorningSignal, buildWelcomePost } from './channelScheduler.mjs';
import { parseReply, handleScreen, handleCountryBrief, handleTender, handleDemo, handlePro, handleHelp } from './replyKeywordRouter.mjs';
// R-F2544 — editorialQueue imports (peekNextEditorial, markEditorialPosted) removed:
// the editorial fallback lane they fed was retired when the channel became Golden
// Intel only, leaving them with zero references in this module.
import { dedupKey, wasRecentlyPosted, recordPosted } from './postDedup.mjs';
import fs from 'node:fs';

// R-F2317 — channel PROPRIOCEPTION (§21a/§25). Report every daily-post outcome
// (delivered / failed / skipped) to the brain so ARIA KNOWS whether the channel
// limb actually fired — the channel was previously DARK (console-only). Fire-and-
// forget; it must NEVER break or delay a post.
// R-F2319 / R-F2473 — normalize the channel chat_id into Telegram's canonical
// -100<internal> form. TELEGRAM_CHAT_ID may arrive in three shapes:
//   -1003836086295   already canonical (signed)     -> as-is
//    1003836086295   abs value, minus dropped        -> prepend '-'   (NOT '-100')
//       3836086295   bare internal id                -> prepend '-100'
// R-F2319 blanket-prefixed '-100', which corrupted the ABS form into
// -1001003836086295 ("chat not found" — the whole channel went dark).
// R-F2716 HONESTY (getChat probe 2026-07-18): -1003836086295 is a PRIVATE
// supergroup whose TITLE happens to be "@ARIAIntelligence" (that is a title, NOT
// a public @username; member_count=7). The PUBLIC handle @ARIAIntelligence is a
// DIFFERENT channel (id -1002311460199, "Aria Intelligence ($ARIA)"). Do NOT
// describe this destination as "the public @ARIAIntelligence channel" — it is a
// private group. The real public destination is an OPERATOR decision (which
// channel to own/publish to); TELEGRAM_CHANNEL_ID is env-driven.
// The abs form already carries the 100 supergroup marker (13+ digits) — detect it
// and add only the sign; a shorter bare internal id still needs the full -100.
export function _channelChatId(bot) {
  const raw = String(bot?.channelId || bot?.chatId || '').trim();
  if (!raw || raw[0] === '@' || raw[0] === '-') return raw;   // username / already-signed
  if (!/^\d+$/.test(raw)) return raw;                          // non-numeric handle — leave alone
  if (/^100\d{10,}$/.test(raw)) return `-${raw}`;             // 1003836086295 -> -1003836086295
  if (raw.length >= 9) return `-100${raw}`;                    // 3836086295    -> -1003836086295
  return raw;
}

// R-F2321 — resilient channel send: try Markdown, and on a parse error (HTTP 400)
// retry as PLAIN text so a stray unescaped character never silently drops the
// day's post. Returns { ok, status }.
async function _channelSend(bot, text, { disablePreview = false } = {}) {
  const chatId = _channelChatId(bot);
  const post = async (useMarkdown) => {
    const body = { chat_id: chatId, text };
    if (useMarkdown) body.parse_mode = 'Markdown';
    if (disablePreview) body.disable_web_page_preview = true;
    return fetch(`https://api.telegram.org/bot${bot.botToken}/sendMessage`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(15000),
    });
  };
  let res = await post(true);
  if (!res.ok && res.status === 400) {
    console.warn('[ChannelCron] Markdown parse failed — retrying as plain text');
    res = await post(false);
  }
  let payload = null;
  try { payload = await res.json(); } catch { /* malformed Telegram response */ }
  return {
    ok: res.ok && payload?.ok === true,
    status: res.status,
    result: payload?.result || null,
    error: payload?.description || (!payload ? 'invalid_telegram_response' : null),
  };
}

export async function validatePublicChannelDestination(bot) {
  const chatId = _channelChatId(bot);
  if (!bot?.botToken || !chatId) return { ok: false, reason: 'missing_channel_config' };
  try {
    const res = await fetch(
      `https://api.telegram.org/bot${bot.botToken}/getChat?chat_id=${encodeURIComponent(chatId)}`,
      { signal: AbortSignal.timeout(15000) },
    );
    const payload = await res.json();
    const type = String(payload?.result?.type || '');
    if (!res.ok || payload?.ok !== true) {
      return { ok: false, reason: 'telegram_get_chat_failed', status: res.status, detail: payload?.description || '' };
    }
    if (type !== 'channel') {
      return { ok: false, reason: 'destination_not_channel', type, title: payload?.result?.title || '' };
    }
    // R-F2894 — getChat proves the destination EXISTS and is a public channel. It
    // does NOT prove we may POST to it. On 2026-07-19 the bot lost its channel-admin
    // rights: getChat kept returning 200, this validator kept returning ok, and every
    // slot from 2026-07-19T16:00Z to 2026-07-23 then died on sendMessage with a bare
    // "HTTP 403" — six consecutive silent failures over four days, each one burning
    // the slot via _recordSlotRun and reporting a status code instead of a cause.
    //
    // A bot that IS a channel administrator can always read the member list; a
    // non-admin gets 400 "member list is inaccessible". That asymmetry is the cheap,
    // read-only write-permission probe. It is FAIL-OPEN by design: only an explicit
    // negative blocks. An unrelated Telegram outage must not stop a publishable post
    // — the send itself remains the final authority, this just names the cause first.
    const perm = await _channelPostPermission(bot, chatId);
    if (perm.canPost === false) {
      return {
        ok: false,
        reason: 'bot_cannot_post_to_channel',
        type,
        detail: perm.detail || 'bot is not an administrator of the channel',
        title: payload?.result?.title || '',
      };
    }
    return { ok: true, type, id: payload.result.id, title: payload.result.title || '', username: payload.result.username || '' };
  } catch (e) {
    return { ok: false, reason: 'telegram_get_chat_exception', detail: e.message };
  }
}

/**
 * R-F2894 — read-only probe of whether the bot may post to `chatId`.
 * Returns { canPost: true | false | null } — `null` means UNDETERMINED (probe
 * itself failed), never "false". "Could not measure" is not "measured and failed".
 */
async function _channelPostPermission(bot, chatId) {
  try {
    const me = await fetch(`https://api.telegram.org/bot${bot.botToken}/getMe`,
      { signal: AbortSignal.timeout(10000) });
    const meJson = await me.json();
    const botId = meJson?.result?.id;
    if (!botId) return { canPost: null, detail: 'getMe failed' };

    const r = await fetch(
      `https://api.telegram.org/bot${bot.botToken}/getChatMember?chat_id=${encodeURIComponent(chatId)}&user_id=${botId}`,
      { signal: AbortSignal.timeout(10000) },
    );
    const j = await r.json();
    if (j?.ok === true) {
      const status = String(j?.result?.status || '');
      if (status === 'administrator') {
        // can_post_messages is channel-specific; absent means "not restricted".
        const canPost = j.result.can_post_messages !== false;
        return canPost
          ? { canPost: true, detail: 'administrator' }
          : { canPost: false, detail: 'administrator without can_post_messages' };
      }
      if (status === 'creator') return { canPost: true, detail: 'creator' };
      if (status === 'left' || status === 'kicked') {
        return { canPost: false, detail: `bot status is "${status}" in the channel` };
      }
      return { canPost: false, detail: `bot is "${status}", not an administrator` };
    }
    const desc = String(j?.description || '');
    // The exact signature of a demoted/removed bot on a channel.
    if (/member list is inaccessible/i.test(desc)) {
      return { canPost: false, detail: 'member list is inaccessible — bot is not a channel administrator' };
    }
    return { canPost: null, detail: desc || `HTTP ${r.status}` };
  } catch (e) {
    return { canPost: null, detail: e?.message || 'probe exception' };
  }
}

function _cardTitleFromText(text, fallback = 'ARIA Intelligence') {
  const first = String(text || '').split('\n').map(s => s.trim()).find(Boolean) || fallback;
  return first.replace(/^[^\w[]+\s*/, '').replace(/\*/g, '').replace(/—/g, '-').slice(0, 110);
}

function _cardBulletsFromText(text) {
  return String(text || '')
    .split('\n')
    .map(s => s.replace(/^[•*\-\d.\s_`]+/, '').replace(/\*/g, '').trim())
    .filter(s => s.length >= 24 && !/^source:/i.test(s) && !/ARIA Intelligence/i.test(s))
    .slice(0, 3);
}

function _cardCaption(cardData) {
  const title = String(cardData?.title || 'ARIA Intelligence').replace(/\*/g, '').slice(0, 120);
  const source = String(cardData?.source || 'ARIA Intelligence').replace(/\*/g, '').slice(0, 80);
  return `*${title}*\n${source}\n\nFull analysis below.`;
}

async function _channelSendWithCard(bot, text, cardData = {}, opts = {}) {
  let _photoSent = false;         // R-F2717 (#11) — track partial delivery
  let _photoMessageId = null;
  try {
    const chatId = _channelChatId(bot);
    const mediaBot = { ...bot, chatId, channelId: chatId };
    const { buildIntelCardData, generateInfographicCard, uploadSvgAsPhoto, sendPhoto } = await import('./channelMedia.mjs');
    const payload = buildIntelCardData({
      title: _cardTitleFromText(text),
      subtitle: String(text || '').replace(/\s+/g, ' ').slice(0, 220),
      bullets: _cardBulletsFromText(text),
      source: 'ARIA Telegram Channel',
      type: 'daily',
      ...cardData,
    });
    const svg = generateInfographicCard(payload);
    // R-F2519 (log-review F4) — PRE-VALIDATE the generated card before upload so a
    // malformed/empty SVG never reaches Telegram as an opaque IMAGE_PROCESS_FAILED, and
    // LOG image metadata on any failure (the review saw the failure with no detail). The
    // text-only fallback below (_channelSend) already runs regardless, so the post is
    // never lost — the card is best-effort polish.
    const _svgLen = (typeof svg === 'string' && svg.length) || 0;
    const _svgOk = _svgLen > 200 && typeof svg === 'string' && svg.includes('<svg');
    if (!_svgOk) {
      console.warn(`[ChannelCron] card skipped — invalid SVG (len=${_svgLen}, has_svg_tag=${typeof svg === 'string' && svg.includes('<svg')}); posting text-only`);
    } else {
      const uploaded = await uploadSvgAsPhoto(mediaBot, svg, `aria_intel_${Date.now()}.svg`);
      if (uploaded.ok && uploaded.fileId) {
        const sent = await sendPhoto(mediaBot, uploaded.fileId, { caption: _cardCaption(payload) });
        if (sent.ok) { _photoSent = true; _photoMessageId = sent.result?.message_id ?? sent.message_id ?? null; }
        else console.warn(`[ChannelCron] card send failed: ${sent.error || sent.status} (svg_len=${_svgLen}B)`);
      } else {
        console.warn(`[ChannelCron] card upload skipped: ${uploaded.error || 'no file id'} (svg_len=${_svgLen}B) — posting text-only`);
      }
    }
  } catch (e) {
    console.warn('[ChannelCron] card generation failed:', e.message);
  }
  // R-F2717 (#11) — return the text-send result annotated with whether the photo
  // was already delivered + its message id, so the caller can dedup a PARTIAL
  // (photo sent, text failed) instead of re-sending the photo next slot.
  const textRes = await _channelSend(bot, text, opts);
  return { ...textRes, photoSent: _photoSent, photoMessageId: _photoMessageId, textMessageId: textRes?.result?.message_id ?? null };
}

// R-F2717 (§25 proprioception) — a durable, queryable channel-outcome ledger.
// The brain signal was fire-and-forget with no response check, no persistence, no
// retry, and a bare catch — so ARIA could not answer "did I deliver X?" when the
// brain was unreachable. Now every outcome is persisted LOCALLY first (survives a
// brain outage), then pushed to the brain with a response check + one retry.
const _OUTCOME_LEDGER_PATH = process.env.CHANNEL_OUTCOME_LEDGER_PATH
  || (fs.existsSync('/data') ? '/data/channel_outcomes.json' : './data/channel_outcomes.json');
const _OUTCOME_MAX = 200;

function _readOutcomes() {
  try {
    const arr = JSON.parse(fs.readFileSync(_OUTCOME_LEDGER_PATH, 'utf8'));
    return Array.isArray(arr) ? arr : [];
  } catch { return []; }
}

function _persistOutcome(rec) {
  try {
    const arr = _readOutcomes();
    arr.push(rec);
    fs.writeFileSync(_OUTCOME_LEDGER_PATH, JSON.stringify(arr.slice(-_OUTCOME_MAX)));
  } catch { /* best-effort — must never break the post */ }
}

// ── R-F2895: operator escalation on sustained delivery failure ──────────────
// §19e is explicit that a blocker the operator has to find HIMSELF is the worst
// outcome. Between 2026-07-19 and 2026-07-23 the public channel failed six
// consecutive sends and the operator was never told: reportChannelOutcome wrote the
// local ledger and pushed a brain signal, and stopped there. The brain signal is
// telemetry for ARIA, not a message to a human. The private ADMIN group was live
// and writable that entire time (verified 2026-07-23: can_send_messages true) —
// nothing was wrong with the escalation path except that nothing used it.
const _ESCALATION_STATE_PATH = process.env.CHANNEL_ESCALATION_STATE_PATH
  || (fs.existsSync('/data') ? '/data/channel_escalation.json' : './data/channel_escalation.json');
const _ESCALATE_AFTER_FAILURES = 2;      // two consecutive failed slots = a real outage
const _ESCALATE_COOLDOWN_MS = 12 * 3600 * 1000;  // re-nag at most twice a day

function _readEscalationState() {
  try {
    const o = JSON.parse(fs.readFileSync(_ESCALATION_STATE_PATH, 'utf8'));
    return (o && typeof o === 'object') ? o : {};
  } catch { return {}; }
}

function _writeEscalationState(state) {
  try { fs.writeFileSync(_ESCALATION_STATE_PATH, JSON.stringify(state)); }
  catch { /* best-effort */ }
}

/**
 * R-F2895 — message the operator on the PRIVATE admin chat (TELEGRAM_CHAT_ID),
 * never the public channel. Rate-limited so a persistent outage nags twice a day
 * rather than every slot. Returns true if a message was actually sent.
 */
async function _escalateToOperator(bot, message, key = 'channel_delivery') {
  const adminChatId = String(process.env.TELEGRAM_CHAT_ID || bot?.chatId || '').trim();
  const token = String(bot?.botToken || process.env.TELEGRAM_BOT_TOKEN || '').trim();
  if (!adminChatId || !token) {
    console.error('[ChannelCron] R-F2895 escalation IMPOSSIBLE — no admin chat id / bot token configured');
    return false;
  }
  const state = _readEscalationState();
  const last = Number(state[key]?.last_sent_ms || 0);
  if (Date.now() - last < _ESCALATE_COOLDOWN_MS) return false;
  try {
    const r = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chat_id: adminChatId, text: message, disable_web_page_preview: true }),
      signal: AbortSignal.timeout(12000),
    });
    if (!r.ok) {
      console.error(`[ChannelCron] R-F2895 escalation FAILED HTTP ${r.status} — operator NOT notified`);
      return false;
    }
    state[key] = { last_sent_ms: Date.now(), last_message: String(message).slice(0, 200) };
    _writeEscalationState(state);
    console.log('[ChannelCron] R-F2895 escalated to operator on the admin chat');
    return true;
  } catch (e) {
    console.error('[ChannelCron] R-F2895 escalation exception — operator NOT notified:', e?.message);
    return false;
  }
}

/**
 * R-F2895 — count consecutive `failed` golden-intel outcomes at the tail of the
 * ledger and escalate once the streak reaches the threshold. Called after every
 * recorded failure, so ANY sustained delivery break reaches the operator, not just
 * the permission case R-F2894 names explicitly.
 */
async function _checkDeliveryStreak(bot) {
  const recent = _readOutcomes().filter(o => String(o?.action || '').includes('golden'));
  let streak = 0;
  for (let i = recent.length - 1; i >= 0; i--) {
    if (String(recent[i]?.outcome) === 'failed') streak++;
    else break;
  }
  if (streak < _ESCALATE_AFTER_FAILURES) return false;
  const last = recent[recent.length - 1] || {};
  return _escalateToOperator(
    bot,
    `BLOCKED: Telegram Golden Intel delivery has failed ${streak} times in a row.\n\n`
    + `Last error: ${String(last.detail || 'unknown').slice(0, 180)}\n`
    + `Last attempt: ${String(last.ts || 'unknown')}\n\n`
    + `Nothing is reaching the public channel. Check the bot's channel permissions first.`,
    'golden_delivery_streak',
  );
}

// Queryable surface (§25): "what did the channel evaluate/deliver, and did the
// brain acknowledge it?" — used by the admin state endpoint.
export function getRecentChannelOutcomes(limit = 25) {
  return _readOutcomes().slice(-Math.max(1, limit)).reverse();
}

async function reportChannelOutcome(action, outcome, detail = '') {
  const rec = {
    ts: new Date().toISOString(), surface: 'telegram_channel',
    action, outcome, detail: String(detail || '').slice(0, 200), brain_ack: false,
  };
  _persistOutcome(rec);   // durable FIRST — survives a brain outage
  const base = process.env.ARIA_SERVICE_URL || 'https://aria-intel.fly.dev';
  const token = process.env.ARIA_API_TOKEN || '';
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const res = await fetch(`${base}/api/aria/brain/signal`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ type: 'channel_delivery', surface: 'telegram_channel', action, outcome, detail: rec.detail }),
        signal: AbortSignal.timeout(8000),
      });
      if (res && res.ok) {
        // Record the ack by rewriting the most recent matching entry.
        try {
          const arr = _readOutcomes();
          for (let i = arr.length - 1; i >= 0; i--) {
            if (arr[i] && arr[i].ts === rec.ts && arr[i].action === action) { arr[i].brain_ack = true; break; }
          }
          fs.writeFileSync(_OUTCOME_LEDGER_PATH, JSON.stringify(arr.slice(-_OUTCOME_MAX)));
        } catch { /* ledger update best-effort */ }
        return;
      }
    } catch { /* retry once, then give up — local record already stands */ }
  }
}

// R-F2723 (§25, #10) — persisted per-day slot-execution ledger + startup catch-up.
// The cron fires at exactly 07:00 / 17:00 Europe/London; if aria-web is down or
// restarting at that minute the slot was silently LOST (markPosted is in-memory and
// resets on restart). Now each slot ATTEMPT is persisted per London-day, and on
// boot a due-but-unrun slot is executed once. Re-running is idempotent — the
// persisted content-dedup (wasRecentlyPosted) prevents a duplicate post.
const _SLOT_LEDGER_PATH = process.env.CHANNEL_SLOT_LEDGER_PATH
  || (fs.existsSync('/data') ? '/data/channel_slot_ledger.json' : './data/channel_slot_ledger.json');

function _londonSlotState() {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', hour12: false,
  }).formatToParts(new Date());
  const get = (t) => parts.find((p) => p.type === t)?.value;
  return { dateKey: `${get('year')}-${get('month')}-${get('day')}`, hour: Number(get('hour')) };
}

function _readSlotLedger() {
  try {
    const o = JSON.parse(fs.readFileSync(_SLOT_LEDGER_PATH, 'utf8'));
    return o && typeof o === 'object' ? o : {};
  } catch { return {}; }
}

function _recordSlotRun(hour) {
  if (!Number.isFinite(Number(hour))) return;
  try {
    const { dateKey } = _londonSlotState();
    const led = _readSlotLedger();
    const arr = Array.isArray(led[dateKey]) ? led[dateKey] : [];
    if (!arr.includes(Number(hour))) arr.push(Number(hour));
    led[dateKey] = arr;
    // keep only the most recent 5 London-days
    const keys = Object.keys(led).sort();
    for (const k of keys.slice(0, Math.max(0, keys.length - 5))) delete led[k];
    fs.writeFileSync(_SLOT_LEDGER_PATH, JSON.stringify(led));
  } catch { /* best-effort */ }
}

function _wasSlotRunToday(hour) {
  const { dateKey } = _londonSlotState();
  const arr = _readSlotLedger()[dateKey];
  return Array.isArray(arr) && arr.includes(Number(hour));
}

// Run any slot that was DUE earlier today but never executed (e.g. the process was
// down at 07:00/17:00). Idempotent via content-dedup. Called shortly after boot.
export async function runStartupCatchUp(currentData, bot) {
  if (!bot?.botToken || !String(bot?.channelId || '').trim()) return { skipped: 'not_configured' };
  const { hour: nowHour } = _londonSlotState();
  const recovered = [];
  for (const slot of [7, 17]) {
    if (nowHour >= slot && !_wasSlotRunToday(slot)) {
      console.log(`[ChannelCron] startup catch-up: slot ${slot}:00 was missed today — running once`);
      try {
        await handleMorningSignalCron(currentData, bot, { hour: slot, catchUp: true });
        recovered.push(slot);
      } catch (e) {
        console.warn(`[ChannelCron] catch-up for ${slot}:00 failed:`, e.message);
      }
    }
  }
  return { recovered };
}

function _ariaServiceHeaders(extra = {}) {
  const token = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';
  return { ...extra, ...(token ? { authorization: `Bearer ${token}` } : {}) };
}

export async function fetchGoldenIntelSignals(opts = {}) {
  const base = opts.serviceUrl || process.env.ARIA_SERVICE_URL || 'https://aria-intel.fly.dev';
  const limit = Math.max(1, Math.min(Number(opts.limit) || 20, 100));
  // R-F2893 — ask the SERVER for the grades we can publish. Grade-filtering after a
  // newest-N fetch is a lottery: on 2026-07-23 the only three Grade A signals sat at
  // positions 66-68 and this cron fetched 60, so it reported "no Grade A" while
  // three official TED tenders sat in the store. The window was already raised once
  // (R-F2715, 20 -> 60) and had been outgrown again within days.
  const grades = String(opts.grades || '').trim();
  const q = `limit=${limit}${grades ? `&grades=${encodeURIComponent(grades)}` : ''}`;
  try {
    const r = await fetch(`${base}/api/aria/intel/signals/recent?${q}`, {
      headers: _ariaServiceHeaders(),
      signal: AbortSignal.timeout(Number(opts.timeoutMs) || 12000),
    });
    if (!r.ok) {
      return { ok: false, error: `HTTP ${r.status}`, signals: [], freshness: { stale: true, stale_reasons: ['fetch_failed'] } };
    }
    const data = await r.json();
    return {
      ok: true,
      signals: Array.isArray(data?.signals) ? data.signals : [],
      freshness: data?.freshness || { stale: true, stale_reasons: ['missing_freshness'] },
      raw: data,
    };
  } catch (e) {
    return { ok: false, error: e.message, signals: [], freshness: { stale: true, stale_reasons: ['fetch_exception'] } };
  }
}

const _GOLDEN_ALLOWED_TYPES = new Set([
  'sanctions_change',
  'active_tender',
  'contract_award',
  'budget_movement',
  'programme_signal',
  'competitor_activity',
  'conflict_escalation',
]);

function _signalEvidenceUrl(signal) {
  // R-F2602: only surface a real http(s) evidence URL. This both hardens the
  // channel gate (Boolean(_signalEvidenceUrl) now demands a valid scheme, mirroring
  // dashboard.html signalEvidenceUrl) and stops a non-URL string reaching the
  // Markdown formatter as an injectable link. The format site _md()-escapes it too.
  const url = String(signal?.url || signal?.evidence?.url || '').trim();
  return /^https?:\/\/\S+$/i.test(url) ? url : '';
}

function _postDedupKey(signal) {
  return dedupKey(signal);
}

function _signalSourceTier(signal) {
  return String(signal?.source_tier || signal?.evidence?.source_tier || '').toLowerCase();
}

function _customerValueScore(signal) {
  const explicit = Number(signal?.customer_value?.score ?? signal?.distribution_score ?? 0);
  return Number.isFinite(explicit) ? explicit : 0;
}

function _customerValueHardRejections(signal) {
  const reasons = Array.isArray(signal?.customer_value?.rejection_reasons)
    ? signal.customer_value.rejection_reasons
    : (Array.isArray(signal?.distribution_rejection_reasons) ? signal.distribution_rejection_reasons : []);
  return reasons.filter(r => !['customer_value_below_distribution_threshold', 'customer_value_below_telegram_threshold'].includes(String(r)));
}

function _md(text) {
  return String(text || '').replace(/([_*`[\]])/g, '\\$1');
}

export function selectTelegramGoldenIntel(feed, opts = {}) {
  const freshness = feed?.freshness || {};
  const allowBackfilled = opts.allowBackfilled === true || String(process.env.CHANNEL_GOLDEN_ALLOW_BACKFILLED || '').toLowerCase() === '1';
  if (!feed?.ok) return null;
  // R-F2715 — per-candidate, not a global kill switch. 'source_failure_degraded'
  // (>15% of UNRELATED monitored feeds down) must NOT suppress a feed whose own
  // newest signal is fresh — a live official tender/designation shouldn't die
  // because regional newspapers failed. Block only on staleness reasons that mean
  // the candidates themselves are stale or absent (no_signals, signals_stale,
  // poll_stale, missing_poll_state, last_poll_failed, missing_signal_timestamp).
  // R-F2896 — prefer the backend's canonical `blocking_stale_reasons`; the local
  // filter stays as the fallback for an older backend, and both must agree by
  // construction (same exclusion set, computed once server-side).
  const staleReasons = Array.isArray(freshness.stale_reasons) ? freshness.stale_reasons.map(String) : [];
  const blockingStale = Array.isArray(freshness.blocking_stale_reasons)
    ? freshness.blocking_stale_reasons.map(String)
    : staleReasons.filter(r => r !== 'source_failure_degraded');
  if (freshness.stale === true && blockingStale.length > 0) return null;
  if (freshness.stale === true && staleReasons.length === 0) return null; // stale w/o reason → conservative
  if (freshness.backfilled && !allowBackfilled) return null;

  // R-F2714 — gate on the formal intel_grade (computed in Python from tier /
  // corroboration / evidence URL / named entity / relevance), NOT the
  // customer_value score, which is never computed and made every raw-news signal
  // structurally unpublishable (score 0 vs a >=80 gate). Grade A is decision-grade
  // (official/corroborated primary). The completeness + evidence-URL + dedup checks
  // stay. R-F2716 — the target grade is a parameter so the A→B policy can ask for
  // the best Grade-B candidate when no Grade A exists.
  const wantGrade = String(opts.grade || 'A').toUpperCase();
  const candidates = (Array.isArray(feed.signals) ? feed.signals : []).filter(s => {
    const grade = String(s?.intel_grade || '').toUpperCase();
    const type = String(s?.signal_type || '');
    return grade === wantGrade
      && _GOLDEN_ALLOWED_TYPES.has(type)
      && Boolean(s?.decision_summary || s?.title)
      && Boolean(s?.why_it_matters)
      && Boolean(s?.recommended_action)
      && Boolean(_signalEvidenceUrl(s))
      && (allowBackfilled || !s?._backfilled)
      && !wasRecentlyPosted(_postDedupKey(s));
  });
  const confidenceRank = { HIGH: 0, MEDIUM: 1, LOW: 2 };
  candidates.sort((a, b) => (
    confidenceRank[String(a.confidence || '').toUpperCase()] - confidenceRank[String(b.confidence || '').toUpperCase()]
    || Number(b.score || 0) - Number(a.score || 0)
    || String(b.detected_at || b.published || '').localeCompare(String(a.detected_at || a.published || ''))
  ));
  return candidates[0] || null;
}

export function formatGoldenIntelChannelPost(signal, freshness = {}) {
  const title = _md(String(signal?.decision_summary || signal?.title || 'Golden Intel').slice(0, 220));
  const why = _md(String(signal?.why_it_matters || '').slice(0, 360));
  const action = _md(String(signal?.recommended_action || '').slice(0, 180));
  const source = _md(String(signal?.source || signal?.evidence?.source || 'ARIA monitored source').slice(0, 120));
  const url = _signalEvidenceUrl(signal);
  const target = signal?.target ? `\nTarget: *${_md(String(signal.target).slice(0, 90))}*` : '';
  const horizon = signal?.action_horizon ? ` · Horizon: ${_md(signal.action_horizon)}` : '';
  const detected = signal?.detected_at || signal?.published || freshness.newest_signal_at || '';
  const dateLine = detected ? `\nDetected: ${String(detected).slice(0, 16).replace('T', ' ')}` : '';
  return [
    '*GOLDEN INTEL*',
    '',
    `*${title}*`,
    target,
    '',
    `Why it matters: ${why}`,
    '',
    `Action: *${action}*`,
    '',
    `Signal: ${_md(signal.priority || 'HIGH')} · ${_md(signal.confidence || 'HIGH')} · ${_md(signal.quality_label || 'decision-grade')}${horizon}`,
    `Evidence: ${source}${url ? ` — ${_md(url)}` : ''}${dateLine}`,
    '',
    'Reply `ARIA [topic]` for the deeper brief.',
  ].filter(Boolean).join('\n');
}

// R-F2716 — the best Grade-B candidate (one credible source, corroboration
// pending). Used ONLY by the 17:00 A→B fallback when no Grade A exists.
export function selectTelegramGradeB(feed, opts = {}) {
  return selectTelegramGoldenIntel(feed, { ...opts, grade: 'B' });
}

// R-F2716 — a Grade-B post that is HONEST about its single-source status. It must
// never imply confirmation (the USP: single-source disclosure is fine, single-source
// that reads as confirmed is fabrication). The label + "corroboration pending" line
// make the uncertainty explicit.
export function formatGradeBChannelPost(signal, freshness = {}) {
  const title = _md(String(signal?.decision_summary || signal?.title || 'Signal').slice(0, 220));
  const why = _md(String(signal?.why_it_matters || '').slice(0, 360));
  const action = _md(String(signal?.recommended_action || '').slice(0, 180));
  const source = _md(String(signal?.source || signal?.evidence?.source || 'ARIA monitored source').slice(0, 120));
  const url = _signalEvidenceUrl(signal);
  const target = signal?.target ? `\nTarget: *${_md(String(signal.target).slice(0, 90))}*` : '';
  const detected = signal?.detected_at || signal?.published || freshness.newest_signal_at || '';
  const dateLine = detected ? `\nDetected: ${String(detected).slice(0, 16).replace('T', ' ')}` : '';
  return [
    '*GRADE B — single credible source*',
    '_Independent corroboration pending — treat as a lead, not confirmation._',
    '',
    `*${title}*`,
    target,
    '',
    `Why it matters: ${why}`,
    '',
    `Suggested next step: *${action}*`,
    '',
    `Source (single): ${source}${url ? ` — ${_md(url)}` : ''}${dateLine}`,
    '',
    'Reply `ARIA [topic]` for the deeper brief.',
  ].filter(Boolean).join('\n');
}

async function _sendScheduledPostOnce(bot, type, post, dedupSignal, opts = {}) {
  const key = _postDedupKey({ type, text: post, ...dedupSignal });
  if (wasRecentlyPosted(key)) {
    markPosted(type);
    console.log(`[ChannelCron] ${type} skipped — recently posted`);
    if (opts.reportAction) reportChannelOutcome(opts.reportAction, 'skipped', 'deduped');
    return { ok: false, skipped: true, reason: 'deduped' };
  }
  const res = await _channelSend(bot, post, { disablePreview: opts.disablePreview === true });
  if (res.ok) {
    markPosted(type);
    recordPosted(key);
  }
  return res;
}

// ── Exports for server.mjs ─────────────────────────────────────────────────────

export {
  // Channel Publisher
  curateSignals,
  formatChannelPost,
  formatDailyBrief,
  canPostNow,
  recordPost,
  getSchedulerState,
  publishSignal,
  // Channel Scheduler
  getCurrentSlot,
  markPosted,
  getTodaySchedule,
  getSchedulerState2,
  buildCaseFile,
  buildKnowYourRights,
  buildCountryRead,
  buildMorningSignal,
  buildWelcomePost,
  // Reply Keyword Router
  parseReply,
  handleScreen,
  handleCountryBrief,
  handleTender,
  handleDemo,
  handlePro,
  handleHelp,
};

// ── Sweep Cycle Hook ───────────────────────────────────────────────────────────

/**
 * Run the channel publisher sweep cycle.
 * Called from server.mjs after each sweep completes.
 *
 * @param {object} currentData — Sweep results.
 * @param {object} bot — Telegram bot config { botToken, chatId, channelId }.
 * @returns {Promise<{posted:number,errors:number}>}
 */
/**
 * R-F2299 — wireBreakingAlertsToChannel: publish genuinely-breaking signals
 * (isBreakingSignal, score >= BREAKING_SCORE) to the channel IMMEDIATELY,
 * bypassing the routine posting-cadence cap (canPostNow). This is the real-time
 * value the strategy calls for — a FLASH/critical item reaches subscribers the
 * moment it lands, not on the next curated slot. Returns { handled, posted,
 * errors }; `handled` is the Set of signal objects it posted so the routine
 * curated pass can exclude them (no double-post).
 * @param {object[]} signals
 * @param {object} bot — { botToken, chatId, channelId }
 */
// R-F2315 — breaking-alert flood guard. publishBreakingSignals runs on EVERY sweep
// (~every refresh interval) and bypasses the 1/day cadence cap by design. With no
// per-day cap it re-posted persistent high-severity correlations every couple of
// minutes → the channel flooded (operator: "sending every two minutes"). Now:
//   • OFF by default (CHANNEL_BREAKING_ENABLED) — matches the one-post/day,
//     keep-noise-down directive; real-time breaking is opt-in, not the default.
//   • when enabled, a HARD per-day cap (CHANNEL_MAX_BREAKING_PER_DAY, default 2)
//     so it can NEVER flood even if the dedup misses.
let _breakingCount = 0;
let _breakingDate = '';
function _breakingPostedToday() {
  const today = new Date().toISOString().slice(0, 10);
  if (_breakingDate !== today) { _breakingDate = today; _breakingCount = 0; }
  return _breakingCount;
}

export async function publishBreakingSignals(signals, bot) {
  const handled = new Set();
  let posted = 0;
  let errors = 0;
  const enabled = ['1', 'true', 'yes', 'on'].includes(String(process.env.CHANNEL_BREAKING_ENABLED || '').toLowerCase());
  if (!enabled) return { handled, posted, errors, disabled: true };
  const capPerDay = Number(process.env.CHANNEL_MAX_BREAKING_PER_DAY) || 2;
  for (const s of (signals || [])) {
    if (_breakingPostedToday() >= capPerDay) break;   // hard daily ceiling — no flood
    if (!isBreakingSignal(s)) continue;
    handled.add(s);
    try {
      const r = await publishSignal(s, bot, { generateImage: true, registerKeyword: true, crossPostLinkedIn: false });
      if (r.ok) { posted++; _breakingCount = _breakingPostedToday() + 1; console.log('[ChannelSweep] 🚨 BREAKING published:', String(s.title || s.summary || '').substring(0, 60)); }
      else { errors++; console.warn('[ChannelSweep] breaking skipped:', r.error); }
    } catch (e) {
      errors++;
      console.warn('[ChannelSweep] breaking error:', e.message);
    }
  }
  return { handled, posted, errors };
}

export async function runChannelSweep(currentData, bot) {
  if (!bot?.botToken || !currentData) return { posted: 0, errors: 0 };
  // R-F2544 — public Telegram channel is Golden Intel only. Sweep-time
  // breaking/routine posts use the older broad curation scorer, so they must not
  // publish to the channel. Golden Intel is posted by handleMorningSignalCron().
  return { posted: 0, errors: 0, skipped: true, reason: 'golden_intel_only' };
}

// ── Cron Job Handlers ──────────────────────────────────────────────────────────

/**
 * Handle the 07:00 Morning Signal cron.
 *
 * @param {object} currentData — Sweep results.
 * @param {object} bot — Telegram bot config.
 */
/**
 * R-F2312 — SANCTIONS SPOTLIGHT: wire ARIA's own Python screening engine
 * (/api/aria/sanctions/fuzzy — the never-false-clean OFAC/UK/EU/UN/OpenSanctions
 * engine that powers SCREEN) into the daily post. Takes the day's topical
 * multi-list entities from the sweep, screens the top few through the REAL engine,
 * and returns ONE verified blocking hit as a spotlight signal — or null. Lean by
 * design: at most one item, only genuine blocking hits (never a fabricated
 * "clear"), and it stays silent when the sweep has no topical entity. Never throws.
 *
 * @param {object} currentData
 * @param {{serviceUrl?:string, token?:string, maxCandidates?:number}} [opts]
 * @returns {Promise<{title:string,text:string}|null>}
 */
export async function fetchSanctionsSpotlight(currentData, opts = {}) {
  try {
    const base = opts.serviceUrl || process.env.ARIA_SERVICE_URL || 'https://aria-intel.fly.dev';
    const token = opts.token || process.env.ARIA_API_TOKEN || '';
    const maxCandidates = opts.maxCandidates || 3;
    const candidates = (Array.isArray(currentData?.opensanctions?.recent) ? currentData.opensanctions.recent : [])
      .map(e => e?.name || e?.caption)
      .filter(n => typeof n === 'string' && n.trim().length >= 2)
      .slice(0, maxCandidates);
    for (const name of candidates) {
      let d;
      try {
        const r = await fetch(`${base}/api/aria/sanctions/fuzzy`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
          body: JSON.stringify({ name }),
          signal: AbortSignal.timeout(20000),
        });
        if (!r.ok) continue;
        d = await r.json();
      } catch { continue; }
      // never-false-clean: only feature a genuinely screened, blocking hit.
      if (d && d.screened === true && !d.error && Array.isArray(d.blocking_matches) && d.blocking_matches.length > 0) {
        const lists = [...new Set(d.blocking_matches.map(m => m.list || m.source || m.dataset).filter(Boolean))].slice(0, 3);
        const n = d.blocking_matches.length;
        return {
          title: `⚖️ Sanctions spotlight: ${name}`,
          text: `ARIA screened *${name}* against OFAC · UK OFSI · EU · UN · OpenSanctions → *${n} blocking match${n === 1 ? '' : 'es'}*${lists.length ? ` (${lists.join(', ')})` : ''}. Do not proceed without enhanced due diligence.\n\nScreen any counterparty free — reply \`SCREEN [company]\`.`,
        };
      }
    }
    return null;
  } catch (e) {
    console.warn('[ChannelCron] sanctions spotlight failed:', e.message);
    return null;
  }
}

/**
 * R-F2310 — build the daily-post signals from the REAL sweep data, in the
 * audit's customer-acquisition priority order: real sanctions designations →
 * verified procurement tenders → a projected market opportunity → (else empty →
 * the cron skips the post). "Screenshot-worthy = a name, a number, a source."
 *
 * Correct field map (the old code read fields that never existed):
 *   sanctions  → currentData.opensanctions.recent  [{ name, datasets }]
 *   tenders    → currentData.bdIntelligence.tenders [{ title, summary, url, verified, date }]
 *   opportunity→ currentData.opportunities          [{ market, tier, score, procurementNeeds }] (no title/summary → project)
 *
 * @param {object} currentData
 * @returns {Array<{title:string,text:string}>}
 */
export function buildDailySignals(currentData) {
  const signals = [];

  // 1. Real sanctions designations — entities surfacing on 2+ lists (highest
  //    credibility for a compliance audience).
  const recent = Array.isArray(currentData?.opensanctions?.recent) ? currentData.opensanctions.recent : [];
  for (const s of recent.slice(0, 2)) {
    const name = s?.name || s?.caption;
    if (!name) continue;
    const lists = Array.isArray(s.datasets) ? s.datasets : [];
    const where = lists.length ? ` — now on ${lists.length} sanctions/watchlist dataset${lists.length === 1 ? '' : 's'}${lists.length ? ` (${lists.slice(0, 3).join(', ')})` : ''}` : '';
    signals.push({
      title: `⚖️ Sanctions exposure: ${name}`,
      text: `${name}${where}. Screen your counterparties before contracting — reply \`SCREEN ${name}\`.`,
    });
  }

  // 2. A verified procurement tender (the strongest broker-acquisition asset) —
  //    only VERIFIED/CORROBORATED, must carry a title.
  const tenders = (Array.isArray(currentData?.bdIntelligence?.tenders) ? currentData.bdIntelligence.tenders : [])
    .filter(t => t && t.title && ['VERIFIED', 'CORROBORATED'].includes(String(t.verified || '').toUpperCase()));
  if (tenders.length) {
    const t = tenders[0];
    signals.push({
      title: `🔍 Procurement signal: ${t.title}`,
      text: [t.summary, t.date ? `Dated ${t.date}.` : '', t.source ? `Source: ${t.source}.` : '', t.url || '']
        .filter(Boolean).join(' ').slice(0, 400),
    });
  }

  // 3. Fallback — a projected market opportunity (only if 1 & 2 were dry).
  if (signals.length === 0) {
    const opps = Array.isArray(currentData?.opportunities) ? currentData.opportunities : [];
    const top = opps.filter(o => o && o.market).sort((a, b) => (b.score || 0) - (a.score || 0))[0];
    if (top) {
      const needs = Array.isArray(top.procurementNeeds) && top.procurementNeeds.length
        ? ` — active needs: ${top.procurementNeeds.slice(0, 3).join(', ')}` : '';
      signals.push({
        title: `🌍 Market watch: ${top.market}`,
        text: `${top.tier ? `${top.tier} ` : ''}defence-market signal${top.score != null ? ` (score ${Math.round(top.score)})` : ''}${needs}.`,
      });
    }
  }

  return signals;
}

export async function handleMorningSignalCron(currentData, bot, opts = {}) {
  if (!bot?.botToken) return;
  // R-F2716 — A→B publish policy. The evening (17:00) slot may fall back to a
  // clearly-labelled Grade B when no Grade A exists; the morning (07:00) slot holds
  // for corroboration. The caller passes { hour }.
  const allowGradeB = Number(opts.hour) === 17;
  console.log(`[ChannelCron] ${allowGradeB ? 'Evening' : 'Morning'} Signal (A→B fallback=${allowGradeB})...`);
  try {
    const channelId = String(bot?.channelId || '').trim();
    if (!channelId) {
      console.log('[ChannelCron] Golden Intel skipped — TELEGRAM_CHANNEL_ID not configured');
      reportChannelOutcome('daily_golden_intel', 'skipped', 'missing TELEGRAM_CHANNEL_ID');
      return { ok: false, skipped: true, reason: 'missing_channel_id' };
    }
    const channelBot = { ...bot, chatId: channelId, channelId };
    const destination = await validatePublicChannelDestination(channelBot);
    if (!destination.ok) {
      // R-F2894 — say WHAT is wrong and WHO must fix it. "HTTP 403" told the
      // operator nothing for four days (§19a/§19e).
      const _blockDetail = destination.detail || destination.type || destination.status || 'unknown';
      console.error(`BLOCKED: telegram channel — Golden Intel cannot publish: ${destination.reason} (${_blockDetail})`);
      reportChannelOutcome('daily_golden_intel', 'failed', `${destination.reason}: ${_blockDetail}`);
      if (destination.reason === 'bot_cannot_post_to_channel') {
        await _escalateToOperator(
          bot,
          `BLOCKED: Telegram channel publishing is DOWN.\n\n`
          + `Cause: ${_blockDetail}\n`
          + `Effect: every Golden Intel slot is being skipped — no intel is reaching the public channel.\n\n`
          + `ACTION NEEDED (operator only): re-add the bot as an administrator of the channel `
          + `with "Post Messages" (and "Post Photos" for signal cards). No code change can fix this.`,
        );
      }
      return { ok: false, skipped: true, reason: destination.reason, destinationType: destination.type || null };
    }
    // R-F2723 — record the slot ATTEMPT (per London-day) so the startup catch-up
    // knows this slot ran today and won't re-run it. Marking on attempt (not just
    // on a successful post) is correct: a slot that held / errored still RAN at its
    // scheduled time and must not be re-fired hours later with stale intel.
    if (Number.isFinite(Number(opts.hour))) _recordSlotRun(Number(opts.hour));
    // R-F2544 — Golden Intel is the ONLY public-channel intel lane. The backend
    // must prove freshness and the signal must pass the public-channel quality
    // gate. Missing/stale/no passing Golden Intel means no Telegram post.
    // R-F2715 — grade+filter over a LARGER bounded window, not just the newest
    // 20. Low-value "context" signals (which grade to REJECT) previously crowded
    // the 20-slot window and pushed a high-value tender/designation out before the
    // quality filter ran. selectTelegramGoldenIntel filters by intel_grade, so a
    // bigger window simply gives Grade-A candidates a chance to be seen.
    // R-F2893 — grade-scoped at the source. The morning slot publishes Grade A only,
    // so it asks for Grade A only; the evening slot may fall back to a labelled
    // Grade B, so it asks for both. Node still applies the full quality gate on top
    // (completeness, evidence URL, dedup) — the server narrows, it does not certify.
    const goldenFeed = await fetchGoldenIntelSignals({ limit: 60, grades: allowGradeB ? 'A,B' : 'A' });
    const golden = selectTelegramGoldenIntel(goldenFeed);
    if (golden) {
      const post = formatGoldenIntelChannelPost(golden, goldenFeed.freshness);
      const res = await _channelSendWithCard(channelBot, post, {
        title: golden.decision_summary || golden.title || 'Golden Intel',
        subtitle: golden.why_it_matters || '',
        bullets: [
          golden.recommended_action,
          golden.corroboration || golden.quality_label,
          golden.target ? `Target: ${golden.target}` : '',
        ].filter(Boolean),
        source: golden.source || 'ARIA Golden Intel',
        type: golden.signal_type === 'sanctions_change' ? 'sanctions' : 'daily',
      }, { disablePreview: false });
      if (res.ok) {
        markPosted('golden_intel');
        recordPosted(_postDedupKey(golden));
        console.log('[ChannelCron] Golden Intel posted:', golden.id || golden.title);
        reportChannelOutcome('daily_golden_intel', 'delivered', `${golden.id || golden.title || 'golden'} text#${res.textMessageId || '?'}`);
      } else if (res.photoSent) {
        // R-F2717 (#11) — the card photo WAS delivered but the text failed. Record
        // the dedup key so the next slot does NOT re-send the same photo, and report
        // a partial so ARIA knows the text is missing (§25 proprioception).
        markPosted('golden_intel');
        recordPosted(_postDedupKey(golden));
        console.warn('[ChannelCron] Golden Intel PARTIAL — photo sent, text failed HTTP', res.status);
        reportChannelOutcome('daily_golden_intel', 'partial', `photo#${res.photoMessageId || '?'} text_failed_HTTP_${res.status}`);
      } else {
        console.warn('[ChannelCron] Golden Intel post failed HTTP', res.status);
        reportChannelOutcome('daily_golden_intel', 'failed', `HTTP ${res.status}`);
        await _checkDeliveryStreak(bot);   // R-F2895 — never fail silently twice
      }
      return { ok: res.ok, grade: 'A', partial: !res.ok && res.photoSent === true };
    }

    // R-F2716 — A→B fallback (17:00 only): publish the best LABELLED Grade B when
    // no Grade A exists. Grade B is a single credible source, explicitly marked
    // "corroboration pending" so it never implies confirmation (USP).
    if (allowGradeB) {
      const gradeB = selectTelegramGradeB(goldenFeed);
      if (gradeB) {
        const post = formatGradeBChannelPost(gradeB, goldenFeed.freshness);
        const res = await _channelSend(channelBot, post, { disablePreview: false });
        if (res.ok) {
          markPosted('golden_intel');
          recordPosted(_postDedupKey(gradeB));
          console.log('[ChannelCron] Grade B posted (corroboration pending):', gradeB.id || gradeB.title);
          reportChannelOutcome('daily_golden_intel', 'delivered', `gradeB:${gradeB.id || gradeB.title || 'b'}`);
        } else {
          console.warn('[ChannelCron] Grade B post failed HTTP', res.status);
          reportChannelOutcome('daily_golden_intel', 'failed', `gradeB HTTP ${res.status}`);
          await _checkDeliveryStreak(bot);   // R-F2895
        }
        return { ok: res.ok, grade: 'B' };
      }
      console.log('[ChannelCron] No qualifying intelligence — no Grade A or Grade B');
      reportChannelOutcome('daily_golden_intel', 'skipped', 'no_qualifying_intelligence');
      return { ok: false, skipped: true, reason: 'no_qualifying_intelligence' };
    }

    // Morning (07:00): hold for corroboration — a Grade B may firm up to A by evening.
    console.log('[ChannelCron] Morning: no Grade A — holding for corroboration');
    reportChannelOutcome('daily_golden_intel', 'skipped', 'held_for_corroboration');
    return { ok: false, skipped: true, reason: 'held_for_corroboration' };
  } catch (e) {
    console.error('[ChannelCron] Morning Signal failed:', e.message);
    reportChannelOutcome('daily_post', 'failed', e.message);
  }
}

// ── RETIRED editorial lanes (R-F2544) ───────────────────────────────────────────
// The four editorial crons below (Case File / Know Your Rights / Country Read /
// Opportunity Signal) and publishBreakingSignals produced NON-Golden channel
// content. When the public channel became Golden Intel only their content logic
// was deleted and they are no longer scheduled — grep confirms NO cron.schedule
// references them; only tests call them. They are RETIRED, not flag-toggled: the
// TELEGRAM_GOLDEN_INTEL_ONLY flag (server.mjs) governs the still-LIVE non-Golden
// paths (manual admin-channel endpoints + sweep/digest/explorer alert lanes), NOT
// these stubs. Setting the flag to 0 re-opens those live paths; it does NOT revive
// these lanes. They return { skipped:true, reason:'golden_intel_only' } — the same
// signal the live guard emits, so downstream callers see one uniform skip reason —
// but that reason is unconditional here, reflecting retirement, not a runtime gate.
// Kept only so existing callers/tests keep a stable, honest no-op.

/**
 * RETIRED editorial lane (R-F2544) — was the 09:00 Case File cron; now an
 * unscheduled no-op that always skips (see the RETIRED block comment above).
 *
 * @param {object} bot — Telegram bot config.
 */
export async function handleCaseFileCron(bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Case File...');
  console.log('[ChannelCron] Case File skipped — Golden Intel only');
  reportChannelOutcome('case_file', 'skipped', 'golden_intel_only');
  return { ok: false, skipped: true, reason: 'golden_intel_only' };
}

/**
 * RETIRED editorial lane (R-F2544) — was the 12:00 Know Your Rights cron; now an
 * unscheduled no-op that always skips (see the RETIRED block comment above).
 *
 * @param {object} bot — Telegram bot config.
 */
export async function handleKnowYourRightsCron(bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Know Your Rights...');
  console.log('[ChannelCron] Know Your Rights skipped — Golden Intel only');
  reportChannelOutcome('know_your_rights', 'skipped', 'golden_intel_only');
  return { ok: false, skipped: true, reason: 'golden_intel_only' };
}

/**
 * RETIRED editorial lane (R-F2544) — was the 15:00 Country Read cron; now an
 * unscheduled no-op that always skips (see the RETIRED block comment above).
 *
 * @param {object} bot — Telegram bot config.
 */
export async function handleCountryReadCron(bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Country Read...');
  console.log('[ChannelCron] Country Read skipped — Golden Intel only');
  reportChannelOutcome('country_read', 'skipped', 'golden_intel_only');
  return { ok: false, skipped: true, reason: 'golden_intel_only' };
}

/**
 * RETIRED editorial lane (R-F2544) — was the 18:00 Opportunity Signal cron; now an
 * unscheduled no-op that always skips (see the RETIRED block comment above).
 *
 * @param {object} bot — Telegram bot config.
 */
export async function handleOpportunityCron(bot) {
  if (!bot?.botToken) return;
  console.log('[ChannelCron] Opportunity Signal...');
  console.log('[ChannelCron] Opportunity Signal skipped — Golden Intel only');
  reportChannelOutcome('opportunity', 'skipped', 'golden_intel_only');
  return { ok: false, skipped: true, reason: 'golden_intel_only' };
}

/**
 * Handle a reply keyword from a user.
 *
 * @param {string} text — User reply text.
 * @param {string} userId — Telegram user ID.
 * @returns {Promise<{text:string}>}
 */
export async function handleReply(text, userId) {
  const parsed = parseReply(text);

  switch (parsed.action) {
    case 'screen':
      return handleScreen(parsed.arg);
    case 'country_brief':
      return handleCountryBrief(parsed.arg);
    case 'tender':
      return handleTender(parsed.arg);
    case 'demo':
      return handleDemo();
    case 'pro':
      return handlePro();
    case 'help':
      return handleHelp();
    case 'deep_dive': {
      const { resolveKeyword, matchKeyword } = await import('./channelInteractive.mjs');
      const match = matchKeyword(parsed.keyword || text);
      if (match.matched) {
        const resolved = resolveKeyword(match.keyword, userId || 'anonymous');
        return resolved.ok ? { text: resolved.response } : { text: resolved.error };
      }
      return { text: 'Unknown keyword. Try HELP for available commands.' };
    }
    default:
      return { text: 'I did not understand that. Try HELP for available commands.' };
  }
}
