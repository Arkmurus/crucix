/**
 * ARIA — Email Intelligence Reader
 * ═══════════════════════════════════════════════════════════════════════════
 * Reads ARIA's email inbox (aria@arkmurus.com) and feeds content to brain.
 * Primary use: LinkedIn Sales Navigator alerts → ARIA learns about
 * job changes, company news, competitor activity, procurement signals.
 *
 * Also captures: Google Alerts, tender notifications, any forwarded intel.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * SEENODE ENV VARS
 * ─────────────────────────────────────────────────────────────────────────
 *   ARIA_EMAIL_HOST       mail.livemail.co.uk
 *   ARIA_EMAIL_PORT       993
 *   ARIA_EMAIL_USER       aria@arkmurus.com
 *   ARIA_EMAIL_PASS       (email password)
 *   ARIA_EMAIL_ENABLED    true
 * ═══════════════════════════════════════════════════════════════════════════
 */

import { createRequire } from 'module';

const ENABLED    = process.env.ARIA_EMAIL_ENABLED === 'true';
const IMAP_HOST  = process.env.ARIA_EMAIL_HOST  || 'mail.livemail.co.uk';
const IMAP_PORT  = parseInt(process.env.ARIA_EMAIL_PORT || '993');
const IMAP_USER  = process.env.ARIA_EMAIL_USER  || '';
const IMAP_PASS  = process.env.ARIA_EMAIL_PASS  || '';
const INT_TOKEN  = process.env.ARIA_INTERNAL_TOKEN || 'aria-internal';

let emailsProcessed = 0;
let lastCheckTime   = null;
let checkInterval   = null;

// ── Feed to brain ────────────────────────────────────────────────────────────
async function feedToARIA(subject, from, body, signalType = 'email_intelligence') {
  const port = process.env.PORT || 3117;
  try {
    await fetch(`http://localhost:${port}/api/brain/signal`, {
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
      connTimeout: 10000,
      authTimeout: 10000,
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
            bodies: ['HEADER.FIELDS (FROM SUBJECT DATE)', 'TEXT'],
            markSeen: true,
          });

          const emails = [];

          f.on('message', (msg) => {
            let header = '', body = '';

            msg.on('body', (stream, info) => {
              let buf = '';
              stream.on('data', (chunk) => { buf += chunk.toString('utf8'); });
              stream.once('end', () => {
                if (info.which === 'TEXT') body = buf;
                else header = buf;
              });
            });

            msg.once('end', () => {
              emails.push({ header, body });
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

  // Status endpoint
  app.get('/api/email-reader/status', (_req, res) => {
    res.json({
      enabled:          true,
      inbox:            IMAP_USER,
      imap_host:        IMAP_HOST,
      emails_processed: emailsProcessed,
      last_check:       lastCheckTime,
      check_interval:   '5 minutes',
    });
  });

  // Manual check trigger
  app.post('/api/email-reader/check', async (_req, res) => {
    await checkInbox().catch(e => console.warn('[Email Reader] Manual check failed:', e.message));
    res.json({ ok: true, emails_processed: emailsProcessed, last_check: lastCheckTime });
  });

  console.log('[Email Reader] Routes mounted — /api/email-reader/*');
}
