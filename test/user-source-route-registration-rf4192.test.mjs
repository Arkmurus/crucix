import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { parse } from 'acorn';

const serverSource = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');

function topLevelAppRoutes(source) {
  const program = parse(source, { ecmaVersion: 'latest', sourceType: 'module' });
  return program.body.flatMap((statement) => {
    if (statement.type !== 'ExpressionStatement') return [];
    const call = statement.expression;
    if (call.type !== 'CallExpression' || call.callee.type !== 'MemberExpression') return [];
    if (call.callee.object.type !== 'Identifier' || call.callee.object.name !== 'app') return [];
    if (call.callee.property.type !== 'Identifier') return [];
    const route = call.arguments[0];
    if (!route || route.type !== 'Literal' || typeof route.value !== 'string') return [];
    return [{ method: call.callee.property.name, path: route.value }];
  });
}

test('R-F4192: tenant source routes register during server boot', () => {
  const routes = topLevelAppRoutes(serverSource);
  for (const expected of [
    { method: 'get', path: '/api/aria/user/sources' },
    { method: 'post', path: '/api/aria/user/sources' },
    { method: 'delete', path: '/api/aria/user/sources/:siteId' },
  ]) {
    assert.deepEqual(
      routes.filter((route) => route.method === expected.method && route.path === expected.path),
      [expected],
      `${expected.method.toUpperCase()} ${expected.path} must be registered exactly once at module scope`,
    );
  }
});

test('R-F4192: the knowledge fact route closes before tenant routes', () => {
  const knowledgeStart = serverSource.indexOf("app.post('/api/aria/knowledge/fact'");
  const sourceStart = serverSource.indexOf("app.get('/api/aria/user/sources'");
  assert.ok(knowledgeStart >= 0 && sourceStart > knowledgeStart);
  const knowledgeBlock = serverSource.slice(knowledgeStart, sourceStart);
  assert.match(knowledgeBlock, /\}\);\s*\/\/ R-F2048/);
});
