'use strict';

import { button, text } from './stable_full_cast_dom.js';

export function renderDirectDossierActivation({
  apiJson, response, resultHost, footerStatus, footerActions,
}) {
  const packageSummary = response.cast_dossier_package || {};
  const activation = packageSummary.activation || {};
  const selected = packageSummary.selected_sections || {};
  const identityReview = packageSummary.visual_identity_review || {};
  const identityIssues = identityReview.issues || [];
  const approvedEntries = identityReview.approved_entries || [];
  const panel = document.createElement('section');
  panel.className = 'stable-complete-cast';
  panel.append(
    text('span', 'Complete Cast dossier validated', 'stable-task-eyebrow'),
    text('h3', 'Import selected dossier sections'),
    text('p', 'The approved Cast is ready. Review any unmatched visual identities, then apply the selected relationships, Voice personas, and visual dossiers to the current project.', 'stable-task-muted'),
  );
  const choices = document.createElement('div');
  choices.className = 'stable-complete-cast__choices';
  const option = (key, label, checked) => {
    const card = document.createElement('label');
    card.className = 'stable-complete-cast__choice';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = checked;
    input.disabled = !checked;
    input.dataset.stableDirectDossierOption = key;
    const copy = document.createElement('span');
    copy.className = 'stable-complete-cast__choice-copy';
    copy.append(text('strong', label), text('span', 'Existing production Voice assignments remain unchanged.', 'stable-task-muted'));
    card.append(input, copy);
    choices.append(card);
    return input;
  };
  const voice = option(
    'voices',
    `Voice personas and Designed Voice definitions (${packageSummary.summary?.voice_dossier_count || 0})`,
    selected.voice_personas_and_designs === true,
  );
  const visual = option(
    'visuals',
    `Visual dossiers (${packageSummary.summary?.visual_dossier_count || 0})`,
    selected.visual_dossiers === true,
  );
  const identityDecisions = document.createElement('section');
  identityDecisions.className = 'stable-visual-identity-review';
  if (visual.checked && identityIssues.length) {
    identityDecisions.append(
      text('h4', 'Match visual identities'),
      text('p', 'Choose an approved Cast identity or keep the dossier identity excluded. Prior roster exclusions remain excluded unless you change them here.', 'stable-task-muted'),
    );
    const list = document.createElement('div');
    list.className = 'stable-visual-identity-review__list';
    identityIssues.forEach((issue) => {
      const row = document.createElement('div');
      row.className = 'stable-visual-identity-review__row';
      const identity = document.createElement('span');
      identity.className = 'stable-visual-identity-review__identity';
      identity.append(
        text('strong', issue.label || issue.identity_key),
        text('span', issue.identity_key, 'stable-task-muted'),
      );
      const controls = document.createElement('div');
      controls.className = 'stable-visual-identity-review__decision';
      const mode = document.createElement('select');
      mode.className = 'form-select';
      mode.dataset.stableVisualIdentityDecision = issue.identity_key;
      mode.dataset.suggestedEntryId = issue.suggested_entry_id || '';
      mode.setAttribute('aria-label', `Decision for ${issue.label || issue.identity_key}`);
      mode.append(new Option('Keep excluded — no visual dossier', 'exclude'));
      if (issue.suggested_entry_id) mode.append(new Option(
        `Use suggested Cast identity: ${issue.suggested_entry_name}`,
        'suggested',
      ));
      mode.append(new Option('Choose another Cast identity…', 'manual'));
      const manual = document.createElement('select');
      manual.className = 'form-select';
      manual.dataset.stableVisualIdentityManual = issue.identity_key;
      manual.setAttribute('aria-label', `Cast identity for ${issue.label || issue.identity_key}`);
      manual.append(new Option('Choose an approved Cast identity…', ''));
      const entries = [...approvedEntries].sort((left, right) => {
        if (left.id === issue.suggested_entry_id) return -1;
        if (right.id === issue.suggested_entry_id) return 1;
        return String(left.display_name || left.canonical_name || left.id)
          .localeCompare(String(right.display_name || right.canonical_name || right.id));
      });
      entries.forEach((entry) => {
        const name = entry.display_name || entry.canonical_name || entry.id;
        manual.append(new Option(name, entry.id));
      });
      mode.value = issue.excluded_during_roster_review
        ? 'exclude' : (issue.suggested_entry_id ? 'suggested' : 'exclude');
      const consequence = text('p', '', 'stable-task-muted stable-visual-identity-review__consequence');
      const sync = () => {
        manual.hidden = mode.value !== 'manual';
        consequence.textContent = mode.value === 'exclude'
          ? 'Skips this imported visual dossier. Source evidence, task history, and the approved Cast stay unchanged.'
          : mode.value === 'suggested'
            ? `Attaches this visual dossier to ${issue.suggested_entry_name}. No new Cast identity is created.`
            : 'Select the approved Cast identity that should own this visual dossier.';
      };
      mode.addEventListener('change', sync);
      sync();
      controls.append(mode, manual, consequence);
      row.append(identity, controls);
      list.append(row);
    });
    identityDecisions.append(list);
  }
  const action = button('Apply selected sections', 'btn btn-primary');
  action.disabled = activation.ready !== true;
  action.addEventListener('click', async () => {
    action.disabled = true;
    action.textContent = 'Importing…';
    footerStatus.textContent = 'Applying selected dossier sections…';
    try {
      const identityCrosswalk = {};
      const excludedVisualIdentityKeys = [];
      let incomplete = null;
      identityDecisions.querySelectorAll('[data-stable-visual-identity-decision]')
        .forEach((mode) => {
          const identityKey = mode.dataset.stableVisualIdentityDecision;
          if (mode.value === 'exclude') excludedVisualIdentityKeys.push(identityKey);
          else if (mode.value === 'suggested') {
            identityCrosswalk[identityKey] = mode.dataset.suggestedEntryId;
          } else {
            const manual = identityDecisions.querySelector(
              `[data-stable-visual-identity-manual="${CSS.escape(identityKey)}"]`,
            );
            if (!manual?.value) incomplete ||= manual;
            else identityCrosswalk[identityKey] = manual.value;
          }
        });
      if (incomplete) {
        action.disabled = false;
        action.textContent = 'Apply selected sections';
        footerStatus.textContent = 'Choose a Cast identity for every manual match.';
        incomplete.focus();
        return;
      }
      const activated = await apiJson(
        `/api/cast-dossier/${encodeURIComponent(packageSummary.parent_candidate_id)}/activate`,
        {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            expected_roster_fingerprint: activation.approved_roster_fingerprint,
            import_voice_dossiers: voice.checked,
            import_visual_dossiers: visual.checked,
            identity_crosswalk: identityCrosswalk,
            excluded_visual_identity_keys: excludedVisualIdentityKeys,
          }),
        },
      );
      const applications = activated.package?.applications || {};
      const visualApplication = applications.visual_dossiers?.application
        || applications.visual_dossiers || {};
      footerStatus.textContent = [
        `Voice personas and definitions: ${applications.voice_dossiers ? 'applied to Voice' : 'not applied'}`,
        `Visual dossiers: ${applications.visual_dossiers ? `${visualApplication.written_count || visualApplication.character_count || 0} written to Appearance` : 'not applied'}`,
      ].join(' · ');
      action.remove();
    } catch (error) {
      action.disabled = false;
      action.textContent = 'Retry dossier import';
      footerStatus.textContent = error.message || 'The dossier sections were not imported.';
    }
  });
  panel.append(choices, identityDecisions, action);
  resultHost.replaceChildren(panel);
  footerStatus.textContent = activation.ready
    ? 'Ready to apply. Existing production Voice assignments remain unchanged.'
    : activation.reason || 'Approve a compatible Character roster first.';
  footerActions.replaceChildren();
}
