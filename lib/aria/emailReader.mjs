/**
 * ARIA — Email Intelligence Reader + Composer
 * ═══════════════════════════════════════════════════════════════════════════
 * Reads ARIA's email inbox (aria@imaria.io) and feeds content to brain.
 * Primary use: LinkedIn Sales Navigator alerts → ARIA learns about
 * job changes, company news, competitor activity, procurement signals.
 *
 * Also captures: Google Alerts, tender notifications, any forwarded intel.
 *
 * NEW: ARIA can compose and send emails via SMTP (reply to contacts,
 * send intelligence briefs, follow up on opportunities).
 *
 * ─────────────────────────────────────────────────────────────────────────
 * SEENODE ENV VARS
 * ─────────────────────────────────────────────────────────────────────────
 *   ARIA_EMAIL_HOST       outlook.office365.com
 *   ARIA_EMAIL_PORT       993
 *   ARIA_EMAIL_USER       aria@imaria.io
 *   ARIA_EMAIL_PASS       (email password)
 *   ARIA_EMAIL_ENABLED    true
 *
 *   # For sending (uses same credentials or falls back to EMAIL_* vars):
 *   ARIA_SMTP_HOST        (defaults to ARIA_EMAIL_HOST = outlook.office365.com)
 *   ARIA_SMTP_PORT        465
 * ═══════════════════════════════════════════════════════════════════════════
 */

import { createRequire } from 'module';
import { processLinkedInEmail } from './linkedinIntel.mjs';
import { brainAbsorb } from '../self/learning_store.mjs';
import { redisGet, redisSet } from '../persist/store.mjs';
import { _ariaFetch } from './_ariaFetch.mjs';
import { xlsxSizeOk, MAX_XLSX_BYTES } from '../util/xlsxGuard.mjs'; // R-F2592

// R-F4152 (C-175) — READ ENV THROUGH `_env`, NEVER RAW.
//
// Live on aria-web, measured 2026-08-18: `ARIA_EMAIL_ENABLED` was `"true\r"`.
// `"true\r" === 'true'` is FALSE, so this module reported "Disabled — set
// ARIA_EMAIL_ENABLED=true to activate" and did nothing, for a flag the operator
// HAD set to true. Nothing failed, nothing alerted; a feature that was switched
// on was simply off.
//
// `ARIA_EMAIL_USER` and `ARIA_EMAIL_PASS` carried the same trailing CR, so even
// with the flag fixed, AUTH would have failed. Proven against the real server:
//
//     AS-IS (with trailing CR): 535 5.7.8 Authentication failed
//     TRIMMED:                  AUTH OK
//
// The credentials were always correct; one invisible byte per secret was not.
// The contamination comes from setting fly secrets out of a CRLF file, which no
// amount of care at the setting end reliably prevents — so the durable fix is
// to be tolerant at the READ end. `lib/auth/email.mjs` already learned this the
// hard way and has its own `_clean`; this module had not, which is exactly why
// a per-module habit is not a fix and a shared discipline is.
const _env = (name, dflt = '') => {
  const v = process.env[name];
  return (v == null ? dflt : String(v).trim()) || dflt;
};

const ENABLED    = _env('ARIA_EMAIL_ENABLED') === 'true';
const IMAP_HOST  = _env('ARIA_EMAIL_HOST', 'outlook.office365.com');
const IMAP_PORT  = parseInt(_env('ARIA_EMAIL_PORT', '993'));
const IMAP_USER  = _env('ARIA_EMAIL_USER');
const IMAP_PASS  = _env('ARIA_EMAIL_PASS');
const INT_TOKEN  = _env('ARIA_INTERNAL_TOKEN');

/**
 * R-F13 2026-05-01: decode RFC 2047 encoded-words ("=?charset?B?...?=" or
 * "?Q?...?=") so non-ASCII Subjects render in logs instead of the raw
 * base64. Live evidence 08:44:59:
 *   [Email→ARIA] Email analysed: =?utf-8?B?44CQ5LiW55WM6YqA6KGM5p2x5Lqs5LqL5YuZ5omA → 28 facts
 * The base64 above decodes to a Japanese World Bank email subject. The
 * email itself was parsed correctly (28 facts learned); only the log
 * line was illegible.
 */
function decodeEncodedWord(s) {
  if (!s || typeof s !== 'string' || !s.includes('=?')) return s;
  return s.replace(/=\?([^?]+)\?([BQbq])\?([^?]*)\?=/g, (match, charset, enc, text) => {
    try {
      const cs = charset.toLowerCase().replace(/^utf-8$/, 'utf8');
      if (enc.toUpperCase() === 'B') {
        return Buffer.from(text, 'base64').toString(cs);
      }
      // Q encoding: `_` → space, `=XX` → hex byte, everything else literal.
      const bytes = [];
      let i = 0;
      while (i < text.length) {
        const c = text[i];
        if (c === '_') { bytes.push(0x20); i++; }
        else if (c === '=' && i + 2 < text.length) {
          bytes.push(parseInt(text.substr(i + 1, 2), 16));
          i += 3;
        } else {
          bytes.push(text.charCodeAt(i));
          i++;
        }
      }
      return Buffer.from(bytes).toString(cs);
    } catch {
      return match;
    }
  });
}

// SMTP config for sending — reuses ARIA email credentials or falls back to system EMAIL_*
// R-F4152 (C-175) — same `_env` discipline on the send path. A trailing CR in
// SMTP_USER/SMTP_PASS fails AUTH with "535 wrong user/password", which reads as
// a credential problem and sends you looking for the wrong thing entirely.
const SMTP_HOST  = _env('ARIA_SMTP_HOST') || _env('EMAIL_HOST') || IMAP_HOST;
const SMTP_PORT  = parseInt(_env('ARIA_SMTP_PORT') || _env('EMAIL_PORT') || '465');
const SMTP_USER  = _env('ARIA_SMTP_USER') || _env('EMAIL_USER') || IMAP_USER;
const SMTP_PASS  = _env('ARIA_SMTP_PASS') || _env('EMAIL_PASS') || IMAP_PASS;
const SMTP_FROM  = _env('ARIA_EMAIL_FROM') || `Arkmurus Intelligence <${IMAP_USER || 'aria@imaria.io'}>`;
const SMTP_SECURE = SMTP_PORT === 465;
const MAX_DOC_CHARS = parseInt(process.env.ARIA_MAX_DOC_CHARS || '200000', 10);

// First-run backfill: how many historical emails to read on first activation.
// Past gap (2026-04-13 → today): emails that arrived BEFORE the reader was
// activated were never read because the IMAP search was UNSEEN-only and the
// user had already opened them. Backfill closes that — set to 100 by default,
// override with ARIA_EMAIL_BACKFILL_COUNT.
const BACKFILL_COUNT = parseInt(process.env.ARIA_EMAIL_BACKFILL_COUNT || '100', 10);
const POLL_INTERVAL_MS = parseInt(process.env.ARIA_EMAIL_POLL_MS || String(5 * 60 * 1000), 10);

// Redis keys for cross-restart state. The `last_uid` is the canonical
// "we've processed everything up to and including this UID" cursor.
// Tracking UIDs (not READ flags) means anything that lands in INBOX gets
// read EXACTLY ONCE regardless of whether the user opens it elsewhere.
const RKEY_LAST_UID = 'crucix:email_reader:last_uid';
const RKEY_STATS    = 'crucix:email_reader:stats';
// R-F346 (2026-05-12): track INBOX UIDVALIDITY so a mailbox-side renumber
// (provider migration, server restore, folder rebuild) doesn't strand
// the cursor at an old value where new mail has lower UIDs.
const RKEY_UIDVALIDITY = 'crucix:email_reader:uidvalidity';
const RKEY_INBOX_TOTAL = 'crucix:email_reader:last_inbox_total';

let emailsProcessed      = 0;
let emailsSent           = 0;
let attachmentsProcessed = 0;
let backfillRuns         = 0;
let lastCheckTime        = null;
let lastUid              = 0;        // hydrated from Redis on first check
let lastUidLoaded        = false;
let checkInterval   = null;
let smtpTransporter = null;

// ── Hydrate persisted state from Redis ─────────────────────────────────────
async function hydrateState() {
  if (lastUidLoaded) return;
  try {
    const rawUid = await redisGet(RKEY_LAST_UID);
    if (rawUid !== null && rawUid !== undefined) {
      const n = typeof rawUid === 'number' ? rawUid : parseInt(rawUid, 10);
      if (Number.isFinite(n) && n > 0) lastUid = n;
    }
    const stats = await redisGet(RKEY_STATS);
    if (stats && typeof stats === 'object') {
      emailsProcessed      = stats.emails_processed      ?? 0;
      emailsSent           = stats.emails_sent           ?? 0;
      attachmentsProcessed = stats.attachments_processed ?? 0;
      backfillRuns         = stats.backfill_runs         ?? 0;
    }
  } catch {/* fire-and-forget */}
  lastUidLoaded = true;
  console.log(`[Email Reader] State hydrated — last_uid=${lastUid} processed=${emailsProcessed} sent=${emailsSent}`);
}

