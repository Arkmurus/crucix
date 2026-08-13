// lib/auth/email.mjs
// Email notifications — outbound SMTP for verification / pending-approval /
// welcome / password-reset / suspension / reactivation messages.
//
// R-F426: env-var fallback chain. The dedicated auth credentials (EMAIL_HOST,
// EMAIL_USER, EMAIL_PASS) take precedence. If they are unset and the ARIA
// inbound-mail bridge is configured (ARIA_EMAIL_HOST / ARIA_EMAIL_USER /
// ARIA_EMAIL_PASS — already live on seenode for the LinkedIn alert pipeline),
// the same credentials power both directions. This removes the need to
// duplicate the SMTP secret on seenode just so forgot-password emails can
// send. SMTP port defaults to 465 (SSL) in the fallback path because
// ARIA_EMAIL_PORT carries the IMAP port (993), not an SMTP port; we let
// ARIA_SMTP_PORT override if the operator has set one for the ARIA composer.
// When SMTP isn't configured via either path, every send becomes a stdout
// log line ("[EMAIL] (SMTP not configured — log relay mode)") so codes can
// still be recovered from seenode logs.

const _ARIA_FALLBACK = !process.env.EMAIL_HOST && !!(process.env.ARIA_SMTP_HOST || process.env.ARIA_EMAIL_HOST);

// R-F2039 (2026-06-27): TRIM every credential value. The ACTUAL root cause of
// the SMTP auth failure was CRLF contamination — the live aria-web secrets were
// set from a Windows/CRLF source, so every value carried a trailing "\r"
// (e.g. "outlook.office365.com\r", "aria@imaria.io\r"). The "\r" broke DNS
// (getaddrinfo ENOTFOUND) and SMTP AUTH (535 wrong user/password). VERIFIED LIVE:
// the SAME creds authenticate the moment they're trimmed. Trimming here makes
// the system permanently robust to whitespace/newline-contaminated secrets — the
// durable mechanism (no per-incident re-setting). The sender is the ARIA mailbox
// (aria@arkmurus.com via ARIA_EMAIL_*), per operator direction.
//
// Supersedes R-F2032: that change added a SMTP_* read on ARIA's diagnosis that
// "the module never reads SMTP_*" — but the creds were never unread, only
// contaminated, and SMTP_* points to a DIFFERENT mailbox (acorrea@), so reading
// it would have made the wrong address the sender. Reverted in favour of trim.
const _clean = v => { const s = (v == null ? '' : String(v)).trim(); return s || undefined; };
const EMAIL_HOST   = _clean(process.env.EMAIL_HOST) || _clean(process.env.ARIA_SMTP_HOST) || _clean(process.env.ARIA_EMAIL_HOST);
const EMAIL_USER   = _clean(process.env.EMAIL_USER) || (_ARIA_FALLBACK ? _clean(process.env.ARIA_EMAIL_USER) : undefined);
const EMAIL_PASS   = _clean(process.env.EMAIL_PASS) || (_ARIA_FALLBACK ? _clean(process.env.ARIA_EMAIL_PASS) : undefined);
const EMAIL_PORT   = parseInt(
  process.env.EMAIL_PORT ||
  (_ARIA_FALLBACK ? (process.env.ARIA_SMTP_PORT || '465') : '587')
);
// R-F2384 — sender is ARIA (aria@imaria.io). Use the authenticated mailbox as
// the address (SPF/DMARC alignment) but always the ARIA display name. If the
// operator has EMAIL_FROM set on fly, that still wins — update that secret too.
const EMAIL_FROM   = process.env.EMAIL_FROM ||
  `ARIA Intelligence <${EMAIL_USER || 'aria@imaria.io'}>`;
const EMAIL_SECURE = process.env.EMAIL_SECURE === 'true' || EMAIL_PORT === 465;
const ADMIN_EMAIL  = process.env.ADMIN_EMAIL || 'aria@imaria.io';
const APP_URL      = process.env.APP_URL || 'https://intel.imaria.io';

// ── R-F3289 — "present" is not "usable" ─────────────────────────────────────
//
// This was `!!(EMAIL_HOST && EMAIL_USER && EMAIL_PASS)`: a presence check. It
// reported TRUE for the live aria-web configuration, which cannot authenticate
// and never could. Diagnosed by secret DIGEST, no value read:
//
//     EMAIL_USER       49bb8a67b557e235
//     EMAIL_PASS       49bb8a67b557e235   <- the same value
//     ARIA_EMAIL_USER  cb26e8b79add1b2e
//     ARIA_EMAIL_PASS  b8e84ce769c9b5e7   <- correctly distinct
//
// A username and a password are never legitimately the same string, so
// EMAIL_USER/EMAIL_PASS are a mis-set pair — and they take precedence over the
// ARIA_EMAIL_* pair, which is the correct one. So every send authenticated with
// user === pass, got 535, and the boot log said "SMTP configured" throughout.
//
// That is the same shape as every false clean in this codebase: a check that
// reports the presence of a value instead of whether the thing can do its job.
// A config that provably cannot authenticate is now reported as NOT configured,
// with the reason, so codes fall back to the stdout path a human can act on
// rather than disappearing into an auth failure nobody sees.
function _credsUsable(user, pass, host) {
  if (!host || !user || !pass) return false;
  if (String(user).trim() === String(pass).trim()) return false;
  return true;
}

