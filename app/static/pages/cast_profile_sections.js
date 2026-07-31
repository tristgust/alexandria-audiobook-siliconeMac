'use strict';

import { createPersonaVisual } from '/static/components/persona_visual.js';
import { createControlledCloneControl } from './cast_controlled_clone.js';
import { createCastProfileMediaSections } from './cast_profile_media_sections.js';
import { createCastProfileVoiceSection } from './cast_profile_voice_section.js';
import { createCastVoiceSummary } from './cast_voice_summary.js';
import {
  castList, castSection, castText, castWords,
} from './cast_model.js';

const UI = globalThis.AlexandriaUI;

export function createCastProfileSections({
  api, signal, shell, getSelected, getVoiceLibrary, getVoiceLibraryState,
  onDirty, onOpenWorkflow, onControlledCloneApplied, onRetryVoiceLibrary,
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

  function sectionHeading({ eyebrow, title, action }) {
    const header = document.createElement('header');
    header.className = 'cast-profile__section-heading';
    const copy = document.createElement('div');
    copy.append(
      castText('span', 'metadata cast-profile__eyebrow', eyebrow),
      castText('h3', 'cast-profile__section-title', title),
    );
    header.append(copy);
    if (action) header.append(action);
    return header;
  }

  function editorFact({ className = '', iconName, label, title, body }) {
    const node = document.createElement('div');
    node.className = `cast-profile__editor-fact${className ? ` ${className}` : ''}`;
    const marker = document.createElement('span');
    marker.className = 'cast-profile__editor-fact-mark';
    marker.setAttribute('aria-hidden', 'true');
    marker.append(UI.icon(iconName));
    const copy = document.createElement('div');
    copy.className = 'cast-profile__editor-fact-copy';
    const labelNode = castText('span', 'cast-profile__editor-fact-label', label);
    const titleNode = castText('strong', '', title);
    const bodyNode = castText('p', 'metadata', body);
    copy.append(labelNode, titleNode, bodyNode);
    node.append(marker, copy);
    return { node, title: titleNode, body: bodyNode };
  }

  const voiceFacts = createCastVoiceSummary({ editorFact });
  const voiceSection = createCastProfileVoiceSection({
    api, signal, shell, getSelected, getVoiceLibrary, getVoiceLibraryState,
    onOpenWorkflow, onRetryVoiceLibrary, fieldControl, sectionHeading, editorFact, voiceFacts,
  });
  const mediaSections = createCastProfileMediaSections({
    api, signal, shell, getSelected, getVoiceLibrary, onOpenWorkflow,
    fieldControl, sectionHeading, getEditingPreview: voiceSection.getEditingPreview,
  });

  function disclosureFor(label, description, content, iconClass) {
    const disclosure = UI.disclosure({ label, description, content });
    disclosure.classList.add('cast-profile__disclosure');
    const trigger = disclosure.querySelector('.disclosure__trigger');
    if (iconClass) {
      const mark = document.createElement('span');
      mark.className = 'cast-profile__disclosure-mark';
      mark.setAttribute('aria-hidden', 'true');
      const icon = document.createElement('i');
      icon.className = iconClass;
      mark.append(icon);
      trigger.prepend(mark);
    }
    const chevron = document.createElement('i');
    chevron.className = 'fas fa-chevron-right';
    chevron.setAttribute('aria-hidden', 'true');
    trigger.append(chevron);
    return disclosure;
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
    const relationshipCount = (summary.relationships || []).length;
    content.append(
      facts,
      castText('h4', 'cast-profile__subheading', 'Aliases'),
      castList(summary.aliases || expanded.nicknames, 'No aliases recorded.'),
      castText('h4', 'cast-profile__subheading', 'Relationships'),
      castList(summary.relationships, 'No relationships recorded.'),
      castText('h4', 'cast-profile__subheading', 'Representative Script lines'),
      castList(expanded.representative_script_lines, 'No representative lines available.'),
    );
    return castSection('character', '', disclosureFor(
      'Character', relationshipCount
        ? `${relationshipCount} relationship${relationshipCount === 1 ? '' : 's'} · identity summary`
        : 'Identity summary',
      content, 'fas fa-user',
    ));
  }

  function appearance() {
    const selected = getSelected();
    const value = selected.appearance || {};
    const content = document.createElement('div');
    content.dataset.appearanceSummary = '';
    content.className = 'cast-profile__summary';
    const visualState = document.createElement('div');
    visualState.className = 'cast-profile__appearance-state';
    const visualMark = document.createElement('span');
    visualMark.className = 'cast-profile__appearance-mark';
    visualMark.setAttribute('aria-hidden', 'true');
    const visualIcon = document.createElement('i');
    visualIcon.className = value.summary ? 'fas fa-image' : 'fas fa-person';
    visualMark.append(visualIcon);
    const visualCopy = document.createElement('div');
    visualCopy.append(
      castText('strong', '', value.summary ? 'Visual dossier ready' : 'Visual evidence not available'),
      castText('p', 'cast-profile__muted', value.summary
        || 'No stable appearance details have been collected for this character.'),
    );
    visualState.append(visualMark, visualCopy);
    content.append(visualState);
    persona?.cleanup?.();
    persona = createPersonaVisual({ api, character: selected, signal });
    content.append(persona);
    const appearanceLabel = value.summary
      ? 'Visual dossier ready'
      : value.optional === true || selected.appearance_required === false || selected.visual_required === false
        ? 'No evidence required' : 'Optional visual context';
    return castSection('appearance', '', disclosureFor(
      'Appearance', appearanceLabel, content, 'fas fa-image',
    ));
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
    return castSection('advanced', '', disclosureFor(
      'Advanced details', 'Evidence, preparation, and provenance',
      content, 'fas fa-sliders',
    ));
  }

  return Object.freeze({
    voice: voiceSection.voice,
    reference: mediaSections.reference,
    preview: mediaSections.preview,
    character,
    appearance,
    advanced,
    syncVoiceLibraryState: voiceSection.syncVoiceLibraryState,
    cleanup() {
      persona?.cleanup?.();
      controlledClone?.cleanup();
      persona = null;
      controlledClone = null;
      voiceSection.cleanup();
    },
  });
}
