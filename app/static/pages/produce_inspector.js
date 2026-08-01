'use strict';

import {
  produceReason, produceState, produceText, produceWords,
} from './produce_model.js';

const UI = globalThis.AlexandriaUI;

const formatScore = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${Math.round(numeric * 100)}%` : 'Not recorded';
};

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
  summary.append(label, UI.icon('chevron'));
  disclosure.append(summary, historyContent(selected));
  return disclosure;
}

function formatTakeDuration(value) {
  const milliseconds = Number(value) || 0;
  if (!milliseconds) return 'Duration unavailable';
  const seconds = milliseconds / 1000;
  return seconds >= 60
    ? `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
    : `${seconds.toFixed(seconds >= 10 ? 1 : 2)}s`;
}

function formatTakeDate(value) {
  if (!value) return 'Time unavailable';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString([], {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  });
}

function takeTitle(take, index) {
  if (take.current) return 'Current Take';
  if (take.kind === 'rendition') return `Processed version ${index + 1}`;
  return `Take ${index + 1}`;
}

function takeList({ selected, aggregate, shell, actions }) {
  const section = document.createElement('section');
  section.className = 'produce-takes-section';
  section.dataset.produceTakes = '';
  const header = document.createElement('header');
  header.className = 'produce-takes-heading';
  const count = Number(selected.takes?.take_count) || 0;
  header.append(
    produceText('span', 'utility-heading', 'Takes'),
    produceText('span', 'metadata', `${count.toLocaleString()} retained`),
  );
  section.append(header);
  const takes = Array.isArray(selected.takes?.items) ? selected.takes.items : [];
  if (!takes.length) {
    section.append(produceText(
      'p', 'metadata',
      'No retained Take exists yet. Generate this chunk to create the first immutable Take.',
    ));
    return section;
  }
  const list = document.createElement('div');
  list.className = 'produce-take-list';
  takes.forEach((take, index) => {
    const row = document.createElement('article');
    row.className = 'produce-take-row';
    row.dataset.produceTake = take.take_id;
    row.dataset.current = String(Boolean(take.current));
    row.dataset.kept = String(Boolean(take.kept));

    const copy = document.createElement('div');
    copy.className = 'produce-take-copy';
    const titleLine = document.createElement('div');
    titleLine.className = 'produce-take-title';
    titleLine.append(produceText('strong', '', takeTitle(take, index)));
    if (take.current) titleLine.append(produceText('span', 'produce-take-state', 'Current'));
    if (take.kept) titleLine.append(produceText('span', 'produce-take-state', 'Kept'));
    if (take.kind === 'rendition') titleLine.append(produceText('span', 'produce-take-state', 'Processed'));
    const provenance = take.generation?.provenance || {};
    const model = provenance.model_id || provenance.provider || 'Model not recorded';
    const detail = [
      formatTakeDate(take.created_at_utc),
      formatTakeDuration(take.audio?.duration_ms),
      model,
    ].join(' · ');
    copy.append(titleLine, produceText('p', 'metadata', detail));
    if (take.source_take_id) {
      copy.append(produceText(
        'p', 'metadata produce-take-lineage',
        `Derived from ${take.source_take_id}`,
      ));
    }

    const controls = document.createElement('div');
    controls.className = 'produce-take-actions';
    const play = UI.button({
      label: 'Play',
      variant: 'secondary',
      size: 'compact',
      disabled: !take.audio?.available,
      attributes: { 'data-produce-take-play': take.take_id },
      onClick: () => shell.player.set({
        state: 'playing',
        src: take.audio?.url || null,
        position: 0,
        duration: Math.max(.01, (Number(take.audio?.duration_ms) || 1000) / 1000),
        title: `${selected.character_name || selected.speaker || 'Audio'} — ${takeTitle(take, index)}`,
        subtitle: selected.text_excerpt || selected.text || 'Production audio Take',
      }),
    });
    const use = UI.button({
      label: take.current ? 'Current' : 'Use this take',
      variant: take.current ? 'secondary' : 'primary',
      size: 'compact',
      disabled: take.current || !take.promotable || actions.busy || aggregate.process?.running,
      attributes: {
        'data-produce-take-use': take.take_id,
        ...(take.promotion_blocked_reason ? { title: take.promotion_blocked_reason } : {}),
      },
      onClick: () => actions.useTake(selected, take),
    });
    const keep = UI.button({
      label: take.kept ? 'Unkeep' : 'Keep',
      variant: 'secondary',
      size: 'compact',
      disabled: actions.busy || aggregate.process?.running,
      attributes: { 'data-produce-take-keep': take.take_id },
      onClick: () => actions.toggleTakeKeep(selected, take),
    });
    const remove = UI.button({
      label: 'Delete',
      variant: 'secondary',
      size: 'compact',
      disabled: take.current || take.kept || actions.busy || aggregate.process?.running,
      attributes: { 'data-produce-take-delete': take.take_id },
      onClick: (event) => actions.reviewTakeDelete(selected, take, event.currentTarget),
    });
    controls.append(play, use, keep, remove);
    row.append(copy, controls);
    list.append(row);
  });
  section.append(list);
  return section;
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
    const factRows = [
      ['Pause', Number(selected.pause_after_ms) ? `${Number(selected.pause_after_ms)} ms` : 'None'],
      ['Production Voice', selected.voice?.configuration_key
        || selected.voice?.resolved_speaker
        || (selected.voice?.valid ? 'Configured Voice' : 'Missing voice')],
      ['Model', modelLabel],
      ['Generator runtime', provenance.runtime || 'Not recorded'],
      ['Voice method', provenance.voice_method || selected.voice?.method || 'Not recorded'],
      ['Model provenance', provenanceLabel],
      ['Audio source', selected.regeneration_lock?.locked
        ? 'Approved adaptation performance'
        : modelLabel],
      ['Regeneration', selected.regeneration_lock?.locked
        ? 'Locked for this approved chunk'
        : 'Available'],
    ];
    const fish = selected.fish_generation;
    if (fish?.provider) {
      factRows.push(
        ['Fish route', fish.style_route || fish.route_reason || 'Not recorded'],
        ['Fish prompt', fish.prompt_variant || 'Not recorded'],
        ['Fish candidates', Number(fish.candidate_count) || 'Not recorded'],
        ['Instruction fit', formatScore(fish.instruction_delivery_score)],
        ['Broad delivery fit', formatScore(fish.delivery_score)],
        ['Voice identity fit', formatScore(fish.identity_score)],
        ['Text accuracy', fish.text_validation_passed === true
          ? 'Passed' : fish.text_validation_passed === false ? 'Failed' : 'Not recorded'],
      );
    } else if (fish?.hybrid_attempted) {
      factRows.push(['Fish attempt', fish.fallback_used ? 'Fell back to local Qwen' : 'Attempted']);
    }
    factRows.push(
      ['Generated', selected.generated_at_utc || 'Not recorded'],
      ['Audio state', produceState(selected.state).label],
      [selected.state === 'stale' ? 'Stale reason' : 'Reason', produceReason(selected)],
    );
    factRows.forEach(([term, value]) => {
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
    play.prepend(UI.icon('play'));

    const regenerate = UI.button({
      label: selected.regeneration_lock?.locked
        ? 'Approved audio - regeneration locked'
        : selected.regenerate_action?.label === 'Generate'
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
    body.append(
      waveform,
      actionRow,
      takeList({ selected, aggregate, shell, actions }),
      historyDisclosure(selected),
    );
    inspector.setContent(body);
  };

  return Object.freeze({ render });
}
