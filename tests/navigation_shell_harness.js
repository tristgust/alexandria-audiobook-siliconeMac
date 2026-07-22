const fs = require('fs');
const path = require('path');
const vm = require('vm');
const routes = require('../app/static/navigation_routes.js');

function extractFunction(source, name) {
  const marker = `function ${name}`;
  const start = source.indexOf(marker);
  if (start < 0) throw new Error(`Missing ${name}`);
  const signatureEnd = source.indexOf(') {', start);
  const brace = signatureEnd < 0 ? -1 : signatureEnd + 2;
  if (brace < 0) throw new Error(`Missing body for ${name}`);
  let depth = 0;
  let quote = null;
  let escaped = false;
  let templateDepth = 0;
  for (let index = brace; index < source.length; index += 1) {
    const character = source[index];
    if (quote) {
      if (escaped) {
        escaped = false;
      } else if (character === '\\') {
        escaped = true;
      } else if (character === quote && templateDepth === 0) {
        quote = null;
      } else if (quote === '`' && character === '$' && source[index + 1] === '{') {
        templateDepth += 1;
        index += 1;
        depth += 1;
      } else if (quote === '`' && templateDepth > 0 && character === '}') {
        templateDepth -= 1;
        depth -= 1;
      }
      continue;
    }
    if (character === '"' || character === "'" || character === '`') {
      quote = character;
      continue;
    }
    if (character === '{') depth += 1;
    if (character === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`Unclosed ${name}`);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const html = fs.readFileSync(
  path.join(__dirname, '..', 'app', 'static', 'index.html'),
  'utf8'
);
const functionNames = [
  'workspaceRouteForActivation',
  'setWorkspaceHash',
  'updateWorkspaceRouteContext',
];
const functions = functionNames.map(name => extractFunction(html, name)).join('\n\n');
const calls = [];
const location = { hash: '#characters' };
const history = {
  pushState(state, _unused, hash) {
    calls.push({ method: 'push', state, hash });
    location.hash = hash;
  },
  replaceState(state, _unused, hash) {
    calls.push({ method: 'replace', state, hash });
    location.hash = hash;
  },
};
const context = vm.createContext({
  routes,
  location,
  history,
  calls,
  console,
});
vm.runInContext(
  `
  const workspaceRouteApi = routes;
  const window = { location, history };
  let currentWorkspaceTab = 'characters';
  let currentWorkspaceRoute = routes.parseHash('#/cast?project=project_1&character=character_1&issue=issue_1');
  let lastTopbar = null;
  function updateTopbarActiveState(tab, route) { lastTopbar = { tab, route }; }
  ${functions}
  `,
  context
);

const sameTab = vm.runInContext(
  `workspaceRouteForActivation('characters', {})`,
  context
);
assert(sameTab.context.character === 'character_1', 'same-tab context was lost');
assert(sameTab.context.issue === 'issue_1', 'same-tab issue was lost');

const switched = vm.runInContext(
  `workspaceRouteForActivation('editor', {})`,
  context
);
assert(switched.destination === 'produce', 'legacy editor did not map to Produce');
assert(switched.context.project === 'project_1', 'project context was not preserved');
assert(!switched.context.character, 'character leaked into Produce without an explicit route');
assert(!switched.context.issue, 'issue leaked into Produce without an explicit route');

vm.runInContext(`setWorkspaceHash(routes.parseHash('#characters'), 'replace')`, context);
assert(calls.at(-1).method === 'replace', 'legacy canonicalization did not replace');
assert(calls.at(-1).hash === '#/cast', 'legacy canonicalization hash was wrong');

vm.runInContext(
  `setWorkspaceHash(routes.routeForDestination('produce', {project: 'project_1', chunk: 'chunk:42'}), 'push')`,
  context
);
assert(calls.at(-1).method === 'push', 'explicit navigation did not push');
assert(
  calls.at(-1).hash === '#/produce?project=project_1&chunk=chunk%3A42',
  `explicit route hash was wrong: ${calls.at(-1).hash}`
);

vm.runInContext(
  `
  currentWorkspaceTab = 'characters';
  currentWorkspaceRoute = routes.parseHash('#/cast?project=project_1&character=character_1&issue=issue_1&filter=needs_attention');
  updateWorkspaceRouteContext(
    { character: 'character_2', search: 'doctor' },
    { remove: ['issue'], historyMode: 'push' }
  );
  `,
  context
);
const updated = vm.runInContext('currentWorkspaceRoute', context);
assert(updated.context.character === 'character_2', 'character context did not update');
assert(updated.context.search === 'doctor', 'search context did not update');
assert(updated.context.filter === 'needs_attention', 'filter context was not preserved');
assert(!updated.context.issue, 'removed issue remained');
assert(calls.at(-1).method === 'push', 'context selection did not push');
assert(
  calls.at(-1).hash === '#/cast?project=project_1&character=character_2&filter=needs_attention&search=doctor',
  `context route hash was wrong: ${calls.at(-1).hash}`
);

console.log(JSON.stringify({
  ok: true,
  calls,
  finalHash: location.hash,
  finalRoute: updated,
}, null, 2));
