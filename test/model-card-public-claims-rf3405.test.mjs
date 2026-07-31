import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const card = readFileSync(new URL('../public/model-card.html', import.meta.url), 'utf8');

test('public model card removes Gespi and production DeepSeek claims', () => {
  assert.doesNotMatch(card, /gespi/i);
  assert.doesNotMatch(card, /currently DeepSeek/i);
  assert.match(card, /DeepSeek is not a production sub-processor/);
  assert.match(card, /DeepSeek is excluded from production personal-data processing/);
});

test('public model card identifies the verified operator without presenting certification', () => {
  assert.match(card, /Arkmurus Limited \(16028039\)/);
  assert.match(card, /not a certification, warranty, legal opinion, accuracy guarantee/);
  assert.match(card, /“Audit-grade” is not a regulatory certification/);
});

test('audit claims distinguish tamper evidence from truth and service verification', () => {
  assert.match(card, /Tamper evidence, not truth certification/);
  assert.match(card, /service-mediated verification, not an independent digital signature/);
  assert.match(card, /does not allow independent HMAC verification without access to that secret/);
  assert.doesNotMatch(card, /Verifiable independently/);
  assert.doesNotMatch(card, /Every output ARIA produces is appended/);
});

test('public model card exposes no WhatsApp inventory and lazy-loads its authenticated manager', () => {
  assert.doesNotMatch(card, /\/api\/wa-listener\/accounts/);
  assert.doesNotMatch(card, /id="wa-accounts"/);
  assert.match(card, /data-src="\/wa-connections\.html"/);
  assert.doesNotMatch(card, /<iframe[^>]*\ssrc="\/wa-connections\.html"/);
  assert.match(card, /available only to authenticated users/);
});

test('public adversarial metric includes its sample size or says it is unavailable', () => {
  assert.match(card, /last\.total_attacks/);
  assert.match(card, /last\.passed/);
  assert.match(card, /sample size unavailable/);
});
