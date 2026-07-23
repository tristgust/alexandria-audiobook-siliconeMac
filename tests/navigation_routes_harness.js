'use strict';

const fs = require('fs');
const path = require('path');
const routes = require('../app/static/navigation_routes.js');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const manifest = JSON.parse(fs.readFileSync(
  path.join(__dirname, 'b19_t06_routes.json'), 'utf8',
));
const results = {};
const pathOnly = (value) => value.split('?')[0];

assert(Object.keys(routes.ROUTES).length === manifest.routes.length, 'canonical route count');
assert(Object.keys(routes.ALIASES).length === manifest.aliases.length, 'alias count');
for (const forbidden of ['legacyTab', 'TAB_TO_TOOL', 'TOOL_TO_TAB', 'routeForLegacyTab']) {
  assert(!(forbidden in routes), `legacy export remained: ${forbidden}`);
}

for (const definition of manifest.routes) {
  const parsed = routes.parseHash(`#/${definition.path}`);
  assert(parsed.path === pathOnly(definition.path), `${definition.path} path`);
  assert(parsed.destination === definition.destination, `${definition.path} destination`);
  assert(parsed.tool === (definition.tool || null), `${definition.path} tool`);
  assert(parsed.heading === definition.heading, `${definition.path} heading`);
  assert(parsed.hash === `#/${definition.path}`, `${definition.path} canonical hash: ${parsed.hash}`);
  for (const [key, value] of Object.entries(definition.context || {})) {
    assert(parsed.context[key] === value, `${definition.path} context ${key}`);
  }
  assert(!('legacyTab' in parsed), `${definition.path} exposed legacy state`);
}
results.canonical_matrix = true;

for (const alias of manifest.aliases) {
  const parsed = routes.parseHash(`#${alias.path}`);
  assert(parsed.hash === `#/${alias.canonical}`, `${alias.path} -> ${parsed.hash}`);
  assert(parsed.aliasUsed === pathOnly(alias.path), `${alias.path} alias receipt`);
  assert(!('legacyTab' in parsed), `${alias.path} activated legacy state`);
}
results.alias_translations = true;

const contextual = routes.routeForPath('cast', {
  project: 'project_1', character: 'character_2', issue: 'issue_3',
  return: '#/projects', filter: 'needs_attention', search: 'doctor',
});
assert(contextual.hash === '#/cast?project=project_1&character=character_2&issue=issue_3&filter=needs_attention&search=doctor&return=%23%2Fprojects', contextual.hash);
assert(routes.sameRoute(contextual, routes.parseHash(contextual.hash)), 'context round trip');
const changed = routes.withContext(contextual, { character: 'character_9', chunk: 'chunk:2' });
assert(changed.context.character === 'character_9', 'context replacement');
assert(changed.context.chunk === 'chunk:2', 'context addition');
const removed = routes.withoutContext(changed, ['issue', 'return']);
assert(!removed.context.issue && !removed.context.return, 'context removal');
results.context_round_trip = true;

const unsafe = routes.parseHash(
  `#/cast?project=project_1&character=%00bad&chunk=${'x'.repeat(600)}&unknown=ignored`,
);
assert(unsafe.context.project === 'project_1', 'safe context lost');
assert(!unsafe.context.character, 'control characters accepted');
assert(!unsafe.context.chunk, 'oversized context accepted');
assert(!unsafe.context.unknown, 'unknown context accepted');
results.context_safety = true;

const unknown = routes.parseHash('#/definitely-unknown?project=project_1');
assert(unknown.path === 'projects', 'unknown path did not fall back');
assert(unknown.destination === 'projects', 'unknown destination did not fall back');
assert(unknown.context.project === 'project_1', 'safe unknown-route context was lost');
assert(unknown.hash === '#/projects?project=project_1', 'unknown route was not canonicalized');
assert(unknown.unrecognized === 'definitely-unknown', 'unknown route receipt missing');
results.unknown_fallback = true;

console.log(JSON.stringify({ ok: true, results }, null, 2));
