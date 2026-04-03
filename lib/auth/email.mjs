// lib/auth/email.mjs
// Email notifications — requires SMTP config in .env
// If not configured, logs to console (admin relay mode)

const EMAIL_HOST   = process.env.EMAIL_HOST;
const EMAIL_PORT   = parseInt(process.env.EMAIL_PORT || '587');
const EMAIL_USER   = process.env.EMAIL_USER;
const EMAIL_PASS   = process.env.EMAIL_PASS;
const EMAIL_FROM   = process.env.EMAIL_FROM || 'Arkmurus Intelligence <noreply@arkmurus.com>';
const EMAIL_SECURE = process.env.EMAIL_SECURE === 'true' || EMAIL_PORT === 465;
const ADMIN_EMAIL  = process.env.ADMIN_EMAIL || 'acorrea@arkmurus.com';
const APP_URL      = process.env.APP_URL || 'https://intel.sursec.co.uk';

export const isConfigured = !!(EMAIL_HOST && EMAIL_USER && EMAIL_PASS);

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

async function sendMail(to, subject, html) {
  const transport = await getTransporter();
  if (!transport) {
    console.log(`[EMAIL] ── ${subject} ──`);
    console.log(`[EMAIL] To: ${to}`);
    console.log(`[EMAIL] (SMTP not configured — log relay mode)`);
    return { sent: false, reason: 'SMTP not configured' };
  }
  try {
    await transport.sendMail({ from: EMAIL_FROM, to, subject, html });
    console.log(`[EMAIL] Sent "${subject}" → ${to}`);
    return { sent: true };
  } catch (err) {
    console.warn(`[EMAIL] Send failed: ${err.message}`);
    return { sent: false, reason: err.message };
  }
}

// ── Shared design system ───────────────────────────────────────────────────────

const BRAND_PURPLE = '#7c3aed';
const BRAND_BLUE   = '#1d4ed8';
const BG_DARK      = '#0c0a1e';
const BG_CARD      = '#13112b';
const BG_PANEL     = '#0f0d24';
const BORDER_COLOR = '#2d2756';
const TEXT_MAIN    = '#e8e4f8';
const TEXT_MUTED   = '#8b85b0';
const TEXT_DIM     = '#5a5480';

