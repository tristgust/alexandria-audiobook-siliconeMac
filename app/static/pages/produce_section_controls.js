'use strict';

const UI = globalThis.AlexandriaUI;

export function createProduceSectionAction({
  section, selectedIds, collectionComplete, disabled, onGenerate,
}) {
  const selectedCount = section.eligibleIds.filter((id) => selectedIds.has(id)).length;
  const count = selectedCount || section.eligibleIds.length;
  const reason = !collectionComplete
    ? 'Section generation is unavailable because the complete audio list is not loaded.'
    : !count ? 'No ready or stale chunks with a valid Voice are available.'
      : disabled ? 'Section generation is unavailable while audio work is running.' : '';
  const button = UI.button({
    label: count
      ? `Generate ${count.toLocaleString()} ${selectedCount ? 'selected' : 'eligible'}`
      : 'No eligible audio',
    variant: 'secondary',
    size: 'compact',
    disabled: Boolean(reason),
    attributes: {
      'data-produce-section-generate': '',
      'aria-label': count
        ? `Generate ${selectedCount ? 'selected' : 'eligible'} audio for ${section.label}`
        : `No eligible audio in ${section.label}`,
      title: reason || `Generate audio for ${section.label}`,
    },
    onClick: () => onGenerate(section),
  });
  return button;
}
