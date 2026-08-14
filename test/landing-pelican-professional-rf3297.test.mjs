import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const html = readFileSync(resolve(root, 'public/index.html'), 'utf8');
const css = readFileSync(resolve(root, 'public/pelican/assets/css/style.css'), 'utf8');
const js = readFileSync(resolve(root, 'public/pelican/assets/js/custom.js'), 'utf8');

test('R-F3297 drives the Pelican design with ARIA information architecture', () => {
  for (const href of ['#platform', '#capabilities', '#method', '#trust', '#pricing']) {
    assert.match(html, new RegExp(`href="${href}"`));
  }
  assert.match(html, /pelican\/assets\/css\/style\.css/);
  assert.match(html, /Decisions you can trace back to evidence\./);
  assert.doesNotMatch(html, /Pelican|Lorem Ipsum|Albert Rossi|Melissa Vanbergh|Joshua Peterson/);
});

test('R-F3297 uses real project assets instead of template placeholders', () => {
  for (const asset of [
    'public/pelican/assets/images/aria-evidence-hero.png',
    'public/pelican/assets/images/aria-analyst-review.png',
  ]) {
    assert.ok(existsSync(resolve(root, asset)), `${asset} must exist`);
  }
  assert.match(css, /aria-analyst-review\.png/);
  assert.doesNotMatch(html, /assets\/images\/(?:hero|feature)\.png/);
});

test('R-F3297 every local Pelican asset referenced by the page exists', () => {
  const references = [
    ...html.matchAll(/(?:href|src)="(pelican\/assets\/[^"]+)"/g),
  ].map((match) => match[1]);
  assert.ok(references.length >= 10, 'the page should retain the Pelican asset system');
  for (const reference of new Set(references)) {
    assert.ok(existsSync(resolve(root, 'public', reference)), `${reference} must resolve`);
  }
});

test('R-F3297 access form uses the real lead endpoint with honest outcomes', () => {
  assert.match(html, /action="\/api\/leads"/);
  assert.match(html, /name="name"/);
  assert.match(html, /name="email"/);
  assert.match(js, /url:\s*'\/api\/leads'/);
  // R-F4012 (C-89) - was /\.done\(function\(\)/, an EMPTY parameter list. The
  // handler now takes `data` so it can read `verification` and say what actually
  // happened (§22): "check your email" is a lie when the confirmation could not
  // be sent. The guard pinned the signature rather than the contract and went red
  // on a change that made the page MORE honest. What matters is that a done
  // handler exists and the success message follows it.
  assert.match(js, /\.done\(function\(/);
  assert.match(js, /\.fail\(function\(xhr\)/);
  assert.ok(js.indexOf('request has been recorded') > js.indexOf('.done(function('));
});

test('R-F3297 avoids unsupported trust theatre', () => {
  assert.doesNotMatch(html, /GDPR Compliant|Nothing missed|No external dependencies|24\s*\/\s*7/i);
  assert.doesNotMatch(html, /customer review|testimonial|five-star|★★★★★/i);
  assert.match(html, /Uncertainty stays visible\./);
  assert.match(html, /Human judgement stays in control/);
});
