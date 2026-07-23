'use strict';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);

function text(tag, className, value) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function field(options) {
  const wrapper = UI.field(options);
  const control = wrapper.querySelector('input, select, textarea');
  control.name = options.name;
  return { wrapper, control };
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
  let releaseOverlay = null;
  let editorLayer = null;
  let catalog = { catalog_fingerprint: '', templates: [] };
  let selected = null;
  const closeEditor = (restoreFocus = true) => {
    const restore = owner?.querySelector('.page-title-block > .ui-button');
    releaseOverlay?.();
    releaseOverlay = null;
    editorLayer = null;
    if (restoreFocus) restore?.focus();
  };
  const owner = ownerFor(route, () => openEditor());
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
    detail.append(facts, UI.button({
      label: 'Use Template', variant: 'primary', onClick: () => useTemplate(template),
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
        action: UI.button({ label: 'New Template', variant: 'primary', onClick: openEditor }),
      }));
      return;
    }
    if (!visible.includes(selected)) selected = visible[0];
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

  async function openEditor() {
    if (disposed || signal.aborted || editorLayer) return;
    editorLayer = document.createElement('div');
    editorLayer.className = 'dialog-layer';
    const surface = document.createElement('section');
    surface.className = 'dialog-surface template-editor';
    surface.setAttribute('role', 'dialog');
    surface.setAttribute('aria-modal', 'true');
    surface.setAttribute('aria-labelledby', 'template-editor-title');
    const heading = text('h2', 'section-title', 'New Template');
    heading.id = 'template-editor-title';
    const form = document.createElement('form');
    form.className = 'template-editor__form';
    const name = field({ label: 'Template name', name: 'name', required: true });
    const intent = field({ label: 'Production intent', name: 'intent', required: true });
    const description = field({ kind: 'textarea', label: 'Description', name: 'description' });
    const method = field({
      kind: 'select', label: 'Script method', name: 'generation_method',
      options: [
        { value: 'local', label: 'Local' },
        { value: 'chatgpt_task_bundle', label: 'ChatGPT task bundle' },
        { value: 'import_existing_script', label: 'Existing Script import' },
      ],
    });
    const preset = field({
      kind: 'select', label: 'Preset', name: 'preset',
      options: ['standard', 'maximum_fidelity', 'faster_draft', 'custom'],
    });
    const sourceLanguage = field({ label: 'Source language', name: 'source_language', value: 'English' });
    const outputLanguage = field({ label: 'Output language', name: 'output_language', value: 'English' });
    const status = text('div', 'transaction-status', '');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const footer = document.createElement('footer');
    footer.className = 'dialog__footer';
    const cancel = UI.button({ label: 'Cancel', variant: 'quiet', onClick: closeEditor });
    const save = UI.button({ label: 'Save Template', variant: 'primary', type: 'submit' });
    footer.append(cancel, save);
    form.append(
      name.wrapper, intent.wrapper, description.wrapper, method.wrapper, preset.wrapper,
      sourceLanguage.wrapper, outputLanguage.wrapper, status, footer,
    );
    surface.append(heading, form);
    editorLayer.append(surface);
    releaseOverlay = shell.overlay.open(editorLayer);
    editorLayer.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeEditor();
      }
      if (event.key !== 'Tab') return;
      const controls = [...editorLayer.querySelectorAll('button, input, select, textarea')].filter((item) => !item.disabled);
      const target = event.shiftKey && document.activeElement === controls[0] ? controls.at(-1)
        : !event.shiftKey && document.activeElement === controls.at(-1) ? controls[0] : null;
      if (!target) return;
      event.preventDefault();
      target.focus();
    });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      save.disabled = true;
      status.textContent = 'Saving template…';
      const result = await api.post('/api/templates', {
        expected_catalog_fingerprint: catalog.catalog_fingerprint,
        template: {
          name: name.control.value.trim(),
          intent: intent.control.value.trim(),
          description: description.control.value.trim(),
          generation_method: method.control.value,
          preset: preset.control.value,
          source_language: sourceLanguage.control.value.trim(),
          output_language: outputLanguage.control.value.trim(),
        },
      }, { signal });
      if (signal.aborted || !editorLayer) return;
      save.disabled = false;
      if (!result.ok) {
        status.textContent = result.error || 'The template could not be saved.';
        return;
      }
      catalog = result.data;
      selected = result.data?.template || null;
      closeEditor();
      render();
    });
    requestAnimationFrame(() => name.control.focus());
  }

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
    closeEditor(false);
  };
}
