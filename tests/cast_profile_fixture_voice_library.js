'use strict';

function voiceLibraryPayload(control) {
  const resources = [
    {
      voice_id: 'voice-builtin-ryan', key: 'Ryan', name: 'Ryan', method: 'built_in',
      method_label: 'Built-in Voice', state: 'available', description: 'Pinned built-in Qwen speaker.',
      assignment: { supported: true, kind: 'built_in', production_method: 'custom', label: 'Use Ryan' },
      assignment_mutation_supported: true,
      preview: { available: false, url: null },
    },
    {
      voice_id: 'voice-benny', key: 'reusable:BERNICE', name: 'Benny / Bernice',
      method: 'instruction_controlled', method_label: 'Instruction-controlled clone',
      state: 'approved', description: 'Saved instruction-controlled clone.',
      assignment: { supported: true, kind: 'reusable_clone', production_method: 'clone', label: 'Use Benny / Bernice' },
      assignment_mutation_supported: true,
      preview: { available: true, url: '/fixture-benny.wav' },
    },
    {
      voice_id: 'voice-narrator', key: 'reusable:NARRATOR', name: 'Narrator',
      method: 'instruction_controlled', method_label: 'Instruction-controlled clone',
      state: 'approved', description: 'Saved instruction-controlled clone.',
      assignment: { supported: true, kind: 'reusable_clone', production_method: 'clone', label: 'Use Narrator' },
      assignment_mutation_supported: true,
      preview: { available: true, url: '/fixture-narrator.wav' },
    },
    {
      voice_id: 'voice-doctor', key: 'reusable:THE DOCTOR', name: 'The Doctor',
      method: 'instruction_controlled', method_label: 'Instruction-controlled clone',
      state: 'approved', description: 'Saved instruction-controlled clone.',
      assignment: { supported: true, kind: 'reusable_clone', production_method: 'clone', label: 'Use The Doctor' },
      assignment_mutation_supported: true,
      preview: { available: true, url: '/fixture-doctor.wav' },
    },
    {
      voice_id: 'voice-computer', key: 'COMPUTER', name: 'Computer',
      method: 'instruction_controlled', method_label: 'Instruction-controlled clone',
      state: 'approved', description: 'Active project Computer Voice.',
      assignment: {
        supported: true, kind: 'project_voice_alias', production_method: 'alias',
        label: 'Use Computer', reuse_basis: 'exact_project_configuration',
      },
      assignment_mutation_supported: true,
      technical_details: { scope: 'project_configuration' },
      usage: [{ character_id: 'cast:computer' }],
      preview: { available: true, url: '/fixture-computer.wav' },
    },
    {
      voice_id: 'voice-narrator-adapter', key: 'narrator_attention_r8_pilot',
      name: 'Narrator Attention R8 Pilot', method: 'adapter', method_label: 'Voice adapter',
      state: 'review_required', description: 'Experimental adapter awaiting listening approval.',
      assignment: { supported: false }, assignment_mutation_supported: false,
      preview: { available: true, url: '/fixture-adapter.wav' },
    },
  ];
  resources.forEach((resource) => {
    resource.capability = {
      production_supported: resource.assignment.supported === true,
      preview_supported: resource.preview.available === true,
    };
    resource.usage = [
      ...(resource.usage || []),
      ...Object.entries(control.libraryAssignments)
      .filter(([, value]) => value.voice_id === resource.voice_id)
      .map(([character_id]) => ({ character_id })),
    ];
    resource.usage_count = resource.usage.length;
    resource.assigned = resource.usage_count > 0;
  });
  return {
    schema_version: 1,
    summary: { voice_count: resources.length },
    methods: [],
    filters: { methods: ['built_in', 'instruction_controlled', 'adapter'], states: ['available', 'approved', 'review_required'] },
    voices: resources,
    assignment_mutation_supported: true,
    cast_is_authoritative: true,
    fingerprint: 'fixture-voice-library',
  };
}

module.exports = { voiceLibraryPayload };
