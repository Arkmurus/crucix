// test/fixtures/recovery-reset-server.mjs
// R-F425 test fixture — spins up a minimal Express server that mounts ONLY
// the recovery-reset handler. Production handler lives in server.mjs around
// line 3520; this fixture mirrors its logic and imports the same primitive
// helpers (findUserByEmail / updateUser / hashPassword) so the test exercises
// the real persistence layer.
//
// The test harness spawns this with USERS_FILE_OVERRIDE pointing at a tmp
// users.json and ADMIN_RECOVERY_TOKEN set (or not, to test the 404 path).

import express from 'express';
import { findUserByEmail, updateUser, hashPassword, initUsersStore } from '../../lib/auth/users.mjs';

const app = express();
app.use(express.json());

app.post('/api/auth/recovery-reset', async (req, res) => {
  const expected = (process.env.ADMIN_RECOVERY_TOKEN || '').trim();
  if (!expected || expected.length < 32) {
    return res.status(404).json({ error: 'Not found' });
  }

  const { email, newPassword, recoveryToken } = req.body || {};
  if (typeof recoveryToken !== 'string' || recoveryToken.length !== expected.length) {
    return res.status(401).json({ error: 'Invalid recovery token' });
  }

  const crypto = await import('node:crypto');
  const a = Buffer.from(recoveryToken, 'utf8');
  const b = Buffer.from(expected, 'utf8');
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
    return res.status(401).json({ error: 'Invalid recovery token' });
  }

  if (!email || typeof email !== 'string') {
    return res.status(400).json({ error: 'Email required' });
  }
  if (!newPassword || typeof newPassword !== 'string' || newPassword.length < 8) {
    return res.status(400).json({ error: 'New password must be at least 8 characters' });
  }

  const user = findUserByEmail(email);
  if (!user) return res.status(404).json({ error: 'No user with that email' });

  updateUser(user.id, {
    passwordHash: hashPassword(newPassword),
    status: 'active',
    tokenVersion: (user.tokenVersion || 0) + 1,
    resetCode: null,
    resetExpiry: null,
  });

  res.json({ message: 'ok' });
});

await initUsersStore();
const server = app.listen(0, () => {
  const port = server.address().port;
  // Print port on its own line so the test harness can read it.
  console.log(`PORT=${port}`);
});
