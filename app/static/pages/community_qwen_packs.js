'use strict';

import {
  DEFAULT_PREVIEW_TEXT, OTHER_FORMATS, createPackFileInput, factList,
  formatSupport, loadInstalledPacks, node, setLiveStatus,
} from './community_qwen_pack_components.js';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);
const PACK_STATES = Object.freeze(['review_required', 'approved', 'mlx_conversion_required']);

export function createCommunityQwenPackController({
  api, signal, shell, onLibraryChanged,
}) {
  let dialog = null;
  let content = null;
  let status = null;
  let packList = null;
  let fileInput = null;
  let selectedFile = null;
  let disposed = false;

  const setStatus = (state, message) => setLiveStatus(status, state, message, STATES);

  const renderReview = (pack) => {
    const review = document.createElement('section');
    review.className = 'community-pack-section community-pack-review';
    review.dataset.packId = pack.pack_id;
    const approved = pack.state === PACK_STATES[1];
    review.append(
      node('h3', 'section-title', pack.name || 'Imported Qwen Voice'),
      UI.status({
        tone: approved ? 'success' : 'warning',
        label: approved ? 'Approved' : 'Review required',
      }),
      factList([
        ['Format', pack.family === 'qvoice_graft' ? '.qvoice graft' : (pack.family || 'Unknown')],
        ['Language', pack.language || 'Not supplied'],
        ['Prompt', pack.prompt_mode === 'xvector' ? 'Stable x-vector identity' : (pack.prompt_mode || 'Unknown')],
        ['License metadata', `${pack.license_name || 'Not supplied'} — informational only`],
      ]),
    );
    const description = UI.field({
      kind: 'textarea', label: 'Persistent voice description',
      value: pack.persistent_description || '',
      description: 'Keeps this identity consistent while each Script line supplies its own emotion.',
      attributes: {
        rows: 3,
        required: true,
        placeholder: 'Describe age, accent, texture, rhythm, and range',
        'data-community-pack-description': '',
      },
    });
    const direction = UI.field({
      label: 'Preview direction',
      value: pack.preview_direction || 'Follow the line’s intended emotion clearly.',
      attributes: { 'data-community-pack-direction': '' },
    });
    const sample = UI.field({
      label: 'Preview text', value: DEFAULT_PREVIEW_TEXT,
      attributes: { 'data-community-pack-text': '' },
    });
    const seed = UI.field({
      label: 'Generation seed', type: 'number', value: '104729',
      attributes: { min: 0, max: 2147483647, 'data-community-pack-seed': '' },
    });
    const actions = document.createElement('div');
    actions.className = 'community-pack-actions';
    const approve = UI.button({
      label: 'Approve for Cast', variant: 'primary',
      disabled: approved || !pack.preview_fingerprint,
      attributes: { 'data-community-pack-approve': '' },
    });
    const generate = UI.button({
      label: 'Generate preview', variant: 'secondary',
      attributes: { 'data-community-pack-generate': '' },
    });
    generate.addEventListener('click', async () => {
      generate.disabled = true;
      setStatus('loading', 'Generating the review preview…');
      const result = await api.post(
        `/api/community-qwen-packs/${encodeURIComponent(pack.pack_id)}/preview`,
        {
          text: sample.querySelector('input').value.trim(),
          persistent_description: description.querySelector('textarea').value.trim(),
          direction: direction.querySelector('input').value.trim(),
          generation_seed: Number(seed.querySelector('input').value) || 0,
        },
        { signal },
      );
      generate.disabled = false;
      if (!result.ok) {
        setStatus('error', result.error || 'The preview could not be generated.');
        return;
      }
      shell.player.set({
        state: 'playing', src: result.data.audio_url, position: 0,
        title: pack.name || 'Community Qwen Voice', subtitle: 'Review preview',
      });
      // Generating a new preview revokes any prior listening approval. Re-render
      // from the server record so Cast availability and the visible badge agree.
      renderReview(result.data);
      setStatus('success', 'Preview ready. Listen before approving it for Cast.');
    });
    approve.addEventListener('click', async () => {
      approve.disabled = true;
      setStatus('loading', 'Saving listening approval…');
      const fingerprint = approve.dataset.previewFingerprint
        || pack.preview_fingerprint;
      const result = await api.post(
        `/api/community-qwen-packs/${encodeURIComponent(pack.pack_id)}/approve`,
        { expected_preview_fingerprint: fingerprint }, { signal },
      );
      if (!result.ok) {
        approve.disabled = false;
        setStatus('error', result.error || 'Approval could not be saved.');
        return;
      }
      setStatus(
        'success',
        'Approved. This Voice appears automatically in Cast under Existing saved Voice.',
      );
      await onLibraryChanged?.();
      await loadPacks();
    });
    actions.append(generate, approve);
    review.append(
      description,
      direction,
      sample,
      seed,
      actions,
      node(
        'p',
        'metadata',
        approved
          ? 'This approved Voice appears automatically in Cast under Existing saved Voice.'
          : 'After approval, this Voice appears automatically in Cast under Existing saved Voice.',
      ),
    );
    content.querySelector('[data-community-pack-review]')?.replaceChildren(review);
  };

  const renderInspection = (inspection) => {
    const region = content.querySelector('[data-community-pack-review]');
    const section = document.createElement('section');
    section.className = 'community-pack-section';
    const runnable = inspection.state === 'ready_for_review';
    section.append(
      node('h3', 'section-title', inspection.name || selectedFile.name),
      UI.status({
        tone: runnable ? 'success' : 'warning',
        label: runnable ? 'Compatible .qvoice' : 'MLX conversion required',
      }),
      factList([
        ['Prompt mode', inspection.prompt_mode || 'Unknown'],
        ['Sections', (inspection.sections || []).join(', ') || 'META'],
        ['License metadata', `${inspection.license_name || 'Not supplied'} — informational only`],
      ]),
    );
    if (!runnable) {
      section.append(node('p', 'metadata', inspection.message));
      region.replaceChildren(section);
      return;
    }
    const install = UI.button({
      label: 'Install for review', variant: 'primary',
      attributes: { 'data-community-pack-import': '' },
    });
    install.addEventListener('click', async () => {
      install.disabled = true;
      setStatus('loading', 'Installing the verified pack…');
      const body = new FormData();
      body.append('file', selectedFile);
      const result = await api.post('/api/community-qwen-packs/import', body, { signal });
      if (!result.ok) {
        install.disabled = false;
        setStatus('error', result.error || 'The pack could not be installed.');
        return;
      }
      setStatus('success', 'Installed. Generate and listen to a preview next.');
      renderReview(result.data);
      await loadPacks();
    });
    section.append(install);
    region.replaceChildren(section);
  };

  async function inspectFile() {
    selectedFile = fileInput.files?.[0] || null;
    if (!selectedFile) return;
    setStatus('loading', `Inspecting ${selectedFile.name}…`);
    const body = new FormData();
    body.append('file', selectedFile);
    const result = await api.post('/api/community-qwen-packs/inspect', body, { signal });
    if (!result.ok) {
      setStatus('error', result.error || 'This file is not a compatible .qvoice pack.');
      return;
    }
    setStatus(
      result.data.state === 'ready_for_review' ? 'success' : 'empty',
      result.data.state === 'ready_for_review'
        ? 'Compatible pack. Nothing has been installed yet.'
        : result.data.message,
    );
    renderInspection(result.data);
  }

  async function loadPacks() {
    await loadInstalledPacks({
      api, signal, packList, onSelect: renderReview, retry: loadPacks,
    });
  }

  const build = () => {
    content = document.createElement('div');
    content.className = 'community-pack-manager';
    const importSection = document.createElement('section');
    importSection.className = 'community-pack-section';
    importSection.append(
      node('h3', 'entity-title', 'Import a community Voice'),
      node('p', 'flat-section__body', '.qvoice grafts can retain a stable identity while Qwen applies each line direction.'),
    );
    fileInput = createPackFileInput(inspectFile);
    const choose = UI.button({
      label: 'Choose .qvoice', variant: 'secondary',
      onClick: () => fileInput.click(),
      attributes: { 'data-community-pack-choose': '' },
    });
    status = node('div', 'community-pack-status metadata', 'Choose a pack to inspect.');
    status.dataset.state = 'empty';
    importSection.append(fileInput, choose, status);
    packList = document.createElement('div');
    packList.className = 'community-pack-list';
    packList.dataset.state = 'loading';
    const installed = document.createElement('section');
    installed.className = 'community-pack-section';
    installed.append(node('h3', 'entity-title', 'Installed community Voices'), packList);
    const reviewRegion = document.createElement('div');
    reviewRegion.dataset.communityPackReview = '';
    content.append(importSection, installed, reviewRegion, formatSupport(OTHER_FORMATS));
    dialog = UI.dialog({
      kind: 'drawer', title: 'Community Qwen packs',
      body: 'Inspect, install, audition, and approve an imported Voice.',
      content, confirmLabel: 'Done',
    });
  };

  return Object.freeze({
    open(opener) {
      if (disposed || signal.aborted) return;
      if (!dialog) build();
      dialog.open(opener);
      loadPacks();
    },
    cleanup() {
      disposed = true;
      dialog?.forceClose('cleanup');
    },
  });
}
