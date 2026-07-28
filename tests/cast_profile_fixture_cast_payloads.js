'use strict';

const { applyVoiceControl, roster } = require('./cast_profile_fixture_characters.js');

function castPayload(control, url) {
  let characters = roster(control.mode).map((character) => applyVoiceControl(character, control));
  const filter = url.searchParams.get('filter') || 'all';
  const search = (url.searchParams.get('search') || '').toLowerCase();
  const all = characters;
  if (search) characters = characters.filter((item) => item.display_name.toLowerCase().includes(search));
  if (filter === 'needs_attention') characters = characters.filter((item) => item.readiness_state !== 'ready');
  if (filter === 'unassigned') characters = characters.filter((item) => item.required_for_completion && !item.voice.valid);
  if (filter === 'speaking_roles') characters = characters.filter((item) => item.speaking_role === 'speaking');
  if (filter === 'non_speaking') characters = characters.filter((item) => item.speaking_role === 'non_speaking');
  if (filter === 'ready') characters = characters.filter((item) => item.readiness_state === 'ready');
  const requested = url.searchParams.get('selected_character_id');
  const selected = all.find((item) => item.character_id === requested)
    || all.find((item) => item.character_id === control.selected)
    || all[0] || null;
  const required = all.filter((item) => item.required_for_completion);
  const blockers = required.reduce((sum, item) => sum + item.blocker_count, 0);
  const discovering = control.mode === 'discovering';
  return {
    schema_version: 1,
    summary: {
      state: discovering ? 'running' : blockers ? 'blocked' : all.length ? 'complete' : 'not_started',
      character_count: all.length, required_speaking_count: required.length,
      ready_required_count: required.filter((item) => item.readiness_state === 'ready').length,
      blocker_count: blockers, complete: all.length > 0 && blockers === 0,
    },
    filters: {
      active: filter, search,
      counts: {
        all: all.length, needs_attention: all.filter((item) => item.readiness_state !== 'ready').length,
        unassigned: all.filter((item) => item.required_for_completion && !item.voice.valid).length,
        speaking_roles: all.filter((item) => item.speaking_role === 'speaking').length,
        non_speaking: all.filter((item) => item.speaking_role === 'non_speaking').length,
        ready: all.filter((item) => item.readiness_state === 'ready').length,
      },
    },
    characters, selected_character_id: selected?.character_id || null, selected_character: selected,
    selection_visible: Boolean(selected && characters.some((item) => item.character_id === selected.character_id)),
    blockers: [], fingerprints: { script: 'fixture-script', roster: 'fixture-roster', voice_config: 'fixture-voice' },
    process: {
      running: discovering,
      logs: discovering ? ['Discovering roster passage 3/42'] : [],
    },
    progress: discovering ? {
      status: 'resumable', completed_passages: 2, total_passages: 42, next_passage: 3,
    } : {},
  };
}

function visualStatus(control) {
  const state = control.visual;
  const entry = {
    entry_id: control.selected, canonical_name: 'Clara Leighton', display_name: 'Clara Leighton',
    entity_kind: 'character', status: state === 'complete' ? 'complete' : state === 'error' ? 'invalid' : 'absent',
    observation_count: state === 'complete' ? 4 : 0, variant_count: 0, conflict_count: 0,
    image_prompt_summary: state === 'complete' ? 'Dark hair and a weathered travelling coat.' : null,
    error: state === 'error' ? 'Fixture dossier invalid.' : null,
  };
  return {
    enabled_by_default: false, approved_roster_available: state !== 'disabled',
    context_error: state === 'disabled' ? 'Fixture roster unavailable.' : null,
    process: { running: state === 'running' },
    progress: {
      status: state === 'running' ? 'running' : state === 'complete' ? 'complete' : 'idle',
      completed_passages: state === 'running' ? 2 : state === 'complete' ? 6 : 0,
      total_passages: 6,
    },
    complete_count: state === 'complete' ? 1 : 0, absent_count: state === 'idle' ? 1 : 0,
    invalid_count: state === 'error' ? 1 : 0, entries: [entry],
  };
}

module.exports = { castPayload, visualStatus };
