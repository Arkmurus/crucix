/**
 * R-F3312 — the hero photo must occupy EXACTLY the box the old one did.
 *
 * The operator asked for the hero image to be swapped and to stay proportional.
 * The hero <img> carries `img-fluid` (`max-width:100%; height:auto`), so the
 * rendered height is decided by the file's INTRINSIC aspect ratio, not by CSS.
 * Dropping in the square source (4587x4587) would have made the hero column
 * ~16% taller and unbalanced it against the headline beside it. So the assertion
 * that matters is on the bytes on disk, not on the markup: the new asset's
 * intrinsic dimensions must equal the old hero's.
 *
 * Dimensions are read from the file headers directly. Adding an image library to
 * the test suite for two header reads would be a heavier dependency than the
 * thing it verifies.
 */
import assert from 'node:assert/strict';
import { readFileSync, existsSync, statSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const IMAGES = join('public', 'pelican', 'assets', 'images');
const INDEX = readFileSync(join('public', 'index.html'), 'utf8');

const OLD_HERO = join(IMAGES, 'aria-evidence-hero.png');
const NEW_HERO = join(IMAGES, 'aria-hero-analyst.jpg');

/** PNG: width/height are big-endian uint32 at offset 16 in the IHDR chunk. */
function pngSize(buf) {
  assert.ok(buf.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])), 'not a PNG');
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

/** JPEG: walk the segment chain to a start-of-frame marker and read its size. */
function jpegSize(buf) {
  assert.equal(buf.readUInt16BE(0), 0xffd8, 'not a JPEG');
  let i = 2;
  while (i < buf.length - 9) {
    if (buf[i] !== 0xff) { i += 1; continue; }
    const marker = buf[i + 1];
    // SOF0..SOF15, excluding the non-frame markers DHT(c4), JPG(c8), DAC(cc)
    if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
      return { height: buf.readUInt16BE(i + 5), width: buf.readUInt16BE(i + 7) };
    }
    i += 2 + buf.readUInt16BE(i + 2);
  }
  throw new Error('no JPEG start-of-frame found');
}

test('R-F3312 the new hero renders in exactly the old hero box', () => {
  assert.ok(existsSync(NEW_HERO), `${NEW_HERO} must exist`);
  assert.ok(existsSync(OLD_HERO), 'the previous hero is still used by the capabilities section');

  const before = pngSize(readFileSync(OLD_HERO));
  const after = jpegSize(readFileSync(NEW_HERO));

  assert.deepEqual(
    after, before,
    `the hero changed shape: was ${before.width}x${before.height}, now ${after.width}x${after.height}. `
    + 'img-fluid sets height:auto, so a different intrinsic ratio resizes the hero column.',
  );
  // stated separately so a future edit that changes BOTH files still fails loudly
  const ratio = after.width / after.height;
  assert.ok(Math.abs(ratio - 1353 / 1163) < 0.001, `hero aspect ${ratio.toFixed(5)} drifted from 1.16337`);
});

test('R-F3312 the hero is wired to the new asset and declares its size', () => {
  const hero = INDEX.match(/<img class="img-fluid hero-visual"[^>]*>/);
  assert.ok(hero, 'the hero <img> is gone or was renamed');
  assert.match(hero[0], /src="pelican\/assets\/images\/aria-hero-analyst\.jpg"/);
  // width/height let the browser reserve the box before the bytes arrive, so the
  // hero does not jump on load.
  assert.match(hero[0], /width="1353"/);
  assert.match(hero[0], /height="1163"/);
  // the alt text must describe THIS photo, not the illustration it replaced
  assert.doesNotMatch(hero[0], /Evidence sources connected/,
    'the alt text still describes the previous illustration');
  assert.match(hero[0], /alt="[^"]{15,}"/, 'the hero needs real alt text');
});

test('R-F3312 the swap did not disturb the capabilities section', () => {
  // aria-evidence-hero.png is used twice; only the hero was meant to change.
  const uses = [...INDEX.matchAll(/aria-evidence-hero\.png/g)];
  assert.equal(uses.length, 1, 'the previous hero should remain in exactly one place');
  assert.match(INDEX, /class="ar-img img-fluid" src="pelican\/assets\/images\/aria-evidence-hero\.png"/,
    'the capabilities illustration lost its image');
});

test('R-F3312 the hero is not a page-weight regression', () => {
  // The landing's largest asset sits above the fold. The PNG it replaces is
  // 1.6MB; a stock JPEG straight off the download would have been 4.4MB.
  const bytes = statSync(NEW_HERO).size;
  assert.ok(bytes < 400 * 1024, `hero is ${Math.round(bytes / 1024)}KB — too heavy for above the fold`);
  assert.ok(bytes < statSync(OLD_HERO).size, 'the new hero should not be heavier than the one it replaces');
});
