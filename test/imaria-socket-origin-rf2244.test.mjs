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
check('legacy intel.arkmurus.com still trusted (migration)', SERVER.includes("add('https://intel.arkmurus.com')"));
console.log(failures === 0 ? '\nR-F2244: PASS' : `\nR-F2244: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
