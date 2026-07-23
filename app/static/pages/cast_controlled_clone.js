'use strict';

const UI = globalThis.AlexandriaUI;

function resultMessage(result, fallback) {
  const detail = result?.data?.detail;
  return (detail && typeof detail === 'object' ? detail.message : detail)
    || result?.data?.message || result?.error || fallback;
}

function control(wrapper) {
  return wrapper.querySelector('input, textarea, select');
}

function numberField(label, value, min, max, step) {
  return UI.field({
    label, type: 'number', value,
    attributes: { min, max, step },
  });
}

export function createControlledCloneControl({ api, signal, getSelected, onApplied }) {
  const selected = getSelected();
  const speaker = selected.voice?.configuration_key
    || selected.script_connection?.resolved_script_voice_label
    || selected.identity?.script_voice_label
    || selected.display_name;
  const body = document.createElement('div');
  body.className = 'cast-controlled-clone';
  body.dataset.controlledClone = '';

  const explanation = document.createElement('p');
  explanation.className = 'cast-profile__muted';
  explanation.textContent = 'The supplied recording remains the identity. Listen through a matching comparison before enabling per-line instruction control.';
  const status = document.createElement('div');
  status.className = 'cast-controlled-clone__status';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.textContent = selected.voice?.clone?.controlled_capability
    ? 'The current instruction-controlled configuration is approved.'
    : 'Generate and finish a comparison before enabling instruction control.';

  const previewText = UI.field({
    id: `controlled-preview-text-${selected.character_id}`,
    label: 'Preview sentence',
    kind: 'textarea',
    value: selected.voice?.representative_text
      || 'The library is never empty; it merely changes who is listening.',
  });
  const direction = UI.field({
    id: `controlled-preview-direction-${selected.character_id}`,
    label: 'Delivery direction',
    kind: 'textarea',
    value: 'Urgent, focused, and emotionally controlled.',
  });
  const comparison = document.createElement('div');
  comparison.className = 'cast-controlled-clone__comparison';
  comparison.append(previewText, direction);

  const temperature = numberField('Temperature', '0.75', '0.05', '2', '0.05');
  const topK = numberField('Top K', '50', '1', '200', '1');
  const topP = numberField('Top P', '0.95', '0.05', '1', '0.05');
  const repetition = numberField('Repetition penalty', '1.5', '1.5', '3', '0.05');
  const maxTokens = numberField('Maximum tokens', '2000', '128', '4096', '128');
  const settingsGrid = document.createElement('div');
  settingsGrid.className = 'cast-controlled-clone__settings';
  settingsGrid.append(temperature, topK, topP, repetition, maxTokens);
  const settings = UI.disclosure({
    label: 'Generation settings',
    content: settingsGrid,
  });

  const generate = UI.button({
    label: 'Generate comparison',
    variant: 'secondary',
    attributes: { 'data-controlled-clone-generate': '' },
  });
  const enable = UI.button({
    label: 'Use instruction control',
    variant: 'primary',
    disabled: true,
    attributes: { 'data-controlled-clone-enable': '' },
  });
  const standard = UI.button({
    label: 'Use standard clone',
    variant: 'quiet',
    hidden: !selected.voice?.clone?.controlled_capability
      && selected.voice?.selected_backend !== 'voxcpm2_controlled',
    attributes: { 'data-controlled-clone-standard': '' },
  });
  standard.hidden = !selected.voice?.clone?.controlled_capability
    && selected.voice?.selected_backend !== 'voxcpm2_controlled';
  const actions = document.createElement('div');
  actions.className = 'cast-controlled-clone__actions';
  actions.append(generate, enable, standard);
  const audioHost = document.createElement('div');
  audioHost.className = 'cast-controlled-clone__audio';
  audioHost.dataset.controlledCloneAudioHost = '';

  let audio = null;
  let approvalToken = '';
  let configurationFingerprint = '';
  let previewFingerprint = '';
  let config = null;
  let played = false;

  const invalidate = (message = 'Inputs changed. Generate and listen to a new comparison.') => {
    approvalToken = '';
    configurationFingerprint = '';
    previewFingerprint = '';
    played = false;
    enable.disabled = true;
    audio?.pause?.();
    audio = null;
    audioHost.replaceChildren();
    status.textContent = message;
  };

  const loadConfiguration = async () => {
    const [capabilities, voices] = await Promise.all([
      api.get('/api/voice_backend/capabilities', { signal }),
      api.get('/api/voices', { signal }),
    ]);
    if (!capabilities.ok) throw new Error(resultMessage(capabilities, 'Voice capability status is unavailable.'));
    const available = capabilities.data?.expressive_clone?.supported === true
      || capabilities.data?.expressive_clone?.experimental_preview_available === true;
    if (!available) throw new Error('Instruction-controlled clone comparison is unavailable in the current runtime.');
    if (!voices.ok) throw new Error(resultMessage(voices, 'Saved Voice configuration is unavailable.'));
    const record = (voices.data || []).find((item) => item.name === speaker);
    const saved = record?.config || {};
    if (!saved.ref_audio) throw new Error('Prepare reference audio before generating a comparison.');
    if (!saved.ref_text) throw new Error('Enter and save the exact reference transcript before generating a comparison.');
    config = saved;
    control(temperature).value = String(saved.instruction_clone_temperature ?? 0.75);
    control(topK).value = String(saved.instruction_clone_top_k ?? 50);
    control(topP).value = String(saved.instruction_clone_top_p ?? 0.95);
    control(repetition).value = String(saved.instruction_clone_repetition_penalty ?? 1.5);
    control(maxTokens).value = String(saved.instruction_clone_max_tokens ?? 2000);
    return saved;
  };

  const confirmListen = async () => {
    if (!played || !previewFingerprint || !configurationFingerprint) return;
    status.textContent = 'Recording listen confirmation…';
    const result = await api.post('/api/clone_voices/controlled_preview/confirm', {
      speaker,
      preview_fingerprint: previewFingerprint,
      configuration_fingerprint: configurationFingerprint,
    }, { signal });
    if (signal.aborted) return;
    if (!result.ok || !result.data?.approval_token) {
      status.textContent = resultMessage(result, 'Listen confirmation failed. Generate a new comparison.');
      return;
    }
    approvalToken = result.data.approval_token;
    enable.disabled = false;
    status.textContent = 'Preview heard and confirmed. This exact configuration can now be enabled.';
  };

  generate.addEventListener('click', async () => {
    invalidate('Loading the saved reference and runtime capability…');
    generate.disabled = true;
    generate.textContent = 'Generating comparison…';
    try {
      const saved = await loadConfiguration();
      const text = control(previewText).value.trim();
      const instruct = control(direction).value.trim();
      if (!text || !instruct) throw new Error('Enter both a preview sentence and a delivery direction.');
      const result = await api.post('/api/clone_voices/controlled_preview', {
        speaker,
        ref_audio: saved.ref_audio,
        ref_text: saved.ref_text,
        text,
        instruct,
        character_style: document.querySelector('[data-cast-voice-description]')?.value
          || saved.character_style || saved.default_style || '',
        temperature: Number(control(temperature).value),
        top_k: Number(control(topK).value),
        top_p: Number(control(topP).value),
        repetition_penalty: Number(control(repetition).value),
        max_tokens: Number(control(maxTokens).value),
        seed: Number(saved.seed ?? -1),
      }, { signal });
      if (!result.ok) throw new Error(resultMessage(result, 'The comparison could not be generated.'));
      if (!result.data?.preview_fingerprint || !result.data?.configuration_fingerprint) {
        throw new Error('The preview response did not include the listen-confirmation fingerprints.');
      }
      previewFingerprint = result.data.preview_fingerprint;
      configurationFingerprint = result.data.configuration_fingerprint;
      audio = document.createElement('audio');
      audio.controls = true;
      audio.preload = 'metadata';
      audio.src = result.data.audio_url;
      audio.dataset.controlledCloneAudio = '';
      audio.addEventListener('play', () => {
        played = true;
        status.textContent = 'Listening… Finish the comparison to enable instruction control.';
      });
      audio.addEventListener('ended', confirmListen, { once: true });
      audioHost.replaceChildren(audio);
      status.textContent = 'Comparison ready. Listen through to the end before enabling it.';
    } catch (error) {
      status.textContent = String(error?.message || error);
    } finally {
      generate.disabled = false;
      generate.textContent = 'Generate comparison';
    }
  });

  enable.addEventListener('click', async () => {
    if (!approvalToken || !config) return;
    enable.disabled = true;
    status.textContent = 'Saving the approved instruction-controlled configuration…';
    const result = await api.post('/api/save_voice_config', {
      [speaker]: {
        type: 'clone',
        clone_backend: 'qwen3_instruction_controlled',
        instruction_clone_temperature: Number(control(temperature).value),
        instruction_clone_top_k: Number(control(topK).value),
        instruction_clone_top_p: Number(control(topP).value),
        instruction_clone_repetition_penalty: Number(control(repetition).value),
        instruction_clone_max_tokens: Number(control(maxTokens).value),
        controlled_clone_approval_token: approvalToken,
        controlled_clone_configuration_fingerprint: configurationFingerprint,
      },
    }, { signal });
    if (!result.ok) {
      enable.disabled = false;
      status.textContent = resultMessage(result, 'The controlled configuration was not saved.');
      return;
    }
    status.textContent = 'Instruction-controlled clone saved for this exact approved configuration.';
    standard.hidden = false;
    await onApplied?.();
  });

  standard.addEventListener('click', async () => {
    standard.disabled = true;
    const result = await api.post('/api/save_voice_config', {
      [speaker]: { type: 'clone', clone_backend: 'qwen3_base' },
    }, { signal });
    standard.disabled = false;
    status.textContent = result.ok
      ? 'Standard supplied-reference cloning is active.'
      : resultMessage(result, 'The standard clone could not be restored.');
    if (result.ok) await onApplied?.();
  });

  [previewText, direction, temperature, topK, topP, repetition, maxTokens]
    .map(control).filter(Boolean)
    .forEach((node) => node.addEventListener('input', () => invalidate()));

  body.append(explanation, comparison, settings, actions, status, audioHost);
  const disclosure = UI.disclosure({
    label: 'Instruction-controlled clone',
    content: body,
    expanded: selected.voice?.clone?.controlled_capability
      || selected.voice?.selected_backend === 'voxcpm2_controlled',
  });
  disclosure.dataset.controlledCloneDisclosure = '';
  return Object.freeze({
    node: disclosure,
    cleanup() { audio?.pause?.(); audio = null; },
  });
}
