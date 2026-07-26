// R-F3160 — the published privacy policy must match what the code does.
//
// A privacy notice is a legal representation to data subjects. When it drifts
// from the implementation it stops being a notice and becomes a misstatement,
// and the drift is invisible because nothing tests prose against behaviour.
//
// These assertions are deliberately paired: each checks a CLAIM in the policy
// AND the code that makes it true, so neither can move alone.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..',
);
const read = (...p) => readFileSync(path.join(ROOT, ...p), 'utf8');

const PRIVACY = read('public', 'about', 'privacy.html');
const TERMS = read('public', 'about', 'terms.html');
const PROCESSORS = read('aria_service', 'vetting', 'processors.py');
const LEGAL_BASIS = read('aria_service', 'vetting', 'legal_basis.py');
const MODELS = read('aria_service', 'vetting', 'models.py');
const CRYPTO = read('aria_service', 'vetting', 'crypto.py');

test('R-F3160 the policy discloses Art. 10 criminal-offence processing', () => {
  assert.match(PRIVACY, /criminal-conviction and offence data/i,
    'the module processes Art. 10 data; the policy must say so');
  assert.match(PRIVACY, /Schedule 1/,
    'the policy must name the DPA 2018 Schedule 1 authorisation');
});

test('R-F3160 the "consent is not used" claim matches the code', () => {
  assert.match(PRIVACY, /[Cc]onsent is not used and is not offered/,
    'the policy claims consent is not an available basis');
  // ...and the type genuinely omits it.
  const literal = /LawfulBasisLiteral\s*=\s*Literal\[([\s\S]*?)\]/.exec(MODELS);
  assert.ok(literal, 'LawfulBasisLiteral must exist');
  assert.ok(!/CONSENT/.test(literal[1]),
    'the policy says consent is not offered, but the code accepts it');
});

test('R-F3160 the "refuses to hold conviction data" claim is enforced', () => {
  assert.match(PRIVACY, /refuse to hold conviction data|refuses to store conviction data/i,
    'the policy claims the system refuses conviction data without a basis');
  assert.match(LEGAL_BASIS, /class\s+Sch1Condition/,
    'the Schedule 1 conditions must be modelled in code');
  assert.match(LEGAL_BASIS, /appropriate policy document/i,
    'the APD requirement must be enforced, not just documented');
  assert.match(LEGAL_BASIS, /def\s+validate_position/,
    'there must be a validator the routes can call');
});

test('R-F3160 the vetting transfer carve-out matches the code', () => {
  assert.match(PRIVACY, /fails closed/i,
    'the policy claims the vetting extraction path fails closed');
  assert.match(PRIVACY, /never sent to the general LLM chain|carved out/i,
    'the policy must state vetting data is carved out of the general chain');
  // The code must actually build an approved-only provider, not a chain.
  assert.match(PROCESSORS, /def\s+resolve_vetting_processor/,
    'there must be a dedicated vetting processor resolver');
  assert.match(PROCESSORS, /_DEFAULT_APPROVED\s*=\s*"anthropic"/,
    'the default approved processor must be anthropic');
  // Precise, not a text sweep: the module's docstring legitimately NAMES
  // deepseek to explain why it is excluded. What must hold is that no
  // credential mapping exists for it, so it cannot be constructed even if
  // someone adds it to ARIA_VETTING_LLM_PROVIDERS.
  const keyMap = /_API_KEY_ENV[^=]*=\s*\{([\s\S]*?)\n\}/.exec(PROCESSORS);
  assert.ok(keyMap, '_API_KEY_ENV must exist');
  assert.ok(!/deepseek/i.test(keyMap[1]),
    'deepseek must have no credential mapping, so it can never be built '
    + 'as a vetting processor');
});

test('R-F3160 the erasure claim matches the crypto-shredding implementation', () => {
  assert.match(PRIVACY, /encrypted with a per-case key/i,
    'the policy claims per-case encryption');
  assert.match(PRIVACY, /destroys that key/i,
    'the policy claims disposal destroys the key');
  assert.match(CRYPTO, /AESGCM/,
    'the crypto module must implement authenticated encryption');
});

test('R-F3160 the retention periods stated match the shipped pack', () => {
  assert.match(PRIVACY, /12 months for unsuccessful/i);
  assert.match(PRIVACY, /7 years from the end of employment/i);
  const builtin = read('aria_service', 'vetting', 'packs', 'builtin.py');
  assert.match(builtin, /retention_unsuccessful_months=12/,
    'the policy says 12 months; the UK pack must declare it');
  assert.match(builtin, /retention_post_employment_years=7/,
    'the policy says 7 years; the UK pack must declare it');
});

test('R-F3160 no published page still points at the retired status page', () => {
  for (const [name, src] of [['privacy.html', PRIVACY], ['terms.html', TERMS]]) {
    assert.ok(!/href="\/status\.html"/.test(src),
      `${name} still links to /status.html, which R-F3142 retired`);
  }
});

test('R-F3160 the controller/processor split is stated for vetting', () => {
  assert.match(PRIVACY, /you are the data controller/i,
    'the policy must state the customer is controller for vetting data');
  assert.match(PRIVACY, /privacy notice \(Arts?\. 13-14\)|Arts\. 13-14/,
    'the controller-side transparency duty must be stated explicitly');
});
