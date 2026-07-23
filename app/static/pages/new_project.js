'use strict';

const UI = globalThis.AlexandriaUI;
const dataNewProject = 'newProject';
const dataNewProjectClose = 'newProjectClose';

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

function choiceValue(form, name) {
  return form.querySelector(`input[name="${name}"]:checked`)?.value || '';
}

export function createNewProjectController({
  shell, api, signal, getCatalogFingerprint, onCreated, templateId = '',
}) {
  const state = { sourceFile: null, inspection: null };
  let layer = null;
  let releaseOverlay = null;
  let opener = null;
  let disposed = false;

  function close(restoreFocus = true) {
    if (!layer) return;
    releaseOverlay?.();
    layer = null;
    releaseOverlay = null;
    state.sourceFile = null;
    state.inspection = null;
    if (restoreFocus && opener?.isConnected) opener.focus();
  }

  function build() {
    layer = document.createElement('div');
    layer.className = 'dialog-layer new-project-layer';
    layer.dataset[dataNewProject] = '';
    const surface = document.createElement('section');
    surface.className = 'dialog-surface new-project';
    surface.setAttribute('role', 'dialog');
    surface.setAttribute('aria-modal', 'true');
    surface.setAttribute('aria-labelledby', 'new-project-title');
    const header = document.createElement('header');
    header.className = 'dialog__header';
    const title = text('h2', 'section-title', 'New Project');
    title.id = 'new-project-title';
    const closeButton = UI.button({ label: 'Close', variant: 'quiet', onClick: () => close() });
    closeButton.dataset[dataNewProjectClose] = '';
    header.append(title, closeButton);

    const form = document.createElement('form');
    form.className = 'new-project__form';
    const source = field({
      label: 'Book or Script source',
      description: 'EPUB, plain text, Markdown, or an existing Script file.',
      type: 'file',
      name: 'source_file',
      required: true,
      attributes: { accept: '.epub,.txt,.md,.json' },
    });
    const sourceStatus = text('div', 'transaction-status', 'Choose a source to inspect.');
    sourceStatus.setAttribute('role', 'status');
    sourceStatus.setAttribute('aria-live', 'polite');

    const identity = document.createElement('div');
    identity.className = 'form-grid';
    const projectName = field({ label: 'Project name', name: 'project_name', required: true });
    const bookTitle = field({ label: 'Book title', name: 'book_title' });
    const author = field({ label: 'Author', name: 'author' });
    const sourceLanguage = field({ label: 'Source language', name: 'source_language', value: 'English', required: true });
    const outputLanguage = field({ label: 'Output language', name: 'output_language', value: 'English', required: true });
    identity.append(projectName.wrapper, bookTitle.wrapper, author.wrapper, sourceLanguage.wrapper, outputLanguage.wrapper);

    const method = UI.radioGroup({
      label: 'Script method',
      name: 'generation_method',
      options: [
        { value: 'local', label: 'Create locally', checked: true },
        { value: 'chatgpt_task_bundle', label: 'ChatGPT task bundle' },
        { value: 'import_existing_script', label: 'Import existing Script' },
      ],
    });
    const preset = UI.radioGroup({
      label: 'Production preset',
      name: 'preset',
      options: [
        { value: 'standard', label: 'Standard', checked: true },
        { value: 'maximum_fidelity', label: 'Maximum fidelity' },
        { value: 'faster_draft', label: 'Faster draft' },
        { value: 'custom', label: 'Custom' },
      ],
    });
    const advanced = document.createElement('div');
    advanced.className = 'new-project__advanced';
    advanced.append(method, preset);
    const disclosure = UI.disclosure({
      label: 'Production intent and language',
      content: advanced,
      expanded: Boolean(templateId),
    });
    const submitStatus = text('div', 'transaction-status', '');
    submitStatus.setAttribute('role', 'status');
    submitStatus.setAttribute('aria-live', 'polite');
    const footer = document.createElement('footer');
    footer.className = 'dialog__footer';
    const cancel = UI.button({ label: 'Cancel', variant: 'quiet', onClick: () => close() });
    const create = UI.button({ label: 'Create Project', variant: 'primary', type: 'submit' });
    footer.append(cancel, create);
    form.append(source.wrapper, sourceStatus, identity, disclosure, submitStatus, footer);
    surface.append(header, form);
    layer.append(surface);

    const applyTemplate = async () => {
      if (!templateId) return;
      const result = await api.get('/api/templates', { signal });
      if (!result.ok || signal.aborted || !layer) return;
      const template = (result.data?.templates || []).find((item) => item.id === templateId);
      if (!template) return;
      for (const [name, value] of [
        ['generation_method', template.generation_method],
        ['preset', template.preset],
      ]) {
        const control = form.querySelector(`input[name="${name}"][value="${value}"]`);
        if (control) control.checked = true;
      }
      sourceLanguage.control.value = template.source_language || 'English';
      outputLanguage.control.value = template.output_language || 'English';
      submitStatus.textContent = `Using template: ${template.name}`;
    };

    const inspect = async () => {
      const candidate = source.control.files?.[0];
      if (!candidate) return;
      const previousFile = state.sourceFile;
      const previousInspection = state.inspection;
      sourceStatus.textContent = `Inspecting ${candidate.name}…`;
      const formData = new FormData();
      formData.append('generation_method', choiceValue(form, 'generation_method') || 'local');
      formData.append('source_file', candidate);
      const result = await api.post('/api/projects/inspect-source', formData, { signal });
      if (signal.aborted || !layer) return;
      if (!result.ok || result.data?.valid === false) {
        state.sourceFile = previousFile;
        state.inspection = previousInspection;
        sourceStatus.textContent = previousFile
          ? `${result.error || result.data?.message || 'That source is not valid'}; the previously validated source is still attached.`
          : (result.error || result.data?.message || 'That source is not valid.');
        return;
      }
      state.sourceFile = candidate;
      state.inspection = result.data;
      bookTitle.control.value = result.data.title || '';
      author.control.value = result.data.author || '';
      projectName.control.value ||= result.data.title || candidate.name.replace(/\.[^.]+$/, '');
      sourceLanguage.control.value = result.data.language || sourceLanguage.control.value;
      sourceStatus.textContent = `${candidate.name} is valid and ready.`;
    };

    const submit = async (event) => {
      event.preventDefault();
      if (!state.sourceFile) {
        sourceStatus.textContent = 'Inspect a valid source before creating the project.';
        source.control.focus();
        return;
      }
      create.disabled = true;
      submitStatus.textContent = 'Creating and activating the project…';
      const formData = new FormData();
      formData.append('project_name', projectName.control.value.trim());
      formData.append('book_title', bookTitle.control.value.trim());
      formData.append('author', author.control.value.trim());
      formData.append('source_language', sourceLanguage.control.value.trim());
      formData.append('output_language', outputLanguage.control.value.trim());
      formData.append('generation_method', choiceValue(form, 'generation_method'));
      formData.append('preset', choiceValue(form, 'preset'));
      formData.append('expected_catalog_fingerprint', getCatalogFingerprint());
      formData.append('source_file', state.sourceFile);
      if (templateId) formData.append('template_id', templateId);
      const result = await api.post('/api/projects', formData, { signal });
      if (signal.aborted || !layer) return;
      create.disabled = false;
      if (!result.ok) {
        submitStatus.textContent = result.error || 'The project could not be created.';
        return;
      }
      const activation = result.data?.activation || {};
      const current = activation.state === 'current'
        || result.data?.activation_state === 'current';
      if (!current) {
        submitStatus.textContent = 'The project was created but could not become current. It remains safe in Projects.';
        return;
      }
      const project = result.data?.project;
      const destination = activation.native_destination || 'script';
      close(false);
      onCreated(project, destination);
    };

    source.control.addEventListener('change', inspect);
    form.addEventListener('submit', submit);
    layer.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = [...layer.querySelectorAll('button, input, select, textarea')].filter((item) => !item.disabled);
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    applyTemplate();
    requestAnimationFrame(() => source.control.focus());
  }

  return Object.freeze({
    open(sourceOpener) {
      if (disposed || signal.aborted || layer) return;
      opener = sourceOpener;
      build();
      releaseOverlay = shell.overlay.open(layer);
    },
    cleanup() {
      if (disposed) return;
      disposed = true;
      close(false);
    },
  });
}
