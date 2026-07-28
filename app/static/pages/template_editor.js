'use strict';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function field(options) {
  const wrapper = UI.field(options);
  const control = wrapper.querySelector('input, select, textarea');
  control.name = options.name;
  return { wrapper, control };
}

function templateFields(template = {}) {
  return {
    name: template.name || '',
    intent: template.intent || '',
    description: template.description || '',
    generation_method: template.generation_method || 'local',
    preset: template.preset || 'standard',
    source_language: template.source_language || 'English',
    output_language: template.output_language || 'English',
  };
}

export function createTemplateEditor({ shell, api, signal, getCatalog, onSaved }) {
  let releaseOverlay = null;
  let layer = null;
  let returnFocus = null;

  const close = (restoreFocus = true) => {
    releaseOverlay?.();
    releaseOverlay = null;
    layer = null;
    if (restoreFocus) returnFocus?.focus();
  };

  const open = (template = null, opener = document.activeElement) => {
    if (layer || signal.aborted) return;
    returnFocus = opener;
    const values = templateFields(template || {});
    layer = document.createElement('div');
    layer.className = 'dialog-layer';
    const surface = document.createElement('section');
    surface.className = 'dialog-surface template-editor';
    surface.setAttribute('role', 'dialog');
    surface.setAttribute('aria-modal', 'true');
    surface.setAttribute('aria-labelledby', 'template-editor-title');
    const heading = text('h2', 'section-title', template ? 'Edit Template' : 'New Template');
    heading.id = 'template-editor-title';
    const form = document.createElement('form');
    form.className = 'template-editor__form';
    const name = field({ label: 'Template name', name: 'name', value: values.name, required: true });
    const intent = field({ label: 'Production intent', name: 'intent', value: values.intent, required: true });
    const description = field({ kind: 'textarea', label: 'Description', name: 'description', value: values.description });
    const method = field({
      kind: 'select', label: 'Script method', name: 'generation_method', value: values.generation_method,
      options: [
        { value: 'local', label: 'Local' },
        { value: 'chatgpt_task_bundle', label: 'ChatGPT task bundle' },
        { value: 'import_existing_script', label: 'Existing Script import' },
      ],
    });
    const preset = field({
      kind: 'select', label: 'Preset', name: 'preset', value: values.preset,
      options: ['standard', 'maximum_fidelity', 'faster_draft', 'custom'],
    });
    const sourceLanguage = field({
      label: 'Source language', name: 'source_language', value: values.source_language,
    });
    const outputLanguage = field({
      label: 'Output language', name: 'output_language', value: values.output_language,
    });
    const status = text('div', 'transaction-status', '');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const footer = document.createElement('footer');
    footer.className = 'dialog__footer';
    const cancel = UI.button({ label: 'Cancel', variant: 'secondary', onClick: close });
    const save = UI.button({
      label: template ? 'Save Changes' : 'Save Template',
      variant: 'primary',
      type: 'submit',
    });
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
        return;
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
      status.textContent = template ? 'Saving changes…' : 'Saving template…';
      const catalog = getCatalog();
      const body = {
        expected_catalog_fingerprint: catalog.catalog_fingerprint,
        ...(template ? { expected_template_fingerprint: template.fingerprint } : {}),
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
      const result = template
        ? await api.put(`/api/templates/${encodeURIComponent(template.id)}`, body, { signal })
        : await api.post('/api/templates', body, { signal });
      if (signal.aborted || !layer) return;
      save.disabled = false;
      if (!result.ok) {
        status.textContent = result.error || 'The template could not be saved.';
        return;
      }
      close();
      await onSaved(result.data, result.data?.template || null);
    });
    requestAnimationFrame(() => name.control.focus());
  };

  return Object.freeze({ open, close, cleanup: () => close(false) });
}