// Describes a configuration that is PRESENT and WRONG. An ABSENT config is
// not an error — it is the documented log-relay mode, and reporting it here
// would collapse the exact distinction this change exists to draw ("nothing is
// set" versus "something is set and cannot work"). Empty string when there is
// nothing present to be wrong about.
export const configError = (() => {
  if (!EMAIL_HOST || !EMAIL_USER || !EMAIL_PASS) return '';
  if (String(EMAIL_USER).trim() === String(EMAIL_PASS).trim()) {
    return ('the SMTP user and password are identical, which cannot '
          + 'authenticate: one of the two secrets was set to the wrong value');
  }
  return '';
})();

export const isConfigured = !!(EMAIL_HOST && EMAIL_USER && EMAIL_PASS) && !configError;

// The second half of the live misconfiguration, worth saying out loud rather
// than leaving to be inferred: EMAIL_HOST is unset, so the host resolves to
// ARIA_SMTP_HOST, while EMAIL_USER/EMAIL_PASS (which ARE set) win over
// ARIA_EMAIL_USER/ARIA_EMAIL_PASS. Host from one mailbox, credentials from
// another. A MIXED set is not always wrong, so this warns rather than refuses.
const _MIXED_SOURCES = !!(
  !process.env.EMAIL_HOST && (process.env.ARIA_SMTP_HOST || process.env.ARIA_EMAIL_HOST)
  && (process.env.EMAIL_USER || process.env.EMAIL_PASS)
);

// Boot-time visibility so the operator can confirm from seenode logs which
// credential set is wired up. No secrets are printed — only host + user + port.
// Diagnostics go to stderr (console.warn) so they don't collide with program
// stdout in test harnesses that spawn this module under capture.
if (isConfigured) {
  console.warn(`[EMAIL] SMTP configured — host=${EMAIL_HOST} port=${EMAIL_PORT} user=${EMAIL_USER} secure=${EMAIL_SECURE} ${_ARIA_FALLBACK ? '(via ARIA fallback)' : '(dedicated EMAIL_* vars)'}`);
} else if (configError) {
  // R-F3289 — distinguish "nothing is set" from "something is set and is
  // wrong". The old single message sent an operator hunting for a missing
  // secret on a box where three SMTP secrets were present and one was wrong.
  console.warn(
    `[EMAIL] R-F3289: SMTP is UNUSABLE, not merely unset — ${configError}. `
    + `host=${EMAIL_HOST || '(unset)'} port=${EMAIL_PORT} user=${EMAIL_USER || '(unset)'}. `
    + 'Mail falls back to stdout until this is corrected.');
} else {
  console.warn('[EMAIL] SMTP NOT configured — forgot-password / verification mails will only appear in stdout. Set EMAIL_HOST/USER/PASS or ARIA_EMAIL_HOST/USER/PASS.');
}

if (_MIXED_SOURCES) {
  console.warn(
    '[EMAIL] R-F3289: MIXED credential sources — the host comes from '
    + 'ARIA_SMTP_HOST/ARIA_EMAIL_HOST while the user/password come from '
    + 'EMAIL_USER/EMAIL_PASS. If those belong to different mailboxes, auth '
    + 'will fail. Set all three from one set.');
}

// Lazy-load nodemailer — graceful degradation if not installed
let transporter = null;
async function getTransporter() {
  if (transporter) return transporter;
  if (!isConfigured) return null;
  try {
    const nodemailer = (await import('nodemailer')).default;
    transporter = nodemailer.createTransport({
      host: EMAIL_HOST,
      port: EMAIL_PORT,
      secure: EMAIL_SECURE,
      auth: { user: EMAIL_USER, pass: EMAIL_PASS },
    });
    return transporter;
  } catch {
    return null;
  }
}

// ── R-F3977 (C-66) §21b/§25 — email delivery outcomes must reach the brain ────
//
// Every failure below used to be a console line and a `{sent:false}` nobody
// inspects. server.mjs:6761 calls the reset sender as
// `.catch(() => {})` and then answers "a reset code has been sent" regardless,
// so a broken SMTP credential breaks signup AND account recovery at 100% with
// no signal anywhere. §21b is explicit that "logged to console" is DARK.
//
// Wired HERE, in the one function all fourteen senders pass through, rather than
// at the call sites: a fifteenth sender added later inherits it. Same reasoning
// as C-43 (mark at the gather, not in each wrapper) and C-40 (a purpose, not a
// route list).
let _smtpUnconfiguredAnnounced = false;

