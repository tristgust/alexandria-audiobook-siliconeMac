'use strict';

import {
  resultMessage,
  supportOwner,
  supportReturn,
  textNode,
} from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;
const LOADING_LABEL = 'Loading Voice Lab';

function candidateList(training, banks, selectedCharacter) {
  const entries = training.entries || [];
  const bankByCharacter = new Map((banks.entries || []).map((item) => [
    item.character_id,
    item,
  ]));
  const rowFor = (entry) => {
    const bank = bankByCharacter.get(entry.character_id);
    const row = document.createElement('div');
    row.className = 'support-list-row';
    if (entry.character_id === selectedCharacter) row.dataset.current = 'true';
    const copy = document.createElement('div');
    const status = entry.status === 'candidate'
      ? 'Candidate'
      : entry.eligible ? 'Eligible' : 'Not eligible';
    const references = bank
      ? `${Number(bank.approved_style_count || 0)} of ${Number(bank.required_style_count || 0)} styles approved`
      : 'No reference bank';
    copy.append(
      textNode('strong', '', entry.display_name || entry.canonical_name),
      textNode('p', 'support-status-copy', `${status} · ${references}`),
    );
    row.append(copy, UI.status({
      label: entry.readiness_status === 'ready_for_feasibility_review'
        ? 'Ready for review' : status,
      tone: entry.readiness_status === 'ready_for_feasibility_review'
        ? 'success' : entry.eligible ? 'neutral' : 'warning',
    }));
    return row;
  };
  if (!entries.length) return UI.emptyState({
    title: 'No Voice Lab project',
    body: 'Approve a resolved speaking character in Cast before preparing experimental Voice material.',
  });
  const active = entries.filter((entry) => (
    entry.character_id === selectedCharacter
    || entry.status === 'candidate'
    || Boolean(entry.readiness_status)
    || entry.reference_selected
    || entry.adapter_assigned
  ));
  const visible = active.length ? active : entries.slice(0, 8);
  const visibleIds = new Set(visible.map((entry) => entry.character_id));
  const remaining = entries.filter((entry) => !visibleIds.has(entry.character_id));
  const content = document.createElement('div');
  const list = document.createElement('div');
  list.className = 'support-list';
  visible.forEach((entry) => list.append(rowFor(entry)));
  content.append(list);
  if (remaining.length) {
    const more = document.createElement('div');
    more.className = 'support-list';
    remaining.forEach((entry) => more.append(rowFor(entry)));
    content.append(UI.disclosure({
      label: `Show ${remaining.length} other identities`,
      content: more,
    }));
  }
  return content;
}

function selectedDetails(character, projectResult, bankResult) {
  const section = document.createElement('section');
  section.className = 'specialist-section';
  section.append(textNode('h2', '', 'Selected character'));
  if (!character) {
    section.append(UI.emptyState({
      title: 'No Voice Lab project',
      body: 'Open Voice Lab from a character context to inspect its project and reference bank.',
    }));
    return section;
  }
  if (!projectResult.ok && !bankResult.ok) {
    section.append(UI.notice({
      tone: 'information',
      title: 'No Voice Lab project',
      body: 'This character has no training project or expressive reference bank yet.',
    }));
    return section;
  }
  const project = projectResult.ok ? projectResult.data : null;
  const bank = bankResult.ok ? bankResult.data : null;
  const list = document.createElement('div');
  list.className = 'support-metric-list';
  const item = (label, value) => {
    const row = document.createElement('div');
    row.className = 'support-metric';
    row.append(textNode('span', 'metadata', label), textNode('strong', '', value));
    return row;
  };
  list.append(
    item('Preparation', project?.status || 'Not started'),
    item('Readiness', project?.readiness?.status || project?.readiness_status || 'Not reviewed'),
    item('Reference styles', bank
      ? `${Number(bank.references?.filter((entry) => entry.review?.approved).length || 0)} approved`
      : 'No bank'),
    item('Identity source', bank?.identity_source?.kind === 'owned_recording'
      ? 'Owned reference recording' : 'Not selected'),
  );
  section.append(list);
  return section;
}

export async function mount({ root, route, shell, api, signal }) {
  const dataRouteOwner = route.path;
  const { owner, stateRegion } = supportOwner(root, route, {
    shell,
    page: 'voice-training',
    title: 'Voice Lab',
    subtitle: 'Experimental preparation, reference-bank, and training artifact review.',
    className: 'voice-training-workspace specialist-workspace',
  });
  owner.dataset.routeOwner = dataRouteOwner;
  stateRegion.setAttribute('data-state-region', '');
  stateRegion.dataset.loadingLabel = LOADING_LABEL;
  const [trainingResult, bankStatusResult, capabilityResult] = await Promise.all([
    api.get('/api/voice_training/status', { signal }),
    api.get('/api/expressive_reference_banks/status', { signal }),
    api.get('/api/voice_backend/capabilities', { signal }),
  ]);
  if (signal.aborted) return () => {};
  const character = route.context.character;
  let projectResult = { ok: false };
  let bankResult = { ok: false };
  if (character) {
    [projectResult, bankResult] = await Promise.all([
      api.get(`/api/voice_training/${encodeURIComponent(character)}`, { signal }),
      api.get(`/api/expressive_reference_banks/${encodeURIComponent(character)}`, { signal }),
    ]);
  }
  if (signal.aborted) return () => {};
  const toolbar = document.createElement('div');
  toolbar.className = 'support-toolbar';
  const returnButton = supportReturn(route, shell);
  returnButton.setAttribute('data-support-return', '');
  toolbar.append(returnButton);
  if (!trainingResult.ok || !bankStatusResult.ok || !capabilityResult.ok) {
    owner.dataset.viewState = 'error';
    stateRegion.replaceChildren(toolbar, UI.notice({
      tone: 'error',
      title: 'Could not load Voice Lab',
      body: resultMessage(
        !trainingResult.ok ? trainingResult
          : !bankStatusResult.ok ? bankStatusResult : capabilityResult,
        'No training or reference material was changed.',
      ),
      live: true,
    }));
    return () => {};
  }
  const grid = document.createElement('div');
  grid.className = 'specialist-section-grid';
  grid.append(
    candidateList(trainingResult.data, bankStatusResult.data, character),
    selectedDetails(character, projectResult, bankResult),
  );
  const trainingSupported = Boolean(capabilityResult.data?.training_action_enabled);
  stateRegion.replaceChildren(
    toolbar,
    UI.notice({
      tone: 'warning',
      title: 'Experimental Voice development',
      body: trainingSupported
        ? 'Training is available for feasibility review. Training, validation, and installation do not change the production Voice. Production Voice assignment happens only in Cast.'
        : 'Stable training is not supported by the current backend. Training, validation, and installation do not change the production Voice. Production Voice assignment happens only in Cast.',
    }),
    grid,
  );
  owner.dataset.viewState = (trainingResult.data?.entries || []).length ? 'ready' : 'empty';
  const cleanup = () => {};
  return cleanup;
}
