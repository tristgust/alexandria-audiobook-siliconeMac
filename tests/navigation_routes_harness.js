const routes = require('../app/static/navigation_routes.js');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const results = {};

const aliases = {
  '#setup': ['projects', 'setup'],
  '#script': ['script', 'script'],
  '#characters': ['cast', 'characters'],
  '#voice-casting': ['cast', 'characters'],
  '#voice-projects': ['cast', 'characters'],
  '#voices': ['voices', 'designer'],
  '#editor': ['produce', 'editor'],
  '#audio': ['export', 'audio'],
  '#result': ['export', 'audio'],
  '#speaker-management': ['more', 'speaker-management'],
  '#designer': ['more', 'designer'],
  '#preparer': ['more', 'preparer'],
  '#dataset-builder': ['more', 'dataset-builder'],
  '#training': ['more', 'training'],
  '#project-recovery': ['more', 'project-recovery'],
};
for (const [hash, expected] of Object.entries(aliases)) {
  const route = routes.parseHash(hash);
  assert(route.destination === expected[0], `${hash} destination`);
  assert(route.legacyTab === expected[1], `${hash} legacy tab`);
}
results.legacy_aliases = true;

const contextual = routes.parseHash(
  '#/cast?project=project_1&character=character_2&issue=issue_3&return=%23%2Fprojects&filter=needs_attention&search=doctor'
);
assert(contextual.destination === 'cast', 'contextual destination');
assert(contextual.context.project === 'project_1', 'project context');
assert(contextual.context.character === 'character_2', 'character context');
assert(contextual.context.issue === 'issue_3', 'issue context');
assert(contextual.context.return === '#/projects', 'return context');
assert(contextual.context.filter === 'needs_attention', 'filter context');
assert(contextual.context.search === 'doctor', 'search context');
assert(
  contextual.hash === '#/cast?project=project_1&character=character_2&issue=issue_3&return=%23%2Fprojects&filter=needs_attention&search=doctor',
  `canonical contextual hash ${contextual.hash}`
);
results.context_round_trip = true;

const produce = routes.routeForLegacyTab('editor', {
  chunk: 'chunk:42',
  project: 'project_1',
});
assert(produce.destination === 'produce', 'editor canonical route');
assert(produce.legacyTab === 'editor', 'editor legacy route');
assert(produce.hash.includes('chunk=chunk%3A42'), 'chunk serialized');
assert(routes.sameRoute(produce, routes.parseHash(produce.hash)), 'produce round trip');
results.produce_deep_link = true;

const more = routes.routeForDestination('more', {
  tool: 'voice-designer',
  character: 'character_1',
  mode: 'preview',
  return: '#/cast?character=character_1',
});
assert(more.legacyTab === 'designer', 'tool legacy tab');
assert(routes.parseHash(more.hash).context.mode === 'preview', 'tool mode');
assert(routes.parseHash('#/more/voice-training').legacyTab === 'training', 'tool path');
results.tool_routes = true;

const library = routes.routeForDestination('library');
const voices = routes.routeForDestination('voices');
const templates = routes.routeForDestination('templates');
const legacyVoices = routes.parseHash('#/library?mode=voices');
const legacyTemplates = routes.parseHash('#/library?mode=templates');
const help = routes.parseHash('#/more/help-center?project=project_1&character=character_1&source=library_1&issue=issue_1&mode=review&help=voice-assignment&topic=cast&return=%23%2Fcast%3Fproject%3Dproject_1');
const settings = routes.routeForDestination('settings', { project: 'project_1' });
assert(library.legacyTab === 'designer', 'library compatibility tab');
assert(voices.destination === 'voices' && voices.legacyTab === 'designer', 'voices destination');
assert(templates.destination === 'templates' && templates.legacyTab === 'designer', 'templates destination');
assert(legacyVoices.destination === 'voices' && !legacyVoices.context.mode, 'legacy voices normalization');
assert(legacyTemplates.destination === 'templates' && !legacyTemplates.context.mode, 'legacy templates normalization');
assert(help.destination === 'more' && help.context.tool === 'help-center', 'help center route');
assert(help.context.help === 'voice-assignment', 'help context ID');
assert(help.context.topic === 'cast', 'help topic');
assert(help.context.source === 'library_1', 'help preserves original source');
assert(help.context.issue === 'issue_1', 'help preserves issue');
assert(help.context.mode === 'review', 'help preserves mode');
assert(help.context.return === '#/cast?project=project_1', 'help exact return');
assert(routes.parseHash(help.hash).context.topic === 'cast', 'help route round trip');
assert(settings.legacyTab === 'setup', 'settings compatibility tab');
assert(
  routes.TAB_TO_TOOL['speaker-management'] === 'advanced-character-operations',
  'shared speaker-management tab prefers advanced identity as its reverse-map tool'
);
assert(
  routes.TAB_TO_TOOL['project-recovery'] === 'maintenance',
  'shared project-recovery tab prefers maintenance as its reverse-map tool'
);
assert(
  routes.routeForDestination('more', { tool: 'help-center' }).legacyTab === 'speaker-management',
  'help center remains explicitly addressable on the shared tab'
);
assert(
  routes.routeForDestination('more', { tool: 'model-cache' }).legacyTab === 'project-recovery',
  'model cache remains explicitly addressable on the shared tab'
);
results.supporting_destinations = true;

const changed = routes.withContext(contextual, { character: 'character_9', chunk: 'chunk:2' });
assert(changed.context.character === 'character_9', 'context replacement');
assert(changed.context.chunk === 'chunk:2', 'context addition');
const removed = routes.withoutContext(changed, ['issue', 'return']);
assert(!removed.context.issue && !removed.context.return, 'context removal');
results.context_updates = true;

const unsafe = routes.parseHash(
  '#/cast?character=%00bad&chunk=' + 'x'.repeat(600) + '&unknown=ignored&project=project_1'
);
assert(!unsafe.context.character, 'control characters rejected');
assert(!unsafe.context.chunk, 'oversized context rejected');
assert(!unsafe.context.unknown, 'unknown context rejected');
assert(unsafe.context.project === 'project_1', 'safe context retained');
results.context_safety = true;

const unknown = routes.parseHash('#/not-a-real-page?project=project_1');
assert(unknown.destination === 'projects', 'unknown fallback destination');
assert(unknown.context.project === 'project_1', 'unknown fallback context');
results.unknown_fallback = true;

const linkRoute = routes.routeForLink({
  route: 'more',
  routeTool: 'audio-preparer',
  routeCharacter: 'character_1',
  routeReturn: '#/cast?character=character_1',
});
assert(linkRoute.legacyTab === 'preparer', 'dataset route tool');
assert(linkRoute.context.character === 'character_1', 'dataset character');
results.link_dataset = true;

console.log(JSON.stringify({ ok: true, results }, null, 2));
