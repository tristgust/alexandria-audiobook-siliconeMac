'use strict';

const UI = globalThis.AlexandriaUI;

function supportingPageLoading({ label, detail, itemLabel, detailLabel }) {
  const fragment = document.createDocumentFragment();
  fragment.append(
    UI.loadingState({ label, detail }),
    UI.skeleton({ kind: 'row', label: itemLabel }),
    UI.skeleton({ kind: 'row', label: itemLabel }),
    UI.skeleton({ kind: 'panel', label: detailLabel }),
  );
  return fragment;
}

const PRESETS = Object.freeze({
  library: {
    label: 'Loading Library',
    detail: 'Reading project artifacts and reusable resources.',
    itemLabel: 'Loading artifact row',
    detailLabel: 'Loading artifact details',
  },
  voices: {
    label: 'Loading Voices',
    detail: 'Reading reusable Voices, capabilities, and current Cast usage.',
    itemLabel: 'Loading Voice row',
    detailLabel: 'Loading Voice details',
  },
  templates: {
    label: 'Loading Templates',
    detail: 'Reading saved project presets and production intent.',
    itemLabel: 'Loading Template row',
    detailLabel: 'Loading Template details',
  },
});

export const libraryLoading = () => supportingPageLoading(PRESETS.library);
export const voicesLoading = () => supportingPageLoading(PRESETS.voices);
export const templatesLoading = () => supportingPageLoading(PRESETS.templates);
