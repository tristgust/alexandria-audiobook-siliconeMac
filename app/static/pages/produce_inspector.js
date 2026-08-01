'use strict';

import {
  produceReason, produceState, produceText, produceWords,
} from './produce_model.js';

const UI = globalThis.AlexandriaUI;

function stableWaveform(selected) {
  const available = Boolean(selected.audio?.available);
  const waveform = document.createElement('div');
  waveform.className = 'produce-inspector-waveform';
  waveform.dataset.audioAvailable = String(available);
  waveform.setAttribute('role', 'img');
  waveform.setAttribute('aria-label', available
    ? `Audio preview for ${selected.character_name || selected.speaker || 'selected chunk'}`
    : 'Audio preview unavailable');
  for (let index = 0; index < 12; index += 1) waveform.append(document.createElement('span'));
  return waveform;
}

function historyContent(selected) {
  const content = document.createElement('div');
  const events = Array.isArray(selected.history) ? selected.history
    : Array.isArray(selected.generation_history) ? selected.generation_history : [];
  if (!events.length) {
    content.textContent = 'No successful generation record is available for this chunk.';
    return content;
  }
  const list = document.createElement('ul');
  list.className = 'produce-history';
  events.slice(0, 8).forEach((event) => {
    const row = document.createElement('li');
    row.append(
      produceText('strong', '', event.label || event.event || event.state || 'Generation event'),
      produceText('span', 'metadata', event.time || event.created_at || event.generated_at || 'Time unavailable'),
      produceText('p', 'metadata', event.reason || event.summary || 'No additional details.'),
    );
    list.append(row);
  });
  content.append(list);
  return content;
}

function historyDisclosure(selected) {
  const disclosure = document.createElement('details');
  disclosure.className = 'produce-history-disclosure';
  disclosure.dataset.produceHistory = '';
  const summary = document.createElement('summary');
  const label = document.createElement('span');
  label.textContent = 'Generation history';
  const chevron = document.createElement('i');
  chevron.className = 'fas fa-chevron-right';
  chevron.setAttribute('aria-hidden', 'true');
  summary.append(label, chevron);
  disclosure.append(summary, historyContent(selected));
  return disclosure;
}

