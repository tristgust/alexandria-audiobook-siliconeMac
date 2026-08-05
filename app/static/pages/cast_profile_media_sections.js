'use strict';

import { castSection, castText, castVoiceMethod } from './cast_model.js';
import { createCastMediaCard } from './cast_media_card.js';

const UI = globalThis.AlexandriaUI;

export function createCastProfileMediaSections({
  api, signal, shell, getSelected, getVoiceLibrary, onOpenWorkflow,
  fieldControl, sectionHeading, getEditingPreview,
}) {
  const mediaCard = createCastMediaCard({ shell });
  function reference({ editing }) {
    const selected = getSelected();
    const cloneMethod = [
      'clone', 'supplied_recording_clone', 'controlled_clone', 'instruction_controlled_clone',
    ].includes(castVoiceMethod(selected));
    const clone = selected.voice?.clone || {};
    const ready = clone.reference_audio_state === 'ready';
    const source = clone.reference_source
      || (ready ? `${selected.display_name} reference clip` : 'No recording selected');
    const audio = mediaCard({
      src: ready ? clone.reference_audio_url : null,
      label: 'Reference clip', source,
      playerTitle: `${selected.display_name} · Reference`,
      playerSubtitle: 'Clone identity reference', dataKey: 'castReferencePlay',
      unavailableCopy: ready ? 'Playback unavailable' : 'Add a recording in Edit Voice',
    });
    audio.dataset.referenceAudio = String(ready);
    const transcriptCard = document.createElement('div');
    transcriptCard.className = 'cast-profile__transcript-card';
    transcriptCard.dataset.referenceTranscript = 'exact';
    const transcriptText = clone.exact_reference_transcript || '';
    const transcriptHeader = document.createElement('header');
    const transcriptHeading = document.createElement('div');
    transcriptHeading.className = 'cast-profile__transcript-heading';
    const transcriptMark = document.createElement('span');
    transcriptMark.className = 'cast-profile__transcript-mark';
    transcriptMark.setAttribute('aria-hidden', 'true');
    transcriptMark.append(UI.icon('document'));
    transcriptHeading.append(transcriptMark, castText('span', 'utility-heading', 'Exact transcript'));
    transcriptHeader.append(
      transcriptHeading,
      castText('span', 'timecode', transcriptText
        ? `${transcriptText.trim().split(/\s+/).length.toLocaleString()} words` : 'Not recorded'),
    );
    transcriptCard.append(transcriptHeader);
    if (editing) {
      const field = fieldControl({
        id: 'cast-reference-transcript', label: 'Exact reference transcript', kind: 'textarea',
        value: transcriptText,
        placeholder: 'Enter the exact words spoken in the reference recording',
        message: 'This must match the reference recording word for word.',
      });
      field.control.dataset.castReferenceTranscript = '';
      transcriptCard.append(field.wrapper);
    } else {
      transcriptCard.append(castText(
        'blockquote', 'cast-profile__reference-transcript', transcriptText,
        ready ? 'Exact transcript has not been recorded.' : 'No exact transcript required.',
      ));
    }
    const grid = document.createElement('div');
    grid.className = 'cast-profile__reference-grid';
    grid.append(audio, transcriptCard);
    if (!ready && editing) {
      const prepare = UI.button({ label: 'Prepare reference audio', variant: 'secondary', size: 'compact' });
      prepare.addEventListener('click', () => onOpenWorkflow('audio-preparer', prepare));
      grid.append(UI.notice({
        tone: 'warning', title: 'Reference audio missing',
        body: 'Add or prepare a recording before using a cloned production Voice.', action: prepare,
      }));
    }
    const content = document.createElement('div');
    content.append(
      sectionHeading({ eyebrow: 'Clone identity', title: 'Reference audio and exact transcript' }),
      grid,
    );
    const section = castSection('reference', '', content);
    section.hidden = !cloneMethod;
    return section;
  }

  function preview() {
    const editingPreview = getEditingPreview();
    if (editingPreview) {
      const content = document.createElement('div');
      const audition = document.createElement('div');
      audition.className = 'cast-profile__preview-editor';
      audition.append(
        editingPreview.studio,
      );
      content.append(sectionHeading({ eyebrow: 'Listening check', title: 'Preview' }), audition);
      return castSection('preview', '', content);
    }
    const selected = getSelected();
    const voiceValue = selected.voice || {};
    const soundEffectMethod = [
      'sound_effect', 'sound_effects', 'sfx', 'non_speech',
    ].includes(castVoiceMethod(selected));
    if (soundEffectMethod) {
      const content = document.createElement('div');
      const definition = document.createElement('div');
      definition.className = 'cast-profile__sound-effect-summary';
      definition.append(
        castText('span', 'utility-heading', 'Persistent sound definition'),
        castText(
          'p',
          '',
          voiceValue.sound_effect?.definition,
          'No sound definition has been saved.',
        ),
      );
      const backend = voiceValue.sound_effect?.backend_status || {};
      const notice = UI.notice({
        tone: backend.available ? 'success' : 'warning',
        title: backend.available
          ? 'Sound-effect backend available'
          : 'Sound-effect backend not installed',
        body: backend.message
          || 'Alexandria will not send this non-speech role through text-to-speech.',
      });
      content.append(
        sectionHeading({ eyebrow: 'Non-speech production', title: 'Sound effect' }),
        definition,
        notice,
      );
      return castSection('preview', '', content);
    }
    const previewState = voiceValue.preview || {};
    const library = getVoiceLibrary?.() || { voices: [] };
    const libraryVoice = (library.voices || []).find((item) => (
      (voiceValue.library_voice_id && item.voice_id === voiceValue.library_voice_id)
      || (item.method === 'built_in' && voiceValue.selected_voice
        && [item.key, item.name].includes(voiceValue.selected_voice))
    ));
    const approved = previewState.approved
      || previewState.status === 'approved' || previewState.status === 'ready';
    const previewUrl = previewState.audio_url || previewState.url || previewState.playback_url || '';
    const savedAuditionUrl = libraryVoice?.preview?.available === true
      ? libraryVoice.preview.url || '' : '';
    const playableUrl = previewUrl || savedAuditionUrl;
    const canGenerateBuiltIn = ['custom', 'builtin', 'built_in', 'standard', 'saved_voice']
      .includes(voiceValue.selected_production_method)
      && Boolean(voiceValue.selected_voice && voiceValue.persistent_voice_description?.trim());
    const canGenerateExisting = [
      'clone', 'supplied_recording_clone', 'controlled_clone', 'instruction_controlled_clone',
      'alias',
    ].includes(voiceValue.selected_production_method)
      && Boolean(voiceValue.configuration_key || voiceValue.library_voice_id);
    const source = approved
      ? previewUrl ? 'Approved production preview' : 'Approved preview audio is not attached'
      : savedAuditionUrl ? `${libraryVoice.name} saved audition · not yet approved`
        : canGenerateBuiltIn || canGenerateExisting
          ? 'No listening-check audio yet · generate an audition here'
          : previewState.status === 'failed' ? 'Generation failed' : 'Generate and review this Voice before production';
    let media = mediaCard({
      src: playableUrl || null,
      label: approved && previewUrl ? 'Approved preview' : 'Voice audition',
      source, playerTitle: `${selected.display_name} · Voice preview`,
      playerSubtitle: 'Cast listening check', dataKey: 'castPreviewPlay', unavailableCopy: source,
    });
    media.classList.add('cast-profile__preview-media');
    if (canGenerateBuiltIn || canGenerateExisting) {
      const openDesigner = UI.button({
        label: canGenerateExisting ? 'Generate Existing Voice audition' : 'Generate audition',
        variant: 'quiet', size: 'compact',
      });
      openDesigner.classList.add('cast-profile__preview-action');
      const feedback = castText('p', 'metadata cast-profile__preview-feedback', '');
      feedback.setAttribute('role', 'status');
      feedback.setAttribute('aria-live', 'polite');
      feedback.hidden = true;
      openDesigner.addEventListener('click', async () => {
        openDesigner.disabled = true;
        openDesigner.textContent = 'Generating audition…';
        feedback.hidden = false;
        feedback.textContent = canGenerateExisting
          ? 'Generating baseline, happy, sad, and angry from the exact saved Voice and this character’s adjustments…'
          : 'Generating baseline, happy, sad, and angry with the saved persistent description…';
        const result = canGenerateExisting
          ? await api.post('/api/voice-library/supplied-range-preview', {
            character_id: selected.character_id,
            voice_overlay: voiceValue.voice_overlay || {},
          }, { signal })
          : await api.post('/api/voice-library/built-in-range-preview', {
            voice: voiceValue.selected_voice,
            persistent_description: voiceValue.persistent_voice_description.trim(),
          }, { signal });
        if (signal.aborted) return;
        if (!result.ok || !result.data?.audio_url) {
          openDesigner.disabled = false;
          openDesigner.textContent = 'Retry audition';
          feedback.textContent = typeof result.data?.detail === 'object'
            ? result.data.detail.message || 'The audition could not be generated.'
            : result.error || 'The audition could not be generated.';
          return;
        }
        const generated = mediaCard({
          src: result.data.audio_url, label: 'Voice audition',
          source: 'Generated listening check · not yet approved',
          playerTitle: `${selected.display_name} · Voice audition`,
          playerSubtitle: canGenerateExisting
            ? 'Baseline → Happy → Sad → Angry · exact saved identity retained'
            : 'Baseline → Happy → Sad → Angry · saved persistent description applied',
          dataKey: 'castPreviewPlay', unavailableCopy: '',
        });
        generated.classList.add('cast-profile__preview-media');
        generated.dataset.castGeneratedAudition = '';
        media.replaceWith(generated);
        media = generated;
        const listeningCheck = document.querySelector('[data-cast-listening-check] dd');
        if (listeningCheck) listeningCheck.textContent = 'Audition ready · review before approval';
        shell.player.set({
          state: 'playing', src: result.data.audio_url, position: 0,
          title: `${selected.display_name} · Voice audition`,
          subtitle: canGenerateExisting
            ? 'Baseline → Happy → Sad → Angry · exact saved identity retained'
            : 'Baseline → Happy → Sad → Angry · saved persistent description applied',
        });
      });
      media.append(openDesigner, feedback);
    }
    const content = document.createElement('div');
    content.append(sectionHeading({ eyebrow: 'Listening check', title: 'Preview' }), media);
    return castSection('preview', '', content);
  }

  return Object.freeze({ reference, preview });
}
