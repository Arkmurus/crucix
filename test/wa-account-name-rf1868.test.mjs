// test/wa-account-name-rf1868.test.mjs
//
// Capability test for R-F1868 (ARIA finding) — the "Add Account" WhatsApp
// button dropped the user-supplied `name`.
//
// Root cause: POST /api/wa-listener/accounts is registered (~server.mjs:1097)
// BEFORE the global `app.use('/api/', express.json())` mount (~1202), so the
// handler read req.body === undefined and forwarded `{}` to aria-wa, which then
// fell back to the auto-generated accountId as the name.
//
// Fix: a route-level `express.json()` runs the body parser before the handler.
// This test reproduces the EXACT ordering (route mounted before the global
// json mount) and proves: WITHOUT the route-level parser the body is lost,
// WITH it the body is parsed.
//
// Run: node test/wa-account-name-rf1868.test.mjs

import express from 'express';
import http from 'node:http';
import assert from 'node:assert';

let failures = 0;
function check(name, fn) {
  return fn().then(() => console.log(`  ok - ${name}`))
    .catch((e) => { failures++; console.error(`  FAIL - ${name}\n     ${e.message}`); });
}

function postJson(port, path, payload) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(payload);
    const req = http.request({ host: '127.0.0.1', port, path, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } },
      (res) => { let b = ''; res.on('data', c => b += c); res.on('end', () => resolve(JSON.parse(b))); });
    req.on('error', reject);
    req.end(data);
  });
}

// Build an app that mirrors server.mjs's ordering: the /api/wa-listener route is
// registered FIRST, then the global /api/ json mount comes AFTER it.
function buildApp({ routeLevelParser }) {
  const app = express();
  const handler = (req, res) => res.json({ received: req.body });
  if (routeLevelParser) {
    app.post('/api/wa-listener/accounts', express.json({ limit: '100kb' }), handler); // R-F1868 fix
  } else {
    app.post('/api/wa-listener/accounts', handler); // pre-fix (buggy) ordering
  }
  app.use('/api/', express.json({ limit: '100kb' })); // global mount AFTER the route
  return app;
}

async function withServer(app, fn) {
  const server = app.listen(0);
  await new Promise(r => server.once('listening', r));
  try { return await fn(server.address().port); }
  finally { await new Promise(r => server.close(r)); }
}

await check('pre-fix ordering loses the body (req.body undefined)', async () => {
  await withServer(buildApp({ routeLevelParser: false }), async (port) => {
    const out = await postJson(port, '/api/wa-listener/accounts', { name: 'My Phone' });
    assert.ok(!out.received || out.received.name === undefined,
      'expected the body to be lost without a route-level parser');
  });
});

await check('R-F1868 fix: route-level express.json parses the name', async () => {
  await withServer(buildApp({ routeLevelParser: true }), async (port) => {
    const out = await postJson(port, '/api/wa-listener/accounts', { name: 'My Phone' });
    assert.strictEqual(out.received?.name, 'My Phone',
      'route-level express.json should have parsed req.body.name');
  });
});

await check('server.mjs POST /api/wa-listener/accounts has a route-level express.json', async () => {
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
  const m = src.match(/app\.post\('\/api\/wa-listener\/accounts',\s*requireAuth,\s*express\.json/);
  assert.ok(m, 'POST /api/wa-listener/accounts is missing the route-level express.json parser');
});

if (failures) { console.error(`\n${failures} check(s) FAILED`); process.exit(1); }
console.log('\nAll R-F1868 checks passed');
