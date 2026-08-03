'use strict';

import { produceText } from './produce_model.js';

const UI = globalThis.AlexandriaUI;

function field({ id, label, value, min, max, step = '0.1', data, description = '' }) {
  return UI.field({
    id,
    label,
    type: 'number',
    value: String(value),
    description,
    attributes: {
      min, max, step, inputmode: 'decimal', [data]: '',
    },
  });
}

function numberValue(wrapper, fallback = null) {
  const raw = wrapper.querySelector('input')?.value?.trim() || '';
  if (!raw) return fallback;
  const value = Number(raw);
  return Number.isFinite(value) ? value : fallback;
}

function currentTake(selected) {
  return (selected.takes?.items || []).find((take) => take.current) || null;
}

function sourceTake(selected, take) {
  if (!take?.source_take_id) return null;
  return (selected.takes?.items || []).find((item) => item.take_id === take.source_take_id) || null;
}

function metric(value, suffix = ' dBFS') {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(1)}${suffix}` : 'Not measured';
}

function masteredSummary(take, selected, actions, busy) {
  if (take?.processing?.operation !== 'publication_mastering') return null;
  const processing = take.processing || {};
  const metrics = processing.metrics_after || {};
  const provenance = processing.provenance || {};
  const c2pa = provenance.c2pa || {};
  const watermark = provenance.watermark || {};
  const panel = document.createElement('section');
  panel.className = 'produce-mastering-current';
  panel.dataset.masteringCurrent = '';
  panel.append(
    produceText('strong', '', 'Current mastered rendition'),
    produceText(
      'p', 'metadata',
      `Estimated loudness ${metric(metrics.estimated_loudness_dbfs)} · peak ${metric(metrics.estimated_true_peak_dbfs)}.`,
    ),
    produceText(
      'p', 'metadata',
      `C2PA ${c2pa.structural_status || 'not present'}; signer trust ${c2pa.signer_trust || 'not evaluated'}. Watermark ${watermark.structural_status || 'not present'}; ownership ${watermark.ownership_trust || 'not evaluated'}.`,
    ),
    produceText(
      'p', 'metadata',
      'Structural provenance does not establish Voice authorization or human approval.',
    ),
  );
  const source = sourceTake(selected, take);
  if (source?.promotable) {
    panel.append(UI.button({
      label: 'Bypass to source Take',
      variant: 'secondary',
      size: 'compact',
      disabled: busy,
      attributes: { 'data-mastering-bypass': source.take_id },
      onClick: () => actions.useTake(selected, source),
    }));
  }
  return panel;
}

function processPanel(process, actions, selected, take) {
  if (!process?.running && !process?.result && !['failed', 'stale', 'cancelled'].includes(process?.status)) return null;
  const panel = document.createElement('section');
  panel.className = 'produce-mastering-process';
  panel.dataset.masteringProcess = process.status || 'idle';
  if (process.running) {
    panel.append(
      UI.progress({
        label: 'Publication mastering',
        state: 'running',
        value: Math.round((Number(process.completed_count || 0) / Math.max(1, Number(process.total_count || 7))) * 100),
        message: process.progress_message || 'Preparing mastered child rendition.',
      }),
      UI.button({
        label: process.cancel_requested ? 'Cancelling…' : 'Cancel mastering',
        variant: 'secondary',
        size: 'compact',
        disabled: process.cancel_requested || !process.background_job_id,
        attributes: { 'data-mastering-cancel': '' },
        onClick: () => actions.cancelMastering(process.background_job_id),
      }),
    );
    return panel;
  }
  const copy = {
    succeeded: ['Mastering complete', 'The mastered child is current and requires Final Listen approval.'],
    stale: ['Mastering discarded', 'The source Take, registry, or Script order changed before publication. No child was exposed.'],
    cancelled: ['Mastering cancelled', 'No mastered rendition was published.'],
    failed: ['Mastering failed', process.last_error || 'The candidate failed before publication.'],
  }[process.status] || ['Mastering status', process.progress_message || 'No active mastering job.'];
  const masteringResultIsCurrent = Boolean(
    process.result?.take_id
    && take?.take_id === process.result.take_id
    && take?.processing?.operation === 'publication_mastering'
    && selected.final_listen?.current_take_pinned !== true
  );
  panel.append(UI.notice({
    tone: process.status === 'succeeded' ? 'success' : process.status === 'failed' ? 'error' : 'information',
    title: copy[0],
    body: copy[1],
    action: process.status === 'succeeded'
      && masteringResultIsCurrent
      && process.result?.operation_id
      && process.result?.registry_fingerprint
      ? UI.button({
        label: 'Undo mastering', variant: 'secondary', size: 'compact',
        attributes: { 'data-mastering-undo': '' },
        onClick: () => actions.undoTakeOperation(
          process.result.operation_id,
          process.result.registry_fingerprint,
        ),
      }) : null,
  }));
  return panel;
}

export function createMasteringSection({ selected, aggregate, actions }) {
  const section = document.createElement('section');
  section.className = 'produce-mastering';
  section.dataset.produceMastering = '';
  const heading = document.createElement('header');
  heading.className = 'produce-mastering-heading';
  heading.append(
    produceText('span', 'utility-heading', 'Publication audio'),
    produceText('h3', '', 'Mastering'),
  );
  section.append(heading);
  const take = currentTake(selected);
  const process = aggregate.mastering_process || {};
  const currentPanel = masteredSummary(take, selected, actions, actions.busy || process.running);
  if (currentPanel) section.append(currentPanel);
  const progress = processPanel(process, actions, selected, take);
  if (progress) section.append(progress);

  if (!take) {
    section.append(UI.notice({
      tone: 'information', title: 'Current Take required',
      body: 'Select or generate current audio before publication mastering.',
    }));
    return section;
  }
  if (!selected.final_listen?.current_take_pinned) {
    section.append(UI.notice({
      tone: 'warning', title: 'Final Listen pin required',
      body: 'Listen in chapter context and pin this exact current Take before mastering it.',
    }));
    return section;
  }
  if (process.running) return section;

  const form = document.createElement('div');
  form.className = 'produce-mastering-form';
  const sampleRate = Number(take.audio?.sample_rate) || 24000;
  const maximumLowPass = Math.max(3000, Math.floor(sampleRate / 2) - 100);
  const defaultLowPass = Math.min(10000, maximumLowPass);
  const gain = field({ id: `master-gain-${selected.index}`, label: 'Gain (dB)', value: 0, min: -12, max: 12, data: 'data-mastering-gain' });
  const highPass = field({ id: `master-hp-${selected.index}`, label: 'High-pass (Hz)', value: 70, min: 20, max: 500, step: '1', data: 'data-mastering-high-pass' });
  const lowPass = field({
    id: `master-lp-${selected.index}`,
    label: 'Low-pass (Hz)',
    value: defaultLowPass,
    min: 3000,
    max: maximumLowPass,
    step: '1',
    data: 'data-mastering-low-pass',
    description: `Maximum for this ${sampleRate.toLocaleString()} Hz source: ${maximumLowPass.toLocaleString()} Hz.`,
  });
  const ratio = field({ id: `master-ratio-${selected.index}`, label: 'Compression ratio', value: 2, min: 1.1, max: 20, data: 'data-mastering-ratio' });
  const loudness = field({ id: `master-loudness-${selected.index}`, label: 'Target loudness (dBFS)', value: -20, min: -30, max: -10, data: 'data-mastering-loudness' });
  const ceiling = field({ id: `master-ceiling-${selected.index}`, label: 'Peak ceiling (dBFS)', value: -1, min: -6, max: -0.1, data: 'data-mastering-ceiling' });
  form.append(gain, highPass, lowPass, ratio, loudness, ceiling);

  const room = document.createElement('details');
  room.className = 'produce-mastering-room';
  const roomSummary = document.createElement('summary');
  roomSummary.textContent = 'Approved room correction';
  const roomToggle = document.createElement('label');
  roomToggle.className = 'produce-mastering-check';
  const roomEnabled = document.createElement('input');
  roomEnabled.type = 'checkbox';
  roomEnabled.dataset.masteringRoomEnabled = '';
  roomToggle.append(roomEnabled, document.createTextNode(' Apply a reviewed room profile'));
  const roomId = UI.field({
    id: `master-room-id-${selected.index}`, label: 'Profile ID', value: '',
    attributes: { maxlength: 120, 'data-mastering-room-id': '' },
  });
  const roomGain = field({ id: `master-room-gain-${selected.index}`, label: 'Profile gain (dB)', value: 0, min: -6, max: 6, data: 'data-mastering-room-gain' });
  room.append(
    roomSummary,
    produceText('p', 'metadata', 'Use only a specifically reviewed corrective profile. This is not a reverb or effects preset.'),
    roomToggle,
    roomId,
    roomGain,
  );

  const apply = UI.button({
    label: 'Review mastering plan',
    variant: 'secondary',
    disabled: actions.busy,
    attributes: { 'data-mastering-review': '' },
    onClick: (event) => {
      const roomCorrection = roomEnabled.checked ? {
        approved: true,
        profile_id: roomId.querySelector('input')?.value?.trim() || '',
        gain_db: numberValue(roomGain, 0),
      } : null;
      actions.reviewMastering(selected, take, {
        schema_version: 1,
        gain_db: numberValue(gain, 0),
        high_pass_hz: numberValue(highPass, null),
        low_pass_hz: numberValue(lowPass, null),
        compression: {
          enabled: true,
          threshold_dbfs: -22,
          ratio: numberValue(ratio, 2),
          attack_ms: 8,
          release_ms: 120,
        },
        normalization: {
          enabled: true,
          target_loudness_dbfs: numberValue(loudness, -20),
          maximum_gain_db: 8,
        },
        limiter_ceiling_dbfs: numberValue(ceiling, -1),
        ...(roomCorrection ? { room_correction: roomCorrection } : {}),
      }, event.currentTarget);
    },
  });
  section.append(
    produceText('p', 'metadata', 'Creates one immutable child rendition; the source Take remains immutable. Pitch shifting, chorus, dramatic reverb, voice transformation, and arbitrary multitrack editing are rejected.'),
    form,
    room,
    apply,
  );
  return section;
}
