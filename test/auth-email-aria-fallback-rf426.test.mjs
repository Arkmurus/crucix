// test/auth-email-aria-fallback-rf426.test.mjs
//
// Capability test for R-F426 — lib/auth/email.mjs fallback chain.
// The user-visible promise: "if EMAIL_HOST/USER/PASS aren't set on the deploy
// but the ARIA inbound-mail bridge IS configured, the auth side reuses those
// credentials instead of silently no-opping reset/verification emails".
//
// Strategy: spawn a child Node that imports email.mjs with a curated env,
// asks the module to dump its computed config + isConfigured flag, and the
// test asserts what was selected.
//
// Run: node test/auth-email-aria-fallback-rf426.test.mjs

import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');

let failures = 0;
function check(label, cond, detail) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? ' — ' + detail : ''}`); failures += 1; }
}

// Tiny probe script — written to a tmp dir to avoid landing in the repo
const probeDir = mkdtempSync(join(tmpdir(), 'rf426-probe-'));
const probe = join(probeDir, 'probe.mjs');
const emailModUrl = pathToFileURL(join(ROOT, 'lib', 'auth', 'email.mjs')).href;
writeFileSync(probe, `
import { isConfigured } from '${emailModUrl}';
console.log(JSON.stringify({ isConfigured }));
`);

function run(env) {
  // Force-blank vars we want to test "unset" — spawnSync inherits process.env
  // unless we provide a complete env. Strip the ones we care about.
  const filtered = { ...process.env };
  for (const k of ['EMAIL_HOST','EMAIL_PORT','EMAIL_USER','EMAIL_PASS','EMAIL_FROM','EMAIL_SECURE',
                   'ARIA_EMAIL_HOST','ARIA_EMAIL_PORT','ARIA_EMAIL_USER','ARIA_EMAIL_PASS',
                   'ARIA_SMTP_HOST','ARIA_SMTP_PORT']) {
    delete filtered[k];
  }
  Object.assign(filtered, env);
  const r = spawnSync(process.execPath, [probe], { env: filtered, encoding: 'utf8' });
  return { stdout: r.stdout, stderr: r.stderr, exit: r.status };
}

console.log('R-F426 capability tests\n');

// 1. Neither EMAIL_* nor ARIA_EMAIL_* set -> isConfigured=false
console.log('1. No SMTP env vars at all -> isConfigured=false');
{
  const r = run({});
  const out = JSON.parse(r.stdout);
  check('isConfigured is false', out.isConfigured === false);
  check('stderr warns operator', /SMTP NOT configured/.test(r.stderr));
}

// 2. ARIA_EMAIL_* set, EMAIL_* unset -> fallback engages, isConfigured=true
console.log('\n2. ARIA_EMAIL_* set only -> fallback engages');
{
  const r = run({
    ARIA_EMAIL_HOST: 'ox.livemail.co.uk',
    ARIA_EMAIL_USER: 'aria@arkmurus.com',
    ARIA_EMAIL_PASS: 'aria-secret',
  });
  const out = JSON.parse(r.stdout);
  check('isConfigured is true', out.isConfigured === true);
  check('boot log marks ARIA fallback', /via ARIA fallback/.test(r.stderr));
  check('boot log shows correct host', /host=ox\.livemail\.co\.uk/.test(r.stderr));
  check('boot log shows correct user', /user=aria@arkmurus\.com/.test(r.stderr));
  check('boot log shows port 465 (SSL default for fallback)', /port=465/.test(r.stderr));
  check('boot log shows secure=true', /secure=true/.test(r.stderr));
  check('secret never printed', !r.stderr.includes('aria-secret'));
}

// 3. EMAIL_* set explicitly takes precedence
console.log('\n3. EMAIL_* set explicitly -> takes precedence over ARIA_*');
{
  const r = run({
    EMAIL_HOST: 'smtp.dedicated.example',
    EMAIL_USER: 'auth@example.com',
    EMAIL_PASS: 'dedicated-secret',
    EMAIL_PORT: '587',
    ARIA_EMAIL_HOST: 'ox.livemail.co.uk',
    ARIA_EMAIL_USER: 'aria@arkmurus.com',
    ARIA_EMAIL_PASS: 'aria-secret',
  });
  const out = JSON.parse(r.stdout);
  check('isConfigured is true', out.isConfigured === true);
  check('boot log marks dedicated EMAIL_* path', /dedicated EMAIL_\* vars/.test(r.stderr));
  check('host is the dedicated one', /host=smtp\.dedicated\.example/.test(r.stderr));
  check('user is the dedicated one', /user=auth@example\.com/.test(r.stderr));
  check('port is 587 from env', /port=587/.test(r.stderr));
  check('ARIA secret not used', !r.stderr.includes('aria-secret'));
  check('dedicated secret not printed', !r.stderr.includes('dedicated-secret'));
}

// 4. ARIA_SMTP_PORT override in fallback path
console.log('\n4. ARIA_SMTP_PORT overrides default 465 in fallback path');
{
  const r = run({
    ARIA_EMAIL_HOST: 'ox.livemail.co.uk',
    ARIA_EMAIL_USER: 'aria@arkmurus.com',
    ARIA_EMAIL_PASS: 'aria-secret',
    ARIA_SMTP_PORT: '2525',
  });
  check('boot log shows port 2525', /port=2525/.test(r.stderr));
  check('non-465 port -> secure=false', /secure=false/.test(r.stderr));
}

// 5. EMAIL_HOST set but EMAIL_USER missing -> isConfigured=false
console.log('\n5. Partial EMAIL_* (host only) -> isConfigured=false');
{
  const r = run({ EMAIL_HOST: 'smtp.example' });
  const out = JSON.parse(r.stdout);
  check('isConfigured is false', out.isConfigured === false);
}

rmSync(probeDir, { recursive: true, force: true });

console.log(`\n${failures === 0 ? '✓ all checks passed' : '✗ ' + failures + ' check(s) failed'}`);
process.exit(failures === 0 ? 0 : 1);
