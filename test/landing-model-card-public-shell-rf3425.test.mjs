import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const landing = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
const card = readFileSync(new URL('../public/model-card.html', import.meta.url), 'utf8');

test('both landing model-card entry points target the public document', () => {
  assert.match(
    landing,
    /<div class="left-content">[\s\S]*?Reliable intelligence[\s\S]*?inspectable method[\s\S]*?href="\/model-card\.html"/,
  );
  assert.match(
    landing,
    /<footer[\s\S]*?href="\/model-card\.html"[\s\S]*?Model card/,
  );
});

test('following either entry point cannot render a phantom account', () => {
  for (const forbidden of [
    'Sidebar.init(',
    'js/sidebar.js',
    'js/app.js',
    'id="nav-avatar"',
    'id="btn-logout"',
    'Sign Out',
    'id="nav-role"',
  ]) {
    assert.ok(!card.includes(forbidden), `public model card contains authenticated shell marker: ${forbidden}`);
  }
  assert.match(card, /Back to imaria\.io/);
  assert.match(card, /href="\/"/);
});
