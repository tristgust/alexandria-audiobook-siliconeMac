'use strict';

const UI = globalThis.AlexandriaUI;
export const DEFAULT_PREVIEW_TEXT = 'I knew you would return before the last lamp went out.';
export const OTHER_FORMATS = Object.freeze([
  ['PEFT + speaker embedding', 'source linked', 'Runs as an MLX overlay on Alexandria’s cached CustomVoice model. The source bundle is not copied.'],
  ['Full CustomVoice checkpoint', 'guarded conversion', 'Creates one quantized MLX checkpoint only when the drive will retain a 16 GiB safety reserve.'],
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
  packList.replaceChildren(UI.skeleton({ label: 'Loading community Voices' }));
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
    node('h3', 'entity-title', 'Qwen directory formats'),
    node('p', 'flat-section__body', 'Paste the local source folder above. Alexandria links small PEFT bundles in place and guards full-checkpoint conversion against low disk space.'),
  );
  const list = document.createElement('ul');
  list.className = 'community-pack-format-list divider-list';
  formats.forEach(([name, state, description]) => {
    const item = document.createElement('li');
    item.append(
      node('strong', 'entity-title', name),
      UI.status({
        tone: state === 'source linked' ? 'success' : 'warning',
        label: state,
      }),
      node('p', 'metadata', description),
    );
    list.append(item);
  });
  section.append(list);
  return section;
}
