// test/upload-tier-limit-rf3988.test.mjs
//
// R-F3988 (C-73) — the web upload cap ignored the caller's billing tier.
//
// THE DEFECT, exactly. `tiers.mjs` defines `uploadBytesMax` per tier — 5 MB for
// free and pro, 50 MB for proIntel — and `/api/billing/me` reports it, so the
// figure is shown to the customer and the public pricing page sells it
// ("5 MB document uploads" / "50 MB document uploads"). The route that actually
// enforces it compared Content-Length against ONE hardcoded literal:
//
//     server.mjs:1636   if (Number(req.headers['content-length']) > 25 * 1024 * 1024)
//
// So the number was never the tier's. A Pro Intel customer paying £199/mo for
// 50 MB was refused at 25 MB — a paid feature that did not exist — while a free
// account sold 5 MB could send 25 MB. Both directions were wrong at once, which
// is why neither showed up as a complaint: nobody hits a limit that is too
// generous, and the one customer who hits the low ceiling assumes it is theirs.
//
// This is the same class as R-F2765 (caps DEFINED but never CHECKED) and of the
// three Phase A gates CLAUDE.md §1 records as "certified by an absence": a value
// that is displayed as though it governs, while the code consults something else.
//
// These tests assert the PROPERTY — the enforced limit IS the advertised limit —
// at the layer that decides it, rather than re-testing one route's wiring. The
// server.mjs assertion at the end is deliberately a bounded source read, for the
// reason R-F3618 gives: server.mjs boots a live app on import.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const { TIERS, DEFAULT_TIER, getTier } = await import('../lib/billing/tiers.mjs');
const {
  uploadLimitBytesFor,
  maxRequestBytesFor,
  uploadTooLarge,
  effectiveUploadLimit,
  DOWNSTREAM_MAX_BODY_BYTES,
  MULTIPART_ENVELOPE_ALLOWANCE_BYTES,
} = await import('../lib/billing/uploadLimit.mjs');

const serverSrc = fs.readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
const MB = 1024 * 1024;

