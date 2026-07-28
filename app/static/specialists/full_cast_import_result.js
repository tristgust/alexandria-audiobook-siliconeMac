'use strict';

import { resultMessage, textNode } from '/static/pages/more.js';
import { renderCompletedCastAudit } from './full_cast_dossier_review.js';
import { isCompletedCastPackage } from '/static/cast_dossier_state.js';

const UI = globalThis.AlexandriaUI;

async function run(button, pendingLabel, operation) {
  const prior = button.textContent;
  button.disabled = true;
  button.textContent = pendingLabel;
  try { return await operation(); }
  finally { button.disabled = false; button.textContent = prior; }
}

export async function renderFullCastImportedResult({
  data, host, shell, route, api, signal,
}) {
  const routing = data.routing || {};
  const packageSummary = data.cast_dossier_package || null;
  if (data.task_type === 'complete_cast_dossier' && isCompletedCastPackage(packageSummary)) {
    host.replaceChildren(renderCompletedCastAudit(data.reconciliation || packageSummary));
    return;
  }
  if (data.task_type === 'complete_cast_dossier' && packageSummary) {
    const activation = packageSummary.activation || {};
    const selected = packageSummary.selected_sections || {};
    const identityReview = packageSummary.visual_identity_review || {};
    const identityIssues = identityReview.issues || [];
    const approvedEntries = identityReview.approved_entries || [];
    const panel = document.createElement('section');
    panel.className = 'complete-cast-bundle complete-cast-bundle--activation';
    panel.append(
      textNode('span', 'metadata task-import-surface__eyebrow', 'Complete Cast dossier validated'),
      textNode('h3', '', 'Import selected dossier sections'),
      textNode('p', 'support-status-copy',
        'The approved Cast is ready. Review any unmatched visual identities, then send the included Voice and visual work into their native review areas.'),
    );
    const choices = document.createElement('div');
    choices.className = 'complete-cast-bundle__choices';
    const voice = UI.checkbox({
      label: `Voice personas and Designed Voice definitions (${packageSummary.summary?.voice_dossier_count || 0})`,
      checked: selected.voice_personas_and_designs === true,
      disabled: selected.voice_personas_and_designs !== true,
    });
    const visual = UI.checkbox({
      label: `Visual dossiers (${packageSummary.summary?.visual_dossier_count || 0})`,
      checked: selected.visual_dossiers === true,
      disabled: selected.visual_dossiers !== true,
    });
    choices.append(voice, visual);
    const identityDecisions = document.createElement('section');
    identityDecisions.className = 'visual-identity-review';
    if (selected.visual_dossiers === true && identityIssues.length) {
      identityDecisions.append(
        textNode('h4', '', 'Match visual identities'),
        textNode('p', 'support-status-copy',
          'Decide only where each imported visual dossier belongs. Keeping one excluded skips that dossier; it does not delete source evidence, task history, or an approved Cast identity.'),
        textNode(
          'p',
          'metadata visual-identity-review__summary',
          `${identityIssues.length} decisions · ${identityIssues.filter((issue) => issue.suggested_entry_id).length} suggested matches · prior exclusions preserved`,
        ),
      );
      const list = document.createElement('div');
      list.className = 'visual-identity-review__list';
      identityIssues.forEach((issue) => {
        const row = document.createElement('div');
        row.className = 'visual-identity-review__row';
        const identity = document.createElement('span');
        identity.className = 'visual-identity-review__identity';
        identity.append(
          textNode('strong', '', issue.label || issue.identity_key),
          textNode('span', 'metadata', issue.identity_key),
        );
        const controls = document.createElement('div');
        controls.className = 'visual-identity-review__decision';
        const mode = document.createElement('select');
        mode.className = 'field__control';
        mode.dataset.visualIdentityDecision = issue.identity_key;
        mode.dataset.suggestedEntryId = issue.suggested_entry_id || '';
        mode.setAttribute('aria-label', `Decision for ${issue.label || issue.identity_key}`);
        mode.append(new Option('Keep excluded — no visual dossier', 'exclude'));
        if (issue.suggested_entry_id) mode.append(new Option(
          `Use suggested Cast identity: ${issue.suggested_entry_name}`,
          'suggested',
        ));
        mode.append(new Option('Choose another Cast identity…', 'manual'));
        const manual = document.createElement('select');
        manual.className = 'field__control';
        manual.dataset.visualIdentityManual = issue.identity_key;
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
        const consequence = textNode(
          'p',
          'metadata visual-identity-review__consequence',
          '',
        );
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
    const action = UI.button({
      label: 'Import selected sections for review',
      variant: 'primary',
      disabled: activation.ready !== true,
    });
    const status = document.createElement('div');
    status.className = 'transaction-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    status.textContent = activation.ready
      ? 'Ready to import. Existing production Voice assignments remain unchanged.'
      : activation.reason || 'Approve a compatible Character roster first.';
    action.addEventListener('click', async () => {
      const identityCrosswalk = {};
      const excludedVisualIdentityKeys = [];
      let incomplete = null;
      identityDecisions.querySelectorAll('[data-visual-identity-decision]')
        .forEach((mode) => {
          const identityKey = mode.dataset.visualIdentityDecision;
          if (mode.value === 'exclude') excludedVisualIdentityKeys.push(identityKey);
          else if (mode.value === 'suggested') {
            identityCrosswalk[identityKey] = mode.dataset.suggestedEntryId;
          } else {
            const manual = identityDecisions.querySelector(
              `[data-visual-identity-manual="${CSS.escape(identityKey)}"]`,
            );
            if (!manual?.value) incomplete ||= manual;
            else identityCrosswalk[identityKey] = manual.value;
          }
        });
      if (incomplete) {
        status.textContent = 'Choose a Cast identity for every manual match.';
        incomplete.focus();
        return;
      }
      const response = await run(action, 'Importing…', () => api.post(
        `/api/cast-dossier/${encodeURIComponent(packageSummary.parent_candidate_id)}/activate`,
        {
          expected_roster_fingerprint: activation.approved_roster_fingerprint,
          import_voice_dossiers: voice.querySelector('input')?.checked === true,
          import_visual_dossiers: visual.querySelector('input')?.checked === true,
          identity_crosswalk: identityCrosswalk,
          excluded_visual_identity_keys: excludedVisualIdentityKeys,
        },
        { signal },
      ));
      if (!response.ok) {
        status.textContent = resultMessage(response, 'No dossier section was imported.');
        return;
      }
      const applications = response.data?.package?.applications || {};
      status.textContent = [
        `Voice dossiers: ${applications.voice_dossiers ? 'ready for review' : 'not imported'}`,
        `Visual dossiers: ${applications.visual_dossiers ? 'ready for review' : 'not imported'}`,
      ].join(' · ');
      action.remove();
    });
    panel.append(choices, identityDecisions, action, status);
    host.replaceChildren(panel);
    return;
  }
  const blocked = ['blocked', 'unsupported'].includes(routing.status);
  host.replaceChildren(UI.notice({
    tone: blocked ? 'warning' : 'success',
    title: blocked ? 'Task imported; review is blocked' : 'Task imported for review',
    body: routing.message || 'The result is ready in its native Cast review.',
    action: UI.button({
      label: 'Open native review', variant: 'secondary',
      onClick: () => shell.navigate(shell.routes.routeForPath(
        'cast', route.projectId ? { project: route.projectId } : {},
      ).hash),
    }),
    live: true,
  }));
}
