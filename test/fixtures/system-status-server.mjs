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
  listUsers,
} from '../../lib/auth/users.mjs';

const BUILD_REV = process.env.TEST_BUILD_REV || 'R-F427-test';
const app = express();
app.use(express.json());

app.get('/api/auth/system-status', (req, res) => {
  const snap = getAdminIdentitySnapshot();
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
  if (snap.adminCount === 0) adminAnomaly = 'no-admin';
  else if (snap.adminCount > 1) adminAnomaly = 'multiple-admins';
  else if (snap.envEmail && !snap.matchesEnv) adminAnomaly = 'env-mismatch';

  res.json({
    bootedAt: snap.bootedAt,
    buildRev: BUILD_REV,
    users: { total: listUsers().length, admins: snap.adminCount },
    admin: {
      envEmailSet: !!snap.envEmail,
      matchesEnv: snap.matchesEnv,
      anomaly: adminAnomaly,
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

await initUsersStore();
await initAdminUser();

const server = app.listen(0, () => {
  const port = server.address().port;
  console.log(`PORT=${port}`);
});
