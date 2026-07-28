// lib/auth/designPartnerAccess.mjs
//
// R-F3328 — approving a design partner must ISSUE THEM A LOGIN.
//
// The defect this closes: the whole approval chain was a status label and
// nothing else. design-partners.html "Approve" → POST /api/design-partners/
// :index/status → PATCH aria-intel → DesignPartnerTracker.update(), which
// writes `notes` and `status` and returns. No account was created, no
// credential was generated, no email was sent, on any branch. So a record could
// read "engaged ✓" while the human it names had no way to sign in — verified
// live 2026-07-28: Ray Ingram applied 10:35:03, was approved 31s later, and
// /data/users.json on aria-web held four accounts, none of them his.
//
// partners.html promises the applicant "Free access during the pilot: full
// platform, no charge while you're a design partner." Approval is therefore a
// promise the platform could not keep, which is why this is a provisioning
// gap and not a UI nicety.
//
// Why the provisioning lives in the web tier: aria-intel owns the design-partner
// RECORD (/data/design_partners.json), aria-web owns ACCOUNTS
// (/data/users.json, lib/auth/users.mjs). Only aria-web can mint a credential,
// so the seam is the admin route that already proxies the status change.
//
// Two properties this module is built around:
//
//  1. NEVER touch an existing account. If the contact email already has a user,
//     we do not reset their password and we never hand their credential to
//     whoever clicked Approve. We report `existing_account` and, at most,
//     activate a pending row (that IS the approval decision) — an account
//     takeover disguised as an approval would be a far worse defect than the
//     one being fixed.
//
//  2. The caller is TOLD what happened to the email, and never has to trust
//     that it worked. This was designed on the R-F3289 reading that aria-web's
//     SMTP cannot authenticate (EMAIL_USER and EMAIL_PASS share a fly secret
//     digest, 49bb8a67b557e235). The live run on 2026-07-28 DISPROVED that for
//     this box: Ray's credential mail was accepted with a real messageId from
//     @arkmurus.com, so the running config does authenticate. Recorded as
//     measured rather than quietly deleted, because the shape of the fix does
//     not depend on it: acceptance by an SMTP server is not delivery to a human
//     either, so the send OUTCOME is returned and the temp password comes back
//     to the operator once, so a credential can never again exist only inside a
//     send nobody checked (CLAUDE.md §19e, §22).
import { randomInt } from 'node:crypto';

import {
  createUser, findUserByEmail, findUserByUsername, updateUser,
} from './users.mjs';
import { sendDesignPartnerCredentialsEmail } from './email.mjs';

// The statuses that mean "this partner is approved and should be able to sign
// in". `contacted` deliberately does NOT provision: we have merely spoken to
// them. `applied` is the un-reviewed public funnel and `declined` is a no.
// Kept in sync with aria_service/intel/design_partner_tracker.py's
// _QUALIFIED_STATUSES, which is the gate-#7 whitelist — access-granting is a
// SUBSET of gate-qualifying, so the two lists are related but not equal.
export const ACCESS_GRANTING_STATUSES = Object.freeze(['engaged', 'onboarded']);

// Design partners get the full platform free for the pilot, which is what
// partners.html advertises. proIntel is the only tier with everything on
// (lib/billing/tiers.mjs); `designPartner:true` marks WHY they hold it, so a
// later billing sweep can tell a pilot grant from an unpaid subscription.
export const DESIGN_PARTNER_TIER = 'proIntel';

// Unambiguous alphabet: no O/0, I/l/1, so a password read off a screen or
// dictated over the phone survives the trip. 3 groups of 6 from 32 symbols
// ≈ 90 bits, which is far past anything the login throttle would ever see.
const PW_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789';

export function generateTempPassword() {
  const group = () => Array.from({ length: 6 },
    () => PW_ALPHABET[randomInt(PW_ALPHABET.length)]).join('');
  return `${group()}-${group()}-${group()}`;
}

// Derive a free username from the email local part. createUser does NOT check
// username uniqueness (lib/auth/users.mjs), and findUserByUsername is an exact
// match, so a collision would create two rows answering to one name.
export function deriveUsername(email) {
  const base = String(email || '').split('@')[0]
    .toLowerCase().replace(/[^a-z0-9._-]/g, '').slice(0, 24) || 'partner';
  if (!findUserByUsername(base)) return base;
  for (let n = 2; n < 100; n += 1) {
    const candidate = `${base}${n}`;
    if (!findUserByUsername(candidate)) return candidate;
  }
  return `${base}${randomInt(100000, 999999)}`;
}

