// lib/orchestrator/retry.mjs
// Reliable task execution with retry, dead-letter queue, and alerting
// Implements LangGraph/Prefect patterns in pure Node.js
//
// Usage:
//   const result = await reliableRun('BD Intelligence', runBDIntelligence, [data, null, llm], { maxRetries: 2 });

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';

const DLQ_FILE = join(process.cwd(), 'runs', 'dead_letter_queue.json');
const MAX_DLQ = 50;

function loadDLQ() {
  try {
    if (existsSync(DLQ_FILE)) return JSON.parse(readFileSync(DLQ_FILE, 'utf8'));
  } catch {}
  return [];
}

function saveDLQ(dlq) {
  try {
    const dir = dirname(DLQ_FILE);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    writeFileSync(DLQ_FILE, JSON.stringify(dlq.slice(0, MAX_DLQ), null, 2), 'utf8');
  } catch {}
}

/**
 * Execute a function with retry logic and dead-letter queue.
 * @param {string} name - Task name for logging
 * @param {Function} fn - Async function to execute
 * @param {Array} args - Arguments to pass to fn
 * @param {Object} opts - { maxRetries, delayMs, onFailure }
 * @returns {*} Result of fn, or null if all retries exhausted
 */
export async function reliableRun(name, fn, args = [], opts = {}) {
  const { maxRetries = 2, delayMs = 3000, onFailure = null } = opts;

  for (let attempt = 1; attempt <= maxRetries + 1; attempt++) {
    try {
      const result = await fn(...args);
      if (attempt > 1) console.log(`[Orchestrator] ${name} succeeded on attempt ${attempt}`);
      return result;
    } catch (err) {
      const isLast = attempt > maxRetries;
      console.warn(`[Orchestrator] ${name} attempt ${attempt}/${maxRetries + 1} failed: ${err.message}`);

      if (isLast) {
        // Dead-letter queue — store for analysis
        const dlq = loadDLQ();
        dlq.unshift({
          task: name,
          error: err.message,
          stack: (err.stack || '').substring(0, 500),
          attempts: attempt,
          ts: new Date().toISOString(),
        });
        saveDLQ(dlq);
        console.error(`[Orchestrator] ${name} FAILED after ${attempt} attempts — added to dead-letter queue`);

        // Alert callback
        if (onFailure) {
          try { await onFailure(name, err, attempt); } catch {}
        }
        return null;
      }

      // Wait before retry (linear backoff: 3s, 6s, 9s...)
      await new Promise(r => setTimeout(r, delayMs * attempt));
    }
  }
  return null;
}

/**
 * Get dead-letter queue contents for debugging.
 */
export function getDLQ() {
  return loadDLQ();
}

/**
 * Clear resolved items from dead-letter queue.
 */
export function clearDLQ() {
  saveDLQ([]);
}

/**
 * Run multiple tasks in parallel with individual retry.
 * Returns results in same order as tasks array.
 */
export async function reliableParallel(tasks, opts = {}) {
  return Promise.all(tasks.map(({ name, fn, args }) =>
    reliableRun(name, fn, args || [], opts)
  ));
}
