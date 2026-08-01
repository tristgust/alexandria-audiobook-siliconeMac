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
  let candidateList = null;
  let fileInput = null;
  let directoryField = null;
  let quantizationField = null;
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
        ['Evidence', pack.evidence_status === 'publisher_claimed_unverified' ? 'Publisher claim · Alexandria review required' : 'Imported artifact'],
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
      label: 'Preview text', value: pack.preview_text_default || DEFAULT_PREVIEW_TEXT,
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

  const formatBytes = (value) => {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return 'Unknown';
    const gib = bytes / (1024 ** 3);
    return gib >= 0.1 ? `${gib.toFixed(1)} GiB` : `${(bytes / (1024 ** 2)).toFixed(0)} MiB`;
  };

  const renderCandidates = (candidates) => {
    candidateList.replaceChildren();
    if (!candidates.length) {
      candidateList.dataset.state = 'empty';
      candidateList.append(node('p', 'metadata', 'No curated community candidates are available.'));
      return;
    }
    candidateList.dataset.state = candidates.length > 4 ? 'dense' : 'success';
    candidates.forEach((candidate) => {
      const item = document.createElement('article');
      item.className = 'community-pack-candidate';
      const selectedBits = Number(quantizationField.querySelector('select').value) || 8;
      const estimate = candidate.conversion_estimates?.[String(selectedBits)] || {};
      item.append(
        node('h4', 'entity-title', candidate.name),
        UI.status({
          tone: candidate.installed ? 'success' : estimate.allowed ? 'warning' : 'error',
          label: candidate.installed
            ? `Installed · ${candidate.installed_state || 'review required'}`
            : estimate.allowed
              ? 'Experimental · space available'
              : 'Disk guard active',
        }),
        node('p', 'flat-section__body', candidate.summary),
        factList([
          ['Publisher', 'ScrappyLabs'],
          ['Speaker', candidate.speaker],
          ['License', candidate.license_name],
          ['Source download', formatBytes(candidate.source_size_bytes)],
          [`Estimated ${selectedBits}-bit MLX`, formatBytes(estimate.estimated_output_bytes)],
          ['Free space', formatBytes(candidate.available_free_bytes)],
        ]),
      );
      if (candidate.installed) {
        const review = UI.button({
          label: 'Open review', variant: 'secondary',
          onClick: async () => {
            await loadPacks();
            const row = packList.querySelector(
              `[data-community-pack-row="${CSS.escape(candidate.installed_pack_id)}"]`,
            );
            row?.click();
          },
        });
        item.append(review);
      } else {
        const install = UI.button({
          label: 'Download, convert, and install', variant: 'primary',
          disabled: !estimate.allowed,
          attributes: { 'data-community-candidate-install': candidate.key },
        });
        install.addEventListener('click', async () => {
          install.disabled = true;
          setStatus(
            'loading',
            `Downloading and converting ${candidate.name}. The source cache will be removed after a successful install…`,
          );
          const result = await api.post(
            `/api/community-qwen-packs/catalog/${encodeURIComponent(candidate.key)}/install`,
            {
              q_bits: selectedBits,
              cleanup_downloaded_source: true,
            },
            { signal },
          );
          if (!result.ok) {
            install.disabled = false;
            setStatus('error', result.error || 'The candidate could not be installed.');
            return;
          }
          const reclaimed = result.data.candidate_install?.source_cache_cleanup?.reclaimed_bytes;
          setStatus(
            'success',
            reclaimed
              ? `Installed for listening review. Removed ${formatBytes(reclaimed)} of temporary source cache.`
              : 'Installed for listening review. Generate an audition before approving it for Cast.',
          );
          renderReview(result.data);
          await loadPacks();
          await loadCandidates();
        });
        item.append(install);
      }
      candidateList.append(item);
    });
  };

  const renderInspection = (inspection, source = { kind: 'file' }) => {
    const region = content.querySelector('[data-community-pack-review]');
    const section = document.createElement('section');
    section.className = 'community-pack-section';
    const reviewReady = inspection.state === 'ready_for_review';
    const conversionReady = inspection.state === 'mlx_conversion_available';
    const runnable = reviewReady || conversionReady;
    const plan = inspection.conversion_plan || null;
    const familyLabels = {
      qvoice_graft: '.qvoice graft',
      peft_speaker_bundle: 'PEFT + speaker embedding',
      full_custom_voice_checkpoint: 'Full CustomVoice checkpoint',
    };
    const facts = [
      ['Format', familyLabels[inspection.family] || inspection.family || 'Unknown'],
      ['Runtime', inspection.runtime?.replaceAll('_', ' ') || 'Unknown'],
      ['Speaker', (inspection.speakers || []).join(', ') || 'Not supplied'],
      ['License metadata', `${inspection.license_name || 'Not supplied'} — informational only`],
    ];
    if (inspection.prompt_mode) facts.splice(2, 0, ['Prompt mode', inspection.prompt_mode]);
    if (plan) {
      facts.push(
        ['Estimated MLX output', formatBytes(plan.estimated_output_bytes)],
        ['Free space available', formatBytes(plan.available_free_bytes)],
        ['Safety reserve', formatBytes(plan.reserved_free_bytes)],
      );
    }
    section.append(
      node('h3', 'section-title', inspection.name || selectedFile?.name || 'Qwen Voice'),
      UI.status({
        tone: reviewReady || plan?.allowed ? 'success' : 'warning',
        label: reviewReady
          ? (inspection.family === 'qvoice_graft' ? 'Compatible .qvoice' : 'Ready to link')
          : conversionReady
            ? (plan?.allowed ? 'Conversion space available' : 'Disk guard active')
            : 'Unsupported',
      }),
      factList(facts),
      node('p', 'metadata', inspection.message),
    );
    if (!runnable || (conversionReady && plan && !plan.allowed)) {
      region.replaceChildren(section);
      return;
    }
    const install = UI.button({
      label: conversionReady ? 'Convert and install' : (source.kind === 'directory' ? 'Link for review' : 'Install for review'),
      variant: 'primary',
      attributes: { 'data-community-pack-import': '' },
    });
    install.addEventListener('click', async () => {
      install.disabled = true;
      setStatus('loading', 'Installing the verified pack…');
      const result = source.kind === 'directory'
        ? await api.post('/api/community-qwen-packs/import-directory', {
          source_path: source.path,
          q_bits: Number(quantizationField.querySelector('select').value) || 8,
        }, { signal })
        : await (() => {
          const body = new FormData();
          body.append('file', selectedFile);
          return api.post('/api/community-qwen-packs/import', body, { signal });
        })();
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
    renderInspection(result.data, { kind: 'file' });
  }

  async function inspectDirectory() {
    const sourcePath = directoryField.querySelector('input').value.trim();
    if (!sourcePath) {
      setStatus('error', 'Enter the local folder containing the Qwen files.');
      directoryField.querySelector('input').focus();
      return;
    }
    setStatus('loading', 'Inspecting the local Qwen folder…');
    const result = await api.post('/api/community-qwen-packs/inspect-directory', {
      source_path: sourcePath,
      q_bits: Number(quantizationField.querySelector('select').value) || 8,
    }, { signal });
    if (!result.ok) {
      setStatus('error', result.error || 'This folder is not a supported Qwen Voice.');
      return;
    }
    const plan = result.data.conversion_plan;
    setStatus(
      result.data.state === 'ready_for_review' || plan?.allowed ? 'success' : 'empty',
      result.data.state === 'ready_for_review'
        ? 'Compatible folder. Nothing has been installed yet.'
        : plan?.allowed
          ? 'Conversion fits within the disk-space guard. Nothing has been installed yet.'
          : result.data.message,
    );
    renderInspection(result.data, { kind: 'directory', path: sourcePath });
  }

  async function loadPacks() {
    await loadInstalledPacks({
      api, signal, packList, onSelect: renderReview, retry: loadPacks,
    });
  }

  async function loadCandidates() {
    candidateList.dataset.state = 'loading';
    candidateList.replaceChildren(UI.skeleton({ label: 'Loading community candidates' }));
    const result = await api.get('/api/community-qwen-packs/catalog', { signal });
    if (!result.ok) {
      candidateList.dataset.state = 'error';
      candidateList.replaceChildren(UI.notice({
        tone: 'error',
        title: 'Community candidates could not load',
        body: result.error,
        action: UI.button({ label: 'Retry', onClick: loadCandidates }),
      }));
      return;
    }
    renderCandidates(Array.isArray(result.data?.candidates) ? result.data.candidates : []);
  }

  const build = () => {
    content = document.createElement('div');
    content.className = 'community-pack-manager';
    const importSection = document.createElement('section');
    importSection.className = 'community-pack-section';
    importSection.append(
      node('h3', 'entity-title', 'Import a community Voice'),
      node('p', 'flat-section__body', 'Choose a .qvoice file or inspect an existing PEFT or CustomVoice folder on this Mac.'),
    );
    fileInput = createPackFileInput(inspectFile);
    const choose = UI.button({
      label: 'Choose .qvoice', variant: 'secondary',
      onClick: () => fileInput.click(),
      attributes: { 'data-community-pack-choose': '' },
    });
    directoryField = UI.field({
      label: 'Local Qwen folder',
      description: 'PEFT folders stay in place. Full checkpoints are converted only after the disk-space guard passes.',
      attributes: {
        placeholder: '/path/to/qwen-voice-folder',
        autocomplete: 'off',
        spellcheck: 'false',
        'data-community-pack-directory': '',
      },
    });
    quantizationField = UI.field({
      kind: 'select',
      label: 'Full-checkpoint quantization',
      value: '8',
      options: [
        { value: '8', label: '8-bit — better fidelity' },
        { value: '4', label: '4-bit — smaller output' },
      ],
    });
    quantizationField.querySelector('select').addEventListener('change', () => {
      if (candidateList) loadCandidates();
    });
    const inspectFolder = UI.button({
      label: 'Inspect folder', variant: 'secondary',
      onClick: inspectDirectory,
      attributes: { 'data-community-pack-inspect-directory': '' },
    });
    status = node('div', 'community-pack-status metadata', 'Choose a .qvoice or inspect a local folder.');
    status.dataset.state = 'empty';
    importSection.append(
      fileInput,
      choose,
      node('p', 'metadata', 'or'),
      directoryField,
      quantizationField,
      inspectFolder,
      status,
    );
    candidateList = document.createElement('div');
    candidateList.className = 'community-pack-list community-pack-candidates';
    candidateList.dataset.state = 'loading';
    const candidates = document.createElement('section');
    candidates.className = 'community-pack-section';
    candidates.append(
      node('h3', 'entity-title', 'Experimental public candidates'),
      node(
        'p',
        'flat-section__body',
        'These are pinned public checkpoints, not approved built-ins. Alexandria removes a newly downloaded source checkpoint after conversion and requires a listening review before Cast assignment.',
      ),
      candidateList,
    );
    packList = document.createElement('div');
    packList.className = 'community-pack-list';
    packList.dataset.state = 'loading';
    const installed = document.createElement('section');
    installed.className = 'community-pack-section';
    installed.append(node('h3', 'entity-title', 'Installed community Voices'), packList);
    const reviewRegion = document.createElement('div');
    reviewRegion.dataset.communityPackReview = '';
    content.append(importSection, candidates, installed, reviewRegion, formatSupport(OTHER_FORMATS));
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
      loadCandidates();
    },
    cleanup() {
      disposed = true;
      dialog?.forceClose('cleanup');
    },
  });
}