function wrapHtml(title, bodyHtml) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${title}</title>
</head>
<body style="margin:0;padding:0;background:${BG_DARK};font-family:'Segoe UI',Helvetica,Arial,sans-serif;color:${TEXT_MAIN};">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:${BG_DARK};padding:48px 16px;">
    <tr><td align="center">

      <!-- Outer card -->
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:${BG_CARD};border:1px solid ${BORDER_COLOR};border-radius:12px;overflow:hidden;max-width:600px;">

        <!-- Header bar -->
        <tr>
          <td style="background:${BG_PANEL};padding:0;border-bottom:1px solid ${BORDER_COLOR};">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding:22px 36px;">
                  <table cellpadding="0" cellspacing="0">
                    <tr>
                      <td style="vertical-align:middle;padding-right:14px;">
                        <!-- Logo mark -->
                        <div style="width:36px;height:36px;background:linear-gradient(135deg,${BRAND_PURPLE},${BRAND_BLUE});
                                    border-radius:9px;display:inline-block;text-align:center;line-height:36px;
                                    font-size:16px;font-weight:900;color:#fff;letter-spacing:-1px;">A</div>
                      </td>
                      <td style="vertical-align:middle;">
                        <div style="font-size:13px;font-weight:800;letter-spacing:3px;color:${TEXT_MAIN};
                                    text-transform:uppercase;line-height:1.2;">ARKMURUS</div>
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
          <td style="height:3px;background:linear-gradient(90deg,${BRAND_PURPLE},${BRAND_BLUE},${BRAND_PURPLE});"></td>
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
          <td style="background:${BG_PANEL};padding:20px 36px;">
            <p style="margin:0 0 6px;font-size:11px;color:${TEXT_DIM};text-align:center;line-height:1.7;">
              This message was sent by the Arkmurus Intelligence Platform. Do not reply to this email.<br/>
              If you did not request this, you may safely disregard it.
            </p>
            <p style="margin:0;font-size:10px;color:${TEXT_DIM};text-align:center;">
              © ${new Date().getFullYear()} Arkmurus · All Rights Reserved
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
    <div style="display:inline-block;background:${BG_PANEL};border:2px solid ${BRAND_PURPLE};
                border-radius:8px;padding:22px 44px;">
      <div style="letter-spacing:12px;font-size:34px;font-weight:700;
                  color:#a78bfa;font-family:'Courier New',monospace;">${code}</div>
    </div>
    <p style="margin:10px 0 0;font-size:12px;color:${TEXT_DIM};">This code expires in 15 minutes</p>
  </div>`;
}

function ctaButton(label, url, color = BRAND_PURPLE) {
  return `
  <div style="text-align:center;margin:32px 0 24px;">
    <a href="${url}"
       style="display:inline-block;background:linear-gradient(135deg,${BRAND_PURPLE},${BRAND_BLUE});
              color:#ffffff;text-decoration:none;font-size:14px;font-weight:700;
              padding:14px 42px;border-radius:8px;letter-spacing:0.5px;">
      ${label}
    </a>
  </div>`;
}

function statusBadge(icon, headline, subline, color = '#10b981') {
  return `
  <div style="text-align:center;margin:0 0 32px;">
    <div style="display:inline-block;width:60px;height:60px;border-radius:50%;
                background:${color}1a;border:2px solid ${color};
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
  <div style="margin:0 0 28px;background:${BG_PANEL};border-radius:8px;
              padding:20px 24px;border:1px solid ${BORDER_COLOR};">
    <p style="margin:0 0 14px;font-size:10px;font-weight:700;color:${BRAND_PURPLE};
              letter-spacing:0.12em;text-transform:uppercase;">Your Access Includes</p>
    <table width="100%" cellpadding="0" cellspacing="0">
      ${items.map(item => `
      <tr>
        <td style="padding:5px 0;font-size:13px;color:${TEXT_MUTED};line-height:1.5;">
          <span style="color:#10b981;margin-right:10px;font-size:11px;">▸</span>${item}
        </td>
      </tr>`).join('')}
    </table>
  </div>`;
}

// ── Public API ─────────────────────────────────────────────────────────────────

export async function sendVerificationEmail(email, fullName, code) {
  const name = fullName || 'there';
  const body = `
    <h2 style="margin:0 0 8px;font-size:22px;font-weight:800;color:${TEXT_MAIN};">
      Verify Your Email Address
    </h2>
    <p style="margin:0 0 24px;font-size:13px;color:${TEXT_MUTED};line-height:1.6;">
      Hi <strong style="color:${TEXT_MAIN}">${name}</strong>, thanks for registering.
      Enter the 6-digit code below to confirm your email address and complete the first step of
      your account setup.
    </p>
    ${codeBox(code)}
    <p style="color:${TEXT_DIM};font-size:12px;line-height:1.7;text-align:center;">
      Once verified, your account will be reviewed by the platform administrator.
      You will receive a separate notification when access is granted.
    </p>`;

  if (!isConfigured) console.log(`[EMAIL] Verification code for ${email}: ${code}`);
  return sendMail(email, 'Arkmurus — Verify Your Email Address', wrapHtml('Verify Your Email', body));
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
      Thank you for your interest in the <strong style="color:${TEXT_MAIN}">Arkmurus Intelligence Platform</strong>.
      Your access request has been received and is currently under review by our administrative team.
    </p>

    <!-- Status timeline -->
    <div style="margin:0 0 28px;background:${BG_PANEL};border-radius:8px;padding:24px;
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
    'Arkmurus — Your Access Request is Under Review',
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
      <strong style="color:${TEXT_MAIN}">Arkmurus Intelligence Platform</strong>
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
    <div style="background:${BG_PANEL};border-radius:6px;padding:14px 18px;
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
    'Arkmurus — Your Account Has Been Approved',
    wrapHtml('Account Approved', body)
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
      <strong style="color:${TEXT_MAIN}">Arkmurus Intelligence Platform</strong>.
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
    'Arkmurus — Access Request Update',
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
      Arkmurus Intelligence Platform password. Use the code below to set a new password:
    </p>
    ${codeBox(code)}
    <p style="color:${TEXT_DIM};font-size:12px;line-height:1.7;text-align:center;">
      If you did not request a password reset, please ignore this email — your account remains secure.
    </p>`;

  if (!isConfigured) console.log(`[EMAIL] Password reset code for ${email}: ${code}`);
  return sendMail(email, 'Arkmurus — Password Reset Request', wrapHtml('Password Reset', body));
}

export async function sendSuspensionEmail(email, fullName) {
  const name = fullName || 'there';
  const body = `
    ${statusBadge('⊘', 'Account Suspended', 'Access Revoked', '#f59e0b')}
    <p style="margin:0 0 20px;font-size:15px;color:${TEXT_MAIN};line-height:1.6;">
      Hi <strong>${name}</strong>,
    </p>
    <p style="margin:0 0 20px;font-size:14px;color:${TEXT_MUTED};line-height:1.75;">
      Your Arkmurus Intelligence Platform account has been temporarily suspended by an administrator.
      You will not be able to log in until the suspension is lifted.
    </p>
    <p style="margin:0;font-size:13px;color:${TEXT_DIM};line-height:1.7;">
      If you believe this is an error, contact
      <a href="mailto:${ADMIN_EMAIL}" style="color:${BRAND_PURPLE};text-decoration:none;">${ADMIN_EMAIL}</a>.
    </p>`;

  return sendMail(email, 'Arkmurus — Account Suspended', wrapHtml('Account Suspended', body));
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
      Your Arkmurus Intelligence Platform account has been reactivated.
      You can now sign in and access all platform features.
    </p>
    ${ctaButton('Sign In to Your Dashboard', loginUrl)}`;

  return sendMail(email, 'Arkmurus — Account Reactivated', wrapHtml('Account Reactivated', body));
}

export async function sendAdminNotification(subject, html) {
  return sendMail(ADMIN_EMAIL, `[Admin] ${subject}`, wrapHtml(subject, html));
}
