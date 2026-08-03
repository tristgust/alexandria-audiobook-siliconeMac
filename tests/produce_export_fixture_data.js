'use strict';

function produceRow(id, state, overrides = {}) {
  const speaker = overrides.speaker || 'Alistair Wren';
  return {
    chunk_id: `chunk:${id}`, index: Number.parseInt(id.replace(/\D/g, ''), 10) || 1,
    speaker, character_name: speaker, text: overrides.text || `Fixture excerpt for ${id}.`,
    text_excerpt: overrides.text || `Fixture excerpt for ${id}.`,
    delivery_direction: overrides.direction || 'Measured, with intent',
    pause_after_ms: 350, duration_ms: state === 'ready' || state === 'missing_voice' ? null : 9000,
    state, reason: overrides.reason || (state === 'stale' ? 'audio_fingerprint_mismatch' : null),
    selected: id === 'stale-1', required_for_completion: true,
    voice: { valid: state !== 'missing_voice', configuration_key: speaker, method: 'built_in' },
    audio: {
      available: ['current', 'needs_listening'].includes(state),
      url: ['current', 'needs_listening'].includes(state) ? `/fixture-audio/${id}.mp3` : null,
      stale_audio_available: state === 'stale',
    },
    review: { listening_required: state === 'needs_listening', listening_state: null },
    blockers: state === 'missing_voice' ? [{
      code: 'produce_voice_missing', title: 'Missing voice',
      explanation: 'Assign a production Voice in Cast.', native_destination: 'cast',
      target_id: 'cast:alistair', blocking: true,
    }] : state === 'failed' ? [{
      code: 'produce_audio_failed', title: 'Generation failed',
      explanation: 'Retry this chunk.', native_destination: 'produce',
      target_id: `chunk:${id}`, blocking: true,
    }] : [],
    regenerate_action: state === 'missing_voice' || state === 'generating' ? null : {
      id: state === 'ready' ? 'generate_chunk' : 'regenerate_chunk',
      label: state === 'ready' ? 'Generate' : 'Regenerate',
    },
  };
}

function produceFixture(mode) {
  const base = [
    produceRow('ready-1', 'ready', { speaker: 'Clara Leighton' }),
    produceRow('current-1', 'current', { speaker: 'Edmund Fairfax' }),
    produceRow('stale-1', 'stale', { reason: 'Direction edited after audio generation.' }),
    produceRow('failed-1', 'failed', { speaker: 'Isobel Marwell' }),
    produceRow('listen-1', 'needs_listening', { speaker: 'Robert Bain' }),
    produceRow('blocked-1', 'missing_voice', { speaker: 'Jane Whitfield' }),
  ];
  const attentionStates = [
    ...Array(12).fill('ready'),
    ...Array(4).fill('stale'),
    ...Array(2).fill('failed'),
    ...Array(7).fill('needs_listening'),
  ];
  const chunks = mode === 'empty' ? [] : mode === 'dense'
    ? Array.from({ length: 24 }, (_, index) => produceRow(
      `dense-${index + 1}`, ['ready', 'current', 'stale', 'needs_listening'][index % 4],
      { text: index === 0 ? '<img src=x onerror="globalThis.fixtureInjection=true">' : `Dense fixture ${index + 1}` },
    )) : mode === 'mixed'
      ? Array.from({ length: 5275 }, (_, index) => {
        const state = index < 5250 ? 'current' : attentionStates[index - 5250];
        const row = produceRow(index === 5263 ? 'stale-1' : `scale-${index + 1}`, state, {
          speaker: index % 7 === 0 ? 'Clara Leighton' : 'Alistair Wren',
          text: index === 0
            ? '<img src=x onerror="globalThis.fixtureInjection=true"> Dense production fixture.'
            : `Production chunk ${index + 1} of 5275.`,
        });
        row.index = index + 1;
        return row;
      })
      : base;
  const selected = chunks.find((item) => item.chunk_id === 'chunk:stale-1') || chunks[0] || null;
  const running = mode === 'running';
  const counts = mode === 'running'
    ? { current: 178, ready: 12, stale: 4, failed: 2, needs_listening: 7, needs_review: 0, generating: 0, missing_voice: 0 }
    : Object.fromEntries(['current', 'ready', 'stale', 'failed', 'needs_listening', 'needs_review', 'generating', 'missing_voice']
      .map((state) => [state, chunks.filter((item) => item.state === state).length]));
  const required = Object.values(counts).reduce((sum, value) => sum + value, 0);
  return {
    schema_version: 1, state: running ? 'running' : mode === 'blocked' ? 'blocked' : chunks.length ? 'ready' : 'not_started',
    summary: {
      required_chunk_count: required, current_count: counts.current,
      needs_generation_count: counts.ready + counts.stale,
      needs_review_count: counts.needs_listening, failed_count: counts.failed,
      missing_voice_count: counts.missing_voice, blocker_count: counts.missing_voice + counts.failed,
      complete: false,
    },
    counts, chunks, all_chunk_count: required, visible_chunk_count: chunks.length,
    selected_chunk_id: selected?.chunk_id || null, selected_chunk: selected,
    selection_visible: Boolean(selected),
    process: {
      running, cancel_requested: false, total_count: 16, completed_count: running ? 6 : 0,
      failed_count: running ? 1 : 0, cancelled_count: 0, queued_chunk_ids: [],
      logs: running ? ['Generation is running.'] : [],
    },
    primary_action: running
      ? { id: 'cancel_produce_generation', label: 'Cancel generation', endpoint: '/api/produce/cancel' }
      : { id: 'generate_missing_stale_audio', label: 'Generate missing and stale audio', endpoint: '/api/produce/generate', mode: 'missing_stale' },
    secondary_actions: [{ id: 'regenerate_all_audio', label: 'Regenerate all audio', mode: 'regenerate_all', destructive: true }],
    fingerprints: { chunks: 'fixture-chunks' },
  };
}

