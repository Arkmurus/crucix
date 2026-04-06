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
 *   ARIA_EMAIL_HOST       mail.livemail.co.uk
 *   ARIA_EMAIL_PORT       993
 *   ARIA_EMAIL_USER       aria@arkmurus.com
 *   ARIA_EMAIL_PASS       (email password)
 *   ARIA_EMAIL_ENABLED    true
 *
 *   # For sending (uses same credentials or falls back to EMAIL_* vars):
 *   ARIA_SMTP_HOST        (defaults to ARIA_EMAIL_HOST)
 *   ARIA_SMTP_PORT        587
 * ═══════════════════════════════════════════════════════════════════════════
 */

import { createRequire } from 'module';
import { processLinkedInEmail } from './linkedinIntel.mjs';

const ENABLED    = process.env.ARIA_EMAIL_ENABLED === 'true';
const IMAP_HOST  = process.env.ARIA_EMAIL_HOST  || 'mail.livemail.co.uk';
const IMAP_PORT  = parseInt(process.env.ARIA_EMAIL_PORT || '993');
const IMAP_USER  = process.env.ARIA_EMAIL_USER  || '';
const IMAP_PASS  = process.env.ARIA_EMAIL_PASS  || '';
const INT_TOKEN  = process.env.ARIA_INTERNAL_TOKEN || 'aria-internal';

// SMTP config for sending — reuses ARIA email credentials or falls back to system EMAIL_*
const SMTP_HOST  = process.env.ARIA_SMTP_HOST || process.env.EMAIL_HOST || IMAP_HOST;
const SMTP_PORT  = parseInt(process.env.ARIA_SMTP_PORT || process.env.EMAIL_PORT || '587');
const SMTP_USER  = process.env.ARIA_SMTP_USER || process.env.EMAIL_USER || IMAP_USER;
const SMTP_PASS  = process.env.ARIA_SMTP_PASS || process.env.EMAIL_PASS || IMAP_PASS;
const SMTP_FROM  = process.env.ARIA_EMAIL_FROM || `ARIA Intelligence <${IMAP_USER || 'aria@arkmurus.com'}>`;
const SMTP_SECURE = SMTP_PORT === 465;

