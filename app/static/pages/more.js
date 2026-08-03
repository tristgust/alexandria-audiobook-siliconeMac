'use strict';

const UI = globalThis.AlexandriaUI;
const STYLESHEET = '/static/styles/pages/settings_more.css';
const DIRECT_TOOL_PATHS = Object.freeze({
  'advanced-character-operations': 'more/advanced-character-operations',
  'voice-designer': 'more/voice-designer',
  'audio-preparer': 'more/audio-preparer',
  'dataset-builder': 'more/dataset-builder',
  'voice-training': 'more/voice-training',
  maintenance: 'more/maintenance',
  'model-cache': 'more/model-cache',
  'help-center': 'more/help-center',
});
const TOOL_ACTION_LABELS = Object.freeze({
  'advanced-character-operations': 'Review identities',
  'voice-designer': 'Design a Voice',
  'audio-preparer': 'Prepare audio',
  'dataset-builder': 'Build a dataset',
  'voice-training': 'Open Voice Lab',
  maintenance: 'Open Maintenance',
  'model-cache': 'Manage model cache',
  'help-center': 'Open Help Center',
});

export function ensureSupportStyles() {
  if (document.querySelector(`link[href="${STYLESHEET}"]`)) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = STYLESHEET;
  link.dataset.settingsMoreStyles = '';
  document.head.append(link);
}