describe('R-F3988 — the enforced upload limit is the advertised one', () => {

  it('THE DEFECT: a paid tier is allowed a strictly larger upload than free', () => {
    // The whole bug in one assertion. Under the hardcoded 25 MB these were EQUAL,
    // so the £199 tier's headline upload benefit did not exist.
    const free = uploadLimitBytesFor('free');
    const top = uploadLimitBytesFor('proIntel');
    assert.ok(top > free,
      `proIntel (${top}) must be allowed more than free (${free}) — it is sold as 50 MB`);
  });

  it('every tier enforces what it is sold — or says plainly that it cannot', () => {
    // Reads the tier table rather than repeating its numbers: if a price change
    // moves uploadBytesMax, enforcement follows it and this test still holds.
    //
    // The escape hatch is deliberate and narrow. proIntel is sold 50 MB of FILE
    // while the brain caps the whole REQUEST at 50 MB, so that tier CANNOT be
    // delivered in full today. The honest options were to under-deliver silently
    // (the defect being fixed) or to under-deliver visibly. This asserts the
    // second: enforcement equals the advertised figure unless the chain cannot
    // carry it, and in that case the shortfall is reported rather than absorbed.
    for (const [id, tier] of Object.entries(TIERS)) {
      const { advertisedBytes, effectiveBytes, constrainedByDownstream } = effectiveUploadLimit(id);
      assert.equal(advertisedBytes, tier.uploadBytesMax, `${id}: advertised figure must come from the tier table`);
      if (constrainedByDownstream) {
        assert.ok(effectiveBytes < advertisedBytes,
          `${id}: constrained must mean it really is smaller`);
      } else {
        assert.equal(effectiveBytes, tier.uploadBytesMax,
          `${id} is sold ${tier.uploadBytesMax} bytes and must be enforced at that number`);
      }
    }
  });

  it('a tier we cannot fully deliver is FLAGGED, never silently shortened', () => {
    // The whole point of the previous test's escape hatch. If effective drops
    // below advertised for any reason — this ceiling, a future one, a lowered
    // ARIA_MAX_BODY_BYTES — it must be readable as a value, because a shortfall
    // nobody can see is how the original defect survived.
    for (const id of Object.keys(TIERS)) {
      const { advertisedBytes, effectiveBytes, constrainedByDownstream } = effectiveUploadLimit(id);
      assert.equal(constrainedByDownstream, effectiveBytes < advertisedBytes,
        `${id}: the flag must track the arithmetic exactly, in both directions`);
    }
    // And the refusal carries it, so a support question can be answered from the
    // response alone rather than by reading two services' configuration.
    const verdict = uploadTooLarge(Number.MAX_SAFE_INTEGER, 'proIntel');
    assert.ok(verdict, 'an absurd upload must be refused');
    assert.equal(typeof verdict.constrainedByDownstream, 'boolean');
    assert.equal(verdict.limitBytes, TIERS.proIntel.uploadBytesMax);
  });

  it('a file of exactly the advertised size is ACCEPTED, not refused by envelope overhead', () => {
    // Content-Length measures the whole multipart REQUEST — boundary, headers and
    // filename — which is strictly larger than the file. Comparing the request
    // against the FILE limit would refuse a document of exactly the advertised
    // size, i.e. reintroduce "sold N, delivers less than N" one boundary at a time.
    for (const id of Object.keys(TIERS)) {
      const fileBytes = uploadLimitBytesFor(id);
      const envelope = fileBytes + 512;   // a realistic multipart envelope
      assert.equal(uploadTooLarge(envelope, id), null,
        `${id}: a file at exactly the advertised limit must survive its own envelope`);
    }
  });

  it('one byte of FILE over the tier limit is refused, with the tier limit reported', () => {
    const overBy = MULTIPART_ENVELOPE_ALLOWANCE_BYTES + 1;
    for (const id of Object.keys(TIERS)) {
      const verdict = uploadTooLarge(uploadLimitBytesFor(id) + overBy, id);
      assert.ok(verdict, `${id}: an oversized upload must be refused`);
      assert.equal(verdict.limitBytes, TIERS[id].uploadBytesMax,
        `${id}: the refusal must quote the tier's own limit, not a global constant`);
    }
  });

  it('never forwards a body the brain will refuse with an opaque 413', () => {
    // The brain caps request bodies at ARIA_MAX_BODY_BYTES (default 50 MB,
    // main.py `_limit_body_size`). proIntel is sold 50 MB of FILE, so file +
    // envelope exceeds that ceiling: without a clamp the top tier's largest
    // uploads would pass this route and die downstream as a bare
    // "request body too large", with nothing telling the user which limit they
    // hit. Clamping keeps the refusal here, where it can name the real number.
    for (const id of Object.keys(TIERS)) {
      assert.ok(maxRequestBytesFor(id) <= DOWNSTREAM_MAX_BODY_BYTES,
        `${id}: accepted request size must not exceed what the brain accepts`);
    }
  });

  it('an unknown, missing or malformed tier falls back to free — never to the largest', () => {
    const freeLimit = uploadLimitBytesFor('free');
    for (const bad of [undefined, null, '', 'enterprise', 'PROINTEL', 0, {}]) {
      assert.equal(uploadLimitBytesFor(bad), freeLimit,
        `a ${JSON.stringify(bad)} tier must not inherit a paid allowance`);
    }
    assert.equal(getTier(DEFAULT_TIER).uploadBytesMax, freeLimit);
  });

  it('an absent Content-Length is not treated as zero bytes', () => {
    // A chunked request carries no Content-Length, so `Number(undefined) > limit`
    // is false and the old guard passed it through unmeasured. That bypass is
    // tracked separately (C-76 / F-05) and is NOT fixed here — one defect per
    // change. What this pins is that the new code does not silently CERTIFY an
    // unmeasurable body as being within the limit: it must report unknown.
    const verdict = uploadTooLarge(undefined, 'free');
    assert.notEqual(verdict, null,
      'an unmeasurable body must not be certified as within the limit');
    assert.equal(verdict.reason, 'length_unknown');
  });

  it('server.mjs resolves the limit per tier instead of a hardcoded literal', () => {
    // Bounded source read: server.mjs boots a live app on import (R-F3618 gives
    // the same reason). The property is that the route no longer decides the
    // limit itself.
    const start = serverSrc.indexOf("app.post('/api/aria/extract-document'");
    assert.ok(start > 0, 'the extract-document route should exist');
    const body = serverSrc.slice(start, start + 2600);
    assert.match(body, /uploadTooLarge\(/,
      'the route must ask the tier-aware helper for the verdict');

    // Strip comments before looking for the literal. The first version of this
    // guard matched `25 * 1024 * 1024` inside the comment that EXPLAINS what was
    // removed, so it failed on a correct fix — a guard testing prose rather than
    // code. Same class as R-F3858, where a line-window heuristic reported a wired
    // backend as dark. The documentation is worth keeping; the guard is what had
    // to get smarter.
    // Drop whole-line comments only. Three traps, all hit while writing this:
    //  1. CRLF checkout (CLAUDE.md §16) — `.` does not match `\r`, so a
    //     `//.*$` stripper matches NOTHING and leaves every comment in place.
    //  2. Stripping from the first `//` anywhere truncates any line holding a URL.
    //  3. A `/\*[\s\S]*?\*\//` block-comment strip is not a parser: a `/*` inside
    //     a string starts a span running to the next `*/`. Measured on server.mjs
    //     it removed 122,623 characters of real code.
    // A line either IS a comment or it is not; that test cannot swallow code.
    const code = body
      .split(/\r?\n/)
      .filter(l => !l.trim().startsWith('//'))
      .join('\n');
    assert.doesNotMatch(code, /25 \* 1024 \* 1024/,
      'the hardcoded 25 MB literal must be gone from the CODE — it was the defect');
  });
});
