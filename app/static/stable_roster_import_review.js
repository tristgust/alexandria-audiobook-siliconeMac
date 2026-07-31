'use strict';

function text(tag, value, className = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function button(label, className) {
  const node = text('button', label, className);
  node.type = 'button';
  return node;
}

function tagList(values, emptyText) {
  const node = document.createElement('div');
  node.className = 'stable-roster-tags';
  const items = Array.isArray(values) ? values.filter(Boolean) : [];
  if (!items.length) node.append(text('span', emptyText, 'stable-task-muted'));
  else items.slice(0, 8).forEach((value) => node.append(text('span', value)));
  if (items.length > 8) node.append(text('span', `+${items.length - 8} more`, 'stable-task-muted'));
  return node;
}

function metric(label, value, detail = '') {
  const node = document.createElement('div');
  node.className = 'stable-roster-metric';
  node.append(text('span', label), text('strong', Number(value || 0).toLocaleString()));
  if (detail) node.append(text('span', detail, 'stable-task-muted'));
  return node;
}

function checkboxCard({ label, body, checked = true, disabled = false, key }) {
  const card = document.createElement('label');
  card.className = 'stable-roster-enrichment-card';
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.checked = checked;
  input.disabled = disabled;
  if (key) input.dataset.enrichmentOption = key;
  const copy = document.createElement('span');
  copy.append(text('strong', label), text('span', body));
  card.append(input, copy);
  return card;
}

function observationRow(observation, issue, currentEntries) {
  const entry = observation.entry || {};
  const row = document.createElement('article');
  row.className = 'stable-roster-row';
  row.dataset.importId = observation.import_id;
  const identity = document.createElement('div');
  identity.className = 'stable-roster-row__identity';
  identity.append(
    text('strong', observation.display_name || observation.canonical_name || 'Unnamed identity'),
    text('span', [
      observation.entity_kind,
      observation.speaking_status,
      `${Math.round(Number(entry.confidence || observation.confidence || 0) * 100)}% confidence`,
    ].filter(Boolean).join(' · '), 'stable-task-muted'),
  );
  const facts = document.createElement('div');
  facts.className = 'stable-roster-row__facts';
  const addFact = (label, values, emptyText, detail = '') => {
    const group = document.createElement('div');
    group.append(text('span', label, 'stable-task-label'), tagList(values, emptyText));
    if (detail) group.append(text('span', detail, 'stable-task-muted'));
    facts.append(group);
  };
  addFact('Aliases', entry.aliases || observation.aliases, 'No aliases');
  addFact('Relationships', entry.relationships, 'No relationships found');
  const importedVoiceClues = observation.voice_clues || [];
  const retainedVoiceClues = entry.voice_clues || [];
  addFact(
    'Voice clues',
    retainedVoiceClues.length ? retainedVoiceClues : importedVoiceClues,
    'No Voice clues',
    importedVoiceClues.length > retainedVoiceClues.length
      ? `${retainedVoiceClues.length} of ${importedVoiceClues.length} currently retained in the draft; the imported clue remains visible for review.`
      : '',
  );
  const decision = document.createElement('div');
  decision.className = 'stable-roster-row__decision';
  if (!issue) {
    decision.append(text(
      'span',
      observation.proposed_action === 'merge' ? 'Safe merge' : 'Safe addition',
      'stable-task-status stable-task-status--success',
    ));
  } else {
    decision.append(
      text('span', issue.title || 'Decision required', 'stable-task-status stable-task-status--warning'),
      text('p', issue.explanation || '', 'stable-task-muted'),
    );
    const action = document.createElement('select');
    action.className = 'form-select';
    action.dataset.rosterImportDecision = issue.import_id;
    const allowed = issue.allowed_actions || [];
    allowed.forEach((value) => action.append(new Option({
      merge: 'Merge into existing Cast identity',
      add: 'Add as confirmed Cast identity',
      unresolved: 'Add as unresolved identity',
      exclude: 'Do not add to Cast',
    }[value] || value, value)));
    const fallbackAction = allowed.includes('unresolved')
      ? 'unresolved' : allowed[0] || '';
    action.value = allowed.includes(issue.proposed_action)
      ? issue.proposed_action : fallbackAction;
    decision.append(action);
    let target = null;
    if (allowed.includes('merge')) {
      target = document.createElement('select');
      target.className = 'form-select';
      target.dataset.rosterImportTarget = issue.import_id;
      target.append(new Option('Choose matching Cast identity…', ''));
      const mergeTargets = Array.isArray(issue.current_matches)
        && issue.current_matches.length
        ? issue.current_matches : currentEntries;
      mergeTargets.forEach((item) => target.append(new Option(
        item.display_name || item.canonical_name,
        item.id,
      )));
      target.value = issue.proposed_current_entry_id || '';
      decision.append(target);
    }
    const decisionHelp = text(
      'p',
      '',
      'stable-task-muted stable-roster-decision-help',
    );
    const syncDecision = () => {
      if (target) target.hidden = action.value !== 'merge';
      decisionHelp.textContent = {
        merge: 'Attach this evidence to the selected existing Cast identity. No duplicate character is created.',
        add: 'Create a new confirmed Cast identity from this observation.',
        unresolved: 'Create a provisional Cast identity and keep its evidence. It stays flagged for later resolution and requires acknowledgment before approval.',
        exclude: 'Leave this observation out of Cast. Its evidence remains in the task audit, but it receives no Voice or visual dossier.',
      }[action.value] || '';
    };
    action.addEventListener('change', syncDecision);
    syncDecision();
    decision.append(decisionHelp);
  }
  row.append(identity, facts, decision);
  return row;
}

function safeChangesDisclosure(observations) {
  const disclosure = document.createElement('details');
  disclosure.className = 'stable-roster-safe-changes';
  const summary = document.createElement('summary');
  summary.append(
    text('strong', `${observations.length} safe changes`),
    text('span', 'Alexandria applies these automatically.', 'stable-task-muted'),
  );
  const list = document.createElement('div');
  list.className = 'stable-roster-safe-changes__list';
  observations.forEach((observation) => {
    const row = document.createElement('div');
    row.className = 'stable-roster-safe-change';
    row.append(
      text('strong', observation.display_name || observation.canonical_name || 'Unnamed identity'),
      text(
        'span',
        observation.proposed_action === 'merge'
          ? 'Merge with existing Cast identity' : 'Add to roster',
        'stable-task-muted',
      ),
    );
    list.append(row);
  });
  disclosure.append(summary, list);
  return disclosure;
}

function dossierAttachment(packageSummary) {
  if (!packageSummary?.parent_candidate_id) return null;
  const counts = packageSummary.summary || {};
  const disclosure = document.createElement('details');
  disclosure.className = 'stable-roster-dossier-attachment';
  const summary = document.createElement('summary');
  summary.append(
    text('strong', 'Attached ChatGPT dossiers'),
    text(
      'span',
      `${counts.voice_dossier_count || 0} Voice · ${counts.visual_dossier_count || 0} visual`,
      'stable-task-muted',
    ),
  );
  const body = document.createElement('div');
  body.className = 'stable-roster-dossier-attachment__body';
  body.append(text(
    'p',
    'These results already exist inside the completed ZIP. Roster approval establishes the identities they attach to; Alexandria does not regenerate them.',
    'stable-task-muted',
  ));
  const previews = document.createElement('div');
  previews.className = 'stable-roster-dossier-preview';
  (packageSummary.voice_preview || []).forEach((voice) => {
    const item = document.createElement('article');
    item.className = 'stable-roster-dossier-preview__item';
    item.append(
      text('strong', voice.speaker || 'Voice dossier'),
      text('p', voice.persona_summary || 'No persona summary returned.', 'stable-task-muted'),
      text(
        'p',
        voice.designed_voice_description || 'No Designed Voice definition returned.',
        'stable-roster-dossier-preview__definition',
      ),
    );
    previews.append(item);
  });
  if (previews.childElementCount) body.append(previews);
  if ((counts.voice_dossier_count || 0) > (packageSummary.voice_preview || []).length) {
    body.append(text(
      'p',
      `Previewing ${(packageSummary.voice_preview || []).length} of ${counts.voice_dossier_count} Voice dossiers. All dossiers are retained after approval.`,
      'stable-task-muted',
    ));
  }
  disclosure.append(summary, body);
  return disclosure;
}

function incompleteDecisions(body) {
  return [...body.querySelectorAll('[data-roster-import-decision]')].filter((select) => {
    if (!select.value) return true;
    if (select.value !== 'merge') return false;
    const target = body.querySelector(
      `[data-roster-import-target="${CSS.escape(select.dataset.rosterImportDecision)}"]`,
    );
    return !target?.value;
  });
}

async function pollEnrichment(apiJson, host) {
  const payload = await apiJson('/api/character_roster/enrichment');
  const plan = payload.plan || {};
  const steps = plan.steps || {};
  host.replaceChildren(
    text('strong', payload.status === 'complete' ? 'Cast enrichment complete' : 'Cast enrichment status'),
    text('p', [
      `Relationships: ${steps.relationships?.state || 'included'}`,
      `Designed Voices: ${steps.designed_voice_profiles?.state || 'not selected'}`,
      `Visual details: ${steps.visual_details?.state || 'not selected'}`,
    ].join(' · '), 'stable-task-muted'),
  );
  if (payload.running) window.setTimeout(() => pollEnrichment(apiJson, host), 1000);
}

export async function renderStableRosterDraftApproval({
  apiJson, status, body, footerStatus, footerActions,
}) {
  const packageSummary = status.cast_dossier_package || null;
  const counts = packageSummary?.summary || {};
  const approval = status.approval || {};
  const unresolvedCount = status.summary?.unresolved_acknowledgement_count || 0;
  body.replaceChildren();
  const header = document.createElement('header');
  header.className = 'stable-roster-review-header';
  header.append(
    text('span', packageSummary
      ? 'Complete Cast dossier · final roster step' : 'Roster draft saved', 'stable-task-eyebrow'),
    text('h3', 'Approve the reviewed Cast roster'),
    text(
      'p',
      packageSummary
        ? 'Your decisions are saved. Approval activates the identity foundation, then routes the already-completed Voice and visual sections into Cast review.'
        : 'Your decisions are saved. Approval makes this roster authoritative; existing production Voice assignments remain unchanged.',
      'stable-task-muted',
    ),
  );
  if (!status.current?.working_draft || !approval.draft_fingerprint) {
    body.append(
      header,
      text('p', 'No reviewable roster draft is active.', 'stable-task-error'),
    );
    footerStatus.textContent = 'Open the completed Cast result again.';
    footerActions.replaceChildren();
    return;
  }
  if (approval.blocked) {
    const blockingCount = status.summary?.blocking_issue_count
      || status.summary?.issue_count || 0;
    body.append(
      header,
      text(
        'p',
        `${blockingCount} blocking issue${blockingCount === 1 ? '' : 's'} remain. Alexandria will not claim the draft is ready until they are resolved.`,
        'stable-task-error',
      ),
    );
    footerStatus.textContent = 'Roster approval remains blocked.';
    footerActions.replaceChildren();
    return;
  }
  const metrics = document.createElement('div');
  metrics.className = 'stable-roster-metrics stable-roster-approval-summary';
  metrics.append(
    metric('Roster draft', 1, 'saved'),
    metric('Unresolved identities', unresolvedCount),
    metric('Voice dossiers', counts.voice_dossier_count || 0),
    metric('Visual dossiers', counts.visual_dossier_count || 0),
  );
  const attached = dossierAttachment(packageSummary);
  let acknowledgment = null;
  if (approval.requires_unresolved_acknowledgement) {
    const label = checkboxCard({
      label: `Approve while keeping ${unresolvedCount} unresolved ${unresolvedCount === 1 ? 'identity' : 'identities'}`,
      body: 'These remain provisional Cast entries with their evidence intact. They stay visibly flagged for later resolution; they are not excluded or silently treated as canonical.',
      checked: false,
    });
    acknowledgment = label.querySelector('input');
    body.append(header, metrics, ...(attached ? [attached] : []), label);
  } else {
    body.append(header, metrics, ...(attached ? [attached] : []));
  }
  const feedback = document.createElement('div');
  feedback.className = 'stable-roster-feedback';
  feedback.setAttribute('aria-live', 'polite');
  body.append(feedback);
  const approve = button(
    packageSummary
      ? 'Approve roster and continue to dossier review'
      : approval.requires_unresolved_acknowledgement
        ? 'Approve roster with unresolved identities'
        : 'Approve roster',
    'btn btn-primary',
  );
  footerStatus.textContent = 'Your previous decisions are saved; nothing needs to be repeated.';
  footerActions.replaceChildren(approve);

  approve.addEventListener('click', async () => {
    if (acknowledgment && !acknowledgment.checked) {
      footerStatus.textContent = 'Approval requires explicit acknowledgment.';
      feedback.replaceChildren(text(
        'p',
        'Confirm that the provisional identities should remain in the approved roster.',
        'stable-task-error',
      ));
      return;
    }
    approve.disabled = true;
    approve.textContent = 'Checking draft…';
    footerStatus.textContent = 'Refreshing approval state…';
    try {
      const latest = await apiJson('/api/character_roster/reconciliation');
      const latestApproval = latest.approval || {};
      const latestPackage = latest.cast_dossier_package || packageSummary;
      if (latestApproval.blocked || !latestApproval.draft_fingerprint) {
        throw new Error('The current roster draft still has blocking issues.');
      }
      approve.textContent = 'Approving roster…';
      const approved = await apiJson('/api/character_roster/reconciliation/approve', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: latestApproval.requires_unresolved_acknowledgement
            ? 'approve_with_unresolved' : 'approve_resolved',
          draft_fingerprint: latestApproval.draft_fingerprint,
          expected_approved_fingerprint: latestApproval.expected_approved_fingerprint,
        }),
      });
      if (latestPackage?.parent_candidate_id) {
        const resumed = await apiJson('/api/character_roster/reconciliation');
        const approvedPackage = resumed.cast_dossier_package || latestPackage;
        if (approvedPackage.visual_identity_review?.required) {
          footerActions.replaceChildren();
          footerStatus.textContent = 'Roster approved. Visual identity review is ready.';
          body.replaceChildren(
            header,
            text('strong', 'Roster approved; visual identity review is ready'),
            text(
              'p',
              'The dossier package remains intact. Close and reopen Full Cast tasks to match its visual identities or keep them excluded, then import the selected sections.',
              'stable-task-muted',
            ),
          );
          return;
        }
        approve.textContent = 'Importing dossier sections…';
        const activated = await apiJson(
          `/api/cast-dossier/${encodeURIComponent(approvedPackage.parent_candidate_id)}/activate`,
          {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              expected_roster_fingerprint: approved.approved?.roster_fingerprint,
              import_voice_dossiers: approvedPackage.selected_sections?.voice_personas_and_designs === true,
              import_visual_dossiers: approvedPackage.selected_sections?.visual_dossiers === true,
            }),
          },
        );
        const applications = activated.package?.applications || {};
        const visualApplication = applications.visual_dossiers?.application
          || applications.visual_dossiers || {};
        footerActions.replaceChildren();
        footerStatus.textContent = 'Complete Cast dossier applied.';
        body.replaceChildren(
          header,
          text('strong', 'Complete Cast dossier imported'),
          text(
            'p',
            [
              'Roster and relationships are active',
              `Voice personas and definitions: ${applications.voice_dossiers ? 'attached to Voice' : 'not applied'}`,
              `Visual dossiers: ${applications.visual_dossiers ? `${visualApplication.written_count || visualApplication.character_count || 0} written to Appearance` : 'not applied'}`,
              'Close this window to refresh Cast.',
            ].join(' · '),
            'stable-task-muted',
          ),
        );
        return;
      }
      const plan = approved.enrichment;
      if (plan && (plan.options?.create_designed_voice_profiles || plan.options?.discover_visual_details)) {
        await apiJson('/api/character_roster/enrichment/start', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            expected_plan_fingerprint: plan.plan_fingerprint,
            expected_roster_fingerprint: approved.approved?.roster_fingerprint,
          }),
        });
      }
      footerActions.replaceChildren();
      footerStatus.textContent = 'Roster approved.';
      await pollEnrichment(apiJson, feedback);
    } catch (error) {
      approve.disabled = false;
      approve.textContent = packageSummary ? 'Retry dossier import' : 'Retry approval';
      footerStatus.textContent = 'The current review state remains available.';
      feedback.replaceChildren(text(
        'p', error.message || 'Roster approval failed.', 'stable-task-error',
      ));
    }
  });
}

