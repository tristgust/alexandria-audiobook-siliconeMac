'use strict';

const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const path = require('node:path');

async function main() {
  globalThis.AlexandriaUI = {};
  const module = await import(pathToFileURL(
    path.resolve(__dirname, '../app/static/pages/cast_model.js'),
  ));
  const form = await import(pathToFileURL(
    path.resolve(__dirname, '../app/static/pages/cast_voice_assignment_form.js'),
  ));
  assert.deepEqual(module.VOICE_METHODS, [
    ['builtin', 'Built-in Voice'],
    ['existing', 'Existing Voice'],
    ['design', 'Designed Voice'],
    ['sound_effect', 'Sound effect'],
  ]);
  for (const method of [
    'clone', 'supplied_recording_clone', 'controlled_clone',
    'instruction_controlled_clone', 'adapter', 'lora', 'trained_voice', 'alias',
  ]) {
    assert.equal(module.castVoiceEditorMode({ selected_production_method: method }), 'existing');
  }
  for (const method of ['custom', 'builtin', 'built_in', 'standard', 'saved_voice']) {
    assert.equal(module.castVoiceEditorMode({ selected_production_method: method }), 'builtin');
  }
  assert.equal(module.castVoiceEditorMode({ selected_production_method: 'design' }), 'design');
  assert.equal(module.castVoiceEditorMode({ selected_production_method: 'sound_effect' }), 'sound_effect');
  assert.equal(
    module.castVoiceEditorMode({
      selected_production_method: '',
      clone: { reference_source: 'clone_voices/example.wav' },
    }),
    'existing',
  );
  const projectVoice = (key, characterId) => ({
    key,
    method: 'instruction_controlled',
    assignment: { supported: true, kind: 'project_voice_alias' },
    technical_details: { scope: 'project_configuration' },
    usage: [{ character_id: characterId }],
  });
  const choices = form.castAssignableExistingVoices(
    {
      voices: [
        projectVoice('COMPUTER', 'character_computer'),
        projectVoice('SECURITYBOT', 'character_securitybot'),
        projectVoice('PURSERBOT', 'character_purserbot'),
        {
          key: 'Ryan', method: 'built_in', assignment: { supported: true },
          technical_details: { scope: 'built_in' }, usage: [],
        },
      ],
    },
    { character_id: 'character_purserbot' },
  );
  assert.deepEqual(choices.map((item) => item.key), ['COMPUTER', 'SECURITYBOT']);
  console.log('CAST_VOICE_MODE_CONTRACT=PASS');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