function reportEmailFailure(subject, to, reason, { announceOnce = false } = {}) {
  // "SMTP not configured" is a STANDING platform state: announce once per
  // process, or a busy signup hour floods the ledger — the C-59 flood this repo
  // has already paid for, in a different sink. A send exception is a per-event
  // incident and is reported EVERY time.
  if (announceOnce) {
    if (_smtpUnconfiguredAnnounced) return;
    _smtpUnconfiguredAnnounced = true;
  }
  // Fire-and-forget. An observability failure must never break or delay a mail
  // path that the user is waiting on.
  try {
    const baseUrl = process.env.ARIA_SERVICE_URL
      || process.env.APP_URL
      || `http://localhost:${process.env.PORT || 3117}`;
    const token = process.env.ARIA_INTERNAL_TOKEN || process.env.ARIA_API_TOKEN || '';
    void fetch(`${baseUrl}/api/aria/brain/signal`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        content: `[EMAIL DELIVERY FAILED] "${subject}" → ${to}: ${reason}`,
        source: 'lib/auth/email.mjs:sendMail',
        signal_type: 'delivery_failure',
        metadata: {
          channel: 'email',
          subject,
          reason: String(reason).slice(0, 200),
          standing_state: !!announceOnce,
          timestamp: new Date().toISOString(),
        },
      }),
    }).catch(() => {});
  } catch { /* never blocks */ }
}

async function sendMail(to, subject, html) {
  const transport = await getTransporter();
  if (!transport) {
    console.log(`[EMAIL] ── ${subject} ──`);
    console.log(`[EMAIL] To: ${to}`);
    console.log(`[EMAIL] (SMTP not configured — log relay mode)`);
    reportEmailFailure(subject, to, 'SMTP not configured', { announceOnce: true });
    return { sent: false, reason: 'SMTP not configured' };
  }
  try {
    // R-F744: capture nodemailer info so callers (e.g. admin.html
    // test-email button) can show the real messageId instead of
    // falsely reporting "OK but nothing sent" when sent:true alone
    // came back. Backwards-compatible: sent:true still set.
    const info = await transport.sendMail({ from: EMAIL_FROM, to, subject, html });
    console.log(`[EMAIL] Sent "${subject}" → ${to} (id=${info?.messageId || '?'})`);
    return { sent: true, messageId: info?.messageId, response: info?.response };
  } catch (err) {
    console.warn(`[EMAIL] Send failed: ${err.message}`);
    // A send EXCEPTION is a per-event incident, not a standing state — report
    // every one. The brain's own dedupe bounds the volume.
    reportEmailFailure(subject, to, err.message);
    return { sent: false, reason: err.message };
  }
}

// ── Shared design system ───────────────────────────────────────────────────────

// R-F2384 — light, warm ARIA theme (mirrors the web app aria.css tokens).
// Replaces the heavy dark-navy shell; every email now reads as the ARIA brand
// on a clean cream ground.
const BRAND_PURPLE = '#7c3aed';
const BRAND_GRAD_A = '#913BFF';
const BRAND_GRAD_B = '#0066FF';
const BG_PAGE      = '#faf9f5';   // warm cream page background (light, no heavy colour)
const BG_CARD      = '#ffffff';   // white card
const BG_SOFT      = '#f5f4ef';   // soft panel / header / footer
const BORDER_COLOR = '#e7e3da';
const BORDER_STRONG= '#d8d2c4';
const TEXT_MAIN    = '#1b1a18';   // warm near-black headings
const TEXT_BODY    = '#33302a';   // body copy
const TEXT_MUTED   = '#5f5c55';   // secondary
const TEXT_DIM     = '#9b968c';   // faint

