'use strict';

const fs = require('fs');
const path = require('path');

function sourceCheck(name, ok, observed) {
  return { name, ok: Boolean(ok), observed };
}

function inspectSources(repoRoot) {
  const pages = path.join(repoRoot, 'app/static/pages');
  const specialists = path.join(repoRoot, 'app/static/specialists');
  const personaPath = path.join(repoRoot, 'app/static/components/persona_visual.js');
  const stylePath = path.join(repoRoot, 'app/static/styles/pages/cast.css');
  const pageModules = [
    'cast.js', 'cast_discovery.js', 'cast_header.js', 'cast_voice_library.js',
    'cast_page_view.js', 'cast_model.js', 'cast_roster.js',
    'cast_profile.js', 'cast_profile_sections.js', 'cast_profile_media_sections.js', 'cast_media_card.js',
    'cast_profile_voice_section.js', 'cast_voice_assignment_form.js', 'cast_voice_audition.js',
    'cast_voice_summary.js', 'cast_voice_save.js',
    'cast_controlled_clone.js', 'cast_workflows.js',
  ].map((name) => path.join(pages, name));
  const specialistModules = [
    'advanced_character_operations.js', 'advanced_identity_action.js',
    'advanced_identity_directory.js', 'advanced_identity_mutation.js',
    'advanced_line_corrections.js', 'advanced_operation_history.js',
    'advanced_speaker_recovery.js',
    'full_cast_dossier_review.js', 'full_cast_import_result.js',
    'full_cast_task_exports.js', 'full_cast_tasks.js', 'roster_import_review.js',
  ].map((name) => path.join(specialists, name));
  const castModules = [...pageModules, ...specialistModules].filter((file) => fs.existsSync(file));
  const cast = castModules.map((file) => fs.readFileSync(file, 'utf8')).join('\n');
  const route = fs.readFileSync(path.join(pages, 'cast.js'), 'utf8');
  const persona = fs.readFileSync(personaPath, 'utf8');
  const style = fs.readFileSync(stylePath, 'utf8');
  const profileSource = fs.readFileSync(path.join(pages, 'cast_profile_sections.js'), 'utf8');
  const profileReturn = profileSource.slice(profileSource.lastIndexOf('return Object.freeze'));
  const markers = ['voice: voiceSection.voice', 'reference,', 'preview,', 'character,', 'appearance,', 'advanced,']
    .map((name) => profileReturn.indexOf(name));
  const checks = [
    sourceCheck('one_roster_one_profile',
      (cast.match(/dataset\.castRoster\s*=/g) || []).length === 1
        && (cast.match(/dataset\.castProfile\s*=/g) || []).length === 1,
      'one roster marker and one profile marker'),
    sourceCheck('profile_order',
      markers.every((value) => value >= 0)
        && markers.every((value, index) => !index || value > markers[index - 1]),
      markers),
    sourceCheck('shell_lifecycle',
      /export async function mount/.test(route)
        && /AbortController/.test(route)
        && /return cleanup/.test(route),
      'mount, abort, cleanup'),
    sourceCheck('real_api_paths',
      ['/api/cast', '/api/cast/characters/', '/api/save_voice_config',
        '/api/voice-library', '/api/voice-library/assign']
        .every((endpoint) => cast.includes(endpoint)),
      'cast detail, catalog, assignment, and advanced save paths'),
    sourceCheck('controlled_clone_gate',
      ['/api/clone_voices/controlled_preview', '/api/clone_voices/controlled_preview/confirm',
        'controlled_clone_approval_token', 'Generate comparison', 'Use instruction control']
        .every((term) => cast.includes(term)),
      'preview, listen confirmation, and approval-bound save'),
    sourceCheck('loading_empty_error_retry',
      ['loading', 'empty', 'error', 'Retry'].every((term) => cast.includes(term)),
      'four route states'),
    sourceCheck('dirty_save_retry',
      ['dirty', 'Save changes', 'saving', 'Retry save'].every((term) => cast.includes(term)),
      'dirty/save/error states'),
    sourceCheck('catalog_voice_picker',
      ['Existing saved Voice', 'Preview selected Voice', 'Preview Voice + delivery range',
        'baseline, happy, sad, and angry', 'built-in-range-preview', 'Choose a production mode',
        'data-cast-voice-choice'].every((term) => cast.includes(term))
        && !cast.includes('Choose or name a production Voice'),
      'named catalog selection replaces free-text assignment'),
    sourceCheck('keyboard_listbox',
      /setAttribute\(['"]role['"], ['"]listbox['"]\)/.test(cast)
        && ['aria-selected', 'ArrowDown', 'Home', 'End'].every((term) => cast.includes(term)),
      'ARIA listbox keyboard model'),
    sourceCheck('safe_dom',
      cast.includes('textContent') && persona.includes('textContent')
        && !cast.includes('innerHTML') && !persona.includes('innerHTML'),
      'text-only DOM construction'),
    sourceCheck('return_context',
      ['source:', 'return:', 'shell.routes.routeForPath'].every((term) => cast.includes(term)),
      'specialist route context'),
    sourceCheck('identity_review_action',
      ['next_useful_action', 'Review identity', 'data-cast-review-identity']
        .every((term) => cast.includes(term)),
      'identity warning has a direct, contextual review action'),
    sourceCheck('persona_states',
      ['disabled', 'idle', 'running', 'error', 'completed']
        .every((term) => persona.includes(`"${term}"`)),
      'exclusive Persona states'),
    sourceCheck('persona_opt_in',
      persona.includes('enabled: true') && persona.includes('Collect appearance details'),
      'explicit opt-in before discovery'),
    sourceCheck('persona_cleanup',
      persona.includes('clearTimeout') && persona.includes('signal.addEventListener'),
      'poll timer and abort cleanup'),
    sourceCheck('no_legacy_workspace',
      !cast.includes('character-workspace') && !cast.includes('character-visual-panel')
        && !persona.includes('character-visual-list'),
      'no legacy or global visual workspace'),
    sourceCheck('token_style',
      style.includes('var(--master-wide)') && style.includes('var(--master-compact)')
        && !/#[0-9a-f]{3,8}/i.test(style),
      'design tokens only'),
  ];
  return {
    status: checks.every((check) => check.ok) ? 'PASS' : 'RED',
    mode: 'source',
    checks: Object.fromEntries(checks.map((check) => [check.name, check])),
  };
}

module.exports = { inspectSources };