export async function renderStableRosterImportReview({
  apiJson, candidate, body, footerStatus, footerActions, onCreateNewBundle,
}) {
  footerStatus.textContent = 'Loading native roster review…';
  footerActions.replaceChildren();
  const candidateId = candidate.candidate_id;
  const [full, status] = await Promise.all([
    apiJson(`/api/character_roster/import-reconciliation?candidate_id=${encodeURIComponent(candidateId)}`),
    apiJson(`/api/character_roster/reconciliation?candidate_id=${encodeURIComponent(candidateId)}`),
  ]);
  if (
    !status.pending_import
    && status.current?.working_draft
    && status.approval?.blocked === false
  ) {
    await renderStableRosterDraftApproval({
      apiJson, status, body, footerStatus, footerActions,
    });
    return;
  }
  const focused = candidate.reconciliation || status.pending_import || {};
  let packageSummary = candidate.cast_dossier_package
    || status.cast_dossier_package
    || null;
  const issueById = new Map((focused.issues || []).map((item) => [item.import_id, item]));
  const summary = full.summary || focused.summary || {};
  body.replaceChildren();
  const header = document.createElement('header');
  header.className = 'stable-roster-review-header';
  header.append(
    text('span', packageSummary ? 'Complete Cast dossier · roster review' : 'Native roster review', 'stable-task-eyebrow'),
    text('h3', 'Review imported Cast roster'),
    text('p', packageSummary
      ? 'Approve the identity foundation first. The included ChatGPT Voice and visual sections remain attached until these identities are resolved.'
      : 'Inspect relationships and identity evidence before creating the roster draft. Nothing is approved yet.', 'stable-task-muted'),
  );
  if (typeof onCreateNewBundle === 'function') {
    const newBundle = button('Create new Complete Cast bundle', 'btn btn-outline-secondary');
    newBundle.dataset.stableCreateNewCastBundle = '';
    newBundle.addEventListener('click', onCreateNewBundle);
    header.append(newBundle);
  }
  const repairWarnings = packageSummary?.repair_warnings || [];
  if (repairWarnings.length) {
    header.append(text(
      'p',
      `Import repair: ${repairWarnings.join(' ')}`,
      'stable-task-muted stable-roster-repair-note',
    ));
  }
  const issues = focused.issues || [];
  const issueIds = new Set(issues.map((item) => item.import_id));
  const observations = full.observations || [];
  const decisions = observations.filter((item) => issueIds.has(item.import_id));
  const safe = observations.filter((item) => !issueIds.has(item.import_id));
  const metrics = document.createElement('div');
  metrics.className = 'stable-roster-metrics';
  metrics.append(
    metric('Decisions', issues.length),
    metric('Safe changes', safe.length),
    metric('Relationships', summary.relationships),
    metric('Aliases', summary.aliases),
    metric(
      'Voice clues',
      summary.voice_clues_imported ?? summary.voice_clues,
      `${summary.voice_clues_retained ?? summary.voice_clues ?? 0} retained`,
    ),
    metric('Appearance evidence', summary.appearance_evidence),
  );
  const decisionHeader = document.createElement('div');
  decisionHeader.className = 'stable-roster-decision-header';
  decisionHeader.append(
    text('h3', `Decisions required (${issues.length})`),
    text('p', 'Review only the identities Alexandria cannot apply safely on its own.', 'stable-task-muted'),
    text(
      'p',
      'Add as unresolved identity creates a provisional Cast entry with its evidence intact and keeps it flagged for later review. Do not add to Cast omits it from the active roster while preserving the task evidence; Script and source text are unchanged.',
      'stable-task-muted stable-roster-decision-help',
    ),
  );
  const list = document.createElement('div');
  list.className = 'stable-roster-list stable-roster-list--decisions';
  decisions.forEach((observation) => list.append(observationRow(
    observation,
    issueById.get(observation.import_id),
    full.current_entries || [],
  )));
  const safeChanges = safeChangesDisclosure(safe);
  const attachedDossiers = dossierAttachment(packageSummary);
  const enrichment = document.createElement('section');
  enrichment.className = 'stable-roster-enrichment';
  const packageSections = packageSummary?.selected_sections || null;
  const packageCounts = packageSummary?.summary || {};
  const packageMode = Boolean(packageSummary?.parent_candidate_id);
  enrichment.append(
    text('span', 'After roster approval', 'stable-task-eyebrow'),
    text('h3', packageMode ? 'Import included dossier sections' : 'Complete the Cast profile'),
    text('p', packageMode
      ? 'ChatGPT already completed the selected Voice and visual sections inside this ZIP. Alexandria will route those exact results into native review after roster approval.'
      : 'Relationships are roster data. Designed Voice definitions and visual dossiers run afterward because they have separate evidence and review rules.', 'stable-task-muted'),
  );
  const voiceChoice = checkboxCard({
    label: packageMode
      ? `Import ChatGPT Voice personas and Designed Voice definitions (${packageCounts.voice_dossier_count || 0})`
      : 'Create missing Designed Voice profiles',
    body: packageMode
      ? packageSections?.voice_personas_and_designs
        ? 'Performance personas, acoustic traits, casting guidance, exact ref text, and persistent Voice definitions.'
        : 'This Complete Cast ZIP did not request a Voice section.'
      : 'Only speaking identities without an existing production Voice are processed.',
    checked: packageMode
      ? packageSections?.voice_personas_and_designs === true : true,
    disabled: packageMode
      && packageSections?.voice_personas_and_designs !== true,
    key: 'voices',
  });
  const visualChoice = checkboxCard({
    label: packageMode
      ? `Import ChatGPT visual dossiers (${packageCounts.visual_dossier_count || 0})`
      : 'Collect source-supported visual details',
    body: packageMode
      ? packageSections?.visual_dossiers
        ? `${packageCounts.visual_observation_count || 0} source observations support the dossiers; unsupported details remain unknown.`
        : 'This Complete Cast ZIP did not request a visual section.'
      : 'Unsupported appearance details remain unknown rather than invented.',
    checked: packageMode
      ? packageSections?.visual_dossiers === true : true,
    disabled: packageMode && packageSections?.visual_dossiers !== true,
    key: 'visuals',
  });
  const enrichmentGrid = document.createElement('div');
  enrichmentGrid.className = 'stable-roster-enrichment-grid';
  if (packageMode) {
    enrichment.classList.add('stable-roster-enrichment--attached');
    enrichment.append(text(
      'p',
      'Relationships, aliases, roles, groups, and speaking status are part of the roster approval.',
      'stable-task-muted',
    ));
    enrichmentGrid.classList.add('stable-roster-enrichment-grid--attached');
    voiceChoice.classList.add('stable-roster-enrichment-card--attached');
    visualChoice.classList.add('stable-roster-enrichment-card--attached');
    enrichmentGrid.append(voiceChoice, visualChoice);
  } else {
    enrichmentGrid.append(
      checkboxCard({
        label: 'Import relationships, aliases, roles, groups, and speaking status',
        body: 'Included in the roster draft and visible before approval.',
        checked: true, disabled: true,
      }),
      voiceChoice,
      visualChoice,
    );
  }
  enrichment.append(enrichmentGrid);
  const feedback = document.createElement('div');
  feedback.className = 'stable-roster-feedback';
  feedback.setAttribute('aria-live', 'polite');
  body.append(
    header,
    metrics,
    ...(attachedDossiers ? [attachedDossiers] : []),
    decisionHeader,
    list,
    safeChanges,
    enrichment,
    feedback,
  );
  const apply = button('Create roster draft', 'btn btn-primary');
  footerActions.replaceChildren(apply);
  const syncReadiness = () => {
    const incomplete = incompleteDecisions(body);
    apply.disabled = incomplete.length > 0;
    footerStatus.textContent = incomplete.length
      ? `${incomplete.length} decision${incomplete.length === 1 ? '' : 's'} still need a valid choice.`
      : `${issues.length} decisions ready · ${safe.length} safe changes will apply automatically.`;
    return incomplete;
  };
  body.querySelectorAll('[data-roster-import-decision], [data-roster-import-target]')
    .forEach((control) => control.addEventListener('change', syncReadiness));
  syncReadiness();

  apply.addEventListener('click', async () => {
    const incomplete = syncReadiness();
    if (incomplete.length) {
      const first = incomplete[0];
      const row = first.closest('.stable-roster-row');
      row?.classList.add('stable-roster-row--incomplete');
      feedback.replaceChildren(text(
        'p',
        'Choose an action and, for a merge, confirm the existing Cast identity.',
        'stable-task-error',
      ));
      row?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      window.setTimeout(() => first.focus(), 180);
      return;
    }
    const decisions = issues.map((issue) => {
      const action = body.querySelector(`[data-roster-import-decision="${CSS.escape(issue.import_id)}"]`)?.value;
      const target = body.querySelector(`[data-roster-import-target="${CSS.escape(issue.import_id)}"]`)?.value || null;
      return {
        import_id: issue.import_id,
        action,
        current_entry_id: action === 'merge' ? target : null,
      };
    });
    apply.disabled = true;
    apply.textContent = 'Creating draft…';
    footerStatus.textContent = 'Creating a reviewable roster draft…';
    try {
      const response = await apiJson('/api/character_roster/reconciliation/apply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          candidate_id: focused.candidate_id,
          result_fingerprint: focused.result_fingerprint,
          current_kind: focused.current_kind,
          current_fingerprint: focused.current_fingerprint,
          decisions,
          create_designed_voice_profiles: body.querySelector('[data-enrichment-option="voices"]')?.checked !== false,
          discover_visual_details: body.querySelector('[data-enrichment-option="visuals"]')?.checked !== false,
        }),
      });
      const reconciliation = response.reconciliation || {};
      packageSummary = response.cast_dossier_package || packageSummary;
      await renderStableRosterDraftApproval({
        apiJson,
        body,
        footerStatus,
        footerActions,
        status: {
          ...reconciliation,
          cast_dossier_package: packageSummary,
        },
      });
    } catch (error) {
      feedback.replaceChildren(text('p', error.message || 'Roster draft was not created.', 'stable-task-error'));
      footerStatus.textContent = 'No roster changes were applied.';
      apply.disabled = false;
      apply.textContent = 'Create roster draft';
    }
  });
}