// A design-partner `contact` is free text — an operator may have logged a phone
// number or a LinkedIn handle. Without a real address there is nothing to
// provision an account against, and that must be said out loud rather than
// skipped quietly.
export function isEmailAddress(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(String(value || '').trim());
}

/**
 * Issue platform access for an approved design partner.
 *
 * Idempotent: safe to call again on an already-provisioned partner (returns
 * existing_account without mutating the credential), which is what makes the
 * "Issue login" button in design-partners.html safe to press twice.
 *
 * @param {{name?:string, contact?:string, company?:string}} entry design-partner record
 * @param {{sendEmail?:function}} [deps] injection seam for tests
 * @returns {Promise<object>} an outcome the caller must SURFACE, not swallow
 */
export async function provisionDesignPartnerAccess(entry, deps = {}) {
  const sendEmail = deps.sendEmail || sendDesignPartnerCredentialsEmail;
  const name = String(entry?.name || '').trim();
  const contact = String(entry?.contact || '').trim();
  const company = String(entry?.company || '').trim();

  if (!isEmailAddress(contact)) {
    return {
      provisioned: false,
      outcome: 'no_email_address',
      reason: `"${contact || '(blank)'}" is not an email address, so no account could be created. `
            + 'Add the partner\'s email to the record, then issue the login again.',
      email: contact || null,
      tempPassword: null,
      emailSent: false,
      emailReason: 'not attempted: no email address on the record',
    };
  }

  const email = contact.toLowerCase();
  const existing = findUserByEmail(email);

  if (existing) {
    // Approval of someone who already has an account means "let them in", so a
    // pending/suspended row is activated — but the password is NEVER reissued
    // and NEVER returned. They own that credential, not the approver.
    const wasInactive = existing.status !== 'active';
    if (wasInactive) updateUser(existing.id, { status: 'active' });
    if (existing.tier !== DESIGN_PARTNER_TIER) {
      updateUser(existing.id, { tier: DESIGN_PARTNER_TIER, designPartner: true });
    }
    return {
      provisioned: false,
      outcome: 'existing_account',
      reason: wasInactive
        ? `${email} already had an account. It has been activated with full pilot access. `
          + 'Their existing password still works; send them a password reset if they have lost it.'
        : `${email} already has an active account with full pilot access. No new credential was issued: `
          + 'their existing password still works.',
      email,
      userId: existing.id,
      username: existing.username,
      tier: DESIGN_PARTNER_TIER,
      activated: wasInactive,
      tempPassword: null,
      emailSent: false,
      emailReason: 'not attempted: no new credential was issued',
    };
  }

  const tempPassword = generateTempPassword();
  const username = deriveUsername(email);
  let created;
  try {
    created = createUser({
      username,
      email,
      password: tempPassword,
      fullName: name || username,
      role: 'viewer',
      accountType: company ? 'business' : 'individual',
      companyName: company,
    });
    // createUser hardcodes status:'pending_verification' and mints a
    // verification code (lib/auth/users.mjs) — it ignores any status passed in.
    // An operator-approved partner must not be sent round an email-verification
    // loop, least of all while SMTP cannot deliver the code, so the row is
    // activated here and the pending code cleared.
    updateUser(created.id, {
      status: 'active',
      verificationCode: null,
      verificationExpiry: null,
      tier: DESIGN_PARTNER_TIER,
      designPartner: true,
      credentialIssuedAt: new Date().toISOString(),
    });
  } catch (err) {
    return {
      provisioned: false,
      outcome: 'error',
      reason: `Account creation failed: ${err.message}`,
      email,
      tempPassword: null,
      emailSent: false,
      emailReason: 'not attempted: account creation failed',
    };
  }

  let emailSent = false;
  let emailReason = '';
  try {
    const res = await sendEmail(email, name || username, tempPassword);
    emailSent = !!res?.sent;
    emailReason = res?.sent ? `sent (id=${res.messageId || '?'})`
                            : (res?.reason || 'send returned no result');
  } catch (err) {
    emailReason = err.message || String(err);
  }

  return {
    provisioned: true,
    outcome: 'provisioned',
    reason: emailSent
      ? `Account created and the credentials were emailed to ${email}.`
      : `Account created, but the credentials email did NOT send (${emailReason}). `
        + 'Send the password below to the partner yourself. It is shown once.',
    email,
    userId: created.id,
    username,
    tier: DESIGN_PARTNER_TIER,
    tempPassword,
    emailSent,
    emailReason,
  };
}
