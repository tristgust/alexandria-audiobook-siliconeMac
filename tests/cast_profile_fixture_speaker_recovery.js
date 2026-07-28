'use strict';

const { castPayload } = require('./cast_profile_fixture_cast_payloads.js');
const { castCharacter } = require('./cast_profile_fixture_characters.js');
const { produceFixture } = require('./produce_export_fixture_data.js');

const json = (value) => JSON.stringify(value);
const SCRIPT_SPEAKER = 'VICAR';
const CHARACTER_ID = 'cast:vicar';
const SAMPLE_LINE = 'You are welcome in this parish.';
const EXCLUDED_AUDIT = Object.freeze({
  name: 'Vicar',
  reason: 'Excluded during imported roster review as an incidental role.',
  evidence: [{
    source_quote: 'The parish register names the Vicar.',
    source_location: 'Characters 120–156',
  }],
});

function vicarCharacter() {
  const character = castCharacter(CHARACTER_ID, 'Vicar', 'needs_voice', 'speaking', 1);
  character.identity.script_voice_label = SCRIPT_SPEAKER;
  character.script_connection.resolved_script_voice_label = SCRIPT_SPEAKER;
  character.script_connection.representative_lines = [SAMPLE_LINE];
  character.character.expanded.representative_script_lines = [SAMPLE_LINE];
  return character;
}

function recoveryProjection(control) {
  return {
    script_speaker: SCRIPT_SPEAKER,
    display_name: 'Vicar',
    line_count: 1,
    sample_lines: [{ index: 12, speaker: SCRIPT_SPEAKER, text: SAMPLE_LINE, instruct: 'Formal but kind.' }],
    sample_lines_truncated: false,
    state: control.recoveryActive ? 'active' : 'eligible',
    blocked_reason: null,
    eligible: !control.recoveryActive,
    active_character_id: control.recoveryActive ? CHARACTER_ID : null,
    excluded_audit: [EXCLUDED_AUDIT],
  };
}

function history(control) {
  if (!control.recoveryActive && !control.recoveryUndone) return [];
  const added = {
    operation_id: 'speaker-add-vicar', operation: 'add',
    affected_speakers: [SCRIPT_SPEAKER], changed_script_indices: [],
    undone: Boolean(control.recoveryUndone), undoable: !control.recoveryUndone,
  };
  return control.recoveryUndone ? [{
    operation_id: 'speaker-undo-vicar', operation: 'undo',
    undoes_operation_id: added.operation_id, affected_speakers: [],
    changed_script_indices: [], undone: false, undoable: false,
  }, added] : [added];
}

function recoveryStatus(control, selected) {
  const entry = control.recoveryActive ? {
    character_id: CHARACTER_ID, canonical_name: 'Vicar', display_name: 'Vicar',
    aliases: [], resolution_status: 'resolved', line_count: 1,
    script_voice_name: SCRIPT_SPEAKER, script_voice_mapping: 'exact',
    script_voice_candidates: [SCRIPT_SPEAKER],
  } : null;
  return {
    available: true,
    script_fingerprint: 'fixture-script',
    roster_fingerprint: 'fixture-roster',
    entry_count: entry ? 1 : 0,
    speaker_counts: { [SCRIPT_SPEAKER]: 1 },
    selected_script_voice: selected ? SCRIPT_SPEAKER : null,
    lines: selected ? [{ index: 12, speaker: SCRIPT_SPEAKER, text: SAMPLE_LINE }] : [],
    entries: entry ? [entry] : [],
    speaker_recovery: selected ? recoveryProjection(control) : null,
    history: history(control),
  };
}

