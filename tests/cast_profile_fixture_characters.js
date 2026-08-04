'use strict';

function castCharacter(id, name, readiness = 'ready', speaking = 'speaking', lineCount = 18) {
  const nonSpeaking = speaking === 'non_speaking';
  const voiceValid = nonSpeaking || readiness !== 'needs_voice';
  const previewApproved = nonSpeaking || readiness === 'ready';
  return {
    character_id: id, display_name: name, canonical_name: name,
    speaking_role: speaking, required_for_completion: !nonSpeaking,
    readiness_state: readiness, voice_summary: voiceValid ? 'Avery · measured alto' : 'No production Voice',
    blocker_count: voiceValid ? 0 : 1,
    blockers: voiceValid ? [] : [{ code: 'cast_voice_missing', title: 'Missing voice', blocking: true }],
    identity: {
      stable_character_id: id, display_name: name, canonical_name: name,
      aliases: id === 'cast:clara' ? ['C. Leighton'] : [], role: nonSpeaking ? 'Silent witness' : 'Principal',
      speaking_state: speaking, species_or_type: 'Human', relationships: ['Connected to the household'],
      source_confidence: 'high', script_voice_label: name,
    },
    script_connection: {
      resolved_script_voice_label: name, mapping_method: 'exact', mapping_confidence: 'high',
      ambiguity_state: 'resolved', script_line_count: nonSpeaking ? 0 : lineCount,
      representative_lines: ['A fixture line supported by the script.'],
    },
    voice: {
      configuration_key: name,
      selected_production_method: voiceValid ? 'clone' : 'custom',
      selected_backend: 'qwen3_base',
      selected_voice: voiceValid ? 'Avery' : null,
      clone: {
        reference_source: 'owned recording',
        reference_audio_url: '/fixture-reference.wav',
        exact_reference_transcript: 'I knew the letter would arrive before dusk.',
        reference_audio_state: 'ready',
        controlled_capability: false,
        controlled_approval_state: 'not_required',
      },
      persistent_voice_description: 'Measured, warm, and exact.',
      representative_text: 'I knew the letter would arrive before dusk.',
      imported_dossier: id === 'cast:clara' ? {
        persona_summary: 'Analytical, dryly funny, and privately compassionate.',
        designed_voice_description: 'Adult woman. A clear adult alto with compact resonance, crisp diction, agile sardonic timing, and restrained warmth.',
        pitch: {
          value: 'Mid-low alto', basis: 'casting_recommendation', evidence_quotes: [],
        },
        weight_and_resonance: {
          value: 'Compact, grounded resonance.', basis: 'casting_recommendation', evidence_quotes: [],
        },
        texture_and_timbre: {
          value: 'Clear, dry timbre with restrained warmth.', basis: 'casting_recommendation', evidence_quotes: [],
        },
        accent_and_language: {
          value: 'Neutral British English.', basis: 'casting_recommendation', evidence_quotes: [],
        },
        casting_guidance: {
          value: 'Prioritize intelligence over fragility',
          basis: 'casting_recommendation', evidence_quotes: [],
        },
        uncertainties: ['No source-supported regional accent.'],
      } : null,
      preview: {
        status: previewApproved ? 'approved' : 'failed',
        listened: previewApproved,
        approved: previewApproved,
        audio_url: previewApproved ? '/fixture-preview.wav' : null,
      },
      designed_voice_state: 'not_selected',
      adapter: { state: 'not_selected' }, alias: { state: 'not_selected' },
      valid: voiceValid, blockers: voiceValid ? [] : [{ code: 'cast_voice_missing' }],
    },
    character: {
      summary: {
        canonical_name: name, display_name: name, aliases: [], role: nonSpeaking ? 'Silent witness' : 'Principal',
        speaking_state: speaking, species_or_type: 'Human', relationships: ['Connected to the household'],
        source_confidence: 'high',
      },
      expanded: {
        titles: [], nicknames: [], representative_script_lines: [
          name,
          'Chapter One',
          'THE BEGINNING OF THE LAST ADVENTURE.',
          'We waited by the station under a sky that turned slowly from silver to black',
          'At dawn, the telegram arrived.',
          '<img src=x onerror="globalThis.fixtureInjection=true">',
        ],
        script_line_count: nonSpeaking ? 0 : lineCount, unresolved_questions: [], conflicts: [],
      },
    },
    appearance: {
      status: 'not_started', summary: 'Dark hair, practical dress, and a weathered travelling coat.',
      stable_traits: ['Dark hair'], variants: [], conflicts: [], unknowns: [], optional: true,
    },
    advanced_voice_setup: {
      expressive_reference_state: 'not_started', owned_recording_preparation_state: 'not_started',
      dataset_state: 'not_started', adapter_training_state: 'not_started',
      compatibility_state: 'current', blockers: [], optional: true,
    },
  };
}

