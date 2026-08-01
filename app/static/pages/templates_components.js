'use strict';

const UI = globalThis.AlexandriaUI;

export const PRESET_LABELS = Object.freeze({
  standard: 'Standard',
  maximum_fidelity: 'Maximum fidelity',
  faster_draft: 'Faster draft',
  custom: 'Custom',
});

const METHOD_LABELS = Object.freeze({
  local: 'Local generation',
  chatgpt_task_bundle: 'ChatGPT task bundle',
  import_existing_script: 'Existing Script import',
});

export const methodLabel = (value) => METHOD_LABELS[value]
  || String(value || 'Local generation').replaceAll('_', ' ');
export const presetLabel = (value) => PRESET_LABELS[value]
  || String(value || 'Standard').replaceAll('_', ' ');

export function templateMark(template, className = 'supporting-list__mark') {
  const mark = document.createElement('span');
  mark.className = className;
  mark.setAttribute('aria-hidden', 'true');
  mark.append(UI.icon(template?.built_in ? 'bookmark' : 'copy'));
  return mark;
}

export function text(tag, className, value) {
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

export function ownerForTemplates(route) {
  const owner = document.createElement('article');
  owner.className = 'supporting-page templates-page';
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = route.path;
  const heading = text('span', 'visually-hidden', 'Templates');
  heading.id = 'templates-page-heading';
  heading.dataset.pageHeading = '';
  owner.append(heading);
  return owner;
}

export function createTemplateEditor({
  shell, api, signal, getCatalog, getOpener, onSaved,
}) {
  let releaseOverlay = null;
  let layer = null;
  let returnFocus = null;

  const close = (restoreFocus = true) => {
    const restore = returnFocus?.isConnected ? returnFocus : getOpener();
    releaseOverlay?.();
    releaseOverlay = null;
    layer = null;
    returnFocus = null;
    if (restoreFocus) restore?.focus();
  };

  const open = (template = null, opener = document.activeElement) => {
    if (signal.aborted || layer) return;
    const editing = Boolean(template?.editable);
    returnFocus = opener;
    layer = document.createElement('div');
    layer.className = 'dialog-layer';
    const surface = document.createElement('section');
    surface.className = 'dialog-surface template-editor';
    surface.setAttribute('role', 'dialog');
    surface.setAttribute('aria-modal', 'true');
    surface.setAttribute('aria-labelledby', 'template-editor-title');
    const heading = text('h2', 'section-title', editing ? 'Edit Template' : 'New Template');
    heading.id = 'template-editor-title';
    const form = document.createElement('form');
    form.className = 'template-editor__form';
    const name = field({ label: 'Template name', name: 'name', required: true, value: template?.name || '' });
    const intent = field({ label: 'Production intent', name: 'intent', required: true, value: template?.intent || '' });
    const description = field({ kind: 'textarea', label: 'Description', name: 'description', value: template?.description || '' });
    const method = field({
      kind: 'select', label: 'Script method', name: 'generation_method',
      options: [
        { value: 'local', label: 'Local' },
        { value: 'chatgpt_task_bundle', label: 'ChatGPT task bundle' },
        { value: 'import_existing_script', label: 'Existing Script import' },
      ],
      value: template?.generation_method || 'local',
    });
    const preset = field({
      kind: 'select', label: 'Preset', name: 'preset',
      options: Object.entries(PRESET_LABELS).map(([value, label]) => ({ value, label })),
      value: template?.preset || 'standard',
    });
    const sourceLanguage = field({ label: 'Source language', name: 'source_language', value: template?.source_language || 'English' });
    const outputLanguage = field({ label: 'Output language', name: 'output_language', value: template?.output_language || 'English' });
    const status = text('div', 'transaction-status', '');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const footer = document.createElement('footer');
    footer.className = 'dialog__footer';
    const cancel = UI.button({ label: 'Cancel', variant: 'quiet', onClick: close });
    const save = UI.button({ label: editing ? 'Save Changes' : 'Save Template', variant: 'primary', type: 'submit' });
    footer.append(cancel, save);
    form.append(
      name.wrapper, intent.wrapper, description.wrapper, method.wrapper, preset.wrapper,
      sourceLanguage.wrapper, outputLanguage.wrapper, status, footer,
    );
    surface.append(heading, form);
    layer.append(surface);
    releaseOverlay = shell.overlay.open(layer);
    layer.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
      }
      if (event.key !== 'Tab') return;
      const controls = [...layer.querySelectorAll('button, input, select, textarea')]
        .filter((item) => !item.disabled);
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
      const payload = {
        expected_catalog_fingerprint: getCatalog().catalog_fingerprint,
        ...(editing ? { expected_template_fingerprint: template.fingerprint } : {}),
        template: {
          name: name.control.value.trim(),
          intent: intent.control.value.trim(),
          description: description.control.value.trim(),
          generation_method: method.control.value,
          preset: preset.control.value,
          source_language: sourceLanguage.control.value.trim(),
          output_language: outputLanguage.control.value.trim(),
        },
      };
      const result = editing
        ? await api.put(`/api/templates/${encodeURIComponent(template.id)}`, payload, { signal })
        : await api.post('/api/templates', payload, { signal });
      if (signal.aborted || !layer) return;
      save.disabled = false;
      if (!result.ok) {
        status.textContent = result.error || 'The template could not be saved.';
        return;
      }
      close();
      onSaved(result.data);
    });
    requestAnimationFrame(() => name.control.focus());
  };

  return Object.freeze({ open, close });
}