export function textNode(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function resultMessage(result, fallback) {
  return result?.data?.detail?.message
    || result?.data?.message
    || result?.error
    || fallback;
}

export function supportOwner(root, route, {
  shell,
  page,
  title,
  subtitle,
  actions = [],
  className = '',
}) {
  ensureSupportStyles();
  const owner = document.createElement('article');
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = page;
  owner.dataset.viewState = 'loading';
  owner.className = `support-page ${className}`.trim();
  const embedded = Boolean(root.closest('[data-cast-workflow]'));
  let heading;
  if (embedded) {
    heading = UI.pageTitleBlock({
      id: `${page}-page-heading`, title, subtitle, actions,
    });
    heading.querySelector('h1').dataset.pageHeading = '';
  } else {
    shell?.globalHeader?.set({ title, subtitle, actions });
    heading = document.createElement('span');
    heading.id = `${page}-page-heading`;
    heading.className = 'visually-hidden';
    heading.dataset.pageHeading = '';
    heading.textContent = title;
  }
  const stateRegion = document.createElement('div');
  stateRegion.setAttribute('data-state-region', '');
  stateRegion.className = 'support-state';
  stateRegion.append(UI.loadingState({
    label: `Loading ${title}`,
    detail: 'Reading the current tool state and available actions.',
  }));
  owner.append(heading, stateRegion);
  root.replaceChildren(owner);
  return { owner, stateRegion };
}

export function supportReturn(route, shell, fallback = '#/more') {
  const destination = route.context.return || fallback;
  const button = UI.button({
    label: 'Back',
    variant: 'quiet',
    attributes: {
      'data-support-return': '',
      'aria-label': 'Back to the previous workspace',
    },
  });
  button.classList.add('support-return');
  button.addEventListener('click', () => shell.navigate(destination));
  return button;
}

export function statusLine(label, detail, tone = 'neutral') {
  const row = document.createElement('div');
  row.className = 'support-list-row';
  const copy = document.createElement('div');
  copy.append(textNode('strong', '', label));
  if (detail) copy.append(textNode('p', 'support-status-copy', detail));
  row.append(copy, UI.status({ label, tone }));
  return row;
}

export function readCount(value) {
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === 'object') return Object.keys(value).length;
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function queryForMore(route) {
  const params = new URLSearchParams();
  if (route.context.project) params.set('project_id', route.context.project);
  if (route.context.character) params.set('character_id', route.context.character);
  if (route.context.source) params.set('source', route.context.source);
  params.set('return_route', route.context.return || route.hash);
  return params;
}

function toolContext(tool, route) {
  const backend = tool.route?.context || {};
  return {
    project: backend.project || route.context.project,
    character: backend.character || route.context.character,
    source: backend.source || route.context.source,
    return: backend.return || route.context.return || route.hash,
  };
}

function renderDirectory({ payload, route, shell, owner, stateRegion }) {
  owner.dataset.landingMutationSupported = String(Boolean(
    payload.landing_mutation_supported,
  ));
  const search = String(route.context.search || '').trim().toLocaleLowerCase();
  const tools = (payload.tools || []).filter((tool) => {
    if (!search) return true;
    return [tool.title, tool.description, tool.category_label]
      .some((value) => String(value || '').toLocaleLowerCase().includes(search));
  });
  const toolbar = document.createElement('div');
  toolbar.className = 'support-toolbar';
  if (route.context.return) toolbar.append(supportReturn(route, shell));
  const searchField = UI.searchField({
    label: 'Search specialist tools',
    placeholder: 'Search tools',
  });
  const input = searchField.querySelector('input');
  input.value = route.context.search || '';
  let searchTimer = 0;
  input.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      const target = shell.routes.withContext(route, { search: input.value.trim() });
      shell.navigate(target.hash, { historyMode: 'replace' });
    }, 120);
  });
  toolbar.append(searchField);

  const groups = document.createElement('div');
  groups.className = 'more-tool-groups';
  const categories = new Map((payload.categories || []).map((category) => [
    category.id,
    { id: category.id, label: category.label, tools: [] },
  ]));
  tools.forEach((tool) => {
    const category = categories.get(tool.category) || {
      id: tool.category || 'other',
      label: tool.category_label || 'Tools',
      tools: [],
    };
    category.tools.push(tool);
    categories.set(tool.category, category);
  });
  categories.forEach((category) => {
    if (!category.tools.length) return;
    const section = document.createElement('section');
    section.className = 'more-tool-group';
    section.dataset.category = category.id;
    section.append(textNode('h2', '', category.label));
    category.tools.forEach((tool) => {
      const row = document.createElement('div');
      row.className = 'more-tool-row';
      const copy = document.createElement('div');
      copy.className = 'more-tool-copy';
      copy.append(
        textNode('strong', '', tool.title),
        textNode('p', 'support-status-copy', tool.description),
      );
      const open = UI.button({
        label: TOOL_ACTION_LABELS[tool.tool] || `Open ${tool.title}`,
        variant: 'quiet',
        attributes: {
          'data-more-tool': tool.tool,
        },
      });
      open.addEventListener('click', () => {
        const path = DIRECT_TOOL_PATHS[tool.tool];
        if (!path) return;
        const target = shell.routes.routeForPath(
          path,
          toolContext(tool, route),
        );
        shell.navigate(target.hash);
      });
      row.append(copy, open);
      section.append(row);
    });
    groups.append(section);
  });

  const content = document.createDocumentFragment();
  content.append(toolbar);
  content.append(groups.childElementCount ? groups : UI.emptyState({
    title: 'No specialist tool matches',
    body: 'Clear the search to see every available tool.',
  }));
  stateRegion.replaceChildren(content);
  owner.dataset.viewState = tools.length ? 'ready' : 'empty';
  return () => clearTimeout(searchTimer);
}

export async function mount({ root, route, shell, api, signal }) {
  const dataRouteOwner = route.path;
  const { owner, stateRegion } = supportOwner(root, route, {
    shell,
    page: 'more',
    title: 'More',
    subtitle: 'Specialist tools, maintenance, and bundled guidance.',
    className: 'more-workspace',
  });
  owner.dataset.routeOwner = dataRouteOwner;
  const query = queryForMore(route);
  const result = await api.get(`/api/more?${query}`, { signal });
  if (signal.aborted) return () => {};
  if (!result.ok) {
    owner.dataset.viewState = 'error';
    stateRegion.replaceChildren(UI.notice({
      tone: 'error',
      title: 'Specialist tools could not be loaded',
      body: resultMessage(result, 'Try again from More.'),
      live: true,
    }));
    return () => {};
  }
  return renderDirectory({
    payload: result.data,
    route,
    shell,
    owner,
    stateRegion,
  });
}