function roster(mode) {
  if (mode === 'empty' || mode === 'discovering') return [];
  if (mode === 'dense') {
    return Array.from({ length: 24 }, (_, index) => castCharacter(
      `cast:dense-${index + 1}`,
      index === 0 ? '<img src=x onerror="globalThis.fixtureInjection=true">' : `Character ${index + 1}`,
      index % 4 === 1 ? 'needs_voice' : index % 4 === 2 ? 'preview_recommended' : 'ready',
      index % 7 === 0 ? 'non_speaking' : 'speaking',
    ));
  }
  return [
    castCharacter('cast:clara', 'Clara Leighton'),
    castCharacter('cast:edmund', 'Edmund Fairfax', 'needs_voice', 'speaking', 42),
    castCharacter('cast:isobel', 'Isobel Marwell', 'preview_recommended', 'speaking', 7),
    castCharacter('cast:witness', 'The Witness', 'ready', 'non_speaking'),
  ];
}

function applyVoiceControl(character, control) {
  const catalogAssignment = control.libraryAssignments[character.character_id];
  if (catalogAssignment) {
    character.voice.library_voice_id = catalogAssignment.voice_id;
    character.voice.selected_production_method = catalogAssignment.production_method;
    character.voice.selected_backend = catalogAssignment.backend;
    character.voice.selected_voice = catalogAssignment.name;
    character.voice.clone.reference_source = catalogAssignment.name;
    character.voice.clone.reference_audio_url = catalogAssignment.preview_url;
    character.voice.clone.exact_reference_transcript = 'Exact reusable Voice transcript.';
    character.voice.clone.reference_audio_state = 'ready';
    character.voice.clone.controlled_capability = catalogAssignment.controlled;
    character.voice.clone.controlled_approval_state = catalogAssignment.controlled
      ? 'approved' : 'not_required';
    character.voice.preview = {
      status: 'approved', listened: true, approved: true,
      audio_url: catalogAssignment.preview_url,
    };
    character.voice.valid = true;
    character.voice.blockers = [];
    character.readiness_state = 'ready';
    character.blocker_count = 0;
    character.blockers = [];
    character.voice_summary = `${catalogAssignment.name} · ${catalogAssignment.method_label}`;
    return character;
  }
  if (control.savedConfig) {
    character.voice.selected_production_method = control.savedConfig.type;
    character.voice.selected_voice = control.savedConfig.voice;
    character.voice.persistent_voice_description = control.savedConfig.description;
    character.voice.clone.reference_source = control.savedConfig.ref_audio;
    character.voice.clone.reference_audio_url = control.savedConfig.ref_audio
      ? `/${control.savedConfig.ref_audio}` : null;
    character.voice.clone.exact_reference_transcript = control.savedConfig.ref_text;
    character.voice.clone.reference_audio_state = control.savedConfig.ref_audio ? 'ready' : 'missing';
  }
  character.voice.selected_backend = control.savedBackend;
  character.voice.clone.controlled_capability = control.savedBackend === 'qwen3_instruction_controlled';
  character.voice.clone.controlled_approval_state = character.voice.clone.controlled_capability
    ? 'approved' : 'not_required';
  character.voice.preview.approved = character.voice.preview.approved
    || character.voice.clone.controlled_capability;
  return character;
}

module.exports = { applyVoiceControl, castCharacter, roster };
