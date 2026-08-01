'use strict';

const UI = globalThis.AlexandriaUI;

export function scriptLoadingEntries() {
  const fragment = document.createDocumentFragment();
  fragment.append(
    UI.loadingState({
      label: 'Loading Script',
      detail: 'Reading the accepted Script and review state.',
    }),
    UI.skeleton({ kind: 'row', label: 'Loading Script entry' }),
    UI.skeleton({ kind: 'row', label: 'Loading Script entry' }),
    UI.skeleton({ kind: 'row', label: 'Loading Script entry' }),
  );
  return fragment;
}

export function scriptApprovalLoading() {
  return UI.loadingState({
    label: 'Approving Script',
    detail: 'Verifying source fidelity and recording the accepted version.',
  });
}
