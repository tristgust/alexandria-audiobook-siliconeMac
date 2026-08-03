'use strict';

import {
  createTemplateDeleteController, createTemplateEditor, methodLabel,
  ownerForTemplates, presetLabel, templateMark, text,
} from './templates_components.js';
import { templatesLoading } from './supporting_page_loading.js';
import { configureSupportingListbox, restoreSupportingSelectionFocus } from './supporting_selection.js';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);
export async function mount({ root, route, shell, api, signal }) {
  let disposed = false;
  let editor = null;
  let deleteController = null;
  let catalog = { catalog_fingerprint: '', templates: [] };
  let selected = null;
  let newTemplateAction = null;
  const owner = ownerForTemplates(route);
  newTemplateAction = UI.button({
    label: 'New template', variant: 'secondary', onClick: () => editor?.open(),
  });
  shell.globalHeader.set({
    title: 'Templates',
    subtitle: 'Save named production intent for future projects.',
    actions: newTemplateAction,
  });
  const toolbar = document.createElement('div');
  toolbar.className = 'page-toolbar';
  const search = UI.searchField({
    label: 'Search Templates', placeholder: 'Search templates',
    iconClass: 'fas fa-magnifying-glass',
  });
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
  content.append(templatesLoading());
  owner.append(toolbar, content);
  root.replaceChildren(owner);
  shell.player.set({ state: 'inactive', title: 'No audio selected' });

  const useTemplate = (template) => {
    shell.navigate(shell.routes.routeForPath('projects', {
      mode: 'new', source: template.id,
    }).hash);
  };

  const useCatalog = (nextCatalog, preferredId = '') => {
    catalog = nextCatalog || catalog;
    selected = (catalog.templates || []).find((item) => item.id === preferredId)
      || (catalog.templates || [])[0] || null;
    render();
  };

  const detailFor = (template) => {
    const detail = document.createElement('section');
    detail.className = 'supporting-detail';
    const identity = document.createElement('header');
    identity.className = 'supporting-detail__identity templates-detail__identity';
    const copy = document.createElement('div');
    copy.append(
      text('div', 'metadata', template.default ? 'Default template' : template.built_in ? 'Built-in template' : 'Custom template'),
      text('h2', 'section-title', template.name || 'Unnamed template'),
    );
    const use = UI.button({
      label: 'Start new project', variant: 'primary', onClick: () => useTemplate(template),
    });
    identity.append(templateMark(template, 'supporting-detail__mark'), copy, use);
    detail.append(
      identity,
      text('p', 'flat-section__body', template.description || template.intent || 'No description supplied.'),
    );
    const facts = document.createElement('dl');
    facts.className = 'fact-list';
    for (const [label, value] of [
      ['Intent', template.intent],
      ['Script method', methodLabel(template.generation_method)],
      ['Preset', presetLabel(template.preset)],
      ['Languages', [template.source_language, template.output_language].filter(Boolean).join(' → ')],
    ]) {
      facts.append(text('dt', 'metadata', label), text('dd', '', value || 'Not specified'));
    }
    const management = document.createElement('div');
    management.className = 'template-management-actions';
    const feedback = text('div', 'transaction-status', '');
    feedback.setAttribute('role', 'status');
    feedback.setAttribute('aria-live', 'polite');
    if (template.editable) {
      const edit = UI.button({
        label: 'Edit template', variant: 'secondary',
        attributes: { 'data-template-edit': '' },
        onClick: () => editor?.open(template, edit),
      });
      management.append(edit);
    }
    const duplicate = UI.button({
      label: 'Duplicate template', variant: 'secondary',
      attributes: { 'data-template-duplicate': '' },
      onClick: async () => {
        duplicate.disabled = true;
        feedback.textContent = 'Duplicating template…';
        const existing = new Set((catalog.templates || []).map((item) => item.name?.toLocaleLowerCase()));
        let name = `${template.name} copy`;
        let ordinal = 2;
        while (existing.has(name.toLocaleLowerCase())) {
          name = `${template.name} copy ${ordinal}`;
          ordinal += 1;
        }
        const result = await api.post(`/api/templates/${encodeURIComponent(template.id)}/duplicate`, {
          expected_catalog_fingerprint: catalog.catalog_fingerprint,
          name,
        }, { signal });
        if (signal.aborted) return;
        duplicate.disabled = false;
        if (!result.ok) {
          feedback.textContent = result.error || 'The template could not be duplicated.';
          return;
        }
        useCatalog(result.data, result.data?.template?.id);
      },
    });
    management.append(duplicate);
    if (!template.default) {
      const makeDefault = UI.button({
        label: 'Make default', variant: 'secondary',
        attributes: { 'data-template-default': '' },
        onClick: async () => {
          makeDefault.disabled = true;
          feedback.textContent = 'Updating default template…';
          const result = await api.post(`/api/templates/${encodeURIComponent(template.id)}/default`, {
            expected_catalog_fingerprint: catalog.catalog_fingerprint,
          }, { signal });
          if (signal.aborted) return;
          makeDefault.disabled = false;
          if (!result.ok) {
            feedback.textContent = result.error || 'The default template could not be changed.';
            return;
          }
          useCatalog(result.data, template.id);
        },
      });
      management.append(makeDefault);
    }
    if (template.deletable) {
      const remove = UI.button({
        label: 'Delete template', variant: 'destructive',
        attributes: { 'data-template-delete': '' },
        onClick: () => deleteController?.open(template, remove, feedback),
      });
      management.append(remove);
    }
    detail.append(facts, management, feedback);
    return detail;
  };

  const render = (focusKey = '') => {
    if (disposed || signal.aborted) return;
    const query = search.querySelector('input').value.trim().toLocaleLowerCase();
    const method = methodFilter.querySelector('select').value;
    const visible = (catalog.templates || []).filter((template) => (
      (method === 'all' || template.generation_method === method)
      && (!query || `${template.name || ''} ${template.intent || ''} ${template.description || ''}`.toLocaleLowerCase().includes(query))
    ));
    content.textContent = '';
    content.dataset.state = visible.length > 20 ? STATES[4] : STATES[3];
    if (!visible.length) {
      content.dataset.state = STATES[1];
      content.append(UI.emptyState({
        iconClass: catalog.templates?.length ? 'fas fa-filter-circle-xmark' : 'far fa-file-lines',
        title: catalog.templates?.length ? 'No templates match' : 'No templates available',
        body: catalog.templates?.length ? 'Clear the search or choose another method.' : 'Create a named production template to begin.',
        action: UI.button({ label: 'New template', variant: 'primary', onClick: () => editor?.open() }),
      }));
      return;
    }
    if (!visible.includes(selected)) selected = visible[0];
    const list = document.createElement('ul');
    list.className = 'supporting-list';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Project templates');
    visible.forEach((template) => {
      const row = document.createElement('li');
      row.setAttribute('role', 'presentation');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'supporting-list__button supporting-list__button--icon';
      const copy = document.createElement('span');
      copy.className = 'supporting-list__copy';
      copy.append(
        text('strong', 'entity-title', template.name || 'Unnamed template'),
        text('span', 'metadata', `${methodLabel(template.generation_method)} · ${presetLabel(template.preset)}`),
      );
      button.append(templateMark(template), copy);
      const selectionKey = String(template.id || template.name || 'template');
      configureSupportingListbox(list, button, {
        selected: template === selected, key: selectionKey,
        onSelect: () => { selected = template; render(selectionKey); },
      });
      row.append(button);
      list.append(row);
    });
    const master = document.createElement('section');
    master.className = 'supporting-master';
    master.append(list);
    content.append(UI.masterDetail({ master, detail: detailFor(selected) }));
    restoreSupportingSelectionFocus(content, focusKey);
  };

  editor = createTemplateEditor({
    shell,
    api,
    signal,
    getCatalog: () => catalog,
    getOpener: () => newTemplateAction,
    onSaved: (nextCatalog) => {
      useCatalog(nextCatalog, nextCatalog?.template?.id);
    },
  });
  deleteController = createTemplateDeleteController({
    shell, api, signal,
    onDeleted: (nextCatalog) => useCatalog(nextCatalog),
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
    editor?.close(false);
    deleteController?.close(false);
  };
}