function exportFixture(mode) {
  const complete = mode === 'complete';
  const running = mode === 'running';
  const blocked = ['blocked', 'empty'].includes(mode);
  const chapters = mode === 'empty' ? [] : Array.from({ length: mode === 'dense' ? 24 : 6 }, (_, index) => ({
    chapter_id: `chapter:${index}`, order: index,
    name: index === 0 ? 'The Letter Arrives' : `Chapter ${index + 1}`,
    start_ms: index * 1800000, end_ms: (index + 1) * 1800000,
  }));
  const current = {
    format: 'm4b', filename: 'audiobook.m4b', state: complete ? 'current' : 'missing',
    exists: complete, playback_url: complete ? '/fixture-audio/audiobook.m4b' : null,
    download_url: complete ? '/api/audiobook_m4b' : null,
    duration_ms: complete ? 45936000 : null, size_bytes: complete ? 1200000000 : null,
  };
  const blockers = blocked ? [{
    code: mode === 'empty' ? 'export_chapters_required' : 'export_produce_incomplete',
    title: mode === 'empty' ? 'No chapters are available' : 'Produce is incomplete',
    explanation: mode === 'empty' ? 'Review Script chapter structure.' : 'Finish required audio in Produce.',
    native_destination: mode === 'empty' ? 'script' : 'produce',
    target_id: 'fixture:blocker', blocking: true,
  }] : [];
  const metadata = blocked ? { title: '', author: '', narrator: '', year: '', description: '' } : {
    title: 'The First Correspondence', author: 'Isobel Marwell',
    narrator: 'Alistair Wren', year: '2026', description: 'A novel.',
  };
  const plan = {
    metadata, formats: ['m4b'], chapter_mode: 'smart', chapters, blockers,
    safe_to_execute: !blocked, plan_fingerprint: 'fixture-export-plan',
    dependency_fingerprint: 'fixture-export-dependencies',
    output_filenames: { m4b: 'audiobook.m4b' },
  };
  const recordedFormats = complete ? ['m4b'] : ['mp3'];
  return {
    schema_version: 1, state: running ? 'running' : complete ? 'complete' : blocked ? 'blocked' : 'ready',
    metadata, formats: recordedFormats, chapter_mode: 'smart', chapters,
    cover: { exists: false, relative_path: null },
    outputs: { m4b: current, mp3: { format: 'mp3', filename: 'cloned_audiobook.mp3', state: 'missing' }, audacity: { format: 'audacity', filename: 'audacity_export.zip', state: 'missing' } },
    selected_outputs: [current],
    summary: { selected_format_count: 1, current_output_count: complete ? 1 : 0, chapter_count: chapters.length, blocker_count: blockers.length, complete },
    blockers, process: {
      running,
      cancel_requested: false,
      formats: running ? ['m4b'] : [],
      logs: running ? ['Loading production audio.'] : [],
      phase: running ? 'loading_audio' : 'idle',
      phase_label: running ? 'Loading production audio' : 'Idle',
      completed_count: running ? 5059 : 0,
      total_count: running ? 5328 : 0,
      overall_percent: running ? 47.7 : 0,
      progress_message: running ? 'Loaded 5,059 of 5,328 chunks · Narrator' : null,
      started_at: running ? new Date(Date.now() - 9 * 60 * 1000).toISOString() : null,
    },
    primary_action: running ? { id: 'cancel_export_build', label: 'Cancel build', endpoint: '/api/export/cancel' }
      : !blocked && !complete ? { id: 'build_export', label: 'Build audiobook', endpoint: '/api/export/build' } : null,
    plan, receipt: complete ? { build_id: 'fixture-build', formats: ['m4b'] } : null,
    player: complete ? { format: 'm4b', url: current.playback_url, duration_ms: current.duration_ms } : null,
    fingerprints: { dependencies: plan.dependency_fingerprint, plan: plan.plan_fingerprint },
  };
}

module.exports = { exportFixture, produceFixture };
