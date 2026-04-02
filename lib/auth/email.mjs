// lib/auth/email.mjs
// Email verification and notifications — requires SMTP config in .env
// If not configured, logs verification codes to console (admin relay mode)

const EMAIL_HOST   = process.env.EMAIL_HOST;
const EMAIL_PORT   = parseInt(process.env.EMAIL_PORT || '587');
const EMAIL_USER   = process.env.EMAIL_USER;
const EMAIL_PASS   = process.env.EMAIL_PASS;
const EMAIL_FROM   = process.env.EMAIL_FROM || 'Arkmurus Intelligence <noreply@arkmurus.com>';
const EMAIL_SECURE = process.env.EMAIL_SECURE === 'true' || EMAIL_PORT === 465;
const ADMIN_EMAIL  = process.env.ADMIN_EMAIL || 'acorrea@arkmurus.com';

const isConfigured = !!(EMAIL_HOST && EMAIL_USER && EMAIL_PASS);

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
    return { sent: true };
  } catch (err) {
    console.warn(`[EMAIL] Send failed: ${err.message}`);
    return { sent: false, reason: err.message };
  }
}

// ── HTML template ─────────────────────────────────────────────────────────────

function wrapHtml(title, bodyHtml) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${title}</title>
</head>
<body style="margin:0;padding:0;background:#0a0e1a;font-family:'Segoe UI',Arial,sans-serif;color:#c8d6e5;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0e1a;padding:40px 0;">
    <tr><td align="center">
      <table width="580" cellpadding="0" cellspacing="0"
             style="background:#111827;border:1px solid #1e3a5f;border-radius:8px;overflow:hidden;">
        <!-- Header -->
        <tr>
          <td style="background:#0d1b2e;padding:28px 36px;border-bottom:2px solid #1e40af;">
            <span style="font-size:11px;letter-spacing:3px;color:#3b82f6;text-transform:uppercase;font-weight:700;">
              ARKMURUS INTELLIGENCE PLATFORM
            </span>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:36px;">
            ${bodyHtml}
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background:#0d1b2e;padding:20px 36px;border-top:1px solid #1e3a5f;">
            <p style="margin:0;font-size:11px;color:#64748b;text-align:center;">
              This message was sent from Arkmurus Intelligence Platform.<br/>
              If you did not request this, please disregard this email.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>`;
}

function codeBox(code) {
  return `<div style="margin:28px 0;text-align:center;">
    <div style="display:inline-block;background:#0a0e1a;border:2px solid #3b82f6;border-radius:6px;
                padding:20px 40px;letter-spacing:10px;font-size:32px;font-weight:700;
                color:#60a5fa;font-family:'Courier New',monospace;">
      ${code}
    </div>
    <p style="margin:12px 0 0;font-size:12px;color:#64748b;">Expires in 15 minutes</p>
  </div>`;
}

// ── Public API ────────────────────────────────────────────────────────────────

export async function sendVerificationEmail(email, fullName, code) {
  const body = `
    <h2 style="margin:0 0 8px;font-size:22px;color:#e2e8f0;">Verify Your Email Address</h2>
    <p style="margin:0 0 20px;color:#94a3b8;font-size:14px;">Hi ${fullName || 'there'},</p>
    <p style="margin:0 0 16px;color:#cbd5e1;line-height:1.6;">
      Welcome to Arkmurus Intelligence Platform. To activate your account, enter the verification
      code below in the app:
    </p>
    ${codeBox(code)}
    <p style="color:#64748b;font-size:13px;line-height:1.6;">
      If you did not create an account, no action is required.
    </p>`;

  if (!isConfigured) {
    console.log(`[EMAIL] Verification code for ${email}: ${code}`);
  }
  return sendMail(
    email,
    'Arkmurus Intelligence Platform — Verify Your Email',
    wrapHtml('Verify Your Email', body)
  );
}

export async function sendPasswordResetEmail(email, fullName, code) {
  const body = `
    <h2 style="margin:0 0 8px;font-size:22px;color:#e2e8f0;">Password Reset Request</h2>
    <p style="margin:0 0 20px;color:#94a3b8;font-size:14px;">Hi ${fullName || 'there'},</p>
    <p style="margin:0 0 16px;color:#cbd5e1;line-height:1.6;">
      We received a request to reset your Arkmurus Intelligence Platform password.
      Use the code below to set a new password:
    </p>
    ${codeBox(code)}
    <p style="color:#64748b;font-size:13px;line-height:1.6;">
      If you did not request a password reset, please ignore this email.
      Your password will remain unchanged.
    </p>`;

  if (!isConfigured) {
    console.log(`[EMAIL] Password reset code for ${email}: ${code}`);
  }
  return sendMail(
    email,
    'Arkmurus Intelligence Platform — Password Reset',
    wrapHtml('Password Reset', body)
  );
}

export async function sendWelcomeEmail(email, fullName) {
  const appUrl = process.env.APP_URL || 'https://crucix.onrender.com';
  const loginUrl = `${appUrl}/auth/sign-in`;

  const body = `
    <!-- Approval badge -->
    <div style="text-align:center;margin-bottom:28px;">
      <div style="display:inline-flex;align-items:center;justify-content:center;
                  width:64px;height:64px;border-radius:50%;
                  background:linear-gradient(135deg,#064e3b,#065f46);
                  border:2px solid #10b981;margin-bottom:16px;">
        <span style="font-size:28px;line-height:1;">✓</span>
      </div>
      <h2 style="margin:0 0 6px;font-size:24px;font-weight:700;color:#e2e8f0;letter-spacing:-0.02em;">
        Account Approved
      </h2>
      <p style="margin:0;font-size:13px;color:#10b981;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;">
        Access Granted
      </p>
    </div>

    <p style="margin:0 0 20px;color:#cbd5e1;font-size:15px;line-height:1.6;">
      Hi <strong style="color:#f1f5f9">${fullName || 'there'}</strong>,
    </p>
    <p style="margin:0 0 24px;color:#94a3b8;font-size:14px;line-height:1.7;">
      Your registration request for the <strong style="color:#e2e8f0">Arkmurus Intelligence Platform</strong>
      has been reviewed and approved. Your account is now fully active.
    </p>

    <!-- What you have access to -->
    <div style="margin:0 0 28px;background:#0a0e1a;border-radius:8px;padding:20px 24px;border:1px solid #1e3a5f;">
      <p style="margin:0 0 14px;font-size:11px;font-weight:700;color:#3b82f6;
                letter-spacing:0.1em;text-transform:uppercase;">
        Your Access Includes
      </p>
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="padding:5px 0;color:#94a3b8;font-size:13px;line-height:1.5;">
            <span style="color:#10b981;margin-right:10px;">●</span>Real-time OSINT &amp; geopolitical intelligence feeds
          </td>
        </tr>
        <tr>
          <td style="padding:5px 0;color:#94a3b8;font-size:13px;line-height:1.5;">
            <span style="color:#10b981;margin-right:10px;">●</span>Cross-source signal correlation and threat analysis
          </td>
        </tr>
        <tr>
          <td style="padding:5px 0;color:#94a3b8;font-size:13px;line-height:1.5;">
            <span style="color:#10b981;margin-right:10px;">●</span>Defence procurement opportunities &amp; Lusophone Africa coverage
          </td>
        </tr>
        <tr>
          <td style="padding:5px 0;color:#94a3b8;font-size:13px;line-height:1.5;">
            <span style="color:#10b981;margin-right:10px;">●</span>Export control &amp; sanctions compliance monitoring
          </td>
        </tr>
        <tr>
          <td style="padding:5px 0;color:#94a3b8;font-size:13px;line-height:1.5;">
            <span style="color:#10b981;margin-right:10px;">●</span>Telegram alerts &amp; daily intelligence digests
          </td>
        </tr>
      </table>
    </div>

    <!-- CTA button -->
    <div style="text-align:center;margin-bottom:24px;">
      <a href="${loginUrl}"
         style="display:inline-block;background:linear-gradient(135deg,#1d4ed8,#1e40af);
                color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;
                padding:14px 40px;border-radius:6px;letter-spacing:0.02em;
                border:1px solid #2563eb;">
        Sign In to Your Dashboard
      </a>
    </div>

    <p style="margin:0;color:#475569;font-size:12px;text-align:center;line-height:1.6;">
      If you have any questions, contact your administrator directly.
    </p>`;

  return sendMail(
    email,
    'Your Arkmurus Intelligence Account Has Been Approved',
    wrapHtml('Account Approved', body)
  );
}

export async function sendRejectionEmail(email, fullName) {
  const body = `
    <h2 style="margin:0 0 8px;font-size:22px;color:#e2e8f0;">Account Registration Update</h2>
    <p style="margin:0 0 20px;color:#94a3b8;font-size:14px;">Hi ${fullName || 'there'},</p>
    <p style="margin:0 0 16px;color:#cbd5e1;line-height:1.6;">
      After review, we are unable to approve your registration request for the
      Arkmurus Intelligence Platform at this time.
    </p>
    <p style="color:#64748b;font-size:13px;line-height:1.6;">
      If you believe this is an error or would like to discuss further,
      please contact your administrator directly.
    </p>`;
  return sendMail(email, 'Arkmurus Intelligence Platform — Registration Update', wrapHtml('Registration Update', body));
}

export async function sendSuspensionEmail(email, fullName) {
  const body = `
    <h2 style="margin:0 0 8px;font-size:22px;color:#e2e8f0;">Account Suspended</h2>
    <p style="margin:0 0 20px;color:#94a3b8;font-size:14px;">Hi ${fullName || 'there'},</p>
    <p style="margin:0 0 16px;color:#cbd5e1;line-height:1.6;">
      Your Arkmurus Intelligence Platform account has been suspended by an administrator.
      You will not be able to log in until the suspension is lifted.
    </p>
    <p style="color:#64748b;font-size:13px;line-height:1.6;">
      If you believe this is an error, please contact your administrator.
    </p>`;
  return sendMail(email, 'Arkmurus Intelligence Platform — Account Suspended', wrapHtml('Account Suspended', body));
}

export async function sendReactivationEmail(email, fullName) {
  const body = `
    <h2 style="margin:0 0 8px;font-size:22px;color:#e2e8f0;">Account Reactivated</h2>
    <p style="margin:0 0 20px;color:#94a3b8;font-size:14px;">Hi ${fullName || 'there'},</p>
    <p style="margin:0 0 16px;color:#cbd5e1;line-height:1.6;">
      Your Arkmurus Intelligence Platform account has been reactivated.
      You can now log in and access the platform.
    </p>`;
  return sendMail(email, 'Arkmurus Intelligence Platform — Account Reactivated', wrapHtml('Account Reactivated', body));
}

export async function sendAdminNotification(subject, html) {
  return sendMail(ADMIN_EMAIL, subject, wrapHtml(subject, html));
}
