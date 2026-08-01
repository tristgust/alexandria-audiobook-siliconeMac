'use strict';

const UI = globalThis.AlexandriaUI;

export function projectHomeLoading() {
  const fragment = document.createDocumentFragment();
  fragment.append(
    UI.loadingState({
      label: 'Loading projects',
      detail: 'Reading the project catalog.',
    }),
    UI.skeleton({ kind: 'panel', label: 'Loading current project' }),
    UI.skeleton({ kind: 'row', label: 'Loading project row' }),
    UI.skeleton({ kind: 'row', label: 'Loading project row' }),
  );
  return fragment;
}
