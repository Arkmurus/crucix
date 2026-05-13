// test/fixtures/system-status-server.mjs
// R-F427 test fixture — minimal Express that mirrors the production
// /api/auth/system-status route logic, with USERS_FILE_OVERRIDE pointing at
// a tmp users.json. Imports the same primitives from lib/auth/users.mjs so
// the snapshot field shape is the real thing.

import express from 'express';
import {
  initUsersStore,
  initAdminUser,
  getAdminIdentitySnapshot,
  getBootstrapTrace,
  listUsers,
  createUser,
  updateUser,
  findUserByEmail,
} from '../../lib/auth/users.mjs';

const BUILD_REV = process.env.TEST_BUILD_REV || 'R-F427-test';
const app = express();
app.use(express.json());

app.get('/api/auth/system-status', (req, res) => {
  // R-F429: compute live from listUsers() each request; only `bootedAt`
  // comes from the boot-time snapshot. Mirrors production handler in
  // server.mjs.
  const snap = getAdminIdentitySnapshot();
  const allUsers = listUsers();
  const adminRows = allUsers.filter(u => u && u.role === 'admin');
  const envEmail = (process.env.ADMIN_EMAIL || '').toLowerCase().trim() || null;
  const matchesEnv = !!envEmail && adminRows.length === 1 && adminRows[0].email === envEmail;
  const dedicatedSet = !!process.env.EMAIL_HOST;
  const ariaFallbackAvailable =
    !dedicatedSet &&
    !!(process.env.ARIA_SMTP_HOST || process.env.ARIA_EMAIL_HOST) &&
    !!process.env.ARIA_EMAIL_USER &&
    !!process.env.ARIA_EMAIL_PASS;
  const smtpConfigured =
    (dedicatedSet && !!process.env.EMAIL_USER && !!process.env.EMAIL_PASS) ||
    ariaFallbackAvailable;
  const smtpVia = smtpConfigured
    ? (dedicatedSet ? 'dedicated' : 'aria-fallback')
    : null;
  const smtpHost = smtpConfigured
    ? (dedicatedSet
        ? process.env.EMAIL_HOST
        : (process.env.ARIA_SMTP_HOST || process.env.ARIA_EMAIL_HOST))
    : null;
  const smtpUser = smtpConfigured
    ? (dedicatedSet ? process.env.EMAIL_USER : process.env.ARIA_EMAIL_USER)
    : null;
  const smtpPort = smtpConfigured
    ? parseInt(
        process.env.EMAIL_PORT ||
        (dedicatedSet ? '587' : (process.env.ARIA_SMTP_PORT || '465'))
      )
    : null;
  const recoveryTokenSet = !!process.env.ADMIN_RECOVERY_TOKEN;
  const recoveryTokenLen = (process.env.ADMIN_RECOVERY_TOKEN || '').length;
  const recoveryEnabled = recoveryTokenSet && recoveryTokenLen >= 32;

  let adminAnomaly = 'ok';
  if (adminRows.length === 0) adminAnomaly = 'no-admin';
  else if (adminRows.length > 1) adminAnomaly = 'multiple-admins';
  else if (envEmail && !matchesEnv) adminAnomaly = 'env-mismatch';

  res.json({
    bootedAt: snap.bootedAt,
    buildRev: BUILD_REV,
    users: { total: allUsers.length, admins: adminRows.length },
    admin: {
      envEmailSet: !!envEmail,
      matchesEnv,
      anomaly: adminAnomaly,
      bootstrap: getBootstrapTrace(),
    },
    smtp: {
      configured: smtpConfigured,
      via: smtpVia,
      host: smtpHost,
      user: smtpUser,
      port: smtpPort,
    },
    recoveryReset: {
      enabled: recoveryEnabled,
      tokenSet: recoveryTokenSet,
      tokenLengthOk: recoveryTokenLen >= 32,
    },
  });
});

// R-F429 test seam: lets the capability test mutate the user store at
// runtime so we can verify /api/auth/system-status reflects post-boot
// state, not just the boot-time snapshot. Bypasses all auth — fixture only.
app.post('/test/seed-user', (req, res) => {
  const { email, role, status } = req.body || {};
  if (!email) return res.status(400).json({ error: 'email required' });
  createUser({
    username: 'seed-' + Math.random().toString(36).slice(2, 8),
    email,
    password: 'SeedPassword!23',
    fullName: 'Seeded ' + email,
    role: role || 'viewer',
  });
  const u = findUserByEmail(email);
  if (status) updateUser(u.id, { status });
  res.json({ ok: true, id: u.id });
});

await initUsersStore();
await initAdminUser();

const server = app.listen(0, () => {
  const port = server.address().port;
  console.log(`PORT=${port}`);
});
