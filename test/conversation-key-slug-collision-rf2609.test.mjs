// test/conversation-key-slug-collision-rf2609.test.mjs
// R-F2609 — the conversation-history bucket key (conversationKeyForUser) is a lossy
// [^A-Za-z0-9] slug, so two DISTINCT emails can share one bucket and read/delete each
// other's chat + DD history. We do NOT change the key (that would orphan every existing
// conversation across Node + browser + the Python brain — a migration, not a fix); we
// enforce slug-injectivity at registration so no two accounts can ever share a bucket.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { conversationKeyForUser, slugifyIdentity } from '../lib/auth/conversationKey.mjs';

const SERVER = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'server.mjs'), 'utf-8');

describe('R-F2609 — conversation-bucket slug collision', () => {
  it('documents the collision the guard defends against (distinct emails, same bucket)', () => {
    const a = conversationKeyForUser({ email: 'john.doe@acme.com' });
    const b = conversationKeyForUser({ email: 'johndoe@acme.com' });
    assert.equal(a, b, 'dot-variant emails collide to one bucket key — this is the risk');
    assert.equal(a, 'johndoeacmecom');
    // sanity: genuinely different local parts do NOT collide
    assert.notEqual(conversationKeyForUser({ email: 'alice@acme.com' }),
                    conversationKeyForUser({ email: 'bob@acme.com' }));
  });

  it('slugifyIdentity is the lossy transform (documents why the guard is needed)', () => {
    assert.equal(slugifyIdentity('a.b+c@x.com'), 'abcxcom');
  });

  it('registration refuses a signup whose bucket key collides with an existing account', () => {
    // Source-parse lock: the register handler computes the new bucket key and blocks on
    // collision (folded into the anti-enumeration generic-success path).
    assert.ok(/const _newBucketKey = slugifyIdentity\(email\)/.test(SERVER),
      'register must compute the new bucket key');
    assert.ok(/const slugCollides =[\s\S]*conversationKeyForUser\(u\) === _newBucketKey/.test(SERVER),
      'register must detect an existing account sharing the new bucket key');
    assert.ok(/if \(emailExists \|\| usernameExists \|\| slugCollides\)/.test(SERVER),
      'a slug collision must block account creation like an email/username collision');
  });
});
