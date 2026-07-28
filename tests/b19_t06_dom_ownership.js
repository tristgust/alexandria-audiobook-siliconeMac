'use strict';

const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');

const OWNERSHIP_SNAPSHOT = `(() => {
  const visible = (node) => {
    if (!node || node.hidden) return false;
    const style = getComputedStyle(node); const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const roots = [...document.querySelectorAll('#canonical-destination-root,[data-canonical-destination-root]')];
  const panels = [...document.querySelectorAll('[data-tab-panel]')];
  const prohibitedIds = [
    'setup-tab','characters-tab','editor-tab','audio-tab','speaker-management-tab',
    'script-tab','designer-tab','preparer-tab','training-tab','dataset-builder-tab',
    'character-workspace','legacy-settings-workspace','legacy-tab-store',
  ].filter((id) => document.getElementById(id));
  const hiddenTrees = panels.filter((node) => (
    node.hidden || node.inert || !visible(node) || node.closest('[hidden],[inert]')
  )).map((node) => ({ id: node.id, panel: node.dataset.tabPanel, hidden: node.hidden,
    inert: node.inert, display: getComputedStyle(node).display }));
  const page = document.querySelector('[data-route-owner="cast"],[data-page="cast"]');
  const persona = document.querySelector('[data-persona-visual],[data-appearance-summary]');
  const selectedProfile = page?.querySelector('[data-selected-character-profile],[data-cast-profile]');
  const visibleLegacyControls = [...document.querySelectorAll('[data-tab],.app-tab-link')]
    .filter(visible).map((node) => node.id || node.textContent.trim()).slice(0, 30);
  const visibleOutsideRoot = [...document.querySelectorAll('main h1,main h2,main button,main input')]
    .filter(visible).filter((node) => !roots.some((root) => root.contains(node)))
    .map((node) => node.id || node.textContent.trim()).slice(0, 30);
  return {
    href: location.href, destination: document.body.dataset.destination || null,
    destinationRootCount: roots.length, directCastOwner: Boolean(page && roots[0]?.contains(page)),
    legacyPanelCount: panels.length, prohibitedIds, hiddenTrees,
    visibleLegacyControls, visibleOutsideRoot,
    personaInsideCast: Boolean(persona && page?.contains(persona)),
    personaInsideSelectedProfile: Boolean(persona && selectedProfile?.contains(persona)),
    personaText: persona?.textContent?.trim().slice(0, 240) || null,
    characterListCount: page?.querySelectorAll('[data-cast-roster] [role="listbox"],[data-character-list],[role="listbox"][aria-label*="Character"]').length || 0,
    bridgePresent: typeof window.activateWorkspaceTab === 'function'
      || typeof window.VoiceCardBridge !== 'undefined',
    persistentPlayerCount: document.querySelectorAll('[data-persistent-player],#persistent-audio-player').length,
  };
})()`;

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const target = new URL(required(args, 'url'));
  target.hash = '/cast';
  const session = await BrowserSession.open({ url: target.href, artifacts });
  let report;
  try {
    await session.waitFor(`document.readyState === 'complete'
      && document.body.dataset.destination === 'cast'
      && document.body.dataset.shellState === 'ready'
      && document.querySelector('[data-cast-page]')?.dataset.castState === 'ready'`);
    const snapshot = await session.evaluate(OWNERSHIP_SNAPSHOT);
    await session.screenshot('cast-ownership.png');
    const assertions = [
      { id: 'one-canonical-destination-root', pass: snapshot.destinationRootCount === 1, expected: 1, observed: snapshot.destinationRootCount },
      { id: 'cast-directly-owned', pass: snapshot.directCastOwner, expected: true, observed: snapshot.directCastOwner },
      { id: 'no-legacy-tab-panels', pass: snapshot.legacyPanelCount === 0, expected: 0, observed: snapshot.legacyPanelCount },
      { id: 'no-prohibited-legacy-ids', pass: snapshot.prohibitedIds.length === 0, expected: [], observed: snapshot.prohibitedIds },
      { id: 'no-hidden-legacy-trees', pass: snapshot.hiddenTrees.length === 0, expected: [], observed: snapshot.hiddenTrees },
      { id: 'no-visible-legacy-tab-controls', pass: snapshot.visibleLegacyControls.length === 0, expected: [], observed: snapshot.visibleLegacyControls },
      { id: 'all-visible-page-controls-owned', pass: snapshot.visibleOutsideRoot.length === 0, expected: [], observed: snapshot.visibleOutsideRoot },
      { id: 'persona-visual-inside-cast-profile', pass: snapshot.personaInsideCast, expected: true, observed: snapshot.personaInsideCast },
      { id: 'persona-visual-inside-selected-profile', pass: snapshot.personaInsideSelectedProfile,
        expected: true, observed: snapshot.personaInsideSelectedProfile },
      { id: 'persona-no-evidence-state-is-truthful', pass:
        snapshot.personaInsideSelectedProfile && snapshot.personaText?.includes('Visual evidence not available'),
        expected: 'Visual evidence not available', observed: snapshot.personaText },
      { id: 'one-character-list', pass: snapshot.characterListCount === 1,
        expected: 1, observed: snapshot.characterListCount },
      { id: 'no-legacy-bridge', pass: !snapshot.bridgePresent, expected: false, observed: snapshot.bridgePresent },
      { id: 'one-persistent-player', pass: snapshot.persistentPlayerCount === 1, expected: 1, observed: snapshot.persistentPlayerCount },
    ];
    report = { status: assertions.every((item) => item.pass) ? 'PASS' : 'RED', snapshot, assertions };
    writeJson(path.join(artifacts, 'report.json'), report);
    process.stdout.write(`B19_T06_OWNERSHIP=${JSON.stringify(report)}\n`);
  } finally {
    await session.close();
  }
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
