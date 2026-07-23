'use strict';

import { buildNewProjectDialog } from './new_project_components.js';

const dataNewProject = 'newProject';
const dataNewProjectClose = 'newProjectClose';
const METHOD_OPTIONS = Object.freeze([
  { value: 'local', label: 'Generate locally', checked: true },
  { value: 'chatgpt_task_bundle', label: 'Use ChatGPT task bundle' },
  { value: 'import_existing_script', label: 'Import existing Script' },
]);
const PRESET_OPTIONS = Object.freeze([
  { value: 'standard', label: 'Standard', checked: true },
  { value: 'maximum_fidelity', label: 'Maximum fidelity' },
  { value: 'faster_draft', label: 'Faster draft' },
  { value: 'custom', label: 'Custom' },
]);

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
    const dialog = buildNewProjectDialog({
      templateId,
      onClose: () => close(),
      dataNewProject,
      dataNewProjectClose,
      methodOptions: METHOD_OPTIONS,
      presetOptions: PRESET_OPTIONS,
    });
    layer = dialog.layer;
    const {
      form, source, sourceStatus, bookTitle, author, sourceLanguage, outputLanguage,
      importNote, submitStatus, create, renderSourcePreview,
    } = dialog;
    const syncMethod = () => {
      const importing = choiceValue(form, 'generation_method') === 'import_existing_script';
      importNote.hidden = !importing;
      source.control.accept = importing ? '.json' : '.epub,.txt,.md,.json';
    };
    const syncCreateState = () => {
      const importing = choiceValue(form, 'generation_method') === 'import_existing_script';
      const sourceMatches = !importing || Boolean(state.sourceFile?.name?.toLowerCase().endsWith('.json'));
      create.disabled = !(state.sourceFile && sourceMatches
        && bookTitle.control.value.trim() && author.control.value.trim()
        && sourceLanguage.control.value.trim() && outputLanguage.control.value.trim());
    };
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
      syncMethod();
      syncCreateState();
    };

    const inspect = async () => {
      const candidate = source.control.files?.[0];
      if (!candidate) return;
      const previousFile = state.sourceFile;
      const previousInspection = state.inspection;
      create.disabled = true;
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
        syncCreateState();
        return;
      }
      state.sourceFile = candidate;
      state.inspection = result.data;
      bookTitle.control.value = result.data.title || candidate.name.replace(/\.[^.]+$/, '');
      author.control.value = result.data.author || '';
      sourceLanguage.control.value = result.data.language || sourceLanguage.control.value;
      renderSourcePreview(candidate, result.data);
      sourceStatus.textContent = `${candidate.name} is valid and ready.`;
      syncCreateState();
      (author.control.value ? bookTitle.control : author.control).focus();
    };

    const submit = async (event) => {
      event.preventDefault();
      if (!state.sourceFile) {
        sourceStatus.textContent = 'Inspect a valid source before creating the project.';
        source.control.focus();
        return;
      }
      if (choiceValue(form, 'generation_method') === 'import_existing_script'
        && !state.sourceFile.name.toLowerCase().endsWith('.json')) {
        sourceStatus.textContent = 'Import existing Script requires an Alexandria Script JSON source.';
        source.control.focus();
        return;
      }
      create.disabled = true;
      submitStatus.textContent = 'Creating and activating the project…';
      const formData = new FormData();
      formData.append('project_name', bookTitle.control.value.trim());
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
      if (!result.ok) {
        submitStatus.textContent = result.error || 'The project could not be created.';
        syncCreateState();
        return;
      }
      const activation = result.data?.activation || {};
      const current = activation.state === 'current'
        || result.data?.activation_state === 'current';
      if (!current) {
        submitStatus.textContent = 'The project was created but could not become current. It remains safe in Projects.';
        syncCreateState();
        return;
      }
      const project = result.data?.project;
      const destination = activation.native_destination || 'script';
      close(false);
      onCreated(project, destination);
    };

    const onMethodChange = () => {
      syncMethod();
      syncCreateState();
    };
    const onRequiredInput = () => syncCreateState();
    source.control.addEventListener('change', inspect);
    form.querySelectorAll('input[name="generation_method"]').forEach((control) => {
      control.addEventListener('change', onMethodChange);
    });
    [bookTitle.control, author.control, sourceLanguage.control, outputLanguage.control]
      .forEach((control) => control.addEventListener('input', onRequiredInput));
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
    syncMethod();
    syncCreateState();
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
