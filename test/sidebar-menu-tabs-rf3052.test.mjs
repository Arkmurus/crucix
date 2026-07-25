// R-F3052 — capability guard for the shared application sidebar.
//
// The menu must expose one ARIA Chat entry and one ARIA Brain entry without
// duplicate "New ARIA chat" or "ARIA Ecosystem" tabs. All menu rows continue
// to use the shared 46px row height and 12px navigation gap.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const sidebar = readFileSync(
  new URL('../public/js/sidebar.js', import.meta.url),
  'utf8',
);
const css = readFileSync(
  new URL('../public/css/aria.css', import.meta.url),
  'utf8',
);

test('R-F3052 removes duplicate ARIA menu tabs while preserving primary entries', () => {
  assert.doesNotMatch(sidebar, /New ARIA chat/i);
  assert.doesNotMatch(sidebar, /ARIA Ecosystem/i);
  assert.doesNotMatch(sidebar, /aria-brain#ecosystem-map/i);
  assert.doesNotMatch(sidebar, /\brail-new\b/);

  assert.match(sidebar, /'ARIA Chat',\s*'aria-link'/);
  assert.match(sidebar, /'ARIA Brain'/);
});

test('R-F3052 keeps one professional spacing system for every menu row', () => {
  assert.match(css, /\.rail-link\s*\{[\s\S]*?height:\s*46px;/);
  assert.match(
    css,
    /\.rail-nav\s*\{[\s\S]*?display:\s*flex;[\s\S]*?flex-direction:\s*column;[\s\S]*?gap:\s*12px;/,
  );
  assert.doesNotMatch(css, /\.rail-new\b/);
});