export function createTemplateDeleteController({ shell, api, signal, onDeleted }) {
  let releaseOverlay = null;
  let layer = null;
  let returnFocus = null;

  const close = (restoreFocus = true) => {
    const restore = returnFocus;
    releaseOverlay?.();
    releaseOverlay = null;
    layer = null;
    returnFocus = null;
    if (restoreFocus && restore?.isConnected) restore.focus();
  };

  const open = async (template, opener, feedback) => {
    if (signal.aborted || layer || !template?.deletable) return;
    returnFocus = opener;
    opener.disabled = true;
    feedback.textContent = 'Checking template usage…';
    const result = await api.get(
      `/api/templates/${encodeURIComponent(template.id)}/delete-impact`, { signal },
    );
    opener.disabled = false;
    if (signal.aborted) return;
    if (!result.ok) {
      feedback.textContent = result.error || 'Deletion safety could not be checked.';
      return;
    }
    const impact = result.data || {};
    if (!impact.safe_to_delete) {
      feedback.textContent = impact.blocking_reasons?.[0]?.message
        || 'Choose another default template before deleting this one.';
      return;
    }
    feedback.textContent = '';
    layer = document.createElement('div');
    layer.className = 'dialog-layer';
    const surface = document.createElement('section');
    surface.className = 'dialog-surface template-editor';
    surface.setAttribute('role', 'dialog');
    surface.setAttribute('aria-modal', 'true');
    surface.setAttribute('aria-labelledby', 'template-delete-title');
    const heading = text('h2', 'section-title', `Delete ${template.name}`);
    heading.id = 'template-delete-title';
    const body = text('p', 'flat-section__body', impact.message
      || 'Deleting this template does not change existing projects.');
    const confirmation = field({
      label: `Type “${impact.confirmation_text}” to confirm`,
      name: 'confirmation_text',
    });
    const acknowledgement = impact.requires_usage_acknowledgement
      ? UI.checkbox({
        label: `I understand ${impact.usage_count} existing project${impact.usage_count === 1 ? '' : 's'} will retain historical references to this template.`,
        checked: false,
      }) : null;
    const status = text('div', 'transaction-status', '');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const footer = document.createElement('footer');
    footer.className = 'dialog__footer';
    const cancel = UI.button({ label: 'Cancel', variant: 'quiet', onClick: close });
    const remove = UI.button({ label: 'Delete template', variant: 'destructive' });
    footer.append(cancel, remove);
    surface.append(heading, body, confirmation.wrapper);
    if (acknowledgement) surface.append(acknowledgement);
    surface.append(status, footer);
    layer.append(surface);
    releaseOverlay = shell.overlay.open(layer);
    layer.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== 'Tab') return;
      const controls = [...layer.querySelectorAll('button, input')]
        .filter((item) => !item.disabled);
      const target = event.shiftKey && document.activeElement === controls[0]
        ? controls.at(-1)
        : !event.shiftKey && document.activeElement === controls.at(-1)
          ? controls[0] : null;
      if (!target) return;
      event.preventDefault();
      target.focus();
    });
    remove.addEventListener('click', async () => {
      remove.disabled = true;
      status.textContent = 'Deleting template…';
      const deleted = await api.delete(`/api/templates/${encodeURIComponent(template.id)}`, {
        signal,
        body: {
          expected_catalog_fingerprint: impact.catalog_fingerprint,
          expected_template_fingerprint: template.fingerprint,
          confirmation_text: confirmation.control.value,
          acknowledge_usage: acknowledgement?.querySelector('input')?.checked === true,
        },
      });
      if (signal.aborted || !layer) return;
      remove.disabled = false;
      if (!deleted.ok) {
        status.textContent = deleted.error || 'The template could not be deleted.';
        return;
      }
      close(false);
      onDeleted(deleted.data);
    });
    requestAnimationFrame(() => confirmation.control.focus());
  };

  return Object.freeze({ open, close });
}