async function persistState() {
  try {
    await redisSet(RKEY_LAST_UID, lastUid);
    await redisSet(RKEY_STATS, {
      emails_processed:      emailsProcessed,
      emails_sent:           emailsSent,
      attachments_processed: attachmentsProcessed,
      backfill_runs:         backfillRuns,
      last_check:            lastCheckTime,
      last_uid:              lastUid,
    });
  } catch {/* fire-and-forget */}
}

// ── SMTP transporter (lazy init) ────────────────────────────────────────────
async function getSmtpTransporter() {
  if (smtpTransporter) return smtpTransporter;
  if (!SMTP_HOST || !SMTP_USER || !SMTP_PASS) return null;
  try {
    const nodemailer = (await import('nodemailer')).default;
    smtpTransporter = nodemailer.createTransport({
      host:   SMTP_HOST,
      port:   SMTP_PORT,
      secure: SMTP_SECURE,
      auth:   { user: SMTP_USER, pass: SMTP_PASS },
    });
    // Verify connection
    await smtpTransporter.verify();
    console.log(`[Email] SMTP ready — ${SMTP_USER} via ${SMTP_HOST}:${SMTP_PORT}`);
    return smtpTransporter;
  } catch (e) {
    console.warn('[Email] SMTP setup failed:', e.message);
    smtpTransporter = null;
    return null;
  }
}

// ── Send email (plain text or HTML) ─────────────────────────────────────────
export async function sendEmail({ to, subject, text, html, replyTo, cc, bcc, attachments }) {
  const transport = await getSmtpTransporter();
  if (!transport) {
    console.warn('[Email] Cannot send — SMTP not configured');
    return { sent: false, reason: 'SMTP not configured' };
  }

  if (!to || !subject) {
    return { sent: false, reason: 'to and subject required' };
  }

  try {
    const info = await transport.sendMail({
      from:        SMTP_FROM,
      to,
      cc:          cc || undefined,
      bcc:         bcc || undefined,
      subject,
      text:        text || undefined,
      html:        html || undefined,
      replyTo:     replyTo || undefined,
      attachments: attachments || undefined,  // nodemailer format: [{filename, content, contentType}]
    });
    emailsSent++;
    console.log(`[Email] Sent "${subject}" → ${to} (${info.messageId})`);
    return { sent: true, messageId: info.messageId };
  } catch (e) {
    console.error('[Email] Send failed:', e.message);
    return { sent: false, reason: e.message };
  }
}

// ── Ask ARIA to compose an email reply ──────────────────────────────────────
async function askARIAToCompose({ to, originalSubject, originalBody, instruction }) {
  const port = process.env.PORT || 3117;
  const prompt = `You are composing a professional email reply as ARIA on behalf of Arkmurus.

RECIPIENT: ${to}
ORIGINAL SUBJECT: ${originalSubject || '(new email)'}
${originalBody ? `ORIGINAL EMAIL:\n${originalBody.slice(0, 2000)}\n` : ''}
INSTRUCTION: ${instruction}

Write a professional, concise email. Use the Arkmurus brand tone: authoritative but approachable, intelligence-focused.
Return ONLY the email body text — no subject line, no "Dear X" salutation unless appropriate, no sign-off beyond "Best regards, ARIA — Arkmurus Intelligence".`;

  try {
    const r = await fetch(`http://localhost:${port}/api/aria/chat`, {
      method:  'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${INT_TOKEN}`,
      },
      body: JSON.stringify({ message: prompt, session_id: `email_compose_${Date.now()}` }),
      signal: AbortSignal.timeout(60000),
    });
    if (!r.ok) throw new Error(`ARIA ${r.status}`);
    const data = await r.json();
    return data.response || data.answer || null;
  } catch (e) {
    console.error('[Email] ARIA compose failed:', e.message);
    return null;
  }
}

