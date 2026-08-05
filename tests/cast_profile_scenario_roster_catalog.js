'use strict';

const { press } = require('./cast_profile_browser_helpers.js');

async function runRosterCatalogScenario({ assertions, details, server, session }) {
  details.lineSort = await session.evaluate(`(() => {
    const select=document.querySelector('[data-cast-sort]');
    const selectedBefore=document.querySelector('[role="option"][aria-selected="true"]')?.dataset.characterId;
    select.value='lines_desc';
    select.dispatchEvent(new Event('change',{bubbles:true}));
    const descending=[...document.querySelectorAll('[data-character-id]')]
      .map((row)=>({id:row.dataset.characterId,text:row.textContent}));
    select.value='lines_asc';
    select.dispatchEvent(new Event('change',{bubbles:true}));
    const ascending=[...document.querySelectorAll('[data-character-id]')]
      .map((row)=>({id:row.dataset.characterId,text:row.textContent}));
    return {
      selectedBefore,
      selectedAfter:document.querySelector('[role="option"][aria-selected="true"]')?.dataset.characterId,
      descending,
      ascending,
    };
  })()`);
  assertions.lineCountSort = details.lineSort.selectedBefore === 'cast:clara'
    && details.lineSort.selectedAfter === 'cast:clara'
    && details.lineSort.descending[0]?.id === 'cast:edmund'
    && /42 lines/.test(details.lineSort.descending[0]?.text || '')
    && details.lineSort.ascending[0]?.id === 'cast:witness'
    && details.lineSort.ascending[1]?.id === 'cast:isobel'
    && /7 lines/.test(details.lineSort.ascending[1]?.text || '');
  await session.evaluate(`{
    const select=document.querySelector('[data-cast-sort]');
    select.value='script_order';
    select.dispatchEvent(new Event('change',{bubbles:true}));
  }`);
  await session.evaluate(`document.querySelector('[data-character-id="cast:clara"]')?.focus()`);
  await session.waitFor(`document.activeElement?.dataset.characterId==='cast:clara'`);
  await press(session, 'ArrowDown');
  await session.waitFor(`document.querySelector('[data-cast-profile] h2')?.textContent==='Edmund Fairfax'`);
  details.keyboardSelected = await session.evaluate(`document.querySelector('[role="option"][aria-selected="true"]')?.dataset.characterId`);
  assertions.keyboardSelection = details.keyboardSelected === 'cast:edmund';
  await session.evaluate(`document.querySelector('[data-cast-edit-voice]').click()`);
  await session.waitFor(`Boolean(document.querySelector('[data-cast-voice-method]'))`);
  details.voiceModeFirst = await session.evaluate(`(() => ({
    catalogVisible: document.querySelector('.cast-profile__voice-catalog')?.hidden === false,
    fieldsVisible: document.querySelector('.cast-profile__field-grid')?.hidden === false,
    methodVisible: document.querySelector('[data-cast-voice-method]')?.offsetParent !== null,
  }))()`);
  assertions.voiceModeFirst = Object.values(details.voiceModeFirst).every(Boolean);
  details.voiceModes = await session.evaluate(`(() => {
    const method=document.querySelector('[data-cast-voice-method]');
    return [...(method?.options || [])].map((option) => ({
      value: option.value,
      label: option.textContent.trim(),
    }));
  })()`);
  assertions.voiceModesAreConsolidated = JSON.stringify(details.voiceModes) === JSON.stringify([
    { value: 'builtin', label: 'Built-in Voice' },
    { value: 'existing', label: 'Existing Voice' },
    { value: 'design', label: 'Designed Voice' },
    { value: 'sound_effect', label: 'Sound effect' },
  ]);
  details.voiceChooser = await session.evaluate(`(() => {
    const select=document.querySelector('[data-cast-voice-choice]');
    return {
      label: select?.closest('.field')?.querySelector('.field__label')?.textContent || '',
      options: [...(select?.options || [])].map((option) => option.textContent),
      freeTextAssignment: document.querySelector('[data-cast-assigned-voice]')?.tagName === 'INPUT',
      directSetup: Boolean(document.querySelector('.cast-profile__voice-setup')),
    };
  })()`);
  assertions.namedVoiceChooser = details.voiceChooser.label === 'Existing Voice'
    && details.voiceChooser.options.some((label) => /Benny \/ Bernice/.test(label))
    && details.voiceChooser.options.some((label) => /^Narrator —/.test(label))
    && details.voiceChooser.options.some((label) => /The Doctor/.test(label))
    && !details.voiceChooser.options.some((label) => /Attention R8 Pilot/.test(label))
    && !details.voiceChooser.freeTextAssignment
    && details.voiceChooser.directSetup;
  assertions.projectVoiceOffersLinkedOrCopy = await session.evaluate(`(() => {
    const select=document.querySelector('[data-cast-voice-choice]');
    const reuse=document.querySelector('[data-cast-existing-reuse-mode]');
    select.value='voice-computer';
    select.dispatchEvent(new Event('change',{bubbles:true}));
    const visible=reuse?.closest('.field')?.hidden === false;
    const options=[...(reuse?.options || [])].map((option)=>option.value);
    reuse.value='independent_copy';
    reuse.dispatchEvent(new Event('change',{bubbles:true}));
    const direction=document.querySelector('[data-cast-voice-overlay-direction]');
    const pitch=document.querySelector('[data-cast-voice-overlay-pitch]');
    const pace=document.querySelector('[data-cast-voice-overlay-pace]');
    const level=document.querySelector('[data-cast-voice-overlay-level]');
    direction.value='higher, clipped, and more synthetic';
    pitch.value='3';
    pace.value='112';
    level.value='-2';
    [direction,pitch,pace,level].forEach((control)=>control.dispatchEvent(new Event('input',{bubbles:true})));
    const selected=reuse.value;
    select.value='voice-benny';
    select.dispatchEvent(new Event('change',{bubbles:true}));
    return visible && options.join(',') === 'linked,independent_copy'
      && selected === 'independent_copy'
      && reuse.closest('.field').hidden === true
      && reuse.value === 'linked';
  })()`);
  await session.evaluate(`(() => {
    const select=document.querySelector('[data-cast-voice-choice]');
    select.value='voice-computer';
    select.dispatchEvent(new Event('change',{bubbles:true}));
    const direction=document.querySelector('[data-cast-voice-overlay-direction]');
    const pitch=document.querySelector('[data-cast-voice-overlay-pitch]');
    const pace=document.querySelector('[data-cast-voice-overlay-pace]');
    const level=document.querySelector('[data-cast-voice-overlay-level]');
    direction.value='higher, clipped, and more synthetic';
    pitch.value='3';
    pace.value='112';
    level.value='-2';
    [direction,pitch,pace,level].forEach((control)=>control.dispatchEvent(new Event('input',{bubbles:true})));
  })()`);
  await session.waitFor(`document.querySelector('[data-cast-preview-choice]')?.textContent.trim()
    === 'Generate Existing Voice audition'`);
  await session.evaluate(`document.querySelector('[data-cast-preview-choice]').click()`);
  await session.waitFor(`document.querySelector('[data-persistent-player]')?.getPlayerState?.().src
    ?.includes('fixture-supplied-range.wav')`);
  details.projectVoiceAuditionRequest = server.control.requests
    .filter((request) => request.path === '/api/voice-library/supplied-range-preview').at(-1)?.body || null;
  assertions.projectVoiceGeneratesAdjustedRangeAudition =
    details.projectVoiceAuditionRequest?.voice_id === 'voice-computer'
    && details.projectVoiceAuditionRequest?.voice_overlay?.direction
      === 'higher, clipped, and more synthetic'
    && details.projectVoiceAuditionRequest?.voice_overlay?.pitch_semitones === 3
    && details.projectVoiceAuditionRequest?.voice_overlay?.pace_percent === 112
    && details.projectVoiceAuditionRequest?.voice_overlay?.level_db === -2;
  assertions.savedVoiceSelectsExistingMode = await session.evaluate(`(() => {
    const select=document.querySelector('[data-cast-voice-choice]');
    const method=document.querySelector('[data-cast-voice-method]');
    select.value='voice-benny';
    select.dispatchEvent(new Event('change',{bubbles:true}));
    return method.value === 'existing'
      && document.querySelector('.cast-profile__voice-catalog')?.hidden === false;
  })()`);
  assertions.modeChangeClearsHiddenVoice = await session.evaluate(`(() => {
    const select=document.querySelector('[data-cast-voice-choice]');
    const method=document.querySelector('[data-cast-voice-method]');
    select.value='voice-benny';
    select.dispatchEvent(new Event('change',{bubbles:true}));
    method.value='builtin';
    method.dispatchEvent(new Event('change',{bubbles:true}));
    const cleared=select.value === '';
    method.value='existing';
    method.dispatchEvent(new Event('change',{bubbles:true}));
    return cleared;
  })()`);
  await session.evaluate(`(() => {
    const select=document.querySelector('[data-cast-voice-choice]');
    select.value='voice-benny';
    select.dispatchEvent(new Event('change',{bubbles:true}));
  })()`);
  await session.waitFor(`document.querySelector('[data-cast-voice-picker-summary]')?.textContent.includes('Benny / Bernice')`);
  await session.waitFor(`document.querySelector('[data-cast-preview-choice]')?.disabled === false`);
  assertions.existingVoiceChangeInvalidatesPriorAudition = await session.evaluate(`
    document.querySelector('[data-cast-preview-choice]')?.textContent.trim()
      === 'Generate Existing Voice audition'
  `);
  await session.evaluate(`document.querySelector('[data-cast-preview-choice]').click()`);
  await session.waitFor(`document.querySelector('[data-persistent-player]')?.getPlayerState?.().src?.includes('fixture-supplied-range.wav')`);
  assertions.catalogPreview = await session.evaluate(`
    document.querySelector('[data-persistent-player]')?.textContent.includes('Existing Voice delivery range')
      || /fixture-supplied-range\\.wav/.test(document.querySelector('[data-persistent-player]')?.getPlayerState?.().src || '')
  `);
  await session.evaluate(`document.querySelector('[data-cast-save]').click()`);
  await session.waitFor(`document.querySelector('[data-cast-profile]')?.textContent.includes('Benny / Bernice')
    && !document.querySelector('[data-cast-voice-choice]')`);
  details.catalogAssignmentRequest = server.control.requests
    .filter((request) => request.path === '/api/voice-library/assign').at(-1)?.body || null;
  assertions.catalogAssignment = server.control.voiceAssignments === 1
    && details.catalogAssignmentRequest?.character_id === 'cast:edmund'
    && details.catalogAssignmentRequest?.voice_id === 'voice-benny'
    && details.catalogAssignmentRequest?.reuse_mode === 'linked'
    && details.catalogAssignmentRequest?.voice_overlay?.direction
      === 'higher, clipped, and more synthetic'
    && details.catalogAssignmentRequest?.voice_overlay?.pitch_semitones === 3
    && details.catalogAssignmentRequest?.voice_overlay?.pace_percent === 112
    && details.catalogAssignmentRequest?.voice_overlay?.level_db === -2;
  assertions.catalogAssignmentStayedInCast = await session.evaluate(`
    document.body.dataset.routePath === 'cast'
    && document.querySelector('[data-cast-profile] h2')?.textContent === 'Edmund Fairfax'
  `);
  await session.evaluate(`document.querySelector('[data-character-id="cast:clara"]').click()`);
  await session.waitFor(`document.querySelector('[data-cast-profile] h2')?.textContent==='Clara Leighton'`);
}

module.exports = { runRosterCatalogScenario };
