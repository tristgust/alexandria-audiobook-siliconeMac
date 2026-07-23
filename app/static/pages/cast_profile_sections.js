'use strict';

import { createPersonaVisual } from '/static/components/persona_visual.js';
import { createControlledCloneControl } from './cast_controlled_clone.js';
import {
  VOICE_METHODS, castList, castSection, castText, castWords,
} from './cast_model.js';

const UI = globalThis.AlexandriaUI;

export function createCastProfileSections({
  api, signal, shell, getSelected, onDirty, onOpenWorkflow,
  onControlledCloneApplied,
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
    const delivery = UI.field({
      label: 'Delivery control',
      value: value.clone?.controlled_capability
        ? 'Instruction-controlled reference clone' : 'Voice description and line direction',
      readOnly: true,
    });
    const grid = document.createElement('div');
    grid.className = 'cast-profile__field-grid';
    grid.append(method.wrapper, assigned.wrapper, delivery, description.wrapper);
    const node = castSection('voice', 'Voice', grid);
    const blockers = value.blockers || selected.blockers || [];
    if (blockers.length) node.append(UI.notice({
      tone: 'warning',
      title: blockers[0].title || 'Voice requires attention',
      body: blockers[0].explanation || 'Resolve the current Voice blocker before production.',
    }));
    return node;
  }

  function reference() {
    const selected = getSelected();
    const clone = selected.voice?.clone || {};
    const ready = clone.reference_audio_state === 'ready';
    const transport = document.createElement('div');
    transport.className = 'cast-profile__transport';
    const play = UI.compactPlay({
      state: ready ? 'ready' : 'disabled',
      label: ready ? 'Play reference audio' : 'Reference audio unavailable',
    });
    play.dataset.castReferencePlay = '';
    if (ready) play.addEventListener('click', () => shell.player.set({
      state: 'playing',
      title: `${selected.display_name} · Reference`,
      subtitle: 'Cast reference audio',
    }));
    transport.append(
      play,
      UI.waveform({ value: 0, maximum: 30, label: 'Reference audio position', disabled: !ready }),
      castText('span', 'metadata', ready ? 'Reference audio ready' : 'No reference audio'),
    );
    const transcript = fieldControl({
      id: 'cast-reference-transcript', label: 'Exact reference transcript', kind: 'textarea',
      value: clone.exact_reference_transcript || '',
      placeholder: 'Enter the exact words spoken in the reference recording',
      message: 'This must match the reference recording word for word.',
    });
    transcript.control.dataset.castReferenceTranscript = '';
    const content = document.createElement('div');
    content.className = 'cast-profile__reference-grid';
    content.append(transport, transcript.wrapper);
    if (!ready) {
      const prepare = UI.button({
        label: 'Prepare reference audio', variant: 'secondary', size: 'compact',
      });
      prepare.addEventListener('click', () => onOpenWorkflow('audio-preparer', prepare));
      content.append(UI.notice({
        tone: 'warning', title: 'Reference audio missing',
        body: 'Add or prepare a recording before using a cloned production voice.',
        action: prepare,
      }));
    }
    return castSection('reference', 'Reference audio and exact transcript', content);
  }

  function preview() {
    const selected = getSelected();
    const previewState = selected.voice?.preview || {};
    const approved = previewState.approved || previewState.status === 'approved' || previewState.status === 'ready';
    const content = document.createElement('div');
    content.className = 'cast-profile__transport';
    const play = UI.compactPlay({
      state: approved ? 'ready' : previewState.status === 'failed' ? 'failed' : 'disabled',
      label: approved ? 'Preview again' : 'Approved preview unavailable',
    });
    play.dataset.castPreviewPlay = '';
    if (approved) play.addEventListener('click', () => shell.player.set({
      state: 'playing', title: `${selected.display_name} · Voice preview`, subtitle: 'Approved Cast preview',
    }));
    content.append(
      play,
      UI.waveform({ value: approved ? 8 : 0, maximum: 18, label: 'Voice preview position', disabled: !approved }),
      castText('span', 'metadata', approved ? 'Approved preview' : 'Preview recommended'),
    );
    if (!approved) {
      const prepare = UI.button({
        label: 'Open Voice preparation', variant: 'quiet', size: 'compact',
      });
      prepare.addEventListener('click', () => onOpenWorkflow('voice-designer', prepare));
      content.append(prepare);
    }
    return castSection('preview', 'Approved preview', content);
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
    const details = document.createElement('div');
    details.append(
      castText('h4', 'cast-profile__subheading', 'Aliases'),
      castList(summary.aliases || expanded.nicknames, 'No aliases recorded.'),
      castText('h4', 'cast-profile__subheading', 'Relationships'),
      castList(summary.relationships, 'No relationships recorded.'),
      castText('h4', 'cast-profile__subheading', 'Representative Script lines'),
      castList(expanded.representative_script_lines, 'No representative lines available.'),
    );
    content.append(facts, UI.disclosure({ label: 'Character details', content: details }));
    return castSection('character', 'Character', content);
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
    content.append(UI.disclosure({
      label: 'More details', id: `persona-${selected.character_id}`, content: persona,
    }));
    return castSection('appearance', 'Appearance', content);
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
    return castSection('advanced', 'Advanced', UI.disclosure({
      label: 'Advanced voice preparation', content,
    }));
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