function wrapHtml(title, bodyHtml) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${title}</title>
</head>
<body style="margin:0;padding:0;background:${BG_PAGE};font-family:'Segoe UI',Helvetica,Arial,sans-serif;color:${TEXT_BODY};">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:${BG_PAGE};padding:40px 16px;">
    <tr><td align="center">

      <!-- Outer card -->
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:${BG_CARD};border:1px solid ${BORDER_STRONG};border-radius:14px;overflow:hidden;max-width:600px;">

        <!-- Header bar -->
        <tr>
          <td style="background:${BG_SOFT};padding:0;border-bottom:1px solid ${BORDER_COLOR};">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding:22px 36px;">
                  <table cellpadding="0" cellspacing="0">
                    <tr>
                      <td style="vertical-align:middle;padding-right:14px;">
                        <!-- Logo mark -->
                        <div style="width:36px;height:36px;background:linear-gradient(135deg,${BRAND_GRAD_A},${BRAND_GRAD_B});
                                    border-radius:9px;display:inline-block;text-align:center;line-height:36px;
                                    font-size:16px;font-weight:900;color:#fff;letter-spacing:-1px;">A</div>
                      </td>
                      <td style="vertical-align:middle;">
                        <div style="font-size:14px;font-weight:800;letter-spacing:3px;color:${TEXT_MAIN};
                                    text-transform:uppercase;line-height:1.2;">ARIA</div>
                        <div style="font-size:10px;letter-spacing:2px;color:${TEXT_MUTED};
                                    text-transform:uppercase;margin-top:2px;">Intelligence Platform</div>
                      </td>
                    </tr>
                  </table>
                </td>
                <td style="padding:22px 36px;text-align:right;vertical-align:middle;">
                  <span style="font-size:10px;letter-spacing:1.5px;color:${TEXT_DIM};text-transform:uppercase;">
                    SECURE · CONFIDENTIAL
                  </span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Thin accent line -->
        <tr>
          <td style="height:3px;background:linear-gradient(90deg,${BRAND_GRAD_A},${BRAND_GRAD_B});"></td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:40px 36px 36px;">
            ${bodyHtml}
          </td>
        </tr>

        <!-- Divider -->
        <tr>
          <td style="height:1px;background:${BORDER_COLOR};"></td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:${BG_SOFT};padding:20px 36px;">
            <p style="margin:0 0 6px;font-size:11px;color:${TEXT_DIM};text-align:center;line-height:1.7;">
              This message was sent by ARIA — the Autonomous Research Intelligence Agent. Do not reply to this email.<br/>
              If you did not request this, you may safely disregard it.
            </p>
            <p style="margin:0;font-size:10px;color:${TEXT_DIM};text-align:center;">
              © ${new Date().getFullYear()} ARIA · <a href="https://imaria.io" style="color:${BRAND_PURPLE};text-decoration:none;">imaria.io</a>
            </p>
          </td>
        </tr>

      </table>
      <!-- /Outer card -->

    </td></tr>
  </table>
