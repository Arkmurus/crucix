// ARIA WA capability policy — intent must survive being addressed, and privilege
// must key on the bound account, never on a handset.
//
// The defect this pins: lib/whatsapp/ariaWhatsApp.mjs (legacy Twilio handler)
// returns on the FIRST match —
//
//     if (COMMAND_RE.test(t))            return 'command';
//     if (MENTIONS.some(p => p.test(t))) return 'mention';
//     if (isDirectRequest(t))            return 'request';
//
// so "aria, run a DD on Acme" and "aria, good morning" both come back 'mention'.
// The imperative test exists one line below and is never reached. Addressing and
// intent are orthogonal; collapsing them into one field loses the one the
// operator asked ARIA to be aware of.

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import {
  classifyRequest,
  roleForBinding,
  maySeeSystemInternals,
  capabilityForCommand,
  ROLE_ADMIN,
  ROLE_USER,
  INTENT_TASK,
  INTENT_COMMAND,
  INTENT_CONVERSATION,
  ADDRESSED_MENTION,
  ADDRESSED_COMMAND,
  CAP_SYSTEM_INTERNALS,
  CAP_MEMORY_WRITE,
  CAP_ORDINARY,
} from '../lib/whatsapp/waCapability.mjs';

describe('intent survives being addressed', () => {
  it('a mention that is also a task reports BOTH, not just the mention', () => {
    const r = classifyRequest('aria, run a DD on Acme Corp');
    assert.equal(r.addressed, ADDRESSED_MENTION, 'she was reached by mention');
    assert.equal(r.intent, INTENT_TASK,
      'the legacy classifier returned "mention" here and discarded the task signal');
  });

  it('a mention that is small talk is a conversation', () => {
    const r = classifyRequest('aria, good morning');
    assert.equal(r.addressed, ADDRESSED_MENTION);
    assert.equal(r.intent, INTENT_CONVERSATION);
  });

  it('the two greetings/tasks above are DISTINGUISHABLE — the whole point', () => {
    const task = classifyRequest('aria, run a DD on Acme Corp');
    const chat = classifyRequest('aria, good morning');
    assert.notEqual(task.intent, chat.intent);
  });

  it('strips the address so the body sent onward is the actual request', () => {
    assert.equal(classifyRequest('aria, investigate Acme Corp').body, 'investigate Acme Corp');
  });

  it('an unaddressed imperative is still a task', () => {
    const r = classifyRequest('please screen Acme Ltd for sanctions');
    assert.equal(r.intent, INTENT_TASK);
  });

  it('a slash command is a command regardless of wording', () => {
    const r = classifyRequest('/screen Acme Ltd');
    assert.equal(r.addressed, ADDRESSED_COMMAND);
    assert.equal(r.intent, INTENT_COMMAND);
    assert.equal(r.command, 'screen');
  });

  it('a bare URL is a task', () => {
    assert.equal(classifyRequest('https://example.com/tender.pdf').intent, INTENT_TASK);
  });
});

describe('asking ABOUT her is not asking her to act', () => {
  it('a status question is a conversation, and flagged as system internals', () => {
    const r = classifyRequest('aria, what is your current status?');
    assert.equal(r.intent, INTENT_CONVERSATION);
    assert.equal(r.systemQuery, true);
    assert.equal(r.capability, CAP_SYSTEM_INTERNALS);
  });

  it('asking which model she is on is a system query, not a task', () => {
    const r = classifyRequest('aria which model are you using right now');
    assert.equal(r.systemQuery, true);
    assert.equal(r.intent, INTENT_CONVERSATION);
  });

  it('a question CONTAINING a task verb is not a task', () => {
    // "what is your research process" must not read as "research something".
    const r = classifyRequest('aria, what is your research process?');
    assert.equal(r.intent, INTENT_CONVERSATION,
      'verb-led matching exists so a topic noun cannot fake an imperative');
  });
});

describe('privilege keys on the bound ACCOUNT, and fails closed', () => {
  it('an admin account is admin', () => {
    assert.equal(roleForBinding({ userId: 'u-1' }, ['u-1', 'u-2']), ROLE_ADMIN);
  });

  it('an ordinary bound account is a user', () => {
    assert.equal(roleForBinding({ userId: 'u-9' }, ['u-1']), ROLE_USER);
  });

  it('no binding is a user, never an admin', () => {
    assert.equal(roleForBinding(null, ['u-1']), ROLE_USER);
    assert.equal(roleForBinding({}, ['u-1']), ROLE_USER);
    assert.equal(roleForBinding({ userId: '' }, ['u-1']), ROLE_USER);
  });

  it('an empty admin list grants nobody', () => {
    assert.equal(roleForBinding({ userId: 'u-1' }, []), ROLE_USER);
    assert.equal(roleForBinding({ userId: 'u-1' }, ''), ROLE_USER);
  });

  it('accepts a comma-separated env string as well as an array', () => {
    assert.equal(roleForBinding({ userId: 'u-2' }, ' u-1 , u-2 '), ROLE_ADMIN);
  });

  it('only admins may see system internals', () => {
    assert.equal(maySeeSystemInternals(ROLE_ADMIN), true);
    assert.equal(maySeeSystemInternals(ROLE_USER), false);
    assert.equal(maySeeSystemInternals(undefined), false);
  });
});

describe('command capabilities are named, not inferred', () => {
  it('permanent-memory writes are identified', () => {
    assert.equal(capabilityForCommand('teach'), CAP_MEMORY_WRITE);
    assert.equal(capabilityForCommand('correct'), CAP_MEMORY_WRITE);
  });

  it('system commands are identified', () => {
    assert.equal(capabilityForCommand('status'), CAP_SYSTEM_INTERNALS);
  });

  it('an unknown command is ORDINARY — this module never widens the gate', () => {
    assert.equal(capabilityForCommand('screen'), CAP_ORDINARY);
    assert.equal(capabilityForCommand('somethingnew'), CAP_ORDINARY);
  });
});
