'use strict';

const {
  assertDesignedPreviewRace, assertVoiceEditingContracts,
} = require('./cast_profile_voice_editing_contracts');

async function runVoiceEditingScenario({ assertions, details, server, session }) {
  const designedDescription = 'A precise, lightly weathered tenor with a restrained French accent and warmth.';
  const revisedDescription = `${designedDescription} Crisp diction carries through every line.`;
  await assertVoiceEditingContracts({ assertions, session });
  await session.evaluate(`document.querySelector('[data-cast-edit-voice]').click()`);
  await session.waitFor(`Boolean(document.querySelector('[data-cast-voice-method]'))`);
  assertions.cloneEditorUsesReferenceIdentity = await session.evaluate(`
    document.querySelector('[data-cast-assigned-voice]')?.closest('.field')?.hidden === true
    && Boolean(document.querySelector('.cast-profile__editor-reference:not([hidden])'))
    && ![...document.querySelectorAll('[data-cast-voice-method] option')]
      .some((option) => /controlled clone/i.test(option.textContent))
  `);
  await session.evaluate(`{
    const transcript=document.querySelector('[data-cast-reference-transcript]');
    transcript.value=transcript.value+' ';
    transcript.dispatchEvent(new Event('input',{bubbles:true}));
  }`);
  await session.evaluate(`document.querySelector('[data-cast-save]').click()`);
  await session.evaluate(`new Promise(resolve=>setTimeout(resolve,800))`);
  details.cloneSaveState = await session.evaluate(`({
    header: document.querySelector('[data-shell-save]')?.textContent.trim() || '',
    button: document.querySelector('[data-cast-save]')?.textContent.trim() || '',
    editing: document.querySelector('[data-cast-profile]')?.dataset.editing || '',
    alert: document.querySelector('[role="alert"]')?.textContent.trim() || ''
  })`);
  assertions.cloneSaveSucceeded = details.cloneSaveState.header === 'Saved';
  details.cloneSavePayload = server.control.requests
    .filter((request) => request.path === '/api/save_voice_config').at(-1)?.body || null;
  assertions.cloneSaveOmitsDirectAssignment = Object.values(details.cloneSavePayload || {})[0]?.voice === null;
  if (details.cloneSaveState.header === 'Saved') {
    await session.evaluate(`document.querySelector('[data-cast-edit-voice]').click()`);
    await session.waitFor(`Boolean(document.querySelector('[data-cast-voice-method]'))`);
  }
  await session.evaluate(`{
    const method=document.querySelector('[data-cast-voice-method]');
    method.value='design';
    method.dispatchEvent(new Event('change',{bubbles:true}));
  }`);
  assertions.designedVoiceUsesImportedDefinition = await session.evaluate(`
    document.querySelector('[data-cast-voice-description]')?.value
      === 'A clear adult alto with compact resonance, crisp diction, agile sardonic timing, and restrained warmth.'
    && document.querySelector('[data-cast-voice-description]')?.dataset.seededFromImportedDossier === 'true'
  `);
  assertions.designedVoiceHasNoAssignedVoice = await session.evaluate(`
    document.querySelector('[data-cast-assigned-voice]')?.closest('.field')?.hidden === true
    && document.querySelector('.cast-profile__editor-reference')?.hidden === true
    && document.querySelector('[data-cast-voice-description]')?.required === true
    && document.querySelector('[data-cast-voice-description]')?.closest('.field')
      ?.querySelector('.field__label')?.textContent === 'Designed Voice definition'
  `);
  await assertDesignedPreviewRace({ assertions, server, session });
  await session.evaluate(`{
    const description=document.querySelector('[data-cast-voice-description]');
    description.value=${JSON.stringify(designedDescription)};
    description.dispatchEvent(new Event('input',{bubbles:true}));
  }`);
  await session.waitFor(`document.querySelector('[data-cast-preview-choice]')?.disabled === false`);
  await session.evaluate(`document.querySelector('[data-cast-preview-choice]').click()`);
  await session.waitFor(`document.querySelector('[data-persistent-player]')?.getPlayerState?.().src?.includes('fixture-designed-audition-range.wav')`);
  details.designedVoicePreviewRequest = server.control.requests
    .filter((request) => request.path === '/api/voice_design/range-preview').at(-1)?.body || null;
  details.designedVoiceAccentStatusRequest = server.control.requests
    .filter((request) => request.path === '/api/voice_design/accent_status').at(-1)?.body || null;
  assertions.designedVoiceAccentAudition = details.designedVoiceAccentStatusRequest?.description
      === designedDescription
    && details.designedVoiceAccentStatusRequest?.output_language === 'English'
    && await session.evaluate(`document.querySelector('.cast-profile__voice-range-feedback')?.textContent
      .includes('French accent pipeline')`);
  assertions.designedVoiceAudition = details.designedVoicePreviewRequest?.description
      === designedDescription
    && details.designedVoicePreviewRequest?.sample_text
      === 'I knew the letter would arrive before dusk.'
    && Boolean(details.designedVoicePreviewRequest?.persona_context)
    && details.designedVoicePreviewRequest?.force_regenerate === false
    && await session.evaluate(`document.querySelector('[data-cast-designed-preview]')?.value === 'fixture-designed-audition.wav'
      && document.querySelector('.cast-profile__voice-range-feedback')?.textContent.includes('one neutral identity recording')
      && document.querySelectorAll('[data-cast-regenerate-audition-lane]:not([hidden])').length === 3`);
  await session.evaluate(`document.querySelector('[data-cast-regenerate-audition-lane="happy"]').click()`);
  await session.waitFor(`document.querySelector('[data-persistent-player]')?.getPlayerState?.().src?.includes('revision=1')`);
  details.designedVoiceLaneRegenerationRequest = server.control.requests
    .filter((request) => request.path === '/api/voice_design/range-preview/regenerate').at(-1)?.body || null;
  assertions.designedVoiceRegeneratesOneLaneAndReplaysAll =
    details.designedVoiceLaneRegenerationRequest?.lane === 'happy'
    && details.designedVoiceLaneRegenerationRequest?.preview_fingerprint === 'a'.repeat(64)
    && await session.evaluate(`
      document.querySelector('.cast-profile__voice-range-feedback')?.textContent
        .includes('other three lanes unchanged')
      && document.querySelectorAll('[data-cast-regenerate-audition-lane]:not([hidden])').length === 3
    `);
  assertions.designedVoiceOffersFullRegeneration = await session.evaluate(`
    document.querySelector('[data-cast-preview-choice]')?.textContent.trim()
      === 'Regenerate full audition'
  `);
  await session.evaluate(`document.querySelector('[data-cast-preview-choice]').click()`);
  await session.waitFor(`document.querySelector('[data-persistent-player]')?.getPlayerState?.().src?.includes('revision=2')`);
  details.designedVoiceFullRegenerationRequest = server.control.requests
    .filter((request) => request.path === '/api/voice_design/range-preview').at(-1)?.body || null;
  assertions.designedVoiceFullRegenerationRebuildsAll =
    details.designedVoiceFullRegenerationRequest?.force_regenerate === true
    && await session.evaluate(`
      document.querySelector('.cast-profile__voice-range-feedback')?.textContent
        .includes('Full audition regenerated')
      && document.querySelector('[data-cast-preview-choice]')?.textContent.trim()
        === 'Regenerate full audition'
      && document.querySelectorAll('[data-cast-regenerate-audition-lane]:not([hidden])').length === 3
    `);
  await session.evaluate(`document.querySelector('[data-cast-preview-choice]')
    ?.scrollIntoView({ block: 'center' })`);
  await session.screenshot('cast-designed-accent-audition.png');
  await session.evaluate(`{
    const description=document.querySelector('[data-cast-voice-description]');
    description.value=${JSON.stringify(revisedDescription)};
    description.dispatchEvent(new Event('input',{bubbles:true}));
  }`);
  assertions.designedVoiceDefinitionInvalidatesAudition = await session.evaluate(`
    document.querySelector('[data-cast-designed-preview]')?.value === ''
      && document.querySelector('.cast-profile__voice-range-feedback')?.textContent
        .includes('You can save the definition now')
  `);
  const designedArtifactSaveCountBefore = server.control.requests
    .filter((request) => request.path === '/api/voice_design/save').length;
  await session.evaluate(`document.querySelector('[data-cast-save]').click()`);
  await session.waitFor(`document.querySelector('[data-shell-save]')?.textContent.trim()==='Saved'`);
  details.designedVoiceSaveRelease = await session.evaluate(`({
    header: document.querySelector('[data-shell-save]')?.textContent.trim() || '',
    editing: document.querySelector('[data-cast-profile]')?.dataset.editing || '',
    editAvailable: Boolean(document.querySelector('[data-cast-edit-voice]')),
  })`);
  assertions.designedVoiceSavesWithoutAudition = details.designedVoiceSaveRelease.header === 'Saved'
    && details.designedVoiceSaveRelease.editing === 'false'
    && details.designedVoiceSaveRelease.editAvailable;
  await session.screenshot('cast-designed-description-saved.png');
  details.designedVoiceSavePayload = server.control.requests
    .filter((request) => request.path === '/api/save_voice_config').at(-1)?.body || null;
  const designedUpdate = Object.values(details.designedVoiceSavePayload || {})[0] || {};
  assertions.designedVoiceSavesDefinition = designedUpdate.type === 'design'
    && designedUpdate.voice === null
    && designedUpdate.description === revisedDescription
    && designedUpdate.ref_audio === null
    && designedUpdate.ref_text === null
    && designedUpdate.clone_backend === undefined;
  const designedArtifactSaveCountAfter = server.control.requests
    .filter((request) => request.path === '/api/voice_design/save').length;
  assertions.designedVoiceAuditionRemainsTransient = designedArtifactSaveCountAfter
    === designedArtifactSaveCountBefore;
  await session.waitFor(`Boolean(document.querySelector('[data-cast-edit-voice]'))`);
  await session.evaluate(`document.querySelector('[data-cast-edit-voice]').click()`);
  await session.waitFor(`Boolean(document.querySelector('[data-cast-voice-method]'))`);
  assertions.designedVoiceReopensAsDesign = await session.evaluate(`
    document.querySelector('[data-cast-voice-method]')?.value === 'design'
      && document.querySelector('[data-cast-voice-description]')?.value
        === ${JSON.stringify(revisedDescription)}
      && document.querySelector('[data-cast-designed-preview]')?.value === ''
  `);
  await session.evaluate(`{
    const description=document.querySelector('[data-cast-voice-description]');
    description.value=${JSON.stringify(`${revisedDescription} Slightly brighter on questions.`)};
    description.dispatchEvent(new Event('input',{bubbles:true}));
  }`);
  await session.waitFor(`document.querySelector('[data-cast-preview-choice]')?.disabled === false`);
  await session.evaluate(`document.querySelector('[data-cast-preview-choice]').click()`);
  await session.waitFor(`document.querySelector('[data-cast-designed-preview]')?.value === 'fixture-designed-audition.wav'`);
  assertions.designedVoiceOffersExplicitCloneConversion = await session.evaluate(`
    document.querySelector('[data-cast-use-audition-as-clone]')?.hidden === false
      && document.querySelector('[data-cast-use-audition-as-clone]')?.getAttribute('aria-pressed') === 'false'
  `);
  await session.evaluate(`document.querySelector('[data-cast-use-audition-as-clone]').click()`);
  assertions.designedVoiceCloneConversionSelected = await session.evaluate(`
    document.querySelector('[data-cast-use-audition-as-clone]')?.getAttribute('aria-pressed') === 'true'
      && document.querySelector('.cast-profile__voice-range-feedback')?.textContent
        .includes('supplied-recording clone')
  `);
  server.control.mode = 'save-error';
  await session.evaluate(`document.querySelector('[data-cast-save]').click()`);
  await session.waitFor(`document.querySelector('[data-cast-save]')?.textContent.includes('Retry save')`);
  assertions.designedVoiceFailedCloneConversionRollsBack = server.control.designedRollbacks === 1
    && await session.evaluate(`
      document.querySelector('[data-cast-designed-preview]')?.value === 'fixture-designed-audition.wav'
        && document.querySelector('[data-cast-use-audition-as-clone]')?.getAttribute('aria-pressed') === 'true'
        && document.querySelector('[data-cast-voice-method]')?.value === 'design'
    `);
  server.control.mode = 'normal';
  await session.evaluate(`document.querySelector('[data-cast-save]').click()`);
  await session.waitFor(`document.querySelector('[data-shell-save]')?.textContent.trim()==='Saved'`);
  details.designedVoiceCloneSavePayload = server.control.requests
    .filter((request) => request.path === '/api/save_voice_config').at(-1)?.body || null;
  const cloneUpdate = Object.values(details.designedVoiceCloneSavePayload || {})[0] || {};
  details.designedVoiceCloneArtifactPayload = server.control.requests
    .filter((request) => request.path === '/api/voice_design/save').at(-1)?.body || null;
  assertions.designedVoiceExplicitlyConvertsToClone = cloneUpdate.type === 'clone'
    && cloneUpdate.voice === null
    && cloneUpdate.clone_backend === 'qwen3_base'
    && cloneUpdate.fish_hybrid_enabled === true
    && Array.isArray(cloneUpdate.fish_hybrid_styles)
    && cloneUpdate.fish_hybrid_styles.join(',') === 'fear,grief,sarcasm,expressive'
    && cloneUpdate.ref_audio === 'designed_voices/clara-designed-fixture.wav'
    && cloneUpdate.ref_text === 'I knew the letter would arrive before dusk.'
    && details.designedVoiceCloneArtifactPayload?.preview_file === 'fixture-designed-audition.wav'
    && details.designedVoiceCloneArtifactPayload?.scope === 'project';
  await session.evaluate(`document.querySelector('[data-cast-edit-voice]').click()`);
  await session.waitFor(`Boolean(document.querySelector('[data-cast-voice-method]'))`);
  assertions.designedVoiceCloneConversionReopensAsClone = await session.evaluate(`
    document.querySelector('[data-cast-voice-method]')?.value === 'clone'
      && document.querySelector('[data-cast-reference-transcript]')?.value
        === 'I knew the letter would arrive before dusk.'
  `);
  await session.evaluate(`{
    const method=document.querySelector('[data-cast-voice-method]');
    method.value='custom';
    method.dispatchEvent(new Event('change',{bubbles:true}));
  }`);
  await session.waitFor(`document.querySelector('[data-cast-assigned-voice]')?.closest('.field')?.hidden === false`);
  await session.evaluate(`{const n=document.querySelector('[data-cast-assigned-voice]');n.value='Ryan';n.dispatchEvent(new Event('change',{bubbles:true}))}`);
  details.builtInRangePreview = await session.evaluate(`(() => {
    const sequence=document.querySelector('.cast-profile__voice-range');
    const preview=document.querySelector('[data-cast-preview-choice]');
    return {
      visible: sequence?.hidden === false,
      labels: [...(sequence?.querySelectorAll('strong') || [])].map((node)=>node.textContent.trim()),
      button: preview?.textContent.trim() || '',
      enabled: preview?.disabled === false,
    };
  })()`);
  assertions.builtInRangePreviewVisible = details.builtInRangePreview.visible
    && ['Baseline', 'Happy', 'Sad', 'Angry'].every(
      (label) => details.builtInRangePreview.labels.includes(label),
    )
    && details.builtInRangePreview.button === 'Preview Voice + delivery range'
    && details.builtInRangePreview.enabled;
  details.builtInRangeDescription = await session.evaluate(
    `document.querySelector('[data-cast-voice-description]')?.value || ''`,
  );
  await session.evaluate(`document.querySelector('[data-cast-preview-choice]').click()`);
  await session.waitFor(`document.querySelector('[data-persistent-player]')?.getPlayerState?.().src?.includes('fixture-built-in-range.wav')`);
  details.builtInRangeRequest = server.control.requests
    .filter((request) => request.path === '/api/voice-library/built-in-range-preview').at(-1)?.body || null;
  assertions.builtInRangeKeepsDescription = details.builtInRangeRequest?.voice === 'Ryan'
    && details.builtInRangeRequest?.persistent_description
      === details.builtInRangeDescription
    && await session.evaluate(`document.querySelector('.cast-profile__voice-range-feedback')?.textContent.includes('applied throughout')`);
  assertions.dirtyState = await session.evaluate(`document.querySelector('[data-shell-save]')?.textContent.includes('Unsaved')&&Boolean(document.querySelector('[data-cast-save]'))`);
  await session.evaluate(`document.querySelector('[data-character-id="cast:edmund"]').click()`);
  await session.waitFor(`Boolean(document.querySelector('.dialog-layer'))`);
  details.dirtyActions = await session.evaluate(`[...document.querySelectorAll('.dialog-layer button')].map(n=>n.textContent.trim())`);
  assertions.dirtyDialog = ['Save Voice changes', 'Discard changes', 'Cancel'].every((label) => details.dirtyActions.includes(label));
  await session.screenshot('cast-dirty-confirmation.png');
  await session.evaluate(`[...document.querySelectorAll('.dialog-layer button')].find(n=>n.textContent.trim()==='Cancel')?.click()`);
  assertions.cancelRestores = await session.evaluate(`!document.querySelector('.dialog-layer')&&document.activeElement?.dataset.characterId==='cast:edmund'`);
  server.control.mode = 'save-error';
  await session.evaluate(`document.querySelector('[data-cast-save]').click()`);
  await session.waitFor(`document.querySelector('[data-cast-save]')?.textContent.includes('Retry save')`);
  assertions.saveFailureRetains = await session.evaluate(`document.querySelector('[data-cast-assigned-voice]')?.value==='Ryan'`);
  await session.screenshot('cast-save-error.png');
  server.control.mode = 'normal';
  await session.evaluate(`document.querySelector('[data-cast-save]').click()`);
  await session.waitFor(`document.querySelector('[data-shell-save]')?.textContent.trim()==='Saved'`);
  assertions.saved = server.control.savedVoice === 'Ryan';
  await session.evaluate(`document.querySelector('[data-cast-preview-play]').click()`);
  await session.waitFor(`Boolean(document.querySelector('[data-persistent-player] audio.persistent-player__media'))`);
  assertions.previewUsesShellPlayer = await session.evaluate(`(() => {
    const player=document.querySelector('[data-persistent-player]');
    const media=player?.querySelector('audio.persistent-player__media');
    return player?.getPlayerState?.().native === true
      && /fixture-preview\.wav/.test(player?.getPlayerState?.().src || '')
      && !media?.error;
  })()`);
  await session.evaluate(`(() => {
    [...document.querySelectorAll('.disclosure__trigger')]
      .find((node) => node.querySelector('.disclosure__copy strong')?.textContent.trim() === 'Advanced details')?.click();
  })()`);
  await session.waitFor(`Boolean([...document.querySelectorAll('.disclosure__trigger')]
    .find((node) => (node.querySelector('.disclosure__copy strong')?.textContent || node.textContent).trim() === 'Instruction-controlled clone'))`);
  await session.evaluate(`(() => {
    [...document.querySelectorAll('.disclosure__trigger')]
      .find((node) => (node.querySelector('.disclosure__copy strong')?.textContent || node.textContent).trim() === 'Instruction-controlled clone')?.click();
    document.querySelector('[data-controlled-clone-generate]')?.click();
  })()`);
  await session.waitFor(`Boolean(document.querySelector('[data-controlled-clone-audio]'))`);
  await session.evaluate(`(() => {
    const audio = document.querySelector('[data-controlled-clone-audio]');
    audio.dispatchEvent(new Event('play'));
    audio.dispatchEvent(new Event('ended'));
  })()`);
  await session.waitFor(`document.querySelector('[data-controlled-clone-enable]')?.disabled === false`);
  assertions.controlledListenGate = server.control.controlledConfirmations === 1;
  await session.evaluate(`document.querySelector('[data-controlled-clone-enable]').click()`);
  await session.waitFor(`document.querySelector('[data-controlled-clone]')?.textContent.includes('current instruction-controlled configuration is approved')`);
  assertions.controlledSaved = server.control.savedBackend === 'qwen3_instruction_controlled'
    && server.control.controlledSaves === 1;
  assertions.controlledStayedInCast = await session.evaluate(`document.body.dataset.routePath === 'cast'
    && document.querySelector('[data-cast-profile] h2')?.textContent === 'Clara Leighton'`);
  await session.screenshot('cast-controlled-clone-approved.png');
}

module.exports = { runVoiceEditingScenario };