// ── Feed to brain ────────────────────────────────────────────────────────────
async function feedToARIA(subject, from, body, signalType = 'email_intelligence') {
  const baseUrl = process.env.APP_URL || `http://localhost:${process.env.PORT || 3117}`;
  try {
    await fetch(`${baseUrl}/api/aria/brain/signal`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${INT_TOKEN}`,
      },
      body: JSON.stringify({
        content:     `[Email] ${subject}\nFrom: ${from}\n\n${body}`,
        source:      `email:${from}`,
        signal_type: signalType,
        metadata: {
          subject,
          from,
          channel:   'email',
          timestamp: new Date().toISOString(),
        },
      }),
      signal: AbortSignal.timeout(5000),
    });
  } catch(e) {
    console.warn('[Email Reader] Feed to ARIA failed:', e.message);
  }
}

// ── Classify email source ────────────────────────────────────────────────────
// R-F474 (2026-05-14): newsletter-sender heuristics. Other-agent audit
// found newsletters were classified deep in the pipeline (after URL
// extraction + read_document fan-out) → cycles wasted on emails that
// have low intel value. The classifyEmail() upstream short-circuit
// catches them by sender shape + subject before feedToARIA() fires.
//
// Order matters: tender/compliance/defence-intel subjects WIN over the
// newsletter check, so a newsletter that legitimately mentions a tender
// still gets classified as tender_alert. Only generic newsletter
// patterns fall to the new bucket.
function _r474IsNewsletterSender(f, s) {
  if (!f) return false;
  // Sender-side heuristics — common newsletter mailer patterns
  const sendsLikeNewsletter = (
    f.includes('newsletter') ||
    f.includes('news@') ||
    f.includes('digest@') ||
    f.includes('updates@') ||
    f.includes('notifications@') ||
    f.includes('no-reply') ||
    f.includes('noreply') ||
    f.includes('do-not-reply') ||
    f.includes('mailings@') ||
    f.includes('list-manage') ||
    f.includes('substack') ||
    f.includes('mailchimp')
  );
  if (!sendsLikeNewsletter) return false;
  // Subject-side guards — these newsletter subjects are clearly low-intel
  const subjectIsNewsletter = (
    s.includes('newsletter') ||
    s.includes('weekly digest') || s.includes('daily digest') ||
    s.includes('weekly roundup') ||
    s.includes('this week') || s.includes('this month') ||
    s.includes('your weekly') || s.includes('your daily') ||
    s.includes('unsubscribe')
  );
  // Sender-shape alone is enough — even if subject is empty, a noreply@
  // sender pattern is overwhelmingly low-intel for ARIA's purposes.
  return subjectIsNewsletter || sendsLikeNewsletter;
}


function classifyEmail(from, subject) {
  const f = (from || '').toLowerCase();
  const s = (subject || '').toLowerCase();

  // Priority topics first — a tender notification from a newsletter
  // sender still classifies as tender_alert (don't drop real intel).
  if (s.includes('job change') || s.includes('new position') || s.includes('started a new'))
    return { type: 'linkedin_job_change', priority: 'critical' };
  if (s.includes('tender') || s.includes('procurement') || s.includes('rfq') || s.includes('rfp'))
    return { type: 'tender_alert', priority: 'high' };
  if (s.includes('sanction') || s.includes('embargo') || s.includes('export control'))
    return { type: 'compliance_alert', priority: 'critical' };

  if (f.includes('linkedin') || f.includes('sales-navigator'))
    return { type: 'linkedin_alert', priority: 'high' };
  if (f.includes('google') && s.includes('alert'))
    return { type: 'google_alert', priority: 'medium' };
  if (s.includes('defence') || s.includes('defense') || s.includes('military'))
    return { type: 'defence_intel', priority: 'medium' };

  // R-F474: newsletter-sender skip — last gate before general_email so
  // we don't pre-empt any of the priority intel buckets above.
  if (_r474IsNewsletterSender(f, s))
    return { type: 'newsletter', priority: 'skip' };

  return { type: 'general_email', priority: 'low' };
}

// ── Extract text from email HTML/raw body ────────────────────────────────────
// 2026-04-21 (round 3): ARIA self-diagnostic flagged that the content landing
// in RAG was "entirely Base64-encoded binary data" — correct. Previous
// extractor only decoded quoted-printable. Many modern mail clients (Gmail,
// mobile, Outlook) encode body parts as base64, which leaked through as noise.
//
// New approach: parse MIME parts properly.
//   1. Split the body on the outer MIME boundary.
//   2. For each part, extract its Content-Type + Content-Transfer-Encoding.
//   3. Decode the part body per encoding (base64, quoted-printable, or none).
//   4. HTML-strip if text/html.
//   5. Prefer text/plain over text/html; if both empty, fall back to raw.
function _decodeQuotedPrintable(s) {
  // Collect bytes via Buffer so multi-byte UTF-8 sequences (=E2=80=94 → "—")
  // reassemble correctly. String.fromCharCode would emit each byte as a
  // Latin-1 code point and mangle non-ASCII text.
  const noSoftBreaks = s.replace(/=\r?\n/g, '');
  const bytes = [];
  for (let i = 0; i < noSoftBreaks.length; i++) {
    if (noSoftBreaks[i] === '=' && i + 2 < noSoftBreaks.length) {
      const hex = noSoftBreaks.slice(i + 1, i + 3);
      if (/^[0-9A-Fa-f]{2}$/.test(hex)) {
        bytes.push(parseInt(hex, 16));
        i += 2;
        continue;
      }
    }
    bytes.push(noSoftBreaks.charCodeAt(i) & 0xff);
  }
  try {
    return Buffer.from(bytes).toString('utf-8');
  } catch {
    return noSoftBreaks;
  }
}

/**
 * F47 (2026-04-27, hoisted 2026-04-29): extract candidate article URLs
 * from an email body, with defensive QP-decoding.
 *
 * email.body is raw and may contain quoted-printable encoding (=3F → '?',
 * =3D → '=', trailing '=' → soft line-break). The 21:18:37 World Bank
 * newsletter sent 4 URLs like
 *   https://t.newsletterext.worldbank.org/r/=3Fid=3D...
 * through to /api/aria/read which all 404'd. We decode QP first, then
 * defensively reject any URL that still has QP residue (regex didn't
 * normalise a soft-break-spanned URL fully). Capped at 5 URLs/email
 * since LinkedIn newsletters frequently embed dozens.
 *
 * Exported (named) so it can be unit-tested without standing up an
 * IMAP fixture. Internal-only API; do not import from outside this
 * package.
 */
// R-F358 (2026-05-12): Node-side mirror of aria_service/intel/security.py
// _AUTH_REQUIRED_URL_PATTERNS + _LOW_VALUE_URL_PATTERNS. Pre-fix, a single
// LinkedIn newsletter triggered 5× POST /api/aria/read against URLs the
// Python security layer was guaranteed to block (live evidence fly logs
// 2026-05-12 10:39:26–10:39:27Z: comm/pulse + 2× help/linkedin/answer +
// comm/feed all blocked, all 200 OK after the security warning).
// Filtering them here removes the round-trip, the security warning, and
// the read_attempts counter pollution. Keep in sync with security.py.
const _AUTH_REQUIRED_URL_RE = new RegExp(
  '^https?://(?:www\\.)?(?:'
  + 'linkedin\\.com/(?:comm/|admin|sales/|messaging/|feed/|in/me|notifications/)'
  + '|facebook\\.com/(?:login|checkpoint)'
  + '|twitter\\.com/(?:i/|messages)'
  + '|x\\.com/(?:i/|messages)'
  + ')',
  'i'
);
const _LOW_VALUE_URL_RE = new RegExp(
  '^https?://(?:'
  + '(?:www\\.)?linkedin\\.com/(?:help/|legal/|psettings/|learning/)'
  + '|(?:help|support)\\.linkedin\\.com/'
  + '|(?:www\\.)?twitter\\.com/(?:help/|about/)'
  + '|(?:www\\.)?x\\.com/(?:help/|about/)'
  + '|help\\.(?:twitter|x)\\.com/'
  + '|(?:www\\.)?facebook\\.com/(?:help/|policies/)'
  + ')',
  'i'
);

// R-F370 (2026-05-12): LinkedIn media-CDN image URLs are NEVER articles.
// Every newsletter embeds profile-displayphoto + article-cover_image links
// that the email reader tries to fetch as articles. Live evidence
// 2026-05-12 12:43:13–12:45:34 BST seenode logs: ~10 different
// media.licdn.com/dms/image/... URLs per LinkedIn email, each:
//   1. GET media.licdn.com/dms/image/... → 403 Forbidden (signed URL expired)
//   2. GET archive.org/wayback/available?url=... → 200 (archive lookup)
//   3. Result: 0 facts, 0 hypotheses — pure waste
// Filtering at the email-reader stage saves the network round-trip + the
// archive.org probe + the LLM extraction attempt. Only matches image-CDN
// paths; static.licdn.com (JS/CSS bundle hashes) is left alone because
// it occasionally returns extractable content per live logs.
const _MEDIA_CDN_URL_RE = new RegExp(
  '^https?://(?:'
  + 'media\\.licdn\\.com/dms/image/'
  + '|media-exp[0-9]*\\.licdn\\.com/'
  + '|(?:[a-z0-9-]+\\.)*licdn\\.com/(?:dms/image|emc)/'
  + ')',
  'i'
);

export function _extractArticleUrls({ textContent = '', rawBody = '' } = {}) {
  const decodedBody = _decodeQuotedPrintable(rawBody || '');
  const emailUrls = ((textContent || '') + ' ' + decodedBody).match(/https?:\/\/[^\s<>"'\]\)]+/gi) || [];
  // F92 fix 2026-04-29: dedupe before filtering. LinkedIn newsletters
  // routinely embed the same article URL in BOTH the plaintext part
  // (textContent) and the HTML part (rawBody). Without dedupe the
  // helper returned the URL twice, the caller then fired two
  // /api/aria/read POSTs for the same URL within ~50ms (live evidence
  // 2026-04-29 15:06:54.929 + .978 — same `floating-network-john-
  // hurwitz-w8ssc` URL blocked twice as auth-required). Each blocked
  // /read still spends an API call + a security warning.
  const unique = Array.from(new Set(emailUrls));
  return unique
    .filter(u => !u.match(/\.(jpg|jpeg|png|gif|mp4|css|js|dtd|xsd|xml|svg|ico|woff2?|ttf|otf)(\?|$)/i))
    .filter(u => !u.match(/unsubscribe|tracking|click\.|email\.|pixel|beacon/i))
    .filter(u => !u.match(/=3[FfDd]|=$|=\r|=\n/))
    .filter(u => !_AUTH_REQUIRED_URL_RE.test(u))
    .filter(u => !_LOW_VALUE_URL_RE.test(u))
    .filter(u => !_MEDIA_CDN_URL_RE.test(u))  // R-F370
    .filter(u => u.length > 30)
    .slice(0, 5);
}

function _decodeBase64(s) {
  try {
    // Strip all whitespace (base64 chunks are line-wrapped at 76 chars)
    const clean = s.replace(/\s+/g, '');
    if (!clean) return '';
    return Buffer.from(clean, 'base64').toString('utf-8');
  } catch {
    return '';
  }
}

function _stripHtml(s) {
  return s
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '')
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n\n')
    .replace(/<\/div>/gi, '\n')
    .replace(/<\/li>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

function _looksLikeBase64(s) {
  // Heuristic: body is almost entirely base64 alphabet, >= 40 chars after
  // whitespace-stripping, and decodes to something with readable letters.
  // IMAP sometimes delivers post-header body only, with the
  // Content-Transfer-Encoding header already stripped upstream. This
  // heuristic catches that case.
  const stripped = s.replace(/\s+/g, '');
  if (stripped.length < 40) return false;
  if (!/^[A-Za-z0-9+/=]+$/.test(stripped)) return false;
  const decoded = _decodeBase64(stripped);
  return decoded.length >= 20 && /[a-zA-Z]{8,}/.test(decoded);
}

function _parseMimeParts(body) {
  // Detect the boundary from the outer Content-Type header, if any.
  const boundaryMatch = body.match(/boundary=(?:"([^"]+)"|([^\s;]+))/i);
  if (!boundaryMatch) {
    // Single-part message — treat the whole body as one part. If the
    // body looks like a bare base64 blob (IMAP handed us post-decode
    // body without CTE headers), treat it as base64; otherwise infer
    // from the content shape (HTML tags vs plain text).
    const looksB64 = _looksLikeBase64(body);
    return [{
      contentType: looksB64
        ? 'text/plain'
        : (/<[a-z][^>]*>/i.test(body) ? 'text/html' : 'text/plain'),
      encoding: looksB64 ? 'base64' : '7bit',
      body: body,
    }];
  }
  const boundary = boundaryMatch[1] || boundaryMatch[2];
  const escapedBoundary = boundary.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Split on boundary markers (--boundary, with optional trailing -- for end)
  const chunks = body.split(new RegExp('--' + escapedBoundary + '(?:--)?', 'g'));
  const parts = [];
  for (const chunk of chunks) {
    const trimmed = chunk.trim();
    if (!trimmed || trimmed.length < 10) continue;
    // Split header block from body at first blank line
    const headerBodyMatch = trimmed.match(/^([\s\S]*?)\r?\n\r?\n([\s\S]*)$/);
    if (!headerBodyMatch) continue;
    const [, headers, partBody] = headerBodyMatch;
    const ctMatch = headers.match(/Content-Type:\s*([^;\r\n]+)/i);
    const cteMatch = headers.match(/Content-Transfer-Encoding:\s*(\S+)/i);
    const contentType = (ctMatch ? ctMatch[1] : 'text/plain').trim().toLowerCase();
    const encoding = (cteMatch ? cteMatch[1] : '7bit').trim().toLowerCase();
    // Nested multipart (e.g. multipart/related > multipart/alternative) —
    // recurse into it so we find the inner text/plain or text/html parts.
    // Without this, LinkedIn emails (outer multipart/related) had their
    // inner text content skipped entirely and my decoder returned the
    // base64 HTML-blob as fallback, leaving QP artefacts from LinkedIn's
    // URL encoding visible in RAG.
    if (contentType.startsWith('multipart/')) {
      // The nested chunk needs its own Content-Type header preserved so
      // the inner boundary parse works. Feed the whole sub-part back in.
      const nested = _parseMimeParts(headers + '\r\n\r\n' + partBody);
      for (const np of nested) parts.push(np);
      continue;
    }
    parts.push({ contentType, encoding, body: partBody });
  }
  return parts;
}

function extractText(body) {
  if (!body) return '';

  const parts = _parseMimeParts(body);
  const decoded = [];
  for (const p of parts) {
    let txt = p.body;
    if (p.encoding === 'base64') {
      txt = _decodeBase64(txt);
    } else if (p.encoding === 'quoted-printable') {
      txt = _decodeQuotedPrintable(txt);
    }
    if (p.contentType.startsWith('text/html')) {
      txt = _stripHtml(txt);
    } else if (p.contentType.startsWith('text/plain')) {
      // plain: nothing more to do
    } else {
      // Non-text part (image, application/*, etc.) — skip
      continue;
    }
    txt = txt.replace(/\n{3,}/g, '\n\n').trim();
    if (txt.length >= 10) {
      decoded.push({ ...p, text: txt });
    }
  }

  // Prefer text/plain over text/html for human readability
  const plain = decoded.find(p => p.contentType.startsWith('text/plain'));
  const html = decoded.find(p => p.contentType.startsWith('text/html'));
  let text = (plain?.text || html?.text || '').slice(0, 10000);

  // Final clean-up on any MIME leftovers that survived
  text = text
    .replace(/^--[\w=_.-]+(?:--)?[\r\n]*/gm, '')
    .replace(/^(?:Content-(?:Type|Transfer-Encoding|Disposition|ID):|MIME-Version:|charset=)[^\r\n]*[\r\n]*/gmi, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  // If we still have nothing, do a last-resort scan: pull base64 blocks
  // out of the raw body and try decoding each — the one that yields
  // readable text wins. Avoids silent drop when part-header parsing fails.
  if (text.length < 20 && body.length >= 20) {
    const base64Blocks = body.match(/[A-Za-z0-9+/]{40,}={0,2}/g) || [];
    for (const blk of base64Blocks) {
      const try_ = _decodeBase64(blk);
      if (try_ && /[a-zA-Z]{10,}/.test(try_) && try_.length >= 20) {
        text = _stripHtml(try_).trim().slice(0, 10000);
        if (text.length >= 20) break;
      }
    }
  }

  return text;
}

// ── Attachment extraction helper ─────────────────────────────────────────────
async function extractAttachment(buffer, filename, mimetype) {
  const ariaUrl = process.env.ARIA_SERVICE_URL;
  if (!ariaUrl) return null;

  let text = '';
  const lname = (filename || '').toLowerCase();
  const lmime = (mimetype || '').toLowerCase();

  try {
    const require = createRequire(import.meta.url);

    // PDF
    if (lmime.includes('pdf') || lname.endsWith('.pdf')) {
      try {
        const pdfParse = require('pdf-parse');
        const pdf = await pdfParse(buffer);
        text = (pdf.text || '').trim().slice(0, MAX_DOC_CHARS);
        console.log(`[Email] Extracted PDF: ${filename} (${text.length} chars, ${pdf.numpages} pages)`);
      } catch { console.warn(`[Email] pdf-parse not available for ${filename}`); }
    }
    // DOCX
    else if (lmime.includes('wordprocessingml') || lname.endsWith('.docx')) {
      try {
        const mammoth = require('mammoth');
        const result = await mammoth.extractRawText({ buffer });
        text = (result.value || '').trim().slice(0, MAX_DOC_CHARS);
        console.log(`[Email] Extracted DOCX: ${filename} (${text.length} chars)`);
      } catch { console.warn(`[Email] mammoth not available for ${filename}`); }
    }
    // Plain text / CSV
    else if (lmime.startsWith('text/') || lname.match(/\.(txt|csv|md|json|xml|log)$/)) {
      text = buffer.toString('utf-8').trim().slice(0, MAX_DOC_CHARS);
      console.log(`[Email] Extracted text: ${filename} (${text.length} chars)`);
    }
    // Excel (xlsx)
    else if (lmime.includes('spreadsheetml') || lname.endsWith('.xlsx') || lname.endsWith('.xls')) {
      try {
        // R-F2592 — reject oversized/untrusted xlsx BEFORE parsing (no upstream
        // xlsx fix for its ReDoS/pollution advisories; XLSX.read parses the whole
        // buffer before any range cap).
        if (!xlsxSizeOk(buffer)) {
          throw new Error(`xlsx refused: ${buffer?.length ?? 0} bytes > ${MAX_XLSX_BYTES} cap (DoS guard)`);
        }
        const XLSX = require('xlsx');
        const wb = XLSX.read(buffer, { type: 'buffer' });
        const rows = [];
        for (const sheetName of wb.SheetNames.slice(0, 3)) {
          const csv = XLSX.utils.sheet_to_csv(wb.Sheets[sheetName]);
          rows.push(`[Sheet: ${sheetName}]\n${csv}`);
        }
        text = rows.join('\n\n').slice(0, MAX_DOC_CHARS);
        console.log(`[Email] Extracted Excel: ${filename} (${text.length} chars, ${wb.SheetNames.length} sheets)`);
      } catch (e) { console.warn(`[Email] xlsx skipped for ${filename}: ${e.message}`); }
    }
  } catch(e) {
    console.warn(`[Email] Attachment extraction failed for ${filename}:`, e.message);
  }

  // Send to ARIA for analysis. Use _ariaFetch (centralised auth) so
  // the bearer header is added — past gap 2026-04-19 00:11: raw fetch
  // here meant 401 from the protected /api/aria/* router and the
  // catch swallowed it silently. Result: 302 emails read by seenode
  // but 0 RAG chunks indexed.
  if (text && text.length > 50 && ariaUrl) {
    try {
      const r = await _ariaFetch(`${ariaUrl}/api/aria/read-document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: text,
          filename,
          source: 'email_attachment',
          context: `Email attachment: ${filename} (${mimetype})`,
        }),
        signal: AbortSignal.timeout(180000),
      });
      if (r.ok) {
        const result = await r.json();
        if (result?.facts_learned > 0) {
          console.log(`[Email→ARIA] Attachment analysed: ${filename} → ${result.facts_learned} facts`);
        }
      } else {
        console.warn(`[Email→ARIA] read-document returned HTTP ${r.status} for ${filename}`);
      }
    } catch (e) {
      console.warn(`[Email→ARIA] read-document failed for ${filename}: ${e.message}`);
    }
  }

  return text;
}

// ── MIME structure parser — find attachments recursively ─────────────────────
function _findAttachments(struct, attachments, prefix = '') {
  if (!struct) return;
  if (Array.isArray(struct)) {
    for (let i = 0; i < struct.length; i++) {
      const part = struct[i];
      if (Array.isArray(part)) {
        _findAttachments(part, attachments, `${prefix}${i + 1}.`);
      } else if (part && typeof part === 'object' && part.disposition) {
        const disp = (part.disposition?.type || '').toLowerCase();
        if (disp === 'attachment' || disp === 'inline') {
          const params = part.disposition?.params || {};
          const filename = params.filename || params.name || part.params?.name || 'unknown';
          const mime = `${part.type || 'application'}/${part.subtype || 'octet-stream'}`.toLowerCase();
          const encoding = (part.encoding || '').toLowerCase();
          const size = part.size || 0;
          attachments.push({
            filename,
            mime,
            encoding,
            size,
            partID: part.partID || `${prefix}${struct.indexOf(part) + 1}`,
          });
        }
      }
    }
  }
}

// ── Check inbox ──────────────────────────────────────────────────────────────
// Strategy: UID-tracked, NOT UNSEEN-flag-based.
//   • First run (no last_uid in Redis) → backfill the BACKFILL_COUNT most
//     recent UIDs so historical emails get read once.
//   • Subsequent runs → fetch every UID > last_uid, regardless of read state.
//   • markSeen is FALSE — we don't touch the user's read flags. Their phone
//     and webmail keep showing unread for emails they haven't opened.
//   • last_uid is persisted to Redis after every successful run so a restart
//     (or seenode redeploy) doesn't lose the cursor.
//
// `forceBackfill` flag = manual rerun via /api/email-reader/backfill
// regardless of cursor state. Useful when the operator wants ARIA to
// re-ingest historical content (e.g. after a brain reset).
async function checkInbox({ forceBackfill = false, backfillCount = BACKFILL_COUNT } = {}) {
  if (!IMAP_USER || !IMAP_PASS) {
    console.warn('[Email Reader] No credentials configured');
    return { ok: false, reason: 'no_credentials' };
  }

  await hydrateState();

  let Imap;
  try {
    const require = createRequire(import.meta.url);
    Imap = require('imap');
  } catch(e) {
    console.warn('[Email Reader] imap package not installed — run: npm install imap');
    return { ok: false, reason: 'imap_not_installed' };
  }

  return new Promise((resolve) => {
    const imap = new Imap({
      user:     IMAP_USER,
      password: IMAP_PASS,
      host:     IMAP_HOST,
      port:     IMAP_PORT,
      tls:      true,
      tlsOptions: { rejectUnauthorized: false },
      connTimeout: 15000,
      authTimeout: 15000,
    });

    imap.once('ready', () => {
      console.log(`[Email Reader] IMAP connected to ${IMAP_HOST} as ${IMAP_USER}`);
      lastCheckTime = new Date().toISOString();
      imap.openBox('INBOX', /* readOnly */ true, async (err, box) => {
        if (err) {
          console.warn('[Email Reader] Open INBOX failed:', err.message);
          imap.end();
          resolve({ ok: false, reason: 'openbox_failed' });
          return;
        }
        const total = box.messages?.total || 0;
        const unseen = box.messages?.unseen || 0;
        const currentUidvalidity = box.uidvalidity || 0;

        // R-F346: detect UIDVALIDITY change (mailbox renumber on the
        // provider side). When this happens, every UID is reassigned —
        // the persisted `last_uid` no longer corresponds to anything
        // meaningful, and new mail can have UIDs lower than the old
        // cursor, hiding it from the `UID > last_uid` incremental
        // search forever. Also: detect "inbox grew but search returned
        // nothing" — the secondary symptom of a silent renumber even
        // when UIDVALIDITY is missing from the server response.
        try {
          const persistedUidvalidity = await redisGet(RKEY_UIDVALIDITY);
          const lastInboxTotal = await redisGet(RKEY_INBOX_TOTAL);

          if (lastUid > 0 && currentUidvalidity > 0 &&
              persistedUidvalidity && Number(persistedUidvalidity) !== currentUidvalidity) {
            console.warn(
              `[Email Reader] R-F346: UIDVALIDITY changed ` +
              `(${persistedUidvalidity} → ${currentUidvalidity}); ` +
              `resetting last_uid (was ${lastUid}) — next run backfills ${BACKFILL_COUNT}`
            );
            lastUid = 0;
            await redisSet(RKEY_LAST_UID, 0);
          }
          if (currentUidvalidity > 0 && Number(persistedUidvalidity || 0) !== currentUidvalidity) {
            await redisSet(RKEY_UIDVALIDITY, currentUidvalidity);
          }
          // Stash the inbox total so the secondary "grew without
          // returning new UIDs" check can fire after the search runs.
          await redisSet(RKEY_INBOX_TOTAL, total);
          // Stash prior total in closure for the search-empty branch
          imap._priorInboxTotal = Number(lastInboxTotal || 0);
        } catch (rErr) {
          console.warn(`[Email Reader] R-F346 state check failed (non-fatal): ${rErr.message}`);
        }

        console.log(
          `[Email Reader] INBOX opened — ${total} total, ${unseen} unseen, ` +
          `last_uid=${lastUid}, uidvalidity=${currentUidvalidity}`
        );

        // Decide search strategy:
        //   - If forceBackfill OR no cursor → backfill mode (last N UIDs)
        //   - Else → UID > last_uid (everything new since last check)
        //
        // R-F549 (2026-05-15) — startup-backfill suppression.
        // Pre-R-F549: every seenode deploy wipes /data per
        // [[seenode_disk_ephemeral]] → state hydrates with last_uid=0 →
        // backfill runs with 100 emails → each spawns concurrent classify +
        // brainAbsorb + /read-document calls (60s timeouts) → memory
        // spike at ~3-4min after boot → seenode container killed → 502
        // crash-loop. Live evidence 2026-05-15 14:19-14:23 BST: log
        // showed `State hydrated — last_uid=0` immediately followed by
        // 100-email burst, crash by 14:24.
        //
        // The backfill is SAFE TO SKIP on cold start: emails are
        // persistent in IMAP and will be picked up by the next
        // incremental tick (5-min interval) as `new emails`. Losing
        // ~5 min of email history vs. losing the entire WA listener
        // is the right trade. Operators who genuinely want post-
        // deploy backfill can POST /api/email-reader/backfill with
        // forceBackfill=true.
        //
        // We still honour explicit forceBackfill (admin-triggered).
        const isStartupBackfill = (lastUid === 0 && !forceBackfill);
        const isBackfill = forceBackfill;  // R-F549: cold-start no longer triggers backfill

        if (isStartupBackfill) {
          // Take the current max UID as the cursor — no fetch, no
          // /read-document burst, no memory spike. Future ticks see
          // only emails that arrive AFTER this point.
          await new Promise((resolveSeed) => {
            imap.search(['ALL'], (err, all) => {
              if (err) {
                console.warn(
                  `[Email Reader] R-F549 cursor-seed search failed: ${err.message}. ` +
                  `Falling back to lastUid=0; will re-attempt on next tick.`
                );
                resolveSeed();
                return;
              }
              const sorted = (all || []).sort((a, b) => a - b);
              const seedUid = sorted.length ? sorted[sorted.length - 1] : 0;
              lastUid = seedUid;
              // Persist immediately so next tick reads the seed.
              persistState().catch((perr) => {
                console.warn(`[Email Reader] R-F549 seed persist failed: ${perr.message}`);
              });
              console.log(
                `[Email Reader] R-F549 cold-start seed — last_uid set to ${seedUid} ` +
                `(was 0). Backfill SKIPPED to prevent memory-spike crash; ` +
                `incremental ticks will pick up new arrivals.`
              );
              resolveSeed();
            });
          });
        }

        const searchPromise = new Promise((searchResolve) => {
          if (isBackfill) {
            // UID search for "all" then take the last N. IMAP doesn't have a
            // native "last N" search so we ask for everything and slice.
            imap.search(['ALL'], (err, all) => {
              if (err) {
                searchResolve({ err, results: [] });
                return;
              }
              const sorted = (all || []).sort((a, b) => a - b);
              const sliced = sorted.slice(-backfillCount);
              backfillRuns++;
              console.log(`[Email Reader] BACKFILL — taking ${sliced.length} of ${sorted.length} total`);
              searchResolve({ err: null, results: sliced });
            });
          } else {
            // Incremental: UID > last_uid. IMAP quirk — `UID N:*` always
            // returns the highest existing UID even when no message has
            // UID >= N (range-end clamp). Without filtering, we'd
            // re-process the highest UID on every empty tick. Filter
            // results to strictly > lastUid in JS to neutralise this.
            imap.search([['UID', `${lastUid + 1}:*`]], (err, results) => {
              if (err) return searchResolve({ err, results: [] });
              const filtered = (results || []).filter(uid => uid > lastUid);
              searchResolve({ err: null, results: filtered });
            });
          }
        });

        searchPromise.then(({ err, results }) => {
          if (err || !results || !results.length) {
            if (err) console.warn(`[Email Reader] Search error: ${err.message}`);
            else {
              // R-F346: if INBOX grew since the last poll but the
              // incremental search returned 0, something is wrong with
              // the cursor (likely a silent UIDVALIDITY reset on a
              // provider that doesn't bump the metadata). Surface a
              // WARN with the gap so operators see it without grepping.
              const priorTotal = imap._priorInboxTotal || 0;
              if (!isBackfill && priorTotal > 0 && total > priorTotal && lastUid > 0) {
                console.warn(
                  `[Email Reader] R-F346: inbox grew ${priorTotal} → ${total} ` +
                  `(+${total - priorTotal}) but UID > ${lastUid} search returned 0. ` +
                  `Likely silent UIDVALIDITY reset. ` +
                  `POST /api/email-reader/reset-cursor to rebuild.`
                );
              } else {
                console.log(`[Email Reader] No new emails since UID ${lastUid} (inbox has ${total} total)`);
              }
            }
            imap.end();
            persistState();
            resolve({ ok: true, processed: 0, backfill: isBackfill });
            return;
          }

          console.log(`[Email Reader] ${results.length} email(s) to process${isBackfill ? ' (backfill)' : ` (UID > ${lastUid})`}`);

          // Cursor advancement: compute the max UID from the search
          // results NOW (these are reliably UIDs because imap.search()
          // returns UIDs). Per-message attrs.uid is unreliable in
          // node-imap without explicit `uid: true` in fetch — we saw
          // last_uid stuck at 0 after the first backfill processed 53
          // emails, which would cause the next 5-min tick to re-trigger
          // backfill and re-process everything.
          const maxUidFromSearch = Math.max(...results, lastUid);

          const f = imap.fetch(results, {
            bodies: ['HEADER.FIELDS (FROM SUBJECT DATE)', 'TEXT', ''],
            struct: true,
            markSeen: false,  // never touch user's read flags — UID tracking handles dedup
          });

          const emails = [];
          let maxUidThisRun = maxUidFromSearch;  // pre-seed from search

          f.on('message', (msg) => {
            let header = '', body = '';
            const attachments = [];
            let emailStruct = null;
            let msgUid = 0;

            msg.on('body', (stream, info) => {
              let buf = '';
              const chunks = [];
              stream.on('data', (chunk) => {
                buf += chunk.toString('utf8');
                chunks.push(chunk);
              });
              stream.once('end', () => {
                if (info.which === 'TEXT') body = buf;
                else if (info.which.includes('HEADER')) header = buf;
              });
            });

            msg.on('attributes', (attrs) => {
              emailStruct = attrs.struct;
              msgUid = attrs.uid || 0;
              if (msgUid > maxUidThisRun) maxUidThisRun = msgUid;
              if (attrs.struct) {
                _findAttachments(attrs.struct, attachments);
              }
            });

            msg.once('end', () => {
              emails.push({ header, body, attachments, struct: emailStruct, uid: msgUid });
            });
          });

          f.once('end', async () => {
            // R-F548 (2026-05-15) — event-loop yield every 5 emails.
            // Pre-R-F548 the for-of loop processed N emails back-to-back
            // (each with classify + brainAbsorb + URL crawl + feedToARIA),
            // potentially monopolising the event loop for 30s+ when
            // post-restart backfill kicks in. seenode platform's liveness
            // probe ran into the busy event loop and (best guess from
            // 502-loop pattern with no obvious crash signal) killed the
            // container as unresponsive. Yielding via setImmediate every
            // 5 emails gives /healthz + WA listener inbound socket time
            // to respond, while keeping throughput similar.
            let _r548_processed = 0;
            for (const email of emails) {
              if (_r548_processed > 0 && _r548_processed % 5 === 0) {
                await new Promise((r) => setImmediate(r));
              }
              _r548_processed++;
              try {
                // Parse header
                const fromMatch  = email.header.match(/From:\s*(.+)/i);
                const subjMatch  = email.header.match(/Subject:\s*(.+)/i);
                // R-F546 (2026-05-15, renumbered from R-F538 due to
                // R-F534 collision) — MIME-decode From before any
                // downstream use. Pre-R-F546 the raw RFC 2047 form
                // (`=?UTF-8?Q?MANUEL_SCH=C3=96LLIG_via_LinkedIn?=`)
                // flowed into the brain/absorb entity_name field,
                // where R-F411 validate_entity_name correctly rejected
                // the `?` and `=` chars → silent data loss. Live
                // evidence (fly logs 2026-05-15 12:20-12:21): 4+
                // rejections per LinkedIn newsletter batch. Subject
                // was already decoded; From was the only sender-bearing
                // header still leaking encoded form.
                const from    = decodeEncodedWord((fromMatch ? fromMatch[1] : 'unknown').trim());
                const subject = decodeEncodedWord((subjMatch ? subjMatch[1] : 'no subject').trim());

                const textContent = extractText(email.body);
                const { type, priority } = classifyEmail(from, subject);

                // Visibility floor: even body-empty emails (calendar
                // invites, attachment-only, image newsletters) must
                // produce a brain signal so we can see arrivals in
                // /api/aria/brain/stats. Without this, 380+ emails
                // processed showed only 1 absorbed because the prior
                // <20-char skip silently dropped them all before
                // brainAbsorb. We still skip the heavy `feedToARIA`
                // brain-signal pipeline + URL crawl when there's no
                // body to learn from.
                //
                // 2026-04-21: added diagnostic log line so we can
                // distinguish "email genuinely empty" (calendar invite,
                // image-only newsletter) from "extractText failed to
                // find content in a real email" — past incident where
                // test emails silently landed here instead of RAG.
                if (!textContent || textContent.length < 20) {
                  const rawLen = (email.body || '').length;
                  const extractedLen = textContent?.length || 0;
                  console.warn(
                    `[Email Reader] EMPTY-BODY path — raw=${rawLen} extracted=${extractedLen} ` +
                    `type=${type} attachments=${email.attachments?.length || 0} ` +
                    `subject="${subject.slice(0, 80)}" from="${from.slice(0, 60)}"`,
                  );
                  brainAbsorb({
                    module: 'email_reader',
                    summary: `[EMPTY-BODY/${type}] ${subject.slice(0, 100)} from ${from.slice(0, 60)}`,
                    detail: `(no extractable text; raw=${rawLen} extracted=${extractedLen}; ${email.attachments?.length || 0} attachment(s))`,
                    entity_name: from.replace(/^.*<([^>]+)>.*$/, '$1').slice(0, 80),
                    success: true,
                    extra_topics: ['general'],
                    source_id: `uid:${email.uid}`,
                    confidence: 'ASSESSED',
                  });
                  emailsProcessed++;
                  continue;
                }

                // R-F474 (2026-05-14): newsletter short-circuit. Skip the
                // full feedToARIA chain (URL extraction + read_document
                // fan-out + brainAbsorb LLM extraction) for emails the
                // classifier flagged as newsletter senders. We still emit
                // a single brain signal so /api/aria/brain/stats reflects
                // the arrival, but the heavy work is skipped — typical
                // newsletter triggers 5-20 LLM-extraction calls + URL
                // crawls that produce ~0-2 useful facts.
                if (priority === 'skip') {
                  console.log(`[Email Reader] SKIP (R-F474) | ${type} | ${subject.slice(0, 80)}`);
                  brainAbsorb({
                    module: 'email_reader',
                    summary: `[R-F474 newsletter-skip] ${subject.slice(0, 100)} from ${from.slice(0, 60)}`,
                    detail: '(newsletter sender shape — feedToARIA fan-out skipped to save LLM/URL fetch cycles)',
                    entity_name: from.replace(/^.*<([^>]+)>.*$/, '$1').slice(0, 80),
                    success: true,
                    extra_topics: ['general'],
                    source_id: `uid:${email.uid}`,
                    confidence: 'ASSESSED',
                  });
                  emailsProcessed++;
                  continue;
                }

                console.log(`[Email Reader] ${priority.toUpperCase()} | ${type} | ${subject.slice(0, 80)}`);

                await feedToARIA(subject, from, textContent, type);

                // Signal brain with the dedicated email_reader module so
                // every inbound email lands as a discrete signal — used to
                // be split across knowledge_ingestor / source_verifier
                // which made it impossible to track email volume in the
                // brain stats endpoint.
                brainAbsorb({
                  module: 'email_reader',
                  summary: `[${priority.toUpperCase()}/${type}] ${subject.slice(0, 100)} from ${from.slice(0, 60)}`,
                  detail: textContent.slice(0, 1500),
                  entity_name: from.replace(/^.*<([^>]+)>.*$/, '$1').slice(0, 80),
                  success: true,
                  extra_topics: type.startsWith('linkedin') ? ['relationships', 'competitor_intel']
                              : type === 'tender_alert' ? ['procurement', 'market_intel']
                              : type === 'compliance_alert' ? ['compliance', 'legal']
                              : type === 'defence_intel' ? ['osint', 'geopolitics']
                              : ['general'],
                  source_id: `uid:${email.uid}`,
                  confidence: priority === 'critical' ? 'CONFIRMED' : 'ASSESSED',
                });

                // Send to ARIA research engine for deep learning.
                //
                // Past gap fix (2026-04-19 00:28): backfill mode was firing
                // 50 emails × 10 URLs each = 500 concurrent /api/aria/read
                // calls in parallel (the for-loop fired without await), plus
                // 50 concurrent /api/aria/read-document calls. This thundered
                // the Python brain → every call timed out, RAG ingest failed,
                // sweep ingest also timed out from CPU starvation.
                //
                // Strategy now:
                //   • Backfill mode: SKIP URL crawling entirely. Body
                //     ingest only, sequentially with await. Historical
                //     emails are mainly there for content corpus, not link
                //     follow-up; URL crawl is for fresh-arrival intel.
                //   • Normal mode (1-2 new emails per poll): serialize the
                //     URL fetches with await, capped at 5 URLs (was 10) so
                //     a single high-link email can't saturate the brain.
                const ariaUrl = process.env.ARIA_SERVICE_URL;
                if (ariaUrl) {
                  // ── 1. Article URLs — only when NOT backfilling ──
                  if (!isBackfill) {
                    // F47 (2026-04-27): see _extractArticleUrls below
                    // for the decode + filter rationale. Hoisted out so
                    // it's testable without standing up an IMAP loop.
                    const articleUrls = _extractArticleUrls({
                      textContent,
                      rawBody: email.body,
                    });
                    for (const articleUrl of articleUrls) {
                      try {
                        const r = await _ariaFetch(`${ariaUrl}/api/aria/read`, {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ url: articleUrl, context: `From email: ${subject} (${from})` }),
                          signal: AbortSignal.timeout(120000),
                        });
                        if (!r.ok) {
                          console.warn(`[Email→ARIA] /read HTTP ${r.status} for ${articleUrl.slice(0, 60)}`);
                          continue;
                        }
                        const result = await r.json();
                        if (result?.facts_learned > 0) {
                          console.log(`[Email→ARIA] Read article: ${articleUrl.slice(0, 60)} → ${result.facts_learned} facts`);
                        }
                      } catch (e) {
                        console.warn(`[Email→ARIA] /read error: ${e.message}`);
                      }
                    }
                  }

                  // ── 2. Email body — always, but awaited (not fire-and-forget) ──
                  // Serialised per-email. The for-loop already iterates
                  // emails one-at-a-time so awaiting here means only ONE
                  // /read-document call is in flight at any moment, not 50.
                  //
                  // 2026-04-21: threshold was `length > 200 && priority !== 'low'`
                  // which silently dropped every short-body or low-priority email
                  // from RAG. Server floor is 20 chars (post-6485b61), so a
                  // brief confirmation email ("Confirmed, see you Tuesday")
                  // must land in RAG too — the user's doctrine is "ARIA
                  // remembers everything". Only genuine junk (spam priority,
                  // or truly empty bodies the empty-body branch already
                  // caught upstream) is skipped here.
                  if (textContent.length >= 20 && priority !== 'spam') {
                    try {
                      const r = await _ariaFetch(`${ariaUrl}/api/aria/read-document`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                          content: textContent,
                          filename: `email_${type}_${subject.slice(0, 40).replace(/[^a-zA-Z0-9]/g, '_')}`,
                          source: `email:${from}`,
                          context: `Email: ${subject} | Type: ${type} | Priority: ${priority}`,
                        }),
                        // Shorter timeout in backfill so a slow LLM response
                        // doesn't block the whole 50-email batch for hours.
                        signal: AbortSignal.timeout(isBackfill ? 60000 : 180000),
                      });
                      if (r.ok) {
                        const result = await r.json();
                        if (result?.facts_learned > 0) {
                          console.log(`[Email→ARIA] Email analysed: ${subject.slice(0, 50)} → ${result.facts_learned} facts`);
                        }
                      } else {
                        console.warn(`[Email→ARIA] /read-document HTTP ${r.status} for ${subject.slice(0, 50)}`);
                      }
                    } catch (e) {
                      console.warn(`[Email→ARIA] /read-document error for ${subject.slice(0, 50)}: ${e.message}`);
                    }
                  }
                }

                // LinkedIn-specific intelligence processing
                if (type.startsWith('linkedin') || from.toLowerCase().includes('linkedin')) {
                  const liResults = await processLinkedInEmail(subject, from, textContent);
                  if (liResults.appointments.length) console.log(`[LinkedIn Intel] 🟢 ${liResults.appointments.length} appointment(s) detected`);
                  if (liResults.competitors.length)  console.log(`[LinkedIn Intel] ⚠️ ${liResults.competitors.length} competitor move(s)`);
                  if (liResults.growth.length)        console.log(`[LinkedIn Intel] 📈 ${liResults.growth.length} growth signal(s)`);
                  if (liResults.content.length)       console.log(`[LinkedIn Intel] 🎯 Capabilities: ${liResults.content.map(c => c.capability).join(', ')}`);
                }

                // Process attachments (PDF, DOCX, Excel, Text)
                // R-F231 (2026-05-11) — cap raised from 5 → 50. Compliance
                // disclosures (FCPA settlements, SAR bundles, registry exports)
                // routinely carry 15-30 attachments per email; the previous
                // 5-cap silently dropped the rest with no operator visibility.
                // Emit a console.warn so operators see when an email hit the
                // ceiling AND log the dropped count for the brain.
                const ATTACHMENT_CAP = 50;
                if (email.attachments && email.attachments.length > 0) {
                  const dropped = Math.max(0, email.attachments.length - ATTACHMENT_CAP);
                  console.log(`[Email Reader] ${email.attachments.length} attachment(s) in "${subject.slice(0, 50)}"${dropped ? ` (PROCESSING FIRST ${ATTACHMENT_CAP}, ${dropped} DROPPED — R-F231)` : ''}`);
                  if (dropped > 0) {
                    // Best-effort brain signal — don't block email ingest
                    try {
                      const _fetch = (typeof fetch === 'function') ? fetch : null;
                      if (_fetch && process.env.ARIA_INGEST_URL) {
                        _fetch(process.env.ARIA_INGEST_URL.replace(/\/ingest$/, '/brain/absorb'), {
                          method: 'POST',
                          headers: {
                            'Content-Type': 'application/json',
                            'Authorization': 'Bearer ' + (process.env.ARIA_INTERNAL_TOKEN || ''),
                          },
                          body: JSON.stringify({
                            module: 'email_reader',
                            summary: `R-F231: dropped ${dropped} of ${email.attachments.length} attachments (cap ${ATTACHMENT_CAP}) from "${subject.slice(0, 80)}"`,
                            gap_type: 'email_attachment_cap_hit',
                            gap_detail: `${dropped} dropped`,
                            success: false,
                          }),
                        }).catch(() => {});
                      }
                    } catch (_e) { /* never block */ }
                  }
                  for (const att of email.attachments.slice(0, ATTACHMENT_CAP)) {
                    try {
                      // Fetch the attachment part
                      const partFetch = imap.fetch([results[emails.indexOf(email)]], {
                        bodies: [att.partID],
                        struct: false,
                      });
                      const attBuffer = await new Promise((res, rej) => {
                        const chunks = [];
                        let settled = false;
                        const timer = setTimeout(() => { if (!settled) { settled = true; rej(new Error('timeout')); } }, 30000);
                        partFetch.on('message', (attMsg) => {
                          attMsg.on('body', (stream) => {
                            stream.on('data', (chunk) => chunks.push(chunk));
                            stream.once('end', () => {
                              let buf = Buffer.concat(chunks);
                              if (att.encoding === 'base64') {
                                buf = Buffer.from(buf.toString('utf8').replace(/\s/g, ''), 'base64');
                              }
                              if (!settled) { settled = true; clearTimeout(timer); res(buf); }
                            });
                          });
                        });
                        partFetch.once('error', (e) => { if (!settled) { settled = true; clearTimeout(timer); rej(e); } });
                      });
                      if (attBuffer && attBuffer.length > 0) {
                        await extractAttachment(attBuffer, att.filename, att.mime);
                        attachmentsProcessed++;
                      }
                    } catch(attErr) {
                      console.warn(`[Email Reader] Attachment "${att.filename}" failed:`, attErr.message);
                    }
                  }
                }

                emailsProcessed++;

              } catch(e) {
                console.warn('[Email Reader] Process email failed:', e.message);
              }
            }

            lastCheckTime = new Date().toISOString();

            // Advance the canonical cursor — the highest UID we processed
            // this run. Persist to Redis so a restart resumes here.
            if (maxUidThisRun > lastUid) {
              lastUid = maxUidThisRun;
              console.log(`[Email Reader] cursor advanced → last_uid=${lastUid}`);
            }
            await persistState();

            imap.end();
            resolve({
              ok: true,
              processed: emails.length,
              backfill: isBackfill,
              new_last_uid: lastUid,
            });
          });

          f.once('error', (err) => {
            console.warn('[Email Reader] Fetch error:', err.message);
            imap.end();
            persistState();
            resolve({ ok: false, reason: 'fetch_error', error: err.message });
          });
        });  // end searchPromise.then
      });
    });

    imap.once('error', (err) => {
      console.warn('[Email Reader] IMAP error:', err.message);
      resolve({ ok: false, reason: 'imap_error', error: err.message });
    });

    imap.once('end', () => {});

    imap.connect();
  });
}

// ── Mount onto Express + start schedule ──────────────────────────────────────
export function mountEmailReader(app) {
  if (!ENABLED) {
    console.log('[Email Reader] Disabled — set ARIA_EMAIL_ENABLED=true to activate');
    return;
  }

  if (!IMAP_USER || !IMAP_PASS) {
    console.warn('[Email Reader] Missing ARIA_EMAIL_USER or ARIA_EMAIL_PASS');
    return;
  }

  const minutes = Math.round(POLL_INTERVAL_MS / 60000);
  console.log(`[Email Reader] Starting — checking ${IMAP_USER} every ${minutes} min (backfill=${BACKFILL_COUNT})`);

  // Initial check after 30s (let server start first). On first activation,
  // last_uid will be 0 in Redis and checkInbox() automatically backfills
  // BACKFILL_COUNT historical emails. Subsequent runs are incremental.
  setTimeout(() => {
    checkInbox().catch(e => console.warn('[Email Reader] Check failed:', e.message));
  }, 30000);

  // Then every POLL_INTERVAL_MS
  checkInterval = setInterval(() => {
    checkInbox().catch(e => console.warn('[Email Reader] Check failed:', e.message));
  }, POLL_INTERVAL_MS);

  // Auth guard for email routes
  const requireEmailAuth = (req, res, next) => {
    if (req.headers.authorization !== `Bearer ${INT_TOKEN}`) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    next();
  };

  // Status endpoint — exposes the UID cursor + lifetime counters so the
  // dashboard can show "ARIA has read N emails total, last batch M new".
  app.get('/api/email-reader/status', requireEmailAuth, async (_req, res) => {
    await hydrateState();
    res.json({
      enabled:               true,
      inbox:                 IMAP_USER,
      imap_host:             IMAP_HOST,
      imap_port:             IMAP_PORT,
      smtp_configured:       !!(SMTP_HOST && SMTP_USER && SMTP_PASS),
      smtp_from:             SMTP_FROM,
      emails_processed:      emailsProcessed,
      emails_sent:           emailsSent,
      attachments_processed: attachmentsProcessed,
      backfill_runs:         backfillRuns,
      last_check:            lastCheckTime,
      last_uid:              lastUid,
      poll_interval_ms:      POLL_INTERVAL_MS,
      backfill_count:        BACKFILL_COUNT,
      strategy:              'uid-tracked (markSeen=false; reads everything once)',
    });
  });

  // Manual incremental check trigger
  app.post('/api/email-reader/check', requireEmailAuth, async (_req, res) => {
    const result = await checkInbox().catch(e => ({ ok: false, error: e.message }));
    res.json({
      ok:                result?.ok ?? false,
      processed:         result?.processed ?? 0,
      new_last_uid:      result?.new_last_uid ?? lastUid,
      emails_processed:  emailsProcessed,
      last_check:        lastCheckTime,
    });
  });

  // Backfill trigger — re-reads the last N emails regardless of cursor.
  // Use after a brain reset, after env-var changes, or when the operator
  // wants ARIA to re-ingest historical content. Body: {count: 200} optional.
  // Past gap fix: emails delivered before ARIA_EMAIL_ENABLED=true was set
  // were invisible to brain because UNSEEN-search missed already-opened ones.
  // This endpoint + first-run auto-backfill closes that gap permanently.
  app.post('/api/email-reader/backfill', requireEmailAuth, async (req, res) => {
    const count = Math.min(parseInt(req.body?.count || BACKFILL_COUNT, 10) || BACKFILL_COUNT, 1000);
    console.log(`[Email Reader] Manual backfill triggered (count=${count})`);
    // Acknowledge immediately. Sequential body-only ingest can take
    // 1+ min per email × N emails, easily exceeding HTTP timeouts.
    // Past behaviour: operator hit POST → endpoint awaited the full
    // checkInbox() → curl timed out at 60s while the work continued
    // silently. Operator never got a confirmation. Fix: return now,
    // process in background, log completion to stdout.
    res.json({
      ok: true,
      queued: true,
      count,
      message: `Backfill of ${count} email(s) queued — check brain email_reader.total in 1-3 minutes (sequential body-only ingest)`,
      emails_processed_at_queue_time: emailsProcessed,
    });
    checkInbox({ forceBackfill: true, backfillCount: count })
      .then(r => console.log(
        `[Email Reader] backfill DONE — processed=${r?.processed ?? 0} ` +
        `new_last_uid=${r?.new_last_uid ?? lastUid} ok=${r?.ok}`
      ))
      .catch(e => console.warn(`[Email Reader] backfill ASYNC ERROR: ${e.message}`));
  });

  // Reset cursor — operator-only. Sets last_uid back to 0 so the next
  // check re-runs full backfill. Useful for testing.
  app.post('/api/email-reader/reset-cursor', requireEmailAuth, async (_req, res) => {
    lastUid = 0;
    await persistState();
    console.log(`[Email Reader] Cursor reset to 0 — next check will backfill ${BACKFILL_COUNT}`);
    res.json({ ok: true, last_uid: 0, message: `Next check will backfill last ${BACKFILL_COUNT} emails` });
  });

  // ── Send email (direct) ─────────────────────────────────────────────────
  app.post('/api/email/send', requireEmailAuth, async (req, res) => {
    const { to, subject, text, html, cc, bcc, replyTo } = req.body || {};
    if (!to || !subject) {
      return res.status(400).json({ error: 'to and subject required' });
    }
    const result = await sendEmail({ to, subject, text, html, cc, bcc, replyTo });
    if (result.sent) {
      res.json({ ok: true, messageId: result.messageId });
    } else {
      res.status(500).json({ error: result.reason });
    }
  });

  // ── ARIA-composed email — provide instruction, ARIA writes the email ────
  app.post('/api/email/compose-and-send', requireEmailAuth, async (req, res) => {
    const { to, subject, instruction, original_subject, original_body, cc, bcc } = req.body || {};
    if (!to || !instruction) {
      return res.status(400).json({ error: 'to and instruction required' });
    }

    // Ask ARIA to compose the email body
    const composedBody = await askARIAToCompose({
      to,
      originalSubject: original_subject || subject,
      originalBody:    original_body,
      instruction,
    });

    if (!composedBody) {
      return res.status(502).json({ error: 'ARIA failed to compose email' });
    }

    // Determine subject line
    const emailSubject = subject || (original_subject ? `Re: ${original_subject}` : 'Arkmurus Intelligence Update');

    const result = await sendEmail({
      to,
      subject: emailSubject,
      text:    composedBody,
      cc,
      bcc,
    });

    if (result.sent) {
      res.json({
        ok:        true,
        messageId: result.messageId,
        subject:   emailSubject,
        body:      composedBody,
        to,
      });
    } else {
      res.status(500).json({ error: result.reason });
    }
  });

  // ── ARIA draft — compose without sending (for review) ───────────────────
  app.post('/api/email/draft', requireEmailAuth, async (req, res) => {
    const { to, subject, instruction, original_subject, original_body } = req.body || {};
    if (!instruction) {
      return res.status(400).json({ error: 'instruction required' });
    }

    const composedBody = await askARIAToCompose({
      to:              to || 'unknown recipient',
      originalSubject: original_subject || subject,
      originalBody:    original_body,
      instruction,
    });

    if (!composedBody) {
      return res.status(502).json({ error: 'ARIA failed to compose email' });
    }

    res.json({
      ok:      true,
      draft:   true,
      subject: subject || (original_subject ? `Re: ${original_subject}` : 'Arkmurus Intelligence Update'),
      body:    composedBody,
      to:      to || null,
    });
  });

  // Init SMTP on startup (non-blocking)
  getSmtpTransporter().catch(() => {});

  console.log('[Email Reader] Routes mounted — /api/email-reader/*, /api/email/*');
}
