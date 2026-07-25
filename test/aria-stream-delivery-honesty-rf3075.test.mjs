import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { containsAnswerChunk } from '../lib/aria_sse_delivery.mjs';

test('R-F3075: Python-spaced SSE answer is real delivered content', () => {
  const pythonEvent = 'data: {"type": "chunk", "text": "Verified answer"}\n\n';
  assert.equal(containsAnswerChunk(pythonEvent), true);
});

test('R-F3075: terminal and empty events do not manufacture delivery', () => {
  assert.equal(containsAnswerChunk('data: {"type": "done"}\n\n'), false);
  assert.equal(containsAnswerChunk('data: {"type": "chunk", "text": ""}\n\n'), false);
  assert.equal(containsAnswerChunk('data: [DONE]\n\n'), false);
});

test('R-F3075: production stream handler uses the serializer-independent detector', () => {
  const server = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
  const handlerStart = server.indexOf("app.post('/api/aria/chat/stream'");
  const handlerEnd = server.indexOf("// Session recovery", handlerStart);
  const handler = server.slice(handlerStart, handlerEnd);

  assert.match(handler, /containsAnswerChunk\(chunk\)/);
  assert.doesNotMatch(handler, /chunk\.indexOf\(['"]\\"type\\":\\"chunk\\"/);
});
