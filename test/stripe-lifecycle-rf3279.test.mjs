// R-F3279 — Stripe lifecycle capability coverage.
//
// These tests drive the real billing configuration and webhook dispatcher.
// A paid subscription must never be provisioned from an unknown Stripe Price,
// and an event that cannot be linked to a local user must be retried rather
// than acknowledged and lost.
import { afterEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import express from 'express';

import { billingConfigStatus, ensureCustomer } from '../lib/billing/stripe.mjs';
import { createBillingRouter, handleBillingEvent } from '../lib/billing/routes.mjs';

const STRIPE_ENV = [
  'STRIPE_SECRET_KEY',
  'STRIPE_WEBHOOK_SECRET',
  'STRIPE_PRICE_PRO',
  'STRIPE_PRICE_PROINTEL',
  'STRIPE_CHECKOUT_RETURN_URL',
  'STRIPE_CHECKOUT_CANCEL_URL',
  'STRIPE_PORTAL_RETURN_URL',
];
const savedEnv = Object.fromEntries(STRIPE_ENV.map(key => [key, process.env[key]]));

afterEach(() => {
  for (const key of STRIPE_ENV) {
    if (savedEnv[key] === undefined) delete process.env[key];
    else process.env[key] = savedEnv[key];
  }
});

function fullConfig() {
  process.env.STRIPE_SECRET_KEY = 'sk_test_capability';
  process.env.STRIPE_WEBHOOK_SECRET = 'whsec_capability';
  process.env.STRIPE_PRICE_PRO = 'price_pro';
  process.env.STRIPE_PRICE_PROINTEL = 'price_intel';
  process.env.STRIPE_CHECKOUT_RETURN_URL = 'https://imaria.io/account.html?checkout=ok';
  process.env.STRIPE_CHECKOUT_CANCEL_URL = 'https://imaria.io/account.html?checkout=cancelled';
  process.env.STRIPE_PORTAL_RETURN_URL = 'https://imaria.io/account.html';
}

test('R-F3279: billing is rollout-ready only when every money-path secret is present', () => {
  fullConfig();
  assert.equal(billingConfigStatus().configured, true);

  delete process.env.STRIPE_WEBHOOK_SECRET;
  const partial = billingConfigStatus();
  assert.equal(partial.configured, false);
  assert.equal(partial.hasSecretKey, true);
  assert.deepEqual(partial.missing, ['STRIPE_WEBHOOK_SECRET']);
});

test('R-F3279: subscription webhook provisions the authoritative current Stripe state', async () => {
  fullConfig();
  const user = { id: 'user_1', stripeCustomerId: 'cus_1', tier: 'free' };
  const writes = [];
  const event = {
    id: 'evt_stale',
    type: 'customer.subscription.updated',
    data: { object: {
      id: 'sub_1',
      customer: 'cus_1',
      status: 'past_due',
      items: { data: [{ price: { id: 'price_pro' } }] },
    } },
  };
  const authoritative = {
    id: 'sub_1',
    customer: 'cus_1',
    status: 'active',
    current_period_end: 1_800_000_000,
    cancel_at_period_end: false,
    metadata: { crucix_user_id: 'user_1' },
    items: { data: [{ price: { id: 'price_intel', product: 'prod_intel' } }] },
  };

  await handleBillingEvent(event, {
    findUserById: id => id === user.id ? user : null,
    updateUser: (id, updates) => writes.push({ id, updates }),
    listUsers: () => [user],
    retrieveSubscription: async () => authoritative,
  });

  assert.equal(writes.length, 1);
  assert.equal(writes[0].updates.tier, 'proIntel');
  assert.equal(writes[0].updates.subscriptionStatus, 'active');
  assert.equal(writes[0].updates.stripeProductId, 'prod_intel');
});

test('R-F3279: unknown Stripe Price fails closed and does not downgrade or acknowledge', async () => {
  fullConfig();
  const user = { id: 'user_1', stripeCustomerId: 'cus_1', tier: 'pro' };
  let writes = 0;
  const subscription = {
    id: 'sub_1',
    customer: 'cus_1',
    status: 'active',
    metadata: { crucix_user_id: 'user_1' },
    items: { data: [{ price: { id: 'price_not_configured', product: 'prod_unknown' } }] },
  };

  await assert.rejects(
    handleBillingEvent({
      id: 'evt_unknown_price',
      type: 'customer.subscription.updated',
      data: { object: subscription },
    }, {
      findUserById: () => user,
      updateUser: () => { writes += 1; },
      listUsers: () => [user],
      retrieveSubscription: async () => subscription,
    }),
    /unconfigured Stripe price/,
  );
  assert.equal(writes, 0);
});

test('R-F3279: unresolved paid webhook fails for retry instead of disappearing', async () => {
  fullConfig();
  const subscription = {
    id: 'sub_orphan',
    customer: 'cus_orphan',
    status: 'active',
    metadata: {},
    items: { data: [{ price: { id: 'price_pro', product: 'prod_pro' } }] },
  };

  await assert.rejects(
    handleBillingEvent({
      id: 'evt_orphan',
      type: 'customer.subscription.created',
      data: { object: subscription },
    }, {
      findUserById: () => null,
      updateUser: () => assert.fail('must not write an unresolved event'),
      listUsers: () => [],
      retrieveSubscription: async () => subscription,
    }),
    /could not resolve crucix_user_id/,
  );
});

test('R-F3279: paid-account plan changes use the portal, never a second checkout', () => {
  const staticAccount = readFileSync(new URL('../public/account.html', import.meta.url), 'utf8');
  const nextAccount = readFileSync(
    new URL('../aria-app/app/(customer)/account/page.tsx', import.meta.url),
    'utf8',
  );
  assert.match(staticAccount, /meResp\.tier !== 'free'[\s\S]{0,500}openPortal/);
  assert.match(nextAccount, /currentTier !== 'free'[\s\S]{0,500}openPortal/);
});

test('R-F3279: checkout endpoint rejects a second active subscription before Stripe', async () => {
  fullConfig();
  const user = {
    id: 'user_paid',
    tier: 'pro',
    stripeCustomerId: 'cus_paid',
    stripeSubscriptionId: 'sub_paid',
    subscriptionStatus: 'active',
  };
  const app = express();
  app.use(express.json());
  app.use('/api/billing', createBillingRouter({
    requireAuth: (req, _res, next) => {
      req.user = { userId: user.id };
      next();
    },
    findUserById: id => id === user.id ? user : null,
    updateUser: () => assert.fail('checkout rejection must not update the user'),
    listUsers: () => [user],
  }));
  const server = app.listen(0, '127.0.0.1');
  try {
    await new Promise((resolve, reject) => {
      server.once('listening', resolve);
      server.once('error', reject);
    });
    const { port } = server.address();
    const response = await fetch(`http://127.0.0.1:${port}/api/billing/checkout`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ tier: 'proIntel' }),
    });
    assert.equal(response.status, 409);
    assert.equal((await response.json()).code, 'ACTIVE_SUBSCRIPTION_EXISTS');
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});

