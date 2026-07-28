'use strict';

import { createPersonaVisual } from '/static/components/persona_visual.js';
import { createControlledCloneControl } from './cast_controlled_clone.js';
import {
  VOICE_METHODS, castList, castText, castWords,
} from './cast_model.js';

const UI = globalThis.AlexandriaUI;

function sectionHeading(kicker, title, actions = null) {
  const heading = document.createElement('div');
  heading.className = 'cast-section-heading';
  const copy = document.createElement('div');
  copy.append(
    castText('span', 'canonical-kicker utility-heading', kicker),
    castText('h3', 'entity-title', title),
  );
  heading.append(copy);
  if (actions) heading.append(actions);
  return heading;
}

function section(name, className) {
  const node = document.createElement('section');
  node.className = `cast-profile__section ${className}`;
  node.dataset.castSection = name;
  return node;
}

function methodLabel(value) {
  return VOICE_METHODS.find(([key]) => key === value)?.[1] || castWords(value || 'custom');
}

export function createCastProfileSections({
  api, signal, shell, getSelected, onDirty, onResetDirty, onCancelEdit,
  onOpenWorkflow, onControlledCloneApplied,
}) {
  let persona = null;
  let controlledClone = null;

  function fieldControl(options) {
    const wrapper = UI.field(options);
    const control = wrapper.querySelector('.field__control');
    control.addEventListener('input', onDirty);
    control.addEventListener('change', onDirty);
    return { wrapper, control };
  }

  function voice() {
    const selected = getSelected();
    const value = selected.voice || {};
    const methodValue = value.selected_production_method || 'custom';
    const methods = VOICE_METHODS.some(([method]) => method === methodValue)
      ? VOICE_METHODS : [[methodValue, castWords(methodValue)], ...VOICE_METHODS];

    const actions = document.createElement('div');
    actions.className = 'cast-voice-heading-actions';
    actions.append(castText('span', 'cast-voice-saved-state', 'Saved'));
    const edit = UI.button({ label: 'Edit Voice', variant: 'secondary', size: 'compact' });
    edit.dataset.castEditVoice = '';
    actions.append(edit);

    const node = section('voice', 'cast-voice-section');
    node.append(sectionHeading('Production', 'Voice', actions));

    const facts = document.createElement('dl');
    facts.className = 'cast-voice-facts';
    const addFact = (term, detail, wide = false) => {
      const row = document.createElement('div');
      if (wide) row.className = 'cast-voice-fact-wide';
      row.append(castText('dt', '', term), castText('dd', '', detail, 'Not recorded'));
      facts.append(row);
    };
    addFact('Method', methodLabel(methodValue));
    addFact('Assigned voice', value.selected_voice || 'Not assigned');
    addFact('Persistent voice description', value.persistent_voice_description || 'Not recorded', true);
    addFact('Delivery control', value.clone?.controlled_capability
      ? 'Instruction-controlled reference clone' : 'Voice description and line direction', true);
    node.append(facts);

    const blockers = value.blockers || selected.blockers || [];
    if (blockers.length) {
      const warning = document.createElement('div');
      warning.className = 'cast-controlled-warning';
      warning.textContent = blockers[0].explanation || blockers[0].title
        || 'Resolve the current Voice blocker before production.';
      node.append(warning);
    }

    const editor = document.createElement('div');
    editor.className = 'cast-voice-editor';
    editor.dataset.castVoiceEditor = '';
    editor.hidden = true;
    const toolbar = document.createElement('div');
    toolbar.className = 'cast-voice-editor-toolbar';
    const editorCopy = document.createElement('div');
    editorCopy.append(
      castText('strong', '', 'Edit production Voice'),
      castText('span', '', 'Changes stay local to this character until Save changes is pressed.'),
    );
    const cancel = UI.button({
      label: 'Cancel', variant: 'quiet', size: 'compact',
      onClick: () => {
        onResetDirty?.();
        onCancelEdit?.();
      },
    });
    toolbar.append(editorCopy, cancel);

    const method = fieldControl({
      id: 'cast-voice-method', label: 'Production method', kind: 'select', value: methodValue,
      options: methods.map(([option, label]) => ({ value: option, label })),
    });
    method.control.dataset.castVoiceMethod = '';
    const assigned = fieldControl({
      id: 'cast-assigned-voice', label: 'Assigned voice', value: value.selected_voice || '',
      placeholder: 'Choose or name a production voice',
      message: 'The saved voice used when Alexandria produces this character.',
    });
    assigned.control.dataset.castAssignedVoice = '';
    const description = fieldControl({
      id: 'cast-voice-description', label: 'Persistent voice description', kind: 'textarea',
      value: value.persistent_voice_description || '',
      placeholder: 'Describe tone, age, rhythm, and delivery',
    });
    description.control.dataset.castVoiceDescription = '';
    const transcript = fieldControl({
      id: 'cast-reference-transcript', label: 'Exact reference transcript', kind: 'textarea',
      value: value.clone?.exact_reference_transcript || '',
      placeholder: 'Enter the exact words spoken in the reference recording',
      message: 'This must match the reference recording word for word.',
    });
    transcript.control.dataset.castReferenceTranscript = '';
    const fields = document.createElement('div');
    fields.className = 'cast-profile__field-grid cast-voice-editor-slot';
    fields.append(method.wrapper, assigned.wrapper, description.wrapper, transcript.wrapper);
    editor.append(toolbar, fields);
    edit.addEventListener('click', () => {
      editor.hidden = false;
      edit.hidden = true;
      requestAnimationFrame(() => method.control.focus());
    });
    node.append(editor);
    return node;
  }

  function reference() {
    const selected = getSelected();
    const clone = selected.voice?.clone || {};
    const ready = clone.reference_audio_state === 'ready';
    const node = section('reference', 'cast-reference-section');
    node.append(sectionHeading('Clone identity', 'Reference audio and exact transcript'));

    const grid = document.createElement('div');
    grid.className = 'cast-reference-grid';
    const audio = document.createElement('div');
    audio.className = 'cast-reference-audio';
    const play = UI.compactPlay({
      state: ready ? 'ready' : 'disabled',
      label: ready ? 'Play reference audio' : 'Reference audio unavailable',
    });
    play.classList.add('cast-reference-icon');
    play.dataset.castReferencePlay = '';
    if (ready) play.addEventListener('click', () => shell.player.set({
      state: 'playing',
      title: `${selected.display_name} · Reference`,
      subtitle: 'Cast reference audio',
    }));
    const audioCopy = document.createElement('div');
    audioCopy.append(
      castText('strong', '', ready ? 'Reference audio ready' : 'No reference audio'),
      castText('span', '', clone.reference_filename || clone.reference_audio_file
        || (ready ? 'Saved supplied recording' : 'Prepare a recording to use a cloned voice')),
    );
    audio.append(play, audioCopy);
    const transcript = document.createElement('blockquote');
    transcript.textContent = clone.exact_reference_transcript || 'No exact transcript recorded.';
    grid.append(audio, transcript);
    node.append(grid);

    if (!ready) {
      const prepare = UI.button({
        label: 'Prepare reference audio', variant: 'secondary', size: 'compact',
      });
      prepare.addEventListener('click', () => onOpenWorkflow('audio-preparer', prepare));
      node.append(prepare);
    }
    return node;
  }

  function preview() {
    const selected = getSelected();
    const previewState = selected.voice?.preview || {};
    const approved = previewState.approved || previewState.status === 'approved' || previewState.status === 'ready';
    const node = section('preview', 'cast-preview-section');
    node.append(sectionHeading('Listening check', 'Preview'));
    const row = document.createElement('div');
    row.className = 'cast-preview-row';
    const play = UI.compactPlay({
      state: approved ? 'ready' : previewState.status === 'failed' ? 'failed' : 'disabled',
      label: approved ? 'Preview again' : 'Approved preview unavailable',
    });
    play.classList.add('cast-preview-icon');
    play.dataset.castPreviewPlay = '';
    if (approved) play.addEventListener('click', () => shell.player.set({
      state: 'playing', title: `${selected.display_name} · Voice preview`, subtitle: 'Approved Cast preview',
    }));
    const copy = document.createElement('div');
    copy.append(
      castText('strong', '', approved ? 'Approved preview' : 'Not generated'),
      castText('span', '', approved
        ? 'This preview is approved for the assigned production Voice.'
        : 'Edit this Voice to generate or review its preview.'),
    );
    row.append(play, copy);
    node.append(row);
    return node;
  }

  function character() {
    const selected = getSelected();
    const summary = selected.character?.summary || {};
    const content = document.createElement('div');
    content.className = 'cast-profile__summary';
    const facts = document.createElement('dl');
    facts.className = 'cast-profile__definition-list';
    [
      ['Role', summary.role],
      ['Speaking', castWords(summary.speaking_state
        || (selected.speaking_role === 'speaking' ? 'speaking role' : 'non-speaking'))],
      ['Type', summary.species_or_type],
      ['Confidence', castWords(summary.source_confidence)],
    ].forEach(([term, value]) => facts.append(castText('dt', '', term), castText('dd', '', value)));
    const expanded = selected.character?.expanded || {};
    content.append(
      facts,
      castText('h4', 'cast-profile__subheading', 'Aliases'),
      castList(summary.aliases || expanded.nicknames, 'No aliases recorded.'),
      castText('h4', 'cast-profile__subheading', 'Relationships'),
      castList(summary.relationships, 'No relationships recorded.'),
      castText('h4', 'cast-profile__subheading', 'Representative Script lines'),
      castList(expanded.representative_script_lines, 'No representative lines available.'),
    );
    const disclosure = UI.disclosure({ label: 'Character', content });
    disclosure.classList.add('cast-detail-disclosure');
    return disclosure;
  }

  function appearance() {
    const selected = getSelected();
    const value = selected.appearance || {};
    const content = document.createElement('div');
    content.dataset.appearanceSummary = '';
    content.className = 'cast-profile__summary';
    content.append(castText('p', 'cast-profile__muted',
      value.summary || 'Visual evidence not available. No stable appearance details have been collected.'));
    persona?.cleanup?.();
    persona = createPersonaVisual({ api, character: selected, signal });
    content.append(persona);
    const disclosure = UI.disclosure({ label: 'Appearance', content });
    disclosure.classList.add('cast-detail-disclosure');
    return disclosure;
  }

  function advanced() {
    const setup = getSelected().advanced_voice_setup || {};
    const content = document.createElement('div');
    content.className = 'cast-profile__advanced-content';
    const facts = document.createElement('dl');
    facts.className = 'cast-profile__definition-list';
    [
      ['Expressive reference', setup.expressive_reference_state],
      ['Recording preparation', setup.owned_recording_preparation_state],
      ['Dataset', setup.dataset_state],
      ['Adapter training', setup.adapter_training_state],
      ['Compatibility', setup.compatibility_state],
    ].forEach(([term, value]) => {
      facts.append(castText('dt', '', term), castText('dd', '', castWords(value || 'not started')));
    });
    controlledClone?.cleanup();
    controlledClone = createControlledCloneControl({
      api, signal, getSelected, onApplied: onControlledCloneApplied,
    });
    content.append(facts, controlledClone.node);
    const disclosure = UI.disclosure({ label: 'Advanced details', content });
    disclosure.classList.add('cast-detail-disclosure');
    return disclosure;
  }

  return Object.freeze({
    voice, reference, preview, character, appearance, advanced,
    cleanup() {
      persona?.cleanup?.();
      controlledClone?.cleanup();
      persona = null;
      controlledClone = null;
    },
  });
}