</body>
</html>`;
}

function codeBox(code) {
  return `
  <div style="margin:32px 0;text-align:center;">
    <div style="display:inline-block;background:${BG_SOFT};border:2px solid ${BRAND_PURPLE};
                border-radius:10px;padding:22px 44px;">
      <div style="letter-spacing:12px;font-size:34px;font-weight:700;
                  color:${BRAND_PURPLE};font-family:'Courier New',monospace;">${code}</div>
    </div>
    <p style="margin:10px 0 0;font-size:12px;color:${TEXT_DIM};">This code expires in 15 minutes</p>
  </div>`;
}

function ctaButton(label, url) {
  return `
  <div style="text-align:center;margin:32px 0 24px;">
    <a href="${url}"
       style="display:inline-block;background:linear-gradient(135deg,${BRAND_GRAD_A},${BRAND_GRAD_B});
              color:#ffffff;text-decoration:none;font-size:14px;font-weight:700;
              padding:14px 42px;border-radius:9px;letter-spacing:0.5px;">
      ${label}
    </a>
  </div>`;
}

function statusBadge(icon, headline, subline, color = '#16a34a') {
  return `
  <div style="text-align:center;margin:0 0 32px;">
    <div style="display:inline-block;width:60px;height:60px;border-radius:50%;
                background:${color}14;border:2px solid ${color};
                line-height:60px;font-size:26px;margin-bottom:16px;">${icon}</div>
    <h2 style="margin:0 0 6px;font-size:22px;font-weight:800;color:${TEXT_MAIN};letter-spacing:-0.02em;">
      ${headline}
    </h2>
    <p style="margin:0;font-size:12px;font-weight:700;letter-spacing:0.1em;
              text-transform:uppercase;color:${color};">${subline}</p>
  </div>`;
}

function accessList(items) {
  return `
  <div style="margin:0 0 28px;background:${BG_SOFT};border-radius:10px;
              padding:20px 24px;border:1px solid ${BORDER_COLOR};">
    <p style="margin:0 0 14px;font-size:10px;font-weight:700;color:${BRAND_PURPLE};
              letter-spacing:0.12em;text-transform:uppercase;">Your Access Includes</p>
    <table width="100%" cellpadding="0" cellspacing="0">
      ${items.map(item => `
      <tr>
        <td style="padding:5px 0;font-size:13px;color:${TEXT_BODY};line-height:1.5;">
          <span style="color:#16a34a;margin-right:10px;font-size:11px;">▸</span>${item}
        </td>
      </tr>`).join('')}
    </table>
  </div>`;
}

// ── Public API ─────────────────────────────────────────────────────────────────

export async function sendVerificationEmail(email, fullName, code) {
  const name = fullName || 'there';
  const body = `
    <h2 style="margin:0 0 8px;font-size:22px;font-weight:800;color:${TEXT_MAIN};letter-spacing:-0.02em;">
      Verify your email
    </h2>
    <p style="margin:0 0 20px;font-size:14px;color:${TEXT_MUTED};line-height:1.7;">
      Hi <strong style="color:${TEXT_MAIN}">${name}</strong>, use the security code below to verify your ARIA account:
    </p>
    ${codeBox(code)}
    <p style="margin:0 0 12px;font-size:12px;color:${TEXT_MUTED};line-height:1.6;">
      Only enter this code on the official ARIA platform. <strong style="color:${TEXT_MAIN}">Never share it with anyone</strong> — we will never ask for it outside the platform.
    </p>
    <p style="margin:0;font-size:12px;color:${TEXT_DIM};line-height:1.6;">
      If you didn't request this, you can safely ignore this email.
    </p>`;

  if (!isConfigured) console.log(`[EMAIL] Verification code for ${email}: ${code}`);
  return sendMail(email, 'Your ARIA security code', wrapHtml('Verify your email', body));
}

export async function sendVerificationSuccessEmail(email, fullName) {
  const name = fullName || 'there';
  const body = `
    ${statusBadge('✓', 'Email Verified', 'Confirmed', '#16a34a')}
    <p style="margin:0 0 16px;font-size:15px;color:${TEXT_MAIN};line-height:1.6;">
      Hi <strong>${name}</strong>,
    </p>
    <p style="margin:0 0 20px;font-size:14px;color:${TEXT_MUTED};line-height:1.75;">
      Your email address has been successfully verified. Your account is now pending
      administrator approval — you'll receive a notification once your access is activated.
    </p>
    <p style="margin:0;font-size:14px;color:${TEXT_MUTED};line-height:1.75;">
      If you have any questions, just reach out to
      <a href="mailto:${ADMIN_EMAIL}" style="color:${BRAND_PURPLE};text-decoration:none;">${ADMIN_EMAIL}</a>.
    </p>`;

  return sendMail(email, 'Email verified — ARIA', wrapHtml('Email Verified', body));
}

// Sent when the user's account enters pending_approval — confirms request is received
export async function sendPendingApprovalEmail(email, fullName) {
  const name = fullName || 'there';
  const body = `
    ${statusBadge('⏳', 'Request Received', 'Under Review', '#f59e0b')}

    <p style="margin:0 0 20px;font-size:15px;color:${TEXT_MAIN};line-height:1.6;">
      Hi <strong>${name}</strong>,
    </p>
    <p style="margin:0 0 20px;font-size:14px;color:${TEXT_MUTED};line-height:1.75;">
      Thank you for your interest in the <strong style="color:${TEXT_MAIN}">ARIA Intelligence Platform</strong>.
      Your access request has been received and is currently under review by our administrative team.
    </p>

    <!-- Status timeline -->
    <div style="margin:0 0 28px;background:${BG_SOFT};border-radius:8px;padding:24px;
                border:1px solid ${BORDER_COLOR};">
      <p style="margin:0 0 18px;font-size:10px;font-weight:700;color:${BRAND_PURPLE};
                letter-spacing:0.12em;text-transform:uppercase;">Account Setup Progress</p>

      <!-- Step 1 — done -->
      <table cellpadding="0" cellspacing="0" style="margin-bottom:14px;">
        <tr>
          <td style="width:28px;vertical-align:top;padding-top:2px;">
            <div style="width:20px;height:20px;border-radius:50%;background:#10b981;
                        text-align:center;line-height:20px;font-size:11px;font-weight:700;color:#fff;">✓</div>
          </td>
          <td style="padding-left:12px;vertical-align:top;">
            <div style="font-size:13px;font-weight:700;color:#10b981;">Account Created</div>
            <div style="font-size:12px;color:${TEXT_DIM};margin-top:2px;">Registration complete</div>
          </td>
        </tr>
      </table>

      <!-- Step 2 — active -->
      <table cellpadding="0" cellspacing="0" style="margin-bottom:14px;">
        <tr>
          <td style="width:28px;vertical-align:top;padding-top:2px;">
            <div style="width:20px;height:20px;border-radius:50%;background:#f59e0b;
                        text-align:center;line-height:20px;font-size:11px;font-weight:700;color:#000;">2</div>
          </td>
          <td style="padding-left:12px;vertical-align:top;">
            <div style="font-size:13px;font-weight:700;color:#f59e0b;">Under Administrator Review</div>
            <div style="font-size:12px;color:${TEXT_DIM};margin-top:2px;">Access requests are typically reviewed within 24–48 hours</div>
          </td>
        </tr>
      </table>

      <!-- Step 3 — pending -->
      <table cellpadding="0" cellspacing="0">
        <tr>
          <td style="width:28px;vertical-align:top;padding-top:2px;">
            <div style="width:20px;height:20px;border-radius:50%;
                        background:${BORDER_COLOR};border:2px solid ${BORDER_COLOR};
                        text-align:center;line-height:18px;font-size:11px;font-weight:700;color:${TEXT_DIM};">3</div>
          </td>
          <td style="padding-left:12px;vertical-align:top;">
            <div style="font-size:13px;font-weight:700;color:${TEXT_DIM};">Access Granted</div>
            <div style="font-size:12px;color:${TEXT_DIM};margin-top:2px;">You will receive a confirmation email once approved</div>
          </td>
        </tr>
      </table>
    </div>

    <p style="margin:0;font-size:12px;color:${TEXT_DIM};text-align:center;line-height:1.7;">
      You do not need to take any further action at this time.<br/>
      If you have an urgent requirement, please contact
      <a href="mailto:${ADMIN_EMAIL}" style="color:${BRAND_PURPLE};text-decoration:none;">${ADMIN_EMAIL}</a>.
    </p>`;

  return sendMail(
    email,
    'ARIA — Your Access Request is Under Review',
    wrapHtml('Access Request Under Review', body)
  );
}

// Sent when admin approves the account
export async function sendWelcomeEmail(email, fullName) {
  const name = fullName || 'there';
  const loginUrl = `${APP_URL}/signin.html`;

  const body = `
    ${statusBadge('✓', 'Access Approved', 'Account Active', '#10b981')}

    <p style="margin:0 0 20px;font-size:15px;color:${TEXT_MAIN};line-height:1.6;">
      Hi <strong>${name}</strong>,
    </p>
    <p style="margin:0 0 24px;font-size:14px;color:${TEXT_MUTED};line-height:1.75;">
      We are pleased to inform you that your access request for the
      <strong style="color:${TEXT_MAIN}">ARIA Intelligence Platform</strong>
      has been approved. Your account is now fully active and ready to use.
    </p>

    ${accessList([
      'Real-time OSINT &amp; geopolitical intelligence feeds',
      'Cross-source signal correlation and threat analysis',
      'Defence procurement opportunities &amp; Lusophone Africa coverage',
      'Export control &amp; sanctions compliance monitoring',
      'Business development pipeline and strategic intelligence',
      'ARIA — AI-powered intelligence assistant',
    ])}

    ${ctaButton('Sign In to Your Dashboard', loginUrl)}

    <!-- Security note -->
    <div style="background:${BG_SOFT};border-radius:6px;padding:14px 18px;
                border-left:3px solid ${BRAND_PURPLE};">
      <p style="margin:0;font-size:12px;color:${TEXT_DIM};line-height:1.6;">
        <strong style="color:${TEXT_MUTED};">Security reminder:</strong>
        Never share your credentials. All platform activity is logged for security and compliance purposes.
        Contact <a href="mailto:${ADMIN_EMAIL}" style="color:${BRAND_PURPLE};text-decoration:none;">${ADMIN_EMAIL}</a>
        if you experience any access issues.
      </p>
    </div>`;

  return sendMail(
    email,
    'ARIA — Your Account Has Been Approved',
    wrapHtml('Account Approved', body)
  );
}

// R-F3328 — an approved design partner's login. sendWelcomeEmail says "your
// account is now active" but carries NO credential, because it is sent to people
// who chose their own password at signup. A design partner never signed up —
// they applied on partners.html and an operator approved them — so the email
// that tells them they are in has to be the one that hands them the password.
export async function sendDesignPartnerCredentialsEmail(email, fullName, tempPassword) {
  const name = fullName || 'there';
  const loginUrl = `${APP_URL}/signin.html`;

  const body = `
    ${statusBadge('✓', 'You\'re In', 'Design Partner Access', '#10b981')}

    <p style="margin:0 0 20px;font-size:15px;color:${TEXT_MAIN};line-height:1.6;">
      Hi <strong>${name}</strong>,
    </p>
    <p style="margin:0 0 24px;font-size:14px;color:${TEXT_MUTED};line-height:1.75;">
      Your application to the <strong style="color:${TEXT_MAIN}">ARIA Intelligence</strong>
      design-partner programme has been approved. Your account is active with full
      platform access, free for the duration of the pilot.
    </p>

    <div style="margin:0 0 26px;background:${BG_SOFT};border:1px solid ${BORDER_STRONG};
                border-radius:10px;padding:20px 24px;">
      <p style="margin:0 0 14px;font-size:10px;font-weight:700;color:${BRAND_PURPLE};
                letter-spacing:0.12em;text-transform:uppercase;">Your Sign-In Details</p>
      <p style="margin:0 0 8px;font-size:13px;color:${TEXT_MUTED};">Email</p>
      <p style="margin:0 0 16px;font-size:15px;font-weight:600;color:${TEXT_MAIN};
                font-family:'Courier New',monospace;">${email}</p>
      <p style="margin:0 0 8px;font-size:13px;color:${TEXT_MUTED};">Temporary password</p>
      <p style="margin:0;font-size:19px;font-weight:700;color:${BRAND_PURPLE};
                letter-spacing:1px;font-family:'Courier New',monospace;">${tempPassword}</p>
    </div>

    <p style="margin:0 0 8px;font-size:14px;color:${TEXT_MUTED};line-height:1.75;">
      Please change this password from your account settings after your first sign-in.
    </p>

    ${ctaButton('Sign In', loginUrl)}

    <div style="background:${BG_SOFT};border-radius:6px;padding:14px 18px;
                border-left:3px solid ${BRAND_PURPLE};">
      <p style="margin:0;font-size:12px;color:${TEXT_DIM};line-height:1.6;">
        <strong style="color:${TEXT_MUTED};">As a design partner</strong> your feedback shapes
        what we build. Tell us what is wrong, missing or slow. Reply to this email or contact
        <a href="mailto:${ADMIN_EMAIL}" style="color:${BRAND_PURPLE};text-decoration:none;">${ADMIN_EMAIL}</a>
        any time.
      </p>
    </div>`;

  return sendMail(
    email,
    'ARIA — Your Design Partner Access',
    wrapHtml('Design Partner Access', body)
  );
}

export async function sendRejectionEmail(email, fullName) {
  const name = fullName || 'there';
  const body = `
    ${statusBadge('✕', 'Access Request Declined', 'Not Approved', '#ef4444')}

    <p style="margin:0 0 20px;font-size:15px;color:${TEXT_MAIN};line-height:1.6;">
      Hi <strong>${name}</strong>,
    </p>
    <p style="margin:0 0 20px;font-size:14px;color:${TEXT_MUTED};line-height:1.75;">
      Thank you for your interest in the
      <strong style="color:${TEXT_MAIN}">ARIA Intelligence Platform</strong>.
      After reviewing your access request, we are unable to approve your registration at this time.
    </p>
    <p style="margin:0 0 24px;font-size:14px;color:${TEXT_MUTED};line-height:1.75;">
      If you believe this decision was made in error, or if you have questions regarding
      your application, please contact the platform administrator directly.
    </p>
    <div style="text-align:center;margin:0 0 8px;">
      <a href="mailto:${ADMIN_EMAIL}"
         style="display:inline-block;background:transparent;color:${TEXT_MUTED};
                text-decoration:none;font-size:13px;font-weight:600;
                padding:11px 32px;border-radius:7px;letter-spacing:0.3px;
                border:1px solid ${BORDER_COLOR};">
        Contact Administrator
      </a>
    </div>`;

  return sendMail(
    email,
    'ARIA — Access Request Update',
    wrapHtml('Access Request Update', body)
  );
}

export async function sendPasswordResetEmail(email, fullName, code) {
  const name = fullName || 'there';
  const body = `
    <h2 style="margin:0 0 8px;font-size:22px;font-weight:800;color:${TEXT_MAIN};">
      Password Reset Request
    </h2>
    <p style="margin:0 0 24px;font-size:14px;color:${TEXT_MUTED};line-height:1.6;">
      Hi <strong style="color:${TEXT_MAIN}">${name}</strong>, we received a request to reset your
      ARIA Intelligence Platform password. Use the code below to set a new password:
    </p>
    ${codeBox(code)}
    <p style="color:${TEXT_DIM};font-size:12px;line-height:1.7;text-align:center;">
      If you did not request a password reset, please ignore this email — your account remains secure.
    </p>`;

  if (!isConfigured) console.log(`[EMAIL] Password reset code for ${email}: ${code}`);
  return sendMail(email, 'ARIA — Password Reset Request', wrapHtml('Password Reset', body));
}

// R-F609 (2026-05-16) — post-reset notification. Fires after a
// successful /api/auth/reset-password. If the rightful owner didn't
// trigger the reset (account-takeover attempt with a stolen code or
// successful brute-force) this email is their first signal something
// is wrong; the body tells them to contact us immediately.
export async function sendPasswordChangedNotification(email, fullName, requestIp = '') {
  const name = fullName || 'there';
  const ipLine = requestIp
    ? `<p style="margin:0 0 16px;font-size:12px;color:${TEXT_DIM};line-height:1.6;">
         Request origin: <code>${String(requestIp).slice(0, 64)}</code>
       </p>`
    : '';
  const body = `
    <h2 style="margin:0 0 8px;font-size:22px;font-weight:800;color:${TEXT_MAIN};">
      Your password was just changed
    </h2>
    <p style="margin:0 0 18px;font-size:14px;color:${TEXT_MUTED};line-height:1.6;">
      Hi <strong style="color:${TEXT_MAIN}">${name}</strong>, the password on your
      ARIA Intelligence Platform account was reset successfully a moment ago.
      You can now log in using the new password.
    </p>
    ${ipLine}
    <p style="margin:0 0 8px;font-size:14px;color:${TEXT_MAIN};line-height:1.6;">
      <strong>If you didn't do this:</strong> reply to this email immediately and
      we'll lock the account while we investigate. Do not delete this message.
    </p>
    <p style="color:${TEXT_DIM};font-size:12px;line-height:1.7;">
      If this was you, no action is needed — this is a routine security notice.
    </p>`;

  if (!isConfigured) console.log(`[EMAIL] Password-changed notice for ${email} (ip=${requestIp})`);
  return sendMail(email, 'ARIA — Your password was just changed', wrapHtml('Password changed', body));
}

export async function sendSuspensionEmail(email, fullName) {
  const name = fullName || 'there';
  const body = `
    ${statusBadge('⊘', 'Account Suspended', 'Access Revoked', '#f59e0b')}
    <p style="margin:0 0 20px;font-size:15px;color:${TEXT_MAIN};line-height:1.6;">
      Hi <strong>${name}</strong>,
    </p>
    <p style="margin:0 0 20px;font-size:14px;color:${TEXT_MUTED};line-height:1.75;">
      Your ARIA Intelligence Platform account has been temporarily suspended by an administrator.
      You will not be able to log in until the suspension is lifted.
    </p>
    <p style="margin:0;font-size:13px;color:${TEXT_DIM};line-height:1.7;">
      If you believe this is an error, contact
      <a href="mailto:${ADMIN_EMAIL}" style="color:${BRAND_PURPLE};text-decoration:none;">${ADMIN_EMAIL}</a>.
    </p>`;

  return sendMail(email, 'ARIA — Account Suspended', wrapHtml('Account Suspended', body));
}

export async function sendReactivationEmail(email, fullName) {
  const name = fullName || 'there';
  const loginUrl = `${APP_URL}/signin.html`;
  const body = `
    ${statusBadge('✓', 'Account Reactivated', 'Access Restored', '#10b981')}
    <p style="margin:0 0 20px;font-size:15px;color:${TEXT_MAIN};line-height:1.6;">
      Hi <strong>${name}</strong>,
    </p>
    <p style="margin:0 0 24px;font-size:14px;color:${TEXT_MUTED};line-height:1.75;">
      Your ARIA Intelligence Platform account has been reactivated.
      You can now sign in and access all platform features.
    </p>
    ${ctaButton('Sign In to Your Dashboard', loginUrl)}`;

  return sendMail(email, 'ARIA — Account Reactivated', wrapHtml('Account Reactivated', body));
}

// R-F3185 — the vetting invite email.
//
// Uses the house template (wrapHtml/ctaButton) rather than raw text: an
// applicant or a previous employer receiving a bare link with no branding is
// exactly what a phishing message looks like, and this one asks them to upload
// identity documents. It has to be recognisably from us.
//
// The link is NEVER logged here. When SMTP is unconfigured the module's
// existing relay writes the message to stdout, so `isConfigured` is checked by
// the caller and reported honestly rather than being presented as "sent".
export async function sendVettingInviteEmail({
  to, recipientName = '', link, expiresOn = '', isReferee = false,
  organisation = '', applicantName = '',
}) {
  const title = isReferee
    ? 'Request to confirm an employment reference'
    : 'Upload your screening documents';

  const intro = isReferee
    ? `<p>Hello${recipientName ? ' ' + recipientName : ''},</p>
       <p>You have been nominated to confirm an employment reference${
         applicantName ? ` for <strong>${applicantName}</strong>` : ''}${
         organisation ? ` at <strong>${organisation}</strong>` : ''}.</p>
       <p>You will be asked <strong>only</strong> to confirm that one engagement.
          No other information about the applicant is shared with you.</p>`
    : `<p>Hello${recipientName ? ' ' + recipientName : ''},</p>
       <p>Please upload the documents needed for your pre-employment screening.
          The link below is private to you.</p>`;

  const outro = `
    <p style="font-size:13px;color:#6b7280;">
      ${expiresOn ? `This link expires on <strong>${expiresOn}</strong>. ` : ''}
      Please do not forward it — it gives access to upload documents to this
      screening file.
    </p>
    <p style="font-size:13px;color:#6b7280;">
      If you were not expecting this message, you can ignore it and the link
      will expire on its own.
    </p>`;

  return sendMail(to, title,
    wrapHtml(title, intro + ctaButton(isReferee ? 'Confirm the reference'
                                                : 'Upload documents', link) + outro));
}

// R-F3531 — confirm ownership of the address on an access request.
//
// This is a double opt-in, not a marketing touch: until the link is used the
// request is `submitted_unverified` and cannot reach review, so the mail has to
// read as a confirmation step rather than a newsletter. The link lands on a page
// with a button — never a bare GET that verifies on load — because mail security
// scanners prefetch links and would otherwise confirm the address on the
// recipient's behalf, which is precisely the thing being proven.
//
// The link is NEVER logged. When SMTP is unconfigured the caller checks
// `isConfigured` and reports 'not_sent' rather than presenting it as sent.
export async function sendLeadVerificationEmail({ to, recipientName = '', link, expiresOn = '' }) {
  const title = 'Confirm your email to complete your ARIA access request';
  const intro = `
    <p>Hello${recipientName ? ' ' + recipientName : ''},</p>
    <p>We received a request for access to ARIA using this address. Please
       confirm it so we can act on the request.</p>
    <p>Until the address is confirmed, the request stays unverified and is not
       progressed.</p>`;
  const outro = `
    <p style="font-size:13px;color:${TEXT_MUTED};">
      ${expiresOn ? `This link expires on <strong>${expiresOn}</strong>. ` : ''}
      It can be used once.
    </p>
    <p style="font-size:13px;color:${TEXT_MUTED};">
      If you did not request access, ignore this message — nothing further
      happens, and the link expires on its own.
    </p>`;
  return sendMail(to, title, wrapHtml(title, intro + ctaButton('Confirm my email', link) + outro));
}

export async function sendAdminNotification(subject, html) {
  return sendMail(ADMIN_EMAIL, `[Admin] ${subject}`, wrapHtml(subject, html));
}
