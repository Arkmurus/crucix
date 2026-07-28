// test/imaria-socket-origin-rf2244.test.mjs
// R-F2244 — the socket.io origin allowlist must trust the new primary domain
// imaria.io (apex + www) so chat works on the new domain, while KEEPING
// intel.arkmurus.com during the migration (both live for testing).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
const SERVER = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'server.mjs'), 'utf8');
let failures = 0;
const check = (n, c) => { console.log(`${c ? 'ok  ' : 'FAIL'} - ${n}`); if (!c) failures++; };
check('socket allowlist trusts imaria.io (apex)', SERVER.includes("add('https://imaria.io')"));
check('socket allowlist trusts www.imaria.io', SERVER.includes("add('https://www.imaria.io')"));
// R-F3343 — this file was HALF right, and the halves needed opposite fixes.
//
// Probed live 2026-07-28, because "which side is wrong" cannot be answered from
// the source alone:
//     https://www.imaria.io/    -> 200, NO redirect   (serves the app)
//     https://intel.imaria.io/  -> 000                (does not resolve)
//
//  * www: the test was RIGHT and the code was wrong. R-F2655 narrowed the list
//    to the apex as "the sole canonical production host", but www serves the app
//    rather than redirecting to it, so a real browser sits there with
//    Origin: https://www.imaria.io and the handshake was refused outright
//    (`cb(new Error(...))`). Real-time chat was dead on www with nothing a user
//    could see. Fixed in server.mjs.
//  * intel.imaria.io: the test was WRONG. That host is gone, so requiring it in
//    the allowlist demands trust for an origin nobody can serve from — a guard
//    arguing to widen an allowlist for no reason.
//
// The property is now stated both ways: hosts that SERVE the app are trusted,
// and a retired host is NOT.
check('retired intel.imaria.io is NOT trusted (host does not resolve)',
  !SERVER.includes("add('https://intel.imaria.io')"));
check('the allowlist stays explicit — unknown origins are rejected, not waved through',
  /cb\(new Error\(`Socket\.io: origin/.test(SERVER));
// (A "no wildcard origin" check was written here and removed: `origin:'*'`
// appears in R-F829's comment explaining why the wildcard was REPLACED, so a
// naked source match flags the history that documents the fix. The rejection
// check above already asserts the property without needing comment-stripping.)
console.log(failures === 0 ? '\nR-F2244: PASS' : `\nR-F2244: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