function producePayload() {
  const aggregate = produceFixture('normal');
  const blocked = aggregate.chunks.find((chunk) => chunk.state === 'missing_voice');
  Object.assign(blocked, {
    chunk_id: 'chunk:blocked-1', index: 12, speaker: SCRIPT_SPEAKER,
    character_name: 'Vicar', text: SAMPLE_LINE, text_excerpt: SAMPLE_LINE,
    voice: { ...blocked.voice, configuration_key: SCRIPT_SPEAKER },
    blockers: [{
      code: 'produce_voice_missing', title: 'Missing voice', blocking: true,
      explanation: 'Recover this excluded Script speaker before assigning a production Voice.',
      native_destination: 'more/advanced-character-operations', target_id: SCRIPT_SPEAKER,
    }],
  });
  aggregate.chunks = [blocked];
  aggregate.state = 'blocked';
  aggregate.counts = {
    current: 0, ready: 0, stale: 0, failed: 0, needs_listening: 0,
    needs_review: 0, generating: 0, missing_voice: 1,
  };
  aggregate.summary = {
    required_chunk_count: 1, current_count: 0, needs_generation_count: 0,
    needs_review_count: 0, failed_count: 0, missing_voice_count: 1,
    blocker_count: 1, complete: false,
  };
  aggregate.all_chunk_count = 1;
  aggregate.visible_chunk_count = 1;
  aggregate.selected_chunk_id = blocked.chunk_id;
  aggregate.selected_chunk = blocked;
  return aggregate;
}

function recoveryCastPayload(control, url) {
  const payload = castPayload(control, url);
  const vicar = vicarCharacter();
  payload.characters = [vicar, ...payload.characters];
  payload.selected_character_id = CHARACTER_ID;
  payload.selected_character = vicar;
  payload.selection_visible = true;
  payload.summary.character_count += 1;
  payload.summary.required_speaking_count += 1;
  payload.summary.blocker_count += 1;
  payload.filters.counts.all += 1;
  payload.filters.counts.needs_attention += 1;
  payload.filters.counts.unassigned += 1;
  payload.filters.counts.speaking_roles += 1;
  return payload;
}

function handleSpeakerRecoveryApi(context) {
  const { control, finish, receipt, request, url } = context;
  if (control.mode !== 'speaker-recovery') return false;
  if (url.pathname === '/api/produce' && request.method === 'GET') {
    finish(200, json(producePayload()), 'application/json');
    return true;
  }
  if (url.pathname === '/api/speaker_management/status' && request.method === 'GET') {
    finish(200, json(recoveryStatus(control, url.searchParams.get('speaker') === SCRIPT_SPEAKER)), 'application/json');
    return true;
  }
  if (url.pathname === '/api/speaker_management/action' && request.method === 'POST') {
    const valid = receipt.body?.operation === 'add'
      && receipt.body?.expected_script_fingerprint === 'fixture-script'
      && receipt.body?.payload?.script_speaker === SCRIPT_SPEAKER
      && !Object.hasOwn(receipt.body?.payload || {}, 'designed_voice_description');
    if (!valid) {
      finish(409, json({ detail: 'Fixture rejected an unsafe recovery payload.' }), 'application/json');
      return true;
    }
    if (control.recoveryRejectNext) {
      control.recoveryRejectNext = false;
      finish(409, json({
        detail: {
          code: 'speaker_management_conflict',
          message: 'The reviewed roster changed. Refresh and review again.',
        },
      }), 'application/json');
      return true;
    }
    control.recoveryActive = true;
    control.recoveryUndone = false;
    finish(200, json({
      operation: { operation_id: 'speaker-add-vicar', operation: 'add' },
      status: recoveryStatus(control, true),
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/speaker_management/undo' && request.method === 'POST') {
    if (receipt.body?.operation_id !== 'speaker-add-vicar') {
      finish(409, json({ detail: 'Fixture rejected an unknown operation.' }), 'application/json');
      return true;
    }
    if (control.undoRejectNext) {
      control.undoRejectNext = false;
      finish(409, json({
        detail: {
          code: 'speaker_management_undo_conflict',
          message: 'A newer identity change must be reviewed first.',
        },
      }), 'application/json');
      return true;
    }
    control.recoveryActive = false;
    control.recoveryUndone = true;
    finish(200, json({
      operation: { operation_id: 'speaker-undo-vicar', operation: 'undo' },
      status: recoveryStatus(control, true),
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/cast' && request.method === 'GET' && control.recoveryActive) {
    finish(200, json(recoveryCastPayload(control, url)), 'application/json');
    return true;
  }
  if (url.pathname === `/api/cast/characters/${encodeURIComponent(CHARACTER_ID)}`
      && request.method === 'GET' && control.recoveryActive) {
    finish(200, json(vicarCharacter()), 'application/json');
    return true;
  }
  return false;
}

module.exports = {
  CHARACTER_ID, EXCLUDED_AUDIT, SAMPLE_LINE, SCRIPT_SPEAKER,
  handleSpeakerRecoveryApi,
};
