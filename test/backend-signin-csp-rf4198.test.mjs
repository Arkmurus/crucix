import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';

import { computeInlineScriptHashes, inlineScriptBodies, sha256Source } from '../lib/http/cspHashes.mjs';

const REPO = path.resolve(path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..');
const PUBLIC = path.join(REPO, 'public');

test('R-F4198 capability: the real sign-in handler survives Windows line endings', () => {
  const original = readFileSync(path.join(PUBLIC, 'signin.html'));
  const windows = Buffer.from(original.toString('utf8').replace(/(?<!\r)\n/g, '\r\n'), 'utf8');
  const [body] = inlineScriptBodies(windows);
  assert.ok(body, 'the real sign-in page must contain its submit handler');

  const browserParsedBody = body.replace(/\r\n?/g, '\n');
  assert.equal(
    sha256Source(body),
    sha256Source(browserParsedBody),
    'the server policy must authorize the text the browser actually evaluates',
  );

  const scan = computeInlineScriptHashes(PUBLIC);
  const [checkedInBody] = inlineScriptBodies(original);
  assert.ok(scan.hashes.includes(sha256Source(checkedInBody)));
});

test('R-F4198 capability: native form fallback cannot put credentials in a URL', () => {
  const html = readFileSync(path.join(PUBLIC, 'signin.html'), 'utf8');
  const form = html.match(/<form\b[^>]*id="signin-form"[^>]*>/i)?.[0] || '';
  assert.match(form, /\bmethod="post"/i);
  assert.match(form, /\baction="\/api\/auth\/login"/i);
  assert.doesNotMatch(form, /\bmethod="get"/i);
});
