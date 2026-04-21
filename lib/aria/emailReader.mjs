/**
 * ARIA — Email Intelligence Reader + Composer
 * ═══════════════════════════════════════════════════════════════════════════
 * Reads ARIA's email inbox (aria@arkmurus.com) and feeds content to brain.
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
 *   ARIA_EMAIL_HOST       ox.livemail.co.uk
 *   ARIA_EMAIL_PORT       993
 *   ARIA_EMAIL_USER       aria@arkmurus.com
 *   ARIA_EMAIL_PASS       (email password)
 *   ARIA_EMAIL_ENABLED    true
 *
 *   # For sending (uses same credentials or falls back to EMAIL_* vars):
 *   ARIA_SMTP_HOST        (defaults to ARIA_EMAIL_HOST = ox.livemail.co.uk)
 *   ARIA_SMTP_PORT        465
 * ═══════════════════════════════════════════════════════════════════════════
 */

import { createRequire } from 'module';
import { processLinkedInEmail } from './linkedinIntel.mjs';
import { brainAbsorb } from '../self/learning_store.mjs';
import { redisGet, redisSet } from '../persist/store.mjs';
import { _ariaFetch } from './_ariaFetch.mjs';

const ENABLED    = process.env.ARIA_EMAIL_ENABLED === 'true';
const IMAP_HOST  = process.env.ARIA_EMAIL_HOST  || 'ox.livemail.co.uk';
const IMAP_PORT  = parseInt(process.env.ARIA_EMAIL_PORT || '993');
const IMAP_USER  = process.env.ARIA_EMAIL_USER  || '';
const IMAP_PASS  = process.env.ARIA_EMAIL_PASS  || '';
const INT_TOKEN  = process.env.ARIA_INTERNAL_TOKEN || 'aria-internal';

// SMTP config for sending — reuses ARIA email credentials or falls back to system EMAIL_*
const SMTP_HOST  = process.env.ARIA_SMTP_HOST || process.env.EMAIL_HOST || IMAP_HOST;
const SMTP_PORT  = parseInt(process.env.ARIA_SMTP_PORT || process.env.EMAIL_PORT || '465');
const SMTP_USER  = process.env.ARIA_SMTP_USER || process.env.EMAIL_USER || IMAP_USER;
const SMTP_PASS  = process.env.ARIA_SMTP_PASS || process.env.EMAIL_PASS || IMAP_PASS;
const SMTP_FROM  = process.env.ARIA_EMAIL_FROM || `ARIA Intelligence <${IMAP_USER || 'aria@arkmurus.com'}>`;
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
    await fetch(`${baseUrl}/api/brain/signal`, {
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
function classifyEmail(from, subject) {
  const f = (from || '').toLowerCase();
  const s = (subject || '').toLowerCase();

  if (f.includes('linkedin') || f.includes('sales-navigator'))
    return { type: 'linkedin_alert', priority: 'high' };
  if (s.includes('job change') || s.includes('new position') || s.includes('started a new'))
    return { type: 'linkedin_job_change', priority: 'critical' };
  if (s.includes('tender') || s.includes('procurement') || s.includes('rfq') || s.includes('rfp'))
    return { type: 'tender_alert', priority: 'high' };
  if (f.includes('google') && s.includes('alert'))
    return { type: 'google_alert', priority: 'medium' };
  if (s.includes('sanction') || s.includes('embargo') || s.includes('export control'))
    return { type: 'compliance_alert', priority: 'critical' };
  if (s.includes('defence') || s.includes('defense') || s.includes('military'))
    return { type: 'defence_intel', priority: 'medium' };

  return { type: 'general_email', priority: 'low' };
}

// ── Extract text from email HTML ─────────────────────────────────────────────
function extractText(html) {
  if (!html) return '';
  return html
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
    .replace(/&#39;/g, "'")
    .replace(/\n{3,}/g, '\n\n')
    .trim()
    .slice(0, 10000);
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
        const XLSX = require('xlsx');
        const wb = XLSX.read(buffer, { type: 'buffer' });
        const rows = [];
        for (const sheetName of wb.SheetNames.slice(0, 3)) {
          const csv = XLSX.utils.sheet_to_csv(wb.Sheets[sheetName]);
          rows.push(`[Sheet: ${sheetName}]\n${csv}`);
        }
        text = rows.join('\n\n').slice(0, MAX_DOC_CHARS);
        console.log(`[Email] Extracted Excel: ${filename} (${text.length} chars, ${wb.SheetNames.length} sheets)`);
      } catch { console.warn(`[Email] xlsx not available for ${filename}`); }
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
      imap.openBox('INBOX', /* readOnly */ true, (err, box) => {
        if (err) {
          console.warn('[Email Reader] Open INBOX failed:', err.message);
          imap.end();
          resolve({ ok: false, reason: 'openbox_failed' });
          return;
        }
        const total = box.messages?.total || 0;
        const unseen = box.messages?.unseen || 0;
        console.log(`[Email Reader] INBOX opened — ${total} total, ${unseen} unseen, last_uid=${lastUid}`);

        // Decide search strategy:
        //   - If forceBackfill OR no cursor → backfill mode (last N UIDs)
        //   - Else → UID > last_uid (everything new since last check)
        const isBackfill = forceBackfill || lastUid === 0;
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
            else console.log(`[Email Reader] No new emails since UID ${lastUid} (inbox has ${total} total)`);
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
            for (const email of emails) {
              try {
                // Parse header
                const fromMatch  = email.header.match(/From:\s*(.+)/i);
                const subjMatch  = email.header.match(/Subject:\s*(.+)/i);
                const from    = (fromMatch ? fromMatch[1] : 'unknown').trim();
                const subject = (subjMatch ? subjMatch[1] : 'no subject').trim();

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
                if (!textContent || textContent.length < 20) {
                  brainAbsorb({
                    module: 'email_reader',
                    summary: `[EMPTY-BODY/${type}] ${subject.slice(0, 100)} from ${from.slice(0, 60)}`,
                    detail: `(no extractable text; ${email.attachments?.length || 0} attachment(s))`,
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
                    const emailUrls = (textContent + ' ' + email.body).match(/https?:\/\/[^\s<>"'\]\)]+/gi) || [];
                    const articleUrls = emailUrls
                      .filter(u => !u.match(/\.(jpg|jpeg|png|gif|mp4|css|js)$/i))
                      .filter(u => !u.match(/unsubscribe|tracking|click\.|email\.|pixel|beacon/i))
                      .filter(u => u.length > 30)
                      .slice(0, 5);  // was 10 — cap lower for safety
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
                if (email.attachments && email.attachments.length > 0) {
                  console.log(`[Email Reader] ${email.attachments.length} attachment(s) in "${subject.slice(0, 50)}"`);
                  for (const att of email.attachments.slice(0, 5)) {
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
