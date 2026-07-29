import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const privacy = readFileSync(new URL('../public/about/privacy.html', import.meta.url), 'utf8');
const terms = readFileSync(new URL('../public/about/terms.html', import.meta.url), 'utf8');

test('privacy notice identifies the legal controller and required contact details', () => {
  assert.match(privacy, /Operator and controller: Arkmurus Limited/);
  assert.match(privacy, /company number <strong>16028039<\/strong>/);
  assert.match(privacy, /71-75 Shelton Street/);
  assert.match(privacy, /support@imaria\.io/);
  assert.doesNotMatch(privacy, /To be determined|DRAFT:|ARIA Intelligence Limited/);
});

test('privacy notice covers core UK GDPR and PECR transparency information', () => {
  for (const requiredText of [
    'UK GDPR lawful basis',
    'right to object',
    'International Data Transfer Agreement',
    'Cookies and browser storage',
    'Automated processing and AI',
    'within one month',
    'without undue delay',
  ]) {
    assert.ok(privacy.includes(requiredText), `missing privacy disclosure: ${requiredText}`);
  }
});

test('privacy notice excludes DeepSeek from production personal-data processing', () => {
  assert.match(privacy, /DeepSeek is not an ARIA production sub-processor/);
  assert.match(privacy, /must not receive production customer prompts, documents, account data or other personal data/);
  assert.doesNotMatch(privacy, /DeepSeek<\/strong><\/td><td>Primary LLM provider/);
});

test('terms use the same verified operator and contracting entity', () => {
  assert.match(terms, /Operator: Arkmurus Limited/);
  assert.match(terms, /company number 16028039/);
  assert.match(terms, /71-75 Shelton Street/);
  assert.doesNotMatch(terms, /ARIA Intelligence Limited|To be determined|DRAFT:/);
});