export function createProduceInspector({
  inspector, shell, projectId, getAggregate, getSelected, actions,
}) {
  const blockerContent = (blockers, selected) => {
    const list = document.createElement('div');
    list.className = 'produce-inspector__blockers';
    blockers.forEach((blocker) => {
      const speakerRecovery = blocker.native_destination === 'more/advanced-character-operations';
      const context = speakerRecovery ? {
        ...(projectId ? { project: projectId } : {}),
        character: blocker.target_id,
        chunk: selected.chunk_id,
        source: selected.chunk_id,
        mode: 'speaker-recovery',
        return: shell.routes.routeForPath('produce', {
          ...(projectId ? { project: projectId } : {}),
          chunk: selected.chunk_id,
        }).hash,
      } : {
        ...(projectId ? { project: projectId } : {}),
        source: blocker.target_id || selected.chunk_id,
      };
      const action = blocker.native_destination ? UI.button({
        label: speakerRecovery ? 'Recover excluded speaker'
          : blocker.native_destination === 'cast' ? 'Open Cast'
            : `Open ${produceWords(blocker.native_destination)}`,
        variant: 'secondary', size: 'compact',
        attributes: speakerRecovery ? { 'data-produce-speaker-recovery': '' } : {},
        onClick: () => shell.navigate(shell.routes.routeForPath(
          blocker.native_destination,
          context,
        ).hash),
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

  const render = () => {
    const aggregate = getAggregate();
    const selected = getSelected();
    if (!selected) {
      inspector.setContent(UI.emptyState({
        iconClass: 'fas fa-wave-square',
        title: 'Select a chunk',
        body: 'Choose an audio chunk to review its Script, Voice, and sample.',
      }));
      inspector.close({ restoreFocus: false });
      return;
    }

    const body = document.createElement('div');
    body.className = 'produce-inspector__body';

    const heading = document.createElement('header');
    heading.className = 'produce-inspector-heading';
    const headingCopy = document.createElement('div');
    headingCopy.append(
      produceText('span', 'utility-heading', 'Selected chunk'),
      produceText('h2', '', selected.character_name || selected.speaker || 'Narrator'),
    );
    heading.append(headingCopy);

    const index = produceText(
      'p', 'produce-inspector-index',
      `Chunk ${Number(selected.index) + 1} of ${Number(aggregate.all_chunk_count || aggregate.chunks?.length || 0).toLocaleString()}`,
    );

    const textSection = document.createElement('section');
    textSection.className = 'produce-inspector-section';
    textSection.append(
      produceText('span', 'utility-heading', 'Full text'),
      produceText('blockquote', '', selected.text || selected.text_excerpt, 'No Script text'),
    );

    const direction = document.createElement('section');
    direction.className = 'produce-inspector-section';
    direction.append(
      produceText('span', 'utility-heading', 'Delivery direction'),
      produceText('p', 'produce-inspector-direction', selected.delivery_direction, 'No delivery direction recorded.'),
    );

    const provenance = selected.generation_provenance || {};
    const modelLabel = provenance.model_id || 'Not recorded';
    const provenanceLabel = provenance.recorded
      ? 'Recorded when generated'
      : 'Inferred from current Voice configuration';
    const facts = document.createElement('dl');
    facts.className = 'produce-inspector-facts';
    [
      ['Pause', Number(selected.pause_after_ms) ? `${Number(selected.pause_after_ms)} ms` : 'None'],
      ['Production Voice', selected.voice?.configuration_key
        || selected.voice?.resolved_speaker
        || (selected.voice?.valid ? 'Configured Voice' : 'Missing voice')],
      ['Model', modelLabel],
      ['Runtime', provenance.runtime || 'Not recorded'],
      ['Voice method', provenance.voice_method || selected.voice?.method || 'Not recorded'],
      ['Model provenance', provenanceLabel],
      ['Generated', selected.generated_at_utc || 'Not recorded'],
      ['Audio state', produceState(selected.state).label],
      [selected.state === 'stale' ? 'Stale reason' : 'Reason', produceReason(selected)],
    ].forEach(([term, value]) => {
      const row = document.createElement('div');
      row.append(produceText('dt', '', term), produceText('dd', '', value));
      facts.append(row);
    });

    const waveform = stableWaveform(selected);

    const actionRow = document.createElement('div');
    actionRow.className = 'produce-inspector-actions';
    const play = UI.button({
      label: 'Play chunk',
      variant: 'secondary',
      disabled: !selected.audio?.available,
      attributes: { 'data-produce-play-selected': '' },
      onClick: () => shell.player.set({
        state: 'playing',
        src: selected.audio?.url || null,
        position: 0,
        duration: Math.max(.01, (Number(selected.duration_ms) || 1000) / 1000),
        title: selected.character_name || selected.speaker || 'Audio chunk',
        subtitle: selected.text_excerpt || selected.text || 'Production audio',
      }),
    });
    const playIcon = document.createElement('i');
    playIcon.className = 'fas fa-play';
    playIcon.setAttribute('aria-hidden', 'true');
    play.prepend(playIcon);

    const regenerate = UI.button({
      label: selected.regenerate_action?.label === 'Generate'
        ? 'Generate this chunk' : 'Regenerate this chunk',
      variant: 'secondary',
      attributes: { 'data-produce-selected-action': '' },
      disabled: actions.busy || aggregate.process?.running || !selected.regenerate_action
        || selected.state === 'generating' || selected.state === 'missing_voice',
      onClick: () => actions.execute('selected', [selected.chunk_id]),
    });
    actionRow.append(play, regenerate);

    body.append(heading, index, textSection, direction, facts);
    if (selected.blockers?.length) body.append(blockerContent(selected.blockers, selected));
    body.append(waveform, actionRow, historyDisclosure(selected));
    inspector.setContent(body);
  };

  return Object.freeze({ render });
}
