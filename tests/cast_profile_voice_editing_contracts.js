'use strict';

async function assertVoiceEditingContracts({ assertions, session }) {
  assertions.designedVoiceRollbackSafety = await session.evaluate(`(async () => {
    const { castShouldRollbackDesignedVoice } = await import('/static/pages/cast_voice_save.js');
    return castShouldRollbackDesignedVoice({ kind: 'http', status: 500 })
      && !castShouldRollbackDesignedVoice({ kind: 'timeout', status: 0 })
      && !castShouldRollbackDesignedVoice({ kind: 'canceled', status: 0 })
      && !castShouldRollbackDesignedVoice({ kind: 'network', status: 0 })
      && !castShouldRollbackDesignedVoice({ kind: 'decode', status: 200 });
  })()`);
  assertions.auditionDecoratedIdentityRejected = await session.evaluate(`(async () => {
    const { castAuditionText } = await import('/static/pages/cast_model.js');
    const canonicalName = 'The Woman Standing Beside The Window';
    return castAuditionText({
      display_name: canonicalName,
      character: { expanded: { representative_script_lines: [
        canonicalName + '.)',
        'At dawn, the telegram arrived.',
      ] } },
    }) === 'At dawn, the telegram arrived.';
  })()`);
  assertions.auditionSentenceSelection = await session.evaluate(`(async () => {
    const { castAuditionText } = await import('/static/pages/cast_model.js');
    const longName = 'The Woman Standing Beside The Window';
    const selected = {
      display_name: longName,
      character: { expanded: { representative_script_lines: [
        longName + '.',
        'THE BEGINNING OF THE LAST ADVENTURE.',
        'We waited by the station under a sky that turned slowly from silver to black',
        '<p>This polished sentence must never become audition content.</p>.',
        'At dawn, the telegram arrived.',
      ] } },
    };
    const importedNameSources = [
      { identity: { aliases: [longName] } },
      { identity: { script_voice_label: longName } },
      { script_connection: { resolved_script_voice_label: longName } },
    ];
    const importedNamesRejected = importedNameSources.every((source) => castAuditionText({
      display_name: 'Canonical Woman',
      ...source,
      character: { expanded: { representative_script_lines: [
        longName + '.', 'At dawn, the telegram arrived.',
      ] } },
    }) === 'At dawn, the telegram arrived.');
    return importedNamesRejected
      && castAuditionText(selected) === 'At dawn, the telegram arrived.'
      && castAuditionText({ display_name: longName, character: { expanded: {
        representative_script_lines: [longName + '.', 'Chapter One', 'A fragment without an ending'],
      } } }) === 'I thought I understood the danger, but tonight everything changed, and now we have one chance to make this right.';
  })()`);
}

async function assertDesignedPreviewRace({ assertions, server, session }) {
  const definitionA = 'Race definition A with a measured alto and quiet authority.';
  const definitionB = 'Race definition B with a bright tenor and urgent warmth.';
  server.control.deferNextDesignedPreview = true;
  await session.evaluate(`{
    const description=document.querySelector('[data-cast-voice-description]');
    description.value=${JSON.stringify(definitionA)};
    description.dispatchEvent(new Event('input',{bubbles:true}));
    document.querySelector('[data-cast-preview-choice]').click();
  }`);
  for (let attempt = 0; attempt < 100 && !server.control.designedPreviewPending.length; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  if (!server.control.designedPreviewPending.length) {
    throw new Error('Deferred Designed Voice audition A did not reach the fixture server.');
  }
  await session.evaluate(`{
    const description=document.querySelector('[data-cast-voice-description]');
    description.value=${JSON.stringify(definitionB)};
    description.dispatchEvent(new Event('input',{bubbles:true}));
    document.querySelector('[data-cast-preview-choice]').click();
  }`);
  await session.waitFor(`document.querySelector('[data-cast-designed-preview]')?.value
    === 'fixture-designed-b.wav'`);
  server.releaseDesignedPreview();
  await session.evaluate(`new Promise((resolve) => setTimeout(resolve, 100))`);
  assertions.designedVoiceLatePreviewIgnored = await session.evaluate(`
    document.querySelector('[data-cast-designed-preview]')?.value === 'fixture-designed-b.wav'
      && document.querySelector('[data-persistent-player]')?.getPlayerState?.().src
        ?.includes('fixture-designed-b-range.wav')
  `);
}

module.exports = { assertDesignedPreviewRace, assertVoiceEditingContracts };
