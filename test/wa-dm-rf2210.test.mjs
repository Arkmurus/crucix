// test/wa-dm-rf2210.test.mjs
//
// Guard test for R-F2210 — 1:1 DM support in the canonical WhatsApp listener.
// The real path needs a live Baileys socket, so this locks the SOURCE contract
// of the routing gate (the class of behaviour, per the same convention as
// wa-listener-delivery-robustness-rf2069.test.mjs): a DM must no longer be
// dropped before handling, must be gated by WA_DM_ENABLED, and must count as an
// implicit mention so plain text + media reach the chat path.
//
// Run: node test/wa-dm-rf2210.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(__dirname, '..', 'services', 'wa-listener', 'aria_wa_listener.mjs'),
  'utf8',
);

let failures = 0;
function check(name, cond) {
  console.log(`${cond ? 'ok  ' : 'FAIL'} - ${name}`);
  if (!cond) failures++;
}

// ── The old hard-drop is gone ─────────────────────────────────────────────────
check('the group-only hard-drop (drop every non-@g.us chat) is removed',
  !SRC.includes("if (!chatId.endsWith('@g.us')) continue;"));

// ── The DM flag exists, default ON, env-reversible ────────────────────────────
check('WA_DM_ENABLED flag exists and defaults ON (env-reversible)',
  /const WA_DM_ENABLED = \(process\.env\.WA_DM_ENABLED \|\| 'true'\)\.toLowerCase\(\) === 'true'/.test(SRC));

// ── The new gate: groups always, DMs only when enabled, others dropped ────────
check('routing computes _isGroup / _isDM',
  SRC.includes("const _isGroup = chatId.endsWith('@g.us')") &&
  SRC.includes("const _isDM    = chatId.endsWith('@s.whatsapp.net')"));
// R-F3582 — assert the PROPERTY, not the exact one-liner. This pinned the
// literal `if (!_isGroup && !(WA_DM_ENABLED && _isDM)) continue;`, so adding a
// log line to the drop branch failed it while the routing rule was untouched.
// The rule this guards is "non-group non-DM jids are dropped, and DMs pass only
// when WA_DM_ENABLED" — the CONDITION plus a continue, however the body is
// written. Pinning the wording turns any comment or diagnostic into a red build
// and teaches people to edit the test instead of thinking.
check('non-group non-DM jids still dropped; DMs pass only when WA_DM_ENABLED',
  /if\s*\(!_isGroup\s*&&\s*!\(WA_DM_ENABLED\s*&&\s*_isDM\)\)/.test(SRC) &&
  /if\s*\(!_isGroup\s*&&\s*!\(WA_DM_ENABLED\s*&&\s*_isDM\)\)[\s\S]{0,1200}?continue;/.test(SRC));
check('TARGET_GROUPS filter no longer gates DMs (group-scoped)',
  SRC.includes('if (_isGroup && TARGET_GROUPS.length'));

// ── A DM is treated as an implicit mention (text + media reach the chat path) ─
check('_ariaCalled is true for a DM (media path proceeds)',
  SRC.includes("MENTIONS_RE.some((p) => p.test(text || '')) || (WA_DM_ENABLED && _isDM)"));
check('mention/chat path fires for a DM without the name',
  SRC.includes('(_isVoiceNote && VOICE_ALWAYS_REPLY) || (WA_DM_ENABLED && _isDM)'));

console.log(failures === 0 ? '\nR-F2210 tests: PASS' : `\nR-F2210 tests: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
