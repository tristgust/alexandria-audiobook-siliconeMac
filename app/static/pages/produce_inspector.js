'use strict';

import {
  produceAudioTransport, produceReason, produceState, produceText, produceWords,
} from './produce_model.js';

const UI = globalThis.AlexandriaUI;

export function createProduceInspector({
  shell, projectId, getAggregate, getSelected, actions, inspectorState,
}) {
  const blockerContent = (blockers, selected) => {
    const list = document.createElement('div');
    list.className = 'produce-inspector__blockers';
    blockers.forEach((blocker) => {
      const action = blocker.native_destination ? UI.button({
        label: blocker.native_destination === 'cast' ? 'Open Cast' : `Open ${produceWords(blocker.native_destination)}`,
        variant: 'secondary', size: 'compact',
        onClick: () => shell.navigate(shell.routes.routeForPath(blocker.native_destination, {
          ...(projectId ? { project: projectId } : {}),
          source: blocker.target_id || selected.chunk_id,
        }).hash),
      }) : null;
      list.append(UI.notice({
        tone: blocker.blocking === false ? 'information' : 'warning',
        title: blocker.title || 'Production blocker',
        body: blocker.explanation || 'Resolve this item before generating audio.',
        action,
      }));
    });
    return list;
  };

  const historyContent = (selected) => {
    const list = document.createElement('ul');
    list.className = 'produce-history';
    const events = Array.isArray(selected.history) ? selected.history
      : Array.isArray(selected.generation_history) ? selected.generation_history : [];
    if (!events.length) {
      list.append(produceText('li', 'metadata', selected.reason
        ? `Current state: ${produceReason(selected)}`
        : 'No earlier generation receipt is available for this chunk.'));
      return list;
    }
    events.slice(0, 8).forEach((event) => {
      const row = document.createElement('li');
      row.append(
        produceText('strong', '', event.label || event.event || event.state || 'Generation event'),
        produceText('span', 'metadata', event.time || event.created_at || event.generated_at || 'Time unavailable'),
        produceText('p', 'metadata', event.reason || event.summary || 'No additional details.'),
      );
      list.append(row);
    });
    return list;
  };

  const render = () => {
    const aggregate = getAggregate();
    const selected = getSelected();
    if (!selected) {
      shell.inspector.set({
        state: 'collapsed',
        title: 'Selected chunk',
        content: produceText('p', 'metadata', 'Choose an audio chunk to review its Script, Voice, and sample.'),
      });
      return;
    }
    const body = document.createElement('div');
    body.className = 'produce-inspector';
    const identity = document.createElement('header');
    identity.className = 'produce-inspector__header';
    identity.append(
      produceText('div', 'metadata', 'Selected chunk'),
      produceText('h3', 'entity-title', selected.character_name || selected.speaker || 'Narrator'),
      produceText('span', 'metadata',
        `Chunk ${Number(selected.index) + 1} of ${Number(aggregate.all_chunk_count || aggregate.chunks?.length || 0).toLocaleString()}`),
      UI.status({ ...produceState(selected.state), domain: 'audio', value: selected.state }),
    );
    const textSection = UI.flatSection({
      title: 'Text',
      content: produceText('p', 'produce-inspector__script', selected.text || selected.text_excerpt, 'No Script text'),
    });
    const direction = UI.flatSection({
      title: 'Delivery direction',
      content: produceText('p', 'produce-inspector__direction', selected.delivery_direction, 'No delivery direction recorded.'),
    });
    const facts = document.createElement('dl');
    facts.className = 'produce-inspector__facts';
    [
      ['Speaker', selected.character_name || selected.speaker || 'Narrator'],
      ['Pause', `${Number(selected.pause_after_ms) || 0} ms`],
      ['Production Voice', selected.voice?.configuration_key
        || (selected.voice?.valid ? 'Assigned in Cast' : 'Missing voice')],
      ['Audio state', produceState(selected.state).label],
      [selected.state === 'stale' ? 'Stale reason' : 'Reason', produceReason(selected)],
    ].forEach(([term, value]) => {
      facts.append(produceText('dt', '', term), produceText('dd', '', value));
    });
    const audio = UI.flatSection({
      title: 'Audio preview',
      content: produceAudioTransport({ chunk: selected, shell, detailed: true }),
    });
    const history = UI.disclosure({
      label: 'Generation history',
      content: historyContent(selected),
    });
    history.dataset.produceHistory = '';
    body.append(identity, textSection, direction, facts, audio, history);
    if (selected.blockers?.length) body.append(blockerContent(selected.blockers, selected));
    if (selected.regenerate_action && !aggregate.process?.running) body.append(UI.button({
      label: selected.regenerate_action.label === 'Generate'
        ? 'Generate this chunk' : 'Regenerate this chunk',
      variant: 'secondary',
      attributes: { 'data-produce-selected-action': '' },
      disabled: actions.busy,
      onClick: () => actions.execute('selected', [selected.chunk_id]),
    }));
    shell.inspector.set({ state: inspectorState(), title: 'Selected chunk', content: body });
  };

  return Object.freeze({ render });
}
