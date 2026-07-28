'use strict';

import { createTemplateActions } from './template_actions.js';
import { createTemplateEditor } from './template_editor.js';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);

function text(tag, className, value) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function ownerFor(route, openEditor) {
  const owner = document.createElement('article');
  owner.className = 'supporting-page templates-page';
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = route.path;
  const action = UI.button({ label: 'New Template', variant: 'primary', onClick: openEditor });
  const title = UI.pageTitleBlock({
    title: 'Templates',
    subtitle: 'Save named production intent for future projects.',
    actions: action,
  });
  title.querySelector('h1').dataset.pageHeading = '';
  owner.append(title);
  return owner;
}

export async function mount({ root, route, shell, api, signal }) {
  let disposed = false;
  let catalog = { catalog_fingerprint: '', templates: [] };
  let selected = null;
  let editor = null;
  const owner = ownerFor(route, () => editor?.open(
    null,
    owner.querySelector('.page-title-block > .ui-button'),
  ));
  const toolbar = document.createElement('div');
  toolbar.className = 'page-toolbar';
  const search = UI.searchField({ label: 'Search Templates', placeholder: 'Search templates' });
  const methodFilter = UI.field({
    kind: 'select', label: 'Method',
    options: [
      { value: 'all', label: 'All methods' },
      { value: 'local', label: 'Local' },
      { value: 'chatgpt_task_bundle', label: 'ChatGPT task bundle' },
      { value: 'import_existing_script', label: 'Existing Script import' },
    ],
    value: route.context.filter || 'all',
  });
  toolbar.append(search, methodFilter);
  const content = document.createElement('section');
  content.className = 'content-state';
  content.dataset.state = STATES[0];
  content.append(UI.skeleton({ label: 'Loading Templates' }), UI.skeleton());
  owner.append(toolbar, content);
  root.replaceChildren(owner);
  shell.player.set({ state: 'inactive', title: 'No audio selected' });

  const useTemplate = (template) => {
    shell.navigate(shell.routes.routeForPath('projects', {
      mode: 'new', source: template.id,
    }).hash);
  };

  const detailFor = (template) => {
    const detail = document.createElement('section');
    detail.className = 'supporting-detail';
    detail.append(
      text('div', 'metadata', template.default ? 'Default template' : template.built_in ? 'Built-in template' : 'Custom template'),
      text('h2', 'section-title', template.name || 'Unnamed template'),
      text('p', 'flat-section__body', template.description || template.intent || 'No description supplied.'),
    );
    const facts = document.createElement('dl');
    facts.className = 'fact-list';
    for (const [label, value] of [
      ['Intent', template.intent],
      ['Script method', template.generation_method],
      ['Preset', template.preset],
      ['Languages', [template.source_language, template.output_language].filter(Boolean).join(' → ')],
    ]) {
      facts.append(text('dt', 'metadata', label), text('dd', '', value || 'Not specified'));
    }
    detail.append(facts, createTemplateActions({
      template,
      getCatalog: () => catalog,
      api,
      signal,
      onChanged: async (nextCatalog, preferred) => {
        catalog = nextCatalog || catalog;
        selected = preferred || catalog.templates?.find((item) => item.id === template.id) || null;
        render();
      },
      onEdit: (item, opener) => editor?.open(item, opener),
      onUse: useTemplate,
    }));
    return detail;
  };

  const render = () => {
    if (disposed || signal.aborted) return;
    const query = search.querySelector('input').value.trim().toLocaleLowerCase();
    const method = methodFilter.querySelector('select').value;
    const visible = (catalog.templates || []).filter((template) => (
      (method === 'all' || template.generation_method === method)
      && (!query || `${template.name || ''} ${template.intent || ''} ${template.description || ''}`.toLocaleLowerCase().includes(query))
    ));
    content.replaceChildren();
    content.dataset.state = visible.length > 20 ? STATES[4] : STATES[3];
    if (!visible.length) {
      content.dataset.state = STATES[1];
      content.append(UI.emptyState({
        title: catalog.templates?.length ? 'No templates match' : 'No templates available',
        body: catalog.templates?.length ? 'Clear the search or choose another method.' : 'Create a named production template to begin.',
        action: UI.button({
          label: 'New Template',
          variant: 'primary',
          onClick: (event) => editor?.open(null, event.currentTarget),
        }),
      }));
      return;
    }
    const selectedId = selected?.id;
    selected = visible.find((template) => template.id === selectedId) || visible[0];
    const list = document.createElement('ul');
    list.className = 'supporting-list';
    list.setAttribute('aria-label', 'Project templates');
    visible.forEach((template) => {
      const row = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'supporting-list__button';
      button.setAttribute('aria-pressed', String(template === selected));
      button.append(
        text('strong', 'entity-title', template.name || 'Unnamed template'),
        text('span', 'metadata', `${template.generation_method || 'local'} · ${template.preset || 'standard'}`),
      );
      button.addEventListener('click', () => {
        selected = template;
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

  editor = createTemplateEditor({
    shell,
    api,
    signal,
    getCatalog: () => catalog,
    onSaved: async (nextCatalog, preferred) => {
      catalog = nextCatalog || catalog;
      selected = preferred || null;
      render();
    },
  });

  const load = async () => {
    const result = await api.get('/api/templates', { signal });
    if (disposed || signal.aborted) return;
    if (!result.ok) {
      content.dataset.state = STATES[2];
      content.replaceChildren(UI.notice({
        tone: 'error', title: 'Templates could not load', body: result.error, live: true,
        action: UI.button({ label: 'Retry', onClick: load }),
      }));
      return;
    }
    catalog = result.data || catalog;
    render();
  };

  search.querySelector('input').addEventListener('input', render);
  methodFilter.querySelector('select').addEventListener('change', render);
  await load();
  return () => {
    if (disposed) return;
    disposed = true;
    editor?.cleanup();
  };
}
