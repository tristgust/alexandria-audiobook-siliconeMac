'use strict';

const { applyVoiceControl, roster } = require('./cast_profile_fixture_characters.js');
const { castPayload } = require('./cast_profile_fixture_cast_payloads.js');
const { voiceLibraryPayload } = require('./cast_profile_fixture_voice_library.js');

const json = (value) => JSON.stringify(value);

function handleVoiceApi(context) {
  const { url, request, receipt, finish, control } = context;
  if (url.pathname === '/api/voice-library' && request.method === 'GET') {
    if (control.deferPostSaveRefresh && control.savedConfig) {
      control.pending.push(() => finish(200, json(voiceLibraryPayload(control)), 'application/json'));
      return true;
    }
    finish(200, json(voiceLibraryPayload(control)), 'application/json');
    return true;
  }
  if (url.pathname === '/api/voice-library/assign' && request.method === 'POST') {
    const resources = voiceLibraryPayload(control).voices;
    const resource = resources.find((item) => item.voice_id === receipt.body?.voice_id);
    if (!resource?.assignment?.supported) {
      finish(409, json({ detail: 'Fixture Voice cannot be assigned.' }), 'application/json');
      return true;
    }
    control.libraryAssignments[receipt.body.character_id] = {
      voice_id: resource.voice_id,
      name: resource.name,
      method_label: resource.method_label,
      production_method: resource.assignment.production_method,
      backend: resource.method === 'instruction_controlled'
        ? 'qwen3_instruction_controlled' : 'qwen3_base',
      controlled: resource.method === 'instruction_controlled',
      preview_url: resource.preview.url,
    };
    control.voiceAssignments += 1;
    control.selected = receipt.body.character_id;
    const character = applyVoiceControl(
      roster('normal').find((item) => item.character_id === receipt.body.character_id)
        || roster('normal')[0],
      control,
    );
    finish(200, json({
      status: 'assigned', voice_id: resource.voice_id,
      voice_name: resource.name, character,
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/voice-library/built-in-range-preview' && request.method === 'POST') {
    finish(200, json({
      status: 'generated',
      audio_url: '/fixture-built-in-range.wav',
      voice: receipt.body?.voice,
      persistent_description: receipt.body?.persistent_description,
      sequence: ['baseline', 'happy', 'sad', 'angry'].map((id) => ({ id })),
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/cast') {
    if (control.mode === 'error') {
      finish(503, json({ detail: 'Fixture Cast unavailable.' }), 'application/json');
      return true;
    }
    if (control.mode === 'loading' || (control.deferPostSaveRefresh && control.savedConfig)) {
      control.pending.push(() => finish(200, json(castPayload(control, url)), 'application/json'));
      return true;
    }
    finish(200, json(castPayload(control, url)), 'application/json');
    return true;
  }
  if (url.pathname.startsWith('/api/cast/characters/')) {
    const id = decodeURIComponent(url.pathname.slice('/api/cast/characters/'.length));
    const character = applyVoiceControl(
      roster(control.mode).find((item) => item.character_id === id) || roster('normal')[0],
      control,
    );
    character.voice.selected_voice = control.savedVoice;
    control.selected = character.character_id;
    finish(200, json(character), 'application/json');
    return true;
  }
  if (url.pathname === '/api/save_voice_config') {
    if (control.mode === 'save-error') {
      finish(500, json({ detail: 'Fixture save failed.' }), 'application/json');
      return true;
    }
    const update = Object.values(receipt.body || {})[0] || {};
    control.savedConfig = update;
    control.savedVoice = update.voice || control.savedVoice;
    if (update.clone_backend) {
      control.savedBackend = update.clone_backend;
      if (update.clone_backend === 'qwen3_instruction_controlled') control.controlledSaves += 1;
    }
    finish(200, json({ status: 'saved' }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/voice_backend/capabilities') {
    finish(200, json({
      expressive_clone: {
        supported: false,
        experimental_preview_available: true,
        preview_and_manual_review_required: true,
      },
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/voices') {
    finish(200, json([{
      name: 'Clara Leighton',
      config: {
        type: 'clone', clone_backend: control.savedBackend,
        ref_audio: 'clone_voices/clara.wav',
        ref_text: 'I knew the letter would arrive before dusk.',
        character_style: 'Measured, warm, and exact.',
        seed: '-1',
        instruction_clone_temperature: 0.75,
        instruction_clone_top_k: 50,
        instruction_clone_top_p: 0.95,
        instruction_clone_repetition_penalty: 1.5,
        instruction_clone_max_tokens: 2000,
      },
    }]), 'application/json');
    return true;
  }
  if (url.pathname === '/api/clone_voices/controlled_preview') {
    finish(200, json({
      requires_listen_confirmation: true,
      preview_fingerprint: 'preview-controlled-1',
      configuration_fingerprint: 'configuration-controlled-1',
      audio_url: '/fixture-controlled.wav',
      audio_duration_seconds: 1.0,
      real_time_factor: 0.5,
      settings: {
        temperature: 0.75, top_k: 50, top_p: 0.95,
        repetition_penalty: 1.5, max_tokens: 2000, seed: -1,
      },
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/clone_voices/controlled_preview/confirm') {
    control.controlledConfirmations += 1;
    finish(200, json({ approval_token: 'controlled-approval-token' }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/voice_design/list') {
    finish(200, json([{ name: 'Avery', description: 'Measured alto' }]), 'application/json');
    return true;
  }
  if (url.pathname === '/api/voice_design/accent_status' && request.method === 'POST') {
    const description = String(receipt.body?.description || '');
    const accentDetected = /french accent/i.test(description);
    finish(200, json({
      status: accentDetected ? 'accent_pipeline' : 'ordinary_design',
      accent_detected: accentDetected,
      accent_label: accentDetected ? 'French' : null,
      native_language: accentDetected ? 'French' : null,
      output_language: receipt.body?.output_language || 'English',
      sequence: accentDetected ? 'native_seed_design -> output_clone' : 'ordinary_design',
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/voice_design/range-preview' && request.method === 'POST') {
    const description = String(receipt.body?.description || '');
    const accentDetected = /french accent/i.test(description);
    const fullRegeneration = receipt.body?.force_regenerate === true;
    const stem = /race definition a/i.test(description) ? 'a'
      : /race definition b/i.test(description) ? 'b' : 'audition';
    const send = () => finish(200, json({
      status: fullRegeneration ? 'regenerated_all' : 'generated',
      audio_url: `/fixture-designed-${stem}-range.wav${fullRegeneration ? '?revision=2' : ''}`,
      clone_source_url: `/fixture-designed-${stem}.wav`,
      clone_source_text: receipt.body?.sample_text || '',
      delivery_backend: 'fish_s21_cloud',
      persona_context_applied: Boolean(receipt.body?.persona_context),
      preview_fingerprint: 'a'.repeat(64),
      revision: fullRegeneration ? 2 : 0,
      full_regeneration: fullRegeneration,
      warnings: [],
      all_lanes_distinct: true,
      sequence: ['baseline', 'happy', 'sad', 'angry'].map((id) => ({
        id,
        label: id[0].toUpperCase() + id.slice(1),
        variance_status: id === 'baseline' ? null : 'distinct',
        reference_identity_mode: 'shared_neutral_identity',
      })),
      accent_pipeline: {
        applied: accentDetected,
        label: accentDetected ? 'French' : null,
        native_language: accentDetected ? 'French' : null,
        output_language: receipt.body?.language || 'English',
        sequence: accentDetected ? 'native_seed_design -> output_clone' : 'direct_voice_design',
      },
    }), 'application/json');
    if (control.deferNextDesignedPreview) {
      control.deferNextDesignedPreview = false;
      control.designedPreviewPending.push(send);
      return true;
    }
    send();
    return true;
  }
  if (url.pathname === '/api/voice_design/range-preview/regenerate'
      && request.method === 'POST') {
    const lane = String(receipt.body?.lane || 'happy');
    finish(200, json({
      status: 'regenerated',
      audio_url: `/fixture-designed-audition-range.wav?revision=1`,
      clone_source_url: '/fixture-designed-audition.wav',
      clone_source_text: 'I knew the letter would arrive before dusk.',
      delivery_backend: 'fish_s21_cloud',
      preview_fingerprint: 'a'.repeat(64),
      revision: 1,
      regenerated_lane: lane,
      warnings: [],
      all_lanes_distinct: true,
      sequence: ['baseline', 'happy', 'sad', 'angry'].map((id) => ({
        id,
        label: id[0].toUpperCase() + id.slice(1),
        variance_status: id === 'baseline' ? null : 'distinct',
        reference_identity_mode: 'shared_neutral_identity',
      })),
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/voice_design/preview' && request.method === 'POST') {
    const description = String(receipt.body?.description || '');
    const accentDetected = /french accent/i.test(description);
    const audioUrl = /race definition a/i.test(description) ? '/fixture-designed-a.wav'
      : /race definition b/i.test(description) ? '/fixture-designed-b.wav'
        : '/fixture-designed-audition.wav';
    const send = () => finish(200, json({
      status: 'ok', audio_url: audioUrl,
      accent_pipeline: {
        applied: accentDetected,
        label: accentDetected ? 'French' : null,
        native_language: accentDetected ? 'French' : null,
        output_language: receipt.body?.language || 'English',
        sequence: accentDetected ? 'native_seed_design -> output_clone' : 'direct_voice_design',
      },
    }), 'application/json');
    if (control.deferNextDesignedPreview) {
      control.deferNextDesignedPreview = false;
      control.designedPreviewPending.push(send);
      return true;
    }
    send();
    return true;
  }
  if (url.pathname === '/api/voice_design/save' && request.method === 'POST') {
    finish(200, json({
      status: 'saved',
      voice_id: 'clara-designed-fixture',
      scope: receipt.body?.scope || 'project',
      audition_bundle_path: 'designed_voices/clara-designed-fixture.audition/metadata.json',
      preview_fingerprint: receipt.body?.preview_fingerprint || null,
    }), 'application/json');
    return true;
  }
  if (url.pathname.startsWith('/api/voice_design/') && request.method === 'DELETE') {
    control.designedRollbacks += 1;
    finish(200, json({ status: 'deleted' }), 'application/json');
    return true;
  }
  return false;
}

module.exports = { handleVoiceApi };
