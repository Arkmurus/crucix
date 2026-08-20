import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import test, { afterEach, beforeEach } from 'node:test';

import { fetchWithDeadline } from '../aria-app/lib/fetch-deadline.ts';
import { allowLoopbackNetwork, blockRealNetwork } from './helpers/net_guard.mjs';

beforeEach(() => allowLoopbackNetwork());
afterEach(() => blockRealNetwork());

test('R-F4187: the real backend fetch aborts a server that never responds', async () => {
  const server = createServer(() => {});
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const started = Date.now();
  try {
    await assert.rejects(
      fetchWithDeadline(`http://127.0.0.1:${address.port}/never`, {}, 75),
      (error) => error instanceof Error && ['AbortError', 'TimeoutError'].includes(error.name),
    );
    assert.ok(Date.now() - started < 1_000, 'deadline must prevent a wedged page render');
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});

test('R-F4187: caller cancellation remains authoritative', () => {
  const controller = new AbortController();
  controller.abort();
  return assert.rejects(
    fetchWithDeadline('http://127.0.0.1:9/', { signal: controller.signal }, 10_000),
    (error) => error instanceof Error && error.name === 'AbortError',
  );
});
