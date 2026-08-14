// test/upload-chunked-bypass-rf3997.test.mjs
//
// R-F3997 (C-78) — a chunked upload bypassed the size guard entirely.
//
// THE DEFECT. The guard measured the Content-Length HEADER and nothing else:
//
//     if (Number(req.headers['content-length']) > LIMIT) return 413;
//
// A chunked request carries no Content-Length. `Number(undefined)` is `NaN`, and
// `NaN > LIMIT` is **false**, so the request passed — and the body was then piped
// straight to the brain with `Readable.toWeb(req)`, unmeasured and unbounded. Any
// client that omits the header, which is a one-line change with curl or fetch,
// uploaded an arbitrarily large body through a route whose whole purpose is to
// cap it.
//
// It is the same absence-reads-as-compliance shape as the §1 Phase A gates: a
// check that cannot see its subject reports success. R-F3988 narrowed the blast
// radius by refusing to CERTIFY an unmeasurable body (`reason: 'length_unknown'`)
// but deliberately left the behaviour alone — one defect per change. This is that
// change.
//
// THE FIX IS TO MEASURE, NOT TO REFUSE. Rejecting every request without a
// Content-Length would bound the body and break legitimate streaming clients, and
// it would still be measuring the wrong thing: the header is a claim, and the
// bytes are the fact. A counting Transform sits in the pipe and aborts the moment
// the real total passes the tier limit — so a LYING Content-Length (small header,
// large body) is caught by the same code, which the header check never could.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';

const { createUploadMeter, maxRequestBytesFor } = await import('../lib/billing/uploadLimit.mjs');
const serverSrc = fs.readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');

function codeOf(src) {
  return src.split(/\r?\n/).filter(l => !l.trim().startsWith('//')).join('\n');
}

/**
 * The body of one route, bounded by the route itself rather than by a character
 * count.
 *
 * The first version of this file sliced `start, start + 4200` and went red on a
 * CORRECT fix: documenting the change pushed `createUploadMeter(` to offset 4881,
 * outside the window, so the guard reported the route as unwired while it was
 * wired. That is precisely R-F3858, where an 80-line window reported a wired
 * search backend as dark because the function had grown — and it is the third
 * fixed-window/text heuristic to misfire in this workstream. A guard must not
 * fail because the code it inspects acquired comments.
 */
function routeBody(src, marker) {
  const start = src.indexOf(marker);
  if (start < 0) return '';
  const end = src.indexOf('\n});', start);
  return codeOf(src.slice(start, end < 0 ? src.length : end));
}

/** Drive `chunks` through a meter and report what happened. */
async function run(limit, chunks) {
  const meter = createUploadMeter(limit);
  const out = [];
  try {
    await pipeline(
      Readable.from(chunks),
      meter.stream,
      async function* (src) { for await (const c of src) out.push(c); },
    );
    return { threw: false, exceeded: meter.exceeded(), bytes: out.reduce((n, c) => n + c.length, 0) };
  } catch (e) {
    return { threw: true, exceeded: meter.exceeded(), error: e, bytes: out.reduce((n, c) => n + c.length, 0) };
  }
}

describe('R-F3997 — the upload body is measured, not just its header', () => {

  it('a body within the limit passes through byte-for-byte', () => {
    return run(1000, [Buffer.alloc(300, 1), Buffer.alloc(400, 2)]).then(r => {
      assert.equal(r.threw, false, 'a legal upload must not be interrupted');
      assert.equal(r.exceeded, false);
      assert.equal(r.bytes, 700, 'the meter must not alter the payload');
    });
  });

  it('THE DEFECT: a body over the limit is aborted even with NO Content-Length', () => {
    // The bypass, reproduced at the layer that now closes it. The meter never
    // consults a header — it counts what actually arrives.
    return run(1000, [Buffer.alloc(600, 1), Buffer.alloc(600, 2)]).then(r => {
      assert.equal(r.threw, true, 'an oversized body must abort the stream');
      assert.equal(r.exceeded, true, 'the meter must report WHY it aborted');
    });
  });

  it('a LYING Content-Length cannot help — the bytes are the fact', () => {
    // The header check could never catch this: declare 10 bytes, send 5000. Only
    // counting the stream sees it.
    return run(1000, [Buffer.alloc(5000, 7)]).then(r => {
      assert.equal(r.threw, true);
      assert.equal(r.exceeded, true);
    });
  });

  it('aborts EARLY — it does not buffer the whole body first', () => {
    // A guard that reads everything before deciding has already paid the cost it
    // exists to avoid. The meter must stop mid-stream, so the bytes forwarded
    // stay bounded by roughly the limit rather than the payload.
    return run(1000, Array.from({ length: 50 }, () => Buffer.alloc(1000, 9))).then(r => {
      assert.equal(r.threw, true);
      assert.ok(r.bytes <= 2000,
        `expected to abort near the limit, forwarded ${r.bytes} bytes of 50000`);
    });
  });

  it('the limit is the TIER limit, not a constant', () => {
    // Ties this to R-F3988: the meter is constructed from the same per-tier
    // number, so the two guards cannot drift apart.
    assert.ok(maxRequestBytesFor('proIntel') > maxRequestBytesFor('free'),
      'the meter budget must still differ by tier');
  });

  it('a zero or negative budget is rejected as a programming error, not silently infinite', () => {
    // A meter built with a falsy limit that then allows everything would be the
    // original defect wearing a new name.
    for (const bad of [0, -1, NaN, undefined, null]) {
      assert.throws(() => createUploadMeter(bad), /limit/i,
        `createUploadMeter(${String(bad)}) must refuse rather than allow everything`);
    }
  });

  it('the upload route pipes the request THROUGH the meter', () => {
    // Bounded source read: server.mjs boots a live app on import (R-F3618).
    const route = routeBody(serverSrc, "app.post('/api/aria/extract-document'");
    assert.ok(route, 'the extract-document route should exist');
    assert.match(route, /createUploadMeter\(/,
      'the route must build a meter for the caller');
    assert.doesNotMatch(route, /Readable\.toWeb\(\s*req\s*\)/,
      'the raw request must NOT be piped straight upstream — that is the bypass');
    assert.match(route, /413/, 'an over-limit stream must still surface as 413');
  });
});
