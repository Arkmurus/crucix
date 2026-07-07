import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..', '..');
const landing = readFileSync(join(repoRoot, 'public', 'index.html'), 'utf8');
const manifest = JSON.parse(
  readFileSync(join(repoRoot, 'public', 'capability-claims.json'), 'utf8'),
);

const forbiddenPhrases = [
  'No external dependencies',
  'Sovereign LLM  AUTO',
  'Nothing missed',
  'Every finding. <em>Fully traced.</em>',
  'GDPR Compliant',
];

const requiredLandingPhrases = [
  'ARIA is being built as a sovereign-grade defence intelligence platform.',
  'evidence-graded reports',
  'source health and confidence visible',
  'Gaps surfaced',
  'Evidence graded',
];

function fail(message) {
  console.error(`landing claim truth failed: ${message}`);
  process.exitCode = 1;
}

for (const phrase of forbiddenPhrases) {
  if (landing.includes(phrase)) {
    fail(`unsupported absolute phrase remains: ${phrase}`);
  }
}

for (const phrase of requiredLandingPhrases) {
  if (!landing.includes(phrase)) {
    fail(`required honest framing missing: ${phrase}`);
  }
}

if (!Array.isArray(manifest.claims) || manifest.claims.length < 5) {
  fail('capability-claims.json must contain at least 5 claim records');
}

const allowedStatuses = new Set([
  'live',
  'live_hardening',
  'live_degraded',
  'conditional',
  'roadmap',
]);

for (const claim of manifest.claims || []) {
  for (const key of ['id', 'label', 'status', 'public_wording']) {
    if (!claim[key]) fail(`claim missing ${key}: ${JSON.stringify(claim)}`);
  }
  if (!allowedStatuses.has(claim.status)) {
    fail(`claim ${claim.id} has invalid status ${claim.status}`);
  }
  if (!Array.isArray(claim.evidence) || claim.evidence.length === 0) {
    fail(`claim ${claim.id} has no evidence references`);
  }
  if (!Array.isArray(claim.required_to_call_complete) || claim.required_to_call_complete.length === 0) {
    fail(`claim ${claim.id} has no completion requirements`);
  }
}

if (!process.exitCode) {
  console.log(`landing claim truth passed: ${manifest.claims.length} claims checked`);
}
