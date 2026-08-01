'use strict';

import {
  resultMessage,
  supportOwner,
  supportReturn,
  textNode,
} from '/static/pages/more.js';
import { createIdentityActionControl } from './advanced_identity_action.js';
import {
  identityDirectory,
  identityList,
} from './advanced_identity_directory.js';
import { createLineCorrectionControl } from './advanced_line_corrections.js';
import { createOperationHistory } from './advanced_operation_history.js';
import { createSpeakerRecovery } from './advanced_speaker_recovery.js';
import { createFullCastTasks } from './full_cast_tasks.js';

const UI = globalThis.AlexandriaUI;

function authorityNote() {
  const note = document.createElement('p');
  note.className = 'full-cast-authority-note';
  note.append(
    textNode('strong', '', 'Cast remains authoritative.'),
    document.createTextNode(
      ' Identity edits can change Script labels; production Voice assignment stays in Cast.',
    ),
  );
  return note;
}

export async function mount({ root, route, shell, api, signal }) {
  const dataRouteOwner = route.path;
  const { owner, stateRegion } = supportOwner(root, route, {
    shell,
    page: 'advanced-identity-operations',
    title: 'Advanced identity operations',
    subtitle: 'Review whole-book Cast enrichment, Voice-profile drafts, and guarded identity changes.',
    className: 'specialist-workspace',
  });
  owner.dataset.routeOwner = dataRouteOwner;
  stateRegion.setAttribute('data-state-region', '');
  const params = new URLSearchParams();
  if (route.context.character) params.set('speaker', route.context.character);
  let manualTools = null;
  let reviewMode = false;
  const enterReviewMode = () => {
    reviewMode = true;
    if (manualTools) manualTools.hidden = true;
  };
  const [result, tasks] = await Promise.all([
    api.get(`/api/speaker_management/status${params.size ? `?${params}` : ''}`, {
      signal,
    }),
    createFullCastTasks({
      api, signal, shell, route, onReviewMode: enterReviewMode,
    }),
  ]);
  if (signal.aborted) return () => {};
  const toolbar = document.createElement('div');
  toolbar.className = 'support-toolbar';
  if (!root.closest('[data-cast-workflow]')) {
    toolbar.append(supportReturn(route, shell));
  }
  toolbar.hidden = toolbar.childElementCount === 0;
  if (!result.ok) {
    owner.dataset.viewState = 'ready';
    stateRegion.replaceChildren(
      toolbar,
      tasks.section,
      tasks.importer,
      UI.notice({
        tone: 'warning',
        title: 'Guarded identity controls could not load',
        body: resultMessage(result, 'Full-Cast tasks remain available above.'),
        live: true,
      }),
    );
    return () => {};
  }
  const payload = result.data;
  const operationContext = { payload, api, signal, route, shell };
  const speakerRecovery = createSpeakerRecovery(operationContext);
  const recoveryMode = route.context.mode === 'speaker-recovery';
  const content = document.createElement('div');
  content.className = 'specialist-section-grid';
  manualTools = document.createElement('details');
  manualTools.className = 'full-cast-manual-tools';
  if (route.context.mode === 'identity-review') {
    manualTools.classList.add('full-cast-manual-tools--identity-review');
  }
  if (recoveryMode) {
    manualTools.classList.add('full-cast-manual-tools--speaker-recovery');
  }
  manualTools.append(textNode(
    'summary', '',
    recoveryMode ? 'Speaker recovery and identity operations' : 'Manual identity operations',
  ));
  const manualContent = document.createElement('div');
  manualContent.className = 'full-cast-manual-tools__content';
  if (speakerRecovery) manualContent.append(speakerRecovery);
  if (payload.available && (payload.entries || []).length) {
    const controls = [
      createIdentityActionControl(operationContext),
      createLineCorrectionControl(operationContext),
    ];
    if (route.context.mode === 'identity-review') {
      manualContent.append(...controls, identityDirectory(payload));
    } else {
      manualContent.append(identityDirectory(payload), ...controls);
    }
  } else if (!speakerRecovery) {
    manualContent.append(identityList(payload));
  }
  if (payload.available) manualContent.append(createOperationHistory(operationContext));
  manualTools.append(manualContent);
  manualTools.open = route.context.mode === 'identity-review' || recoveryMode;
  manualTools.hidden = reviewMode || tasks.reviewing;
  if (route.context.mode === 'identity-review' || recoveryMode) {
    content.append(manualTools, tasks.section, tasks.importer);
  } else {
    content.append(tasks.section, tasks.importer, manualTools);
  }
  const notices = [];
  if (!payload.available) notices.push(UI.notice({
    tone: 'warning',
    title: 'Guarded operations are blocked',
    body: payload.reason || 'Repair or reapprove the character roster before changing Script identities.',
    live: true,
  }));
  notices.push(authorityNote());
  stateRegion.replaceChildren(toolbar, ...notices, content);
  owner.dataset.viewState = payload.available
    ? ((payload.entries || []).length || speakerRecovery ? 'ready' : 'empty')
    : 'blocked';
  return () => {};
}
