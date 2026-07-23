'use strict';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);

function text(tag, className, value) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function ownerFor(route) {
  const owner = document.createElement('article');
  owner.className = 'supporting-page voices-page';
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = route.path;
  const title = UI.pageTitleBlock({
    title: 'Voices',
    subtitle: 'Browse available voice resources and see where they are used.',
  });
  title.querySelector('h1').dataset.pageHeading = '';
  owner.append(title);
  return owner;
}

export async function mount({ root, route, shell, api, signal }) {
  const owner = ownerFor(route);
  owner.append(UI.notice({
    tone: 'information',
    title: 'Voices is read-only',
    body: 'Assignment happens only in Cast. This page shows voice capability and current usage.',
  }));
  const toolbar = document.createElement('div');
  toolbar.className = 'page-toolbar';
  const search = UI.searchField({ label: 'Search Voices', placeholder: 'Search voices' });
  const method = UI.field({
    kind: 'select', label: 'Method',
    options: [{ value: 'all', label: 'All methods' }],
    value: route.context.filter || 'all',
  });
  toolbar.append(search, method);
  const content = document.createElement('section');
  content.className = 'content-state';
  content.dataset.state = STATES[0];
  content.append(UI.skeleton({ label: 'Loading Voices' }), UI.skeleton());
  owner.append(toolbar, content);
  root.replaceChildren(owner);
  shell.player.set({ state: 'inactive', title: 'No voice preview selected' });

  let disposed = false;
  let voices = [];
  let selected = null;
  let projectId = route.context.project || '';

  const detailFor = (voice) => {
    const detail = document.createElement('section');
    detail.className = 'supporting-detail';
    detail.append(
      text('div', 'metadata', voice.method_label || String(voice.method || 'Voice').replaceAll('_', ' ')),
      text('h2', 'section-title', voice.name || 'Unnamed voice'),
      UI.status({
        tone: ['invalid', 'legacy_blocked'].includes(voice.state) ? 'error' : 'success',
        label: voice.state || 'available',
      }),
      text('p', 'flat-section__body', voice.description || 'No description supplied.'),
    );
    const usage = Array.isArray(voice.usage) ? voice.usage : [];
    const usageSection = document.createElement('section');
    usageSection.className = 'voice-usage';
    usageSection.append(text('h3', 'entity-title', usage.length ? 'Used by' : 'Not assigned'));
    if (usage.length) {
      const list = document.createElement('ul');
      list.className = 'divider-list';
      usage.forEach((item) => list.append(text('li', '', item.character_name || item.name || item.character_id || 'Character')));
      usageSection.append(list);
    } else {
      usageSection.append(text('p', 'metadata', 'Choose this voice from a character in Cast.'));
    }
    const castContext = { project: projectId };
    const firstCharacter = usage[0]?.character_id;
    if (firstCharacter) castContext.character = firstCharacter;
    detail.append(usageSection, UI.button({
      label: usage.length ? 'Open usage in Cast' : 'Open Cast',
      variant: 'primary',
      onClick: () => shell.navigate(shell.routes.routeForPath('cast', castContext).hash),
    }));
    return detail;
  };

  const render = () => {
    if (disposed || signal.aborted) return;
    const query = search.querySelector('input').value.trim().toLocaleLowerCase();
    const chosen = method.querySelector('select').value;
    const visible = voices.filter((voice) => (
      (chosen === 'all' || voice.method === chosen)
      && (!query || `${voice.name || ''} ${voice.description || ''} ${voice.method_label || ''}`.toLocaleLowerCase().includes(query))
    ));
    content.replaceChildren();
    content.dataset.state = visible.length > 25 ? STATES[4] : STATES[3];
    if (!visible.length) {
      content.dataset.state = STATES[1];
      content.append(UI.emptyState({
        title: voices.length ? 'No voices match' : 'No voice resources',
        body: voices.length ? 'Clear the search or choose another method.' : 'Voice resources appear after local capabilities are discovered.',
      }));
      return;
    }
    if (!visible.includes(selected)) selected = visible[0];
    const list = document.createElement('ul');
    list.className = 'supporting-list';
    list.setAttribute('aria-label', 'Voice resources');
    visible.forEach((voice) => {
      const row = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'supporting-list__button';
      button.setAttribute('aria-pressed', String(voice === selected));
      button.append(
        text('strong', 'entity-title', voice.name || 'Unnamed voice'),
        text('span', 'metadata', `${voice.method_label || voice.method || 'Voice'} · ${voice.state || 'unknown'}`),
      );
      button.addEventListener('click', () => {
        selected = voice;
        render();
      });
      row.append(button);
      list.append(row);
    });
    const master = document.createElement('section');
    master.className = 'supporting-master';
    master.append(list);
    content.append(UI.masterDetail({ master, detail: detailFor(selected) }));
  };

  const load = async () => {
    const result = await api.get('/api/voice-library', { signal });
    if (disposed || signal.aborted) return;
    if (!result.ok) {
      content.dataset.state = STATES[2];
      content.replaceChildren(UI.notice({
        tone: 'error', title: 'Voices could not load', body: result.error, live: true,
        action: UI.button({ label: 'Retry', onClick: load }),
      }));
      return;
    }
    voices = Array.isArray(result.data?.voices) ? result.data.voices : [];
    projectId = result.data?.project_id || projectId;
    const options = [{ value: 'all', label: 'All methods' }, ...new Set(voices.map((item) => item.method))]
      .map((entry) => typeof entry === 'string' ? { value: entry, label: entry.replaceAll('_', ' ') } : entry);
    const select = method.querySelector('select');
    select.replaceChildren();
    options.forEach((entry) => {
      const option = document.createElement('option');
      option.value = entry.value;
      option.textContent = entry.label;
      select.append(option);
    });
    render();
  };

  search.querySelector('input').addEventListener('input', render);
  method.querySelector('select').addEventListener('change', render);
  await load();
  return () => {
    if (disposed) return;
    disposed = true;
  };
}
