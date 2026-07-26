// R-F3147 — capability guard for exact shared-menu spacing.
//
// The production sidebar is injected by public/js/sidebar.js on every app page.
// Its menu rows must share one fixed vertical rhythm and the same horizontal
// inset as the top and bottom rail controls.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const css = readFileSync(
  new URL('../public/css/aria.css', import.meta.url),
  'utf8',
);

test('R-F3147 keeps every shared menu icon on one exact spacing grid', () => {
  assert.match(
    css,
    /\.rail-link\s*\{[\s\S]*?height:\s*46px;[\s\S]*?flex-shrink:\s*0;/,
  );
  assert.match(
    css,
    /\.rail-nav\s*\{[^}]*padding:\s*12px 10px;[^}]*gap:\s*12px;/,
  );
  assert.match(
    css,
    /\.rail-nav\s*>\s*\[data-gated\]\s*\{[^}]*flex:\s*0 0 auto;[^}]*width:\s*100%;/,
  );
});