test('R-F3279: transient customer lookup failure never creates a duplicate customer', async () => {
  let creates = 0;
  const transient = Object.assign(new Error('Stripe temporarily unavailable'), { code: 'api_connection_error' });
  await assert.rejects(
    ensureCustomer({
      id: 'user_1',
      email: 'one@example.com',
      stripeCustomerId: 'cus_existing',
    }, {
      updateUser: () => assert.fail('must not rewrite the customer on a transient failure'),
      client: {
        customers: {
          retrieve: async () => { throw transient; },
          create: async () => { creates += 1; },
        },
      },
    }),
    /temporarily unavailable/,
  );
  assert.equal(creates, 0);
});

test('R-F3279: confirmed deleted customer is recreated idempotently and persisted', async () => {
  const writes = [];
  let requestOptions;
  const customer = await ensureCustomer({
    id: 'user_2',
    email: 'two@example.com',
    fullName: 'User Two',
    stripeCustomerId: 'cus_deleted',
  }, {
    updateUser: (id, updates) => writes.push({ id, updates }),
    client: {
      customers: {
        retrieve: async () => ({ id: 'cus_deleted', deleted: true }),
        create: async (_params, options) => {
          requestOptions = options;
          return { id: 'cus_replacement' };
        },
      },
    },
  });
  assert.equal(customer.id, 'cus_replacement');
  assert.equal(requestOptions.idempotencyKey, 'crucix-customer-user_2');
  assert.deepEqual(writes, [{
    id: 'user_2',
    updates: { stripeCustomerId: 'cus_replacement' },
  }]);
});
