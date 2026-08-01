'use strict';

const UI = globalThis.AlexandriaUI;
export const DEFAULT_PREVIEW_TEXT = 'I knew you would return before the last lamp went out.';
export const OTHER_FORMATS = Object.freeze([
  ['PEFT + speaker embedding', 'mlx_conversion_required', 'Not importable here; validated MLX conversion is not available yet.'],
  ['Full CustomVoice checkpoint', 'mlx_conversion_required', 'Not importable here; a large checkpoint still needs validated MLX conversion.'],
]);

export function createPackFileInput(onChange) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.qvoice';
  input.hidden = true;
  input.dataset.communityPackFile = '';
  input.addEventListener('change', onChange);
  return input;
}

export function node(tag, className, value) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = value;
  return element;
}

export function factList(entries) {
  const list = document.createElement('dl');
  list.className = 'fact-list community-pack-facts';
  entries.forEach(([label, value]) => list.append(
    node('dt', 'metadata', label), node('dd', '', value),
  ));
  return list;
}

export function setLiveStatus(status, state, message, states) {
  status.dataset.state = states.includes(state) ? state : 'success';
  status.textContent = message;
  status.setAttribute('aria-live', state === 'error' ? 'assertive' : 'polite');
}

export async function loadInstalledPacks({ api, signal, packList, onSelect, retry }) {
  packList.dataset.state = 'loading';
  packList.replaceChildren(UI.loadingState({
    label: 'Loading community Voices',
    detail: 'Reading installed packs and approval state.',
    size: 'compact',
  }));
  const result = await api.get('/api/community-qwen-packs', { signal });
  if (!result.ok) {
    packList.dataset.state = 'error';
    packList.replaceChildren(UI.notice({
      tone: 'error', title: 'Community Voices could not load', body: result.error,
      action: UI.button({ label: 'Retry', onClick: retry }),
    }));
    return;
  }
  const packs = Array.isArray(result.data?.packs) ? result.data.packs : [];
  packList.dataset.state = packs.length > 8 ? 'dense' : packs.length ? 'success' : 'empty';
  packList.replaceChildren();
  if (!packs.length) {
    packList.append(node('p', 'metadata', 'No community Qwen Voices are installed.'));
    return;
  }
  packs.forEach((pack) => packList.append(UI.button({
    label: `${pack.name || pack.pack_id} · ${pack.state}`,
    variant: 'quiet', onClick: () => onSelect(pack),
    attributes: { 'data-community-pack-row': pack.pack_id },
  })));
}

export function formatSupport(formats) {
  const section = document.createElement('section');
  section.className = 'community-pack-section';
  section.append(
    node('h3', 'entity-title', 'Other Qwen formats (not importable here)'),
    node('p', 'flat-section__body', 'Alexandria has backend-only inspectors for these directory formats, but this workflow cannot import or run them.'),
  );
  const list = document.createElement('ul');
  list.className = 'community-pack-format-list divider-list';
  formats.forEach(([name, state, description]) => {
    const item = document.createElement('li');
    item.append(
      node('strong', 'entity-title', name),
      UI.status({ tone: 'warning', label: state.replaceAll('_', ' ') }),
      node('p', 'metadata', description),
    );
    list.append(item);
  });
  section.append(list);
  return section;
}