let emailsProcessed      = 0;
let emailsSent           = 0;
let attachmentsProcessed = 0;
let lastCheckTime        = null;
let checkInterval   = null;
let smtpTransporter = null;

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
        text = (pdf.text || '').trim().slice(0, 15000);
        console.log(`[Email] Extracted PDF: ${filename} (${text.length} chars, ${pdf.numpages} pages)`);
      } catch { console.warn(`[Email] pdf-parse not available for ${filename}`); }
    }
    // DOCX
    else if (lmime.includes('wordprocessingml') || lname.endsWith('.docx')) {
      try {
        const mammoth = require('mammoth');
        const result = await mammoth.extractRawText({ buffer });
        text = (result.value || '').trim().slice(0, 15000);
        console.log(`[Email] Extracted DOCX: ${filename} (${text.length} chars)`);
      } catch { console.warn(`[Email] mammoth not available for ${filename}`); }
    }
    // Plain text / CSV
    else if (lmime.startsWith('text/') || lname.match(/\.(txt|csv|md|json|xml|log)$/)) {
      text = buffer.toString('utf-8').trim().slice(0, 15000);
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
        text = rows.join('\n\n').slice(0, 15000);
        console.log(`[Email] Extracted Excel: ${filename} (${text.length} chars, ${wb.SheetNames.length} sheets)`);
      } catch { console.warn(`[Email] xlsx not available for ${filename}`); }
    }
  } catch(e) {
    console.warn(`[Email] Attachment extraction failed for ${filename}:`, e.message);
  }

  // Send to ARIA for analysis
  if (text && text.length > 50 && ariaUrl) {
    try {
      const r = await fetch(`${ariaUrl}/api/aria/read-document`, {
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
      }
    } catch {}
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
async function checkInbox() {
  if (!IMAP_USER || !IMAP_PASS) {
    console.warn('[Email Reader] No credentials configured');
    return;
  }

  let Imap;
  try {
    const require = createRequire(import.meta.url);
    Imap = require('imap');
  } catch(e) {
    console.warn('[Email Reader] imap package not installed — run: npm install imap');
    return;
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
      imap.openBox('INBOX', false, (err, box) => {
        if (err) {
          console.warn('[Email Reader] Open INBOX failed:', err.message);
          imap.end();
          resolve();
          return;
        }

        // Search for unseen emails
        imap.search(['UNSEEN'], (err, results) => {
          if (err || !results || !results.length) {
            if (!err) console.log(`[Email Reader] No new emails`);
            imap.end();
            resolve();
            return;
          }

          console.log(`[Email Reader] ${results.length} new email(s) found`);

          const f = imap.fetch(results, {
            bodies: ['HEADER.FIELDS (FROM SUBJECT DATE)', 'TEXT', ''],
            struct: true,
            markSeen: true,
          });

          const emails = [];

          f.on('message', (msg) => {
            let header = '', body = '';
            const attachments = [];
            let emailStruct = null;

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
              // Extract attachment info from MIME structure
              if (attrs.struct) {
                _findAttachments(attrs.struct, attachments);
              }
            });

            msg.once('end', () => {
              emails.push({ header, body, attachments, struct: emailStruct });
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
                if (!textContent || textContent.length < 20) continue;

                const { type, priority } = classifyEmail(from, subject);

                console.log(`[Email Reader] ${priority.toUpperCase()} | ${type} | ${subject.slice(0, 80)}`);

                await feedToARIA(subject, from, textContent, type);

                // Send to ARIA research engine for deep learning
                const ariaUrl = process.env.ARIA_SERVICE_URL;
                if (ariaUrl) {
                  // 1. Send article URLs to ARIA for reading
                  const emailUrls = (textContent + ' ' + email.body).match(/https?:\/\/[^\s<>"'\]\)]+/gi) || [];
                  const articleUrls = emailUrls
                    .filter(u => !u.match(/\.(jpg|jpeg|png|gif|mp4|css|js)$/i))
                    .filter(u => !u.match(/unsubscribe|tracking|click\.|email\.|pixel|beacon/i))
                    .filter(u => u.length > 30)
                    .slice(0, 10);
                  for (const articleUrl of articleUrls) {
                    fetch(`${ariaUrl}/api/aria/read`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ url: articleUrl, context: `From email: ${subject} (${from})` }),
                      signal: AbortSignal.timeout(120000),
                    }).then(r => r.ok ? r.json() : null).then(result => {
                      if (result?.facts_learned > 0) {
                        console.log(`[Email→ARIA] Read article: ${articleUrl.slice(0, 60)} → ${result.facts_learned} facts`);
                      }
                    }).catch(() => {});
                  }

                  // 2. Send substantial email body as a document for deep analysis
                  if (textContent.length > 200 && priority !== 'low') {
                    fetch(`${ariaUrl}/api/aria/read-document`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        content: textContent,
                        filename: `email_${type}_${subject.slice(0, 40).replace(/[^a-zA-Z0-9]/g, '_')}`,
                        source: `email:${from}`,
                        context: `Email: ${subject} | Type: ${type} | Priority: ${priority}`,
                      }),
                      signal: AbortSignal.timeout(180000),
                    }).then(r => r.ok ? r.json() : null).then(result => {
                      if (result?.facts_learned > 0) {
                        console.log(`[Email→ARIA] Email analysed: ${subject.slice(0, 50)} → ${result.facts_learned} facts`);
                      }
                    }).catch(() => {});
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
            imap.end();
            resolve();
          });

          f.once('error', (err) => {
            console.warn('[Email Reader] Fetch error:', err.message);
            imap.end();
            resolve();
          });
        });
      });
    });

    imap.once('error', (err) => {
      console.warn('[Email Reader] IMAP error:', err.message);
      resolve();
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

  // Check inbox every 5 minutes
  console.log(`[Email Reader] Starting — checking ${IMAP_USER} every 5 min`);

  // Initial check after 30s (let server start first)
  setTimeout(() => {
    checkInbox().catch(e => console.warn('[Email Reader] Check failed:', e.message));
  }, 30000);

  // Then every 5 minutes
  checkInterval = setInterval(() => {
    checkInbox().catch(e => console.warn('[Email Reader] Check failed:', e.message));
  }, 5 * 60 * 1000);

  // Auth guard for email routes
  const requireEmailAuth = (req, res, next) => {
    if (req.headers.authorization !== `Bearer ${INT_TOKEN}`) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    next();
  };

  // Status endpoint
  app.get('/api/email-reader/status', requireEmailAuth, (_req, res) => {
    res.json({
      enabled:               true,
      inbox:                 IMAP_USER,
      imap_host:             IMAP_HOST,
      smtp_configured:       !!(SMTP_HOST && SMTP_USER && SMTP_PASS),
      smtp_from:             SMTP_FROM,
      emails_processed:      emailsProcessed,
      emails_sent:           emailsSent,
      attachments_processed: attachmentsProcessed,
      last_check:            lastCheckTime,
      check_interval:        '5 minutes',
    });
  });

  // Manual check trigger
  app.post('/api/email-reader/check', requireEmailAuth, async (_req, res) => {
    await checkInbox().catch(e => console.warn('[Email Reader] Manual check failed:', e.message));
    res.json({ ok: true, emails_processed: emailsProcessed, last_check: lastCheckTime });
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
