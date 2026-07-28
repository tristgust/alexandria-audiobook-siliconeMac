'use strict';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function metric(label, value, detail = '') {
  const node = document.createElement('div');
  node.className = 'roster-import-metric';
  node.append(
    text('span', 'metadata', label),
    text('strong', '', Number(value || 0).toLocaleString()),
  );
  if (detail) node.append(text('span', 'metadata', detail));
  return node;
}

function tags(values, empty = 'None found') {
  const node = document.createElement('div');
  node.className = 'roster-import-tags';
  const items = Array.isArray(values) ? values.filter(Boolean) : [];
  if (!items.length) node.append(text('span', 'metadata', empty));
  else items.slice(0, 8).forEach((value) => node.append(text('span', '', value)));
  if (items.length > 8) node.append(text('span', 'metadata', `+${items.length - 8} more`));
  return node;
}

function observationRow(observation, issueById, currentEntries) {
  const entry = observation.entry || {};
  const issue = issueById.get(observation.import_id);
  const row = document.createElement('article');
  row.className = 'roster-import-row';
  row.dataset.importId = observation.import_id;
  const identity = document.createElement('div');
  identity.className = 'roster-import-row__identity';
  identity.append(
    text('strong', '', observation.display_name || observation.canonical_name || 'Unnamed identity'),
    text('span', 'metadata', [
      observation.entity_kind,
      observation.speaking_status,
      `${Number(entry.confidence || observation.confidence || 0) * 100}% confidence`,
    ].filter(Boolean).join(' · ')),
  );
  const facts = document.createElement('div');
  facts.className = 'roster-import-row__facts';
  const fact = (label, values, empty, detail = '') => {
    const group = document.createElement('div');
    group.append(text('span', 'metadata', label), tags(values, empty));
    if (detail) group.append(text('span', 'metadata roster-import-fact-note', detail));
    facts.append(group);
  };
  fact('Aliases', entry.aliases || observation.aliases, 'No aliases');
  fact('Relationships', entry.relationships, 'No relationships found');
  const importedVoiceClues = observation.voice_clues || [];
  const retainedVoiceClues = entry.voice_clues || [];
  fact(
    'Voice clues',
    retainedVoiceClues.length ? retainedVoiceClues : importedVoiceClues,
    'No Voice clues',
    importedVoiceClues.length > retainedVoiceClues.length
      ? `${retainedVoiceClues.length} of ${importedVoiceClues.length} currently retained in the draft; the imported clue remains visible for this identity decision.`
      : '',
  );
  const decision = document.createElement('div');
  decision.className = 'roster-import-row__decision';
  if (!issue) {
    decision.append(UI.status({
      label: observation.proposed_action === 'merge' ? 'Safe merge' : 'Safe addition',
      tone: 'success',
    }));
  } else {
    decision.append(
      UI.status({ label: issue.title || 'Decision required', tone: 'warning' }),
      text('p', 'metadata', issue.explanation || ''),
    );
    const select = document.createElement('select');
    select.className = 'field__control';
    select.dataset.rosterImportDecision = issue.import_id;
    const allowed = issue.allowed_actions || [];
    allowed.forEach((action) => {
      const label = {
        merge: 'Merge into existing Cast identity',
        add: 'Add as confirmed Cast identity',
        unresolved: 'Add as unresolved identity',
        exclude: 'Do not add to Cast',
      }[action] || action;
      select.append(new Option(label, action));
    });
    const fallbackAction = allowed.includes('unresolved')
      ? 'unresolved' : allowed[0] || '';
    select.value = allowed.includes(issue.proposed_action)
      ? issue.proposed_action : fallbackAction;
    const target = allowed.includes('merge')
      ? document.createElement('select') : null;
    if (target) {
      target.className = 'field__control';
      target.dataset.rosterImportTarget = issue.import_id;
      target.append(new Option('Choose matching Cast identity…', ''));
      const mergeTargets = Array.isArray(issue.current_matches)
        && issue.current_matches.length
        ? issue.current_matches : currentEntries;
      mergeTargets.forEach((item) => target.append(new Option(
        item.display_name || item.canonical_name, item.id,
      )));
      target.value = issue.proposed_current_entry_id || '';
    }
    const decisionHelp = text('p', 'metadata roster-import-decision-help', '');
    const syncDecision = () => {
      if (target) target.hidden = select.value !== 'merge';
      decisionHelp.textContent = {
        merge: 'Attach this imported evidence to the selected existing Cast identity. No duplicate character is created.',
        add: 'Create a new confirmed Cast identity from this imported observation.',
        unresolved: 'Create a provisional Cast identity and keep its evidence. It remains flagged for later resolution and requires explicit acknowledgment before roster approval.',
        exclude: 'Leave this observation out of the Cast roster. Its evidence remains in the task audit, but it will not receive a Voice or visual dossier.',
      }[select.value] || '';
    };
    select.addEventListener('change', syncDecision);
    syncDecision();
    decision.append(select, ...(target ? [target] : []), decisionHelp);
  }
  row.append(identity, facts, decision);
  return row;
}

function safeChangesDisclosure(observations) {
  const disclosure = document.createElement('details');
  disclosure.className = 'roster-safe-changes';
  const summary = document.createElement('summary');
  summary.append(
    text('strong', '', `${observations.length} safe changes`),
    text('span', 'metadata', 'Alexandria will apply these automatically.'),
  );
  const list = document.createElement('div');
  list.className = 'roster-safe-changes__list';
  observations.forEach((observation) => {
    const row = document.createElement('div');
    row.className = 'roster-safe-change';
    row.append(
      text(
        'strong',
        '',
        observation.display_name || observation.canonical_name || 'Unnamed identity',
      ),
      text(
        'span',
        'metadata',
        observation.proposed_action === 'merge' ? 'Merge with existing Cast identity' : 'Add to roster',
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
  const section = document.createElement('details');
  section.className = 'roster-dossier-attachment';
  const summary = document.createElement('summary');
  summary.append(
    text('strong', '', 'Attached ChatGPT dossiers'),
    text(
      'span',
      'metadata',
      `${counts.voice_dossier_count || 0} Voice · ${counts.visual_dossier_count || 0} visual`,
    ),
  );
  const body = document.createElement('div');
  body.className = 'roster-dossier-attachment__body';
  body.append(text(
    'p',
    'support-status-copy',
    'These results are already inside the imported ZIP. Roster approval only establishes the stable identities they attach to; Alexandria does not regenerate them.',
  ));
  const previews = document.createElement('div');
  previews.className = 'roster-dossier-preview';
  (packageSummary.voice_preview || []).forEach((voice) => {
    const item = document.createElement('article');
    item.className = 'roster-dossier-preview__item';
    item.append(
      text('strong', '', voice.speaker || 'Voice dossier'),
      text('p', 'metadata', voice.persona_summary || 'No persona summary returned.'),
      text(
        'p',
        'roster-dossier-preview__definition',
        voice.designed_voice_description || 'No Designed Voice definition returned.',
      ),
    );
    previews.append(item);
  });
  if (previews.childElementCount) body.append(previews);
  if ((counts.voice_dossier_count || 0) > (packageSummary.voice_preview || []).length) {
    body.append(text(
      'p',
      'metadata',
      `Previewing ${(packageSummary.voice_preview || []).length} of ${counts.voice_dossier_count} Voice dossiers. All dossiers will be retained on their Cast identities after approval.`,
    ));
  }
  section.append(summary, body);
  return section;
}

function incompleteDecisions(root) {
  return [...root.querySelectorAll('[data-roster-import-decision]')].filter((select) => {
    if (!select.value) return true;
    if (select.value !== 'merge') return false;
    const target = root.querySelector(
      `[data-roster-import-target="${CSS.escape(select.dataset.rosterImportDecision)}"]`,
    );
    return !target?.value;
  });
}

function enrichmentChoices(packageSummary = null) {
  const section = document.createElement('section');
  section.className = 'roster-enrichment-choices';
  const selected = packageSummary?.selected_sections || null;
  const counts = packageSummary?.summary || {};
  const packageMode = Boolean(packageSummary?.parent_candidate_id);
  section.append(
    text('span', 'metadata task-import-surface__eyebrow', 'After roster approval'),
    text('h3', '', packageMode ? 'Import included dossier sections' : 'Complete the Cast profile'),
    text('p', 'support-status-copy', packageMode
      ? 'ChatGPT already completed the selected Voice and visual work inside this ZIP. Alexandria will route those exact results into native review after the roster identities are approved.'
      : 'Relationships and identity facts belong to the roster. Voice profiles and visual dossiers can be generated afterward because they use different evidence and review rules.'),
  );
  const relationships = UI.checkbox({
    label: 'Import relationships, aliases, roles, groups, and speaking status',
    checked: true, disabled: true,
  });
  const voiceIncluded = packageMode
    ? selected?.voice_personas_and_designs === true : true;
  const voices = UI.checkbox({
    label: packageMode
      ? `Import ChatGPT Voice personas and Designed Voice definitions (${counts.voice_dossier_count || 0})`
      : 'Create missing Designed Voice profiles for speaking identities',
    checked: voiceIncluded,
    disabled: packageMode && !voiceIncluded,
  });
  voices.dataset.enrichmentOption = 'voices';
  const visualIncluded = packageMode
    ? selected?.visual_dossiers === true : true;
  const visuals = UI.checkbox({
    label: packageMode
      ? `Import ChatGPT visual dossiers (${counts.visual_dossier_count || 0})`
      : 'Collect source-supported visual details and dossiers',
    checked: visualIncluded,
    disabled: packageMode && !visualIncluded,
  });
  visuals.dataset.enrichmentOption = 'visuals';
  if (packageMode) {
    section.classList.add('roster-enrichment-choices--attached');
    section.append(text(
      'p',
      'metadata roster-enrichment-choices__roster-status',
      'Relationships, aliases, roles, groups, and speaking status are part of the roster approval.',
    ));
    const attachedGrid = document.createElement('div');
    attachedGrid.className = 'roster-enrichment-grid roster-enrichment-grid--attached';
    [
      [voices, voiceIncluded
        ? 'Performance personas, acoustic traits, casting guidance, exact ref text, and persistent Voice definitions.'
        : 'No Voice section was requested in this ZIP.'],
      [visuals, visualIncluded
        ? `${counts.visual_observation_count || 0} source observations support the compiled dossiers; unsupported details remain unknown.`
        : 'No visual section was requested in this ZIP.'],
    ].forEach(([control, body]) => {
      const row = document.createElement('div');
      row.className = 'roster-enrichment-card roster-enrichment-card--attached';
      row.append(control, text('p', 'metadata', body));
      attachedGrid.append(row);
    });
    section.append(attachedGrid);
    return { section, voices, visuals, packageMode };
  }
  const grid = document.createElement('div');
  grid.className = 'roster-enrichment-grid';
  [
    [relationships, 'Core roster data. It is visible in this review and cannot be separated from the roster import.'],
    [voices, packageMode
      ? voiceIncluded
        ? 'Includes performance personas, structured acoustic traits, casting guidance, exact ref text, and synthesis-ready persistent Voice definitions.'
        : 'This Complete Cast ZIP did not request a Voice section.'
      : 'Runs only for speakers without an existing production Voice. Existing assignments are preserved.'],
    [visuals, packageMode
      ? visualIncluded
        ? `${counts.visual_observation_count || 0} source observations support the compiled dossiers; missing details remain explicit unknowns.`
        : 'This Complete Cast ZIP did not request a visual section.'
      : 'Runs after approval for the approved roster; unsupported appearance details remain unknown.'],
  ].forEach(([control, body]) => {
    const card = document.createElement('div');
    card.className = 'roster-enrichment-card';
    card.append(control, text('p', 'metadata', body));
    grid.append(card);
  });
  section.append(grid);
  return { section, voices, visuals, packageMode };
}

async function pollEnrichment(api, signal, host) {
  const response = await api.get('/api/character_roster/enrichment', { signal });
  if (!response.ok || signal.aborted) return;
  const payload = response.data || {};
  const plan = payload.plan || {};
  const steps = plan.steps || {};
  host.replaceChildren(UI.notice({
    tone: payload.status === 'complete' ? 'success'
      : ['failed', 'partial', 'invalid'].includes(payload.status) ? 'warning' : 'information',
    title: payload.status === 'complete' ? 'Cast enrichment complete'
      : payload.running ? 'Cast enrichment is running' : 'Cast enrichment needs attention',
    body: [
      `Relationships: ${steps.relationships?.state || 'included'}`,
      `Designed Voices: ${steps.designed_voice_profiles?.state || 'not selected'}`,
      `Visual details: ${steps.visual_details?.state || 'not selected'}`,
    ].join(' · '),
    live: true,
  }));
  if (payload.running) window.setTimeout(() => pollEnrichment(api, signal, host), 1000);
}

export async function renderRosterDraftApproval({ api, signal, status, host, report }) {
  const packageSummary = status.cast_dossier_package || null;
  const counts = packageSummary?.summary || {};
  const approval = status.approval || {};
  const unresolvedCount = status.summary?.unresolved_acknowledgement_count || 0;
  const root = document.createElement('section');
  root.className = 'roster-import-review roster-import-review--approval';
  root.dataset.rosterApprovalResume = '';
  const header = document.createElement('header');
  header.className = 'roster-import-review__header';
  header.append(
    text('span', 'metadata task-import-surface__eyebrow', packageSummary
      ? 'Complete Cast dossier · final roster step' : 'Roster draft saved'),
    text('h2', '', 'Approve the reviewed Cast roster'),
    text(
      'p',
      'support-status-copy',
      packageSummary
        ? 'Your decisions are saved. Approval activates the identity foundation, then routes the already-completed Voice and visual sections into Cast review.'
        : 'Your decisions are saved. Approval makes this roster authoritative; existing production Voice assignments remain unchanged.',
    ),
  );
  root.append(header);
  if (!status.current?.working_draft || !approval.draft_fingerprint) {
    root.append(UI.notice({
      tone: 'warning', title: 'No reviewable roster draft is active',
      body: 'Open the completed Cast result again to create a roster draft.', live: true,
    }));
    host.replaceChildren(root);
    return;
  }
  if (approval.blocked) {
    const blockingCount = status.summary?.blocking_issue_count
      || status.summary?.issue_count || 0;
    root.append(UI.notice({
      tone: 'error', title: 'Roster approval is still blocked',
      body: `${blockingCount} blocking issue${blockingCount === 1 ? '' : 's'} remain. Alexandria will not claim the draft is ready until they are resolved.`,
      live: true,
    }));
    host.replaceChildren(root);
    return;
  }
  const summary = document.createElement('div');
  summary.className = 'roster-approval-summary';
  summary.append(
    metric('Roster draft', 1, 'saved'),
    metric('Unresolved identities', unresolvedCount),
    metric('Voice dossiers', counts.voice_dossier_count || 0),
    metric('Visual dossiers', counts.visual_dossier_count || 0),
  );
  const attached = dossierAttachment(packageSummary);
  const acknowledgment = approval.requires_unresolved_acknowledgement
    ? UI.checkbox({
      label: `Approve while keeping ${unresolvedCount} unresolved ${unresolvedCount === 1 ? 'identity' : 'identities'}`,
      checked: false,
    })
    : null;
  const acknowledgmentCopy = acknowledgment
    ? text(
      'p',
      'metadata roster-approval-unresolved-copy',
      'These remain provisional Cast entries with their evidence intact. They stay visibly flagged for later resolution; they are not excluded or silently treated as canonical.',
    )
    : null;
  const feedback = document.createElement('div');
  feedback.className = 'roster-import-feedback';
  feedback.setAttribute('aria-live', 'polite');
  const approve = UI.button({
    label: packageSummary
      ? 'Approve roster and continue to dossier review'
      : approval.requires_unresolved_acknowledgement
        ? 'Approve roster with unresolved identities'
        : 'Approve roster',
    variant: 'primary',
  });
  const footerStatus = text(
    'span',
    'metadata roster-import-review__readiness',
    'Your previous decisions are saved; nothing needs to be repeated.',
  );
  const footer = document.createElement('footer');
  footer.className = 'roster-import-review__footer';
  footer.append(footerStatus, approve);
  root.append(
    summary,
    ...(attached ? [attached] : []),
    ...(acknowledgment ? [acknowledgment, acknowledgmentCopy] : []),
    feedback,
    footer,
  );
  host.replaceChildren(root);

  approve.addEventListener('click', async () => {
    if (acknowledgment && acknowledgment.querySelector('input')?.checked !== true) {
      feedback.replaceChildren(UI.notice({
        tone: 'warning', title: 'Acknowledgment required',
        body: 'Confirm that the provisional identities should remain in the approved roster.',
        live: true,
      }));
      footerStatus.textContent = 'Approval requires explicit acknowledgment.';
      return;
    }
    approve.disabled = true;
    approve.textContent = 'Checking draft…';
    footerStatus.textContent = 'Refreshing approval state…';
    const latest = await api.get('/api/character_roster/reconciliation', { signal });
    const latestApproval = latest.data?.approval || {};
    const latestPackage = latest.data?.cast_dossier_package || packageSummary;
    if (!latest.ok || latestApproval.blocked || !latestApproval.draft_fingerprint) {
      approve.disabled = false;
      approve.textContent = 'Retry approval';
      feedback.replaceChildren(UI.notice({
        tone: 'error', title: 'Roster approval is not ready',
        body: latest.error || 'The current draft still has blocking issues.', live: true,
      }));
      footerStatus.textContent = 'The draft remains available.';
      return;
    }
    approve.textContent = 'Approving roster…';
    const approved = await api.post('/api/character_roster/reconciliation/approve', {
      action: latestApproval.requires_unresolved_acknowledgement
        ? 'approve_with_unresolved' : 'approve_resolved',
      draft_fingerprint: latestApproval.draft_fingerprint,
      expected_approved_fingerprint: latestApproval.expected_approved_fingerprint,
    }, { signal });
    if (!approved.ok) {
      approve.disabled = false;
      approve.textContent = 'Retry approval';
      feedback.replaceChildren(UI.notice({
        tone: 'error', title: 'Roster was not approved',
        body: approved.error, live: true,
      }));
      footerStatus.textContent = 'The draft remains available.';
      return;
    }
    if (latestPackage?.parent_candidate_id) {
      const resumed = await api.get('/api/character_roster/reconciliation', { signal });
      const approvedPackage = resumed.ok
        ? resumed.data?.cast_dossier_package || latestPackage
        : latestPackage;
      if (approvedPackage.visual_identity_review?.required) {
        root.replaceChildren(
          header,
          UI.notice({
            tone: 'success',
            title: 'Roster approved; visual identity review is ready',
            body: 'The dossier package remains intact. Reopen Full Cast tasks to match its visual identities or keep them excluded, then import the selected sections.',
            live: true,
          }),
        );
        report?.('Cast roster approved', 'Visual identity decisions are ready in Full Cast tasks.', 'success');
        return;
      }
      approve.textContent = 'Importing dossier sections…';
      const activated = await api.post(
        `/api/cast-dossier/${encodeURIComponent(approvedPackage.parent_candidate_id)}/activate`,
        {
          expected_roster_fingerprint: approved.data?.approved?.roster_fingerprint,
          import_voice_dossiers: approvedPackage.selected_sections?.voice_personas_and_designs === true,
          import_visual_dossiers: approvedPackage.selected_sections?.visual_dossiers === true,
        },
        { signal },
      );
      if (!activated.ok) {
        approve.disabled = false;
        approve.textContent = 'Retry dossier import';
        feedback.replaceChildren(UI.notice({
          tone: 'warning', title: 'Roster approved; dossier import needs attention',
          body: activated.error, live: true,
        }));
        footerStatus.textContent = 'The roster is approved; the dossier package remains intact.';
        return;
      }
      const applications = activated.data?.package?.applications || {};
      root.replaceChildren(
        header,
        UI.notice({
          tone: 'success',
          title: 'Complete Cast dossier entered native review',
          body: [
            'Roster and relationships are active',
            `Voice personas and definitions: ${applications.voice_dossiers ? 'attached to Cast identities' : 'not imported'}`,
            `Visual dossiers: ${applications.visual_dossiers ? 'ready in Cast' : 'not imported'}`,
            'Close this window to refresh Cast.',
          ].join(' · '),
          live: true,
        }),
      );
      report?.('Complete Cast dossier imported', 'Voice and visual sections are ready in Cast.', 'success');
      return;
    }
    const plan = approved.data?.enrichment;
    if (plan && (plan.options?.create_designed_voice_profiles || plan.options?.discover_visual_details)) {
      const started = await api.post('/api/character_roster/enrichment/start', {
        expected_plan_fingerprint: plan.plan_fingerprint,
        expected_roster_fingerprint: approved.data?.approved?.roster_fingerprint,
      }, { signal });
      if (!started.ok) report?.('Roster approved; enrichment did not start', started.error, 'warning');
    }
    report?.('Roster approved', 'Relationships are active. Selected Cast enrichment is running.', 'success');
    root.replaceChildren(header, feedback);
    await pollEnrichment(api, signal, feedback);
  });
}

export async function renderRosterImportReview({
  api, signal, candidate, host, report,
}) {
  const candidateId = candidate.candidate_id;
  const [fullResult, focusedResult] = await Promise.all([
    api.get(`/api/character_roster/import-reconciliation?candidate_id=${encodeURIComponent(candidateId)}`, { signal }),
    api.get(`/api/character_roster/reconciliation?candidate_id=${encodeURIComponent(candidateId)}`, { signal }),
  ]);
  const focusedStatus = focusedResult.data || {};
  if (
    focusedResult.ok
    && !focusedStatus.pending_import
    && focusedStatus.current?.working_draft
    && focusedStatus.approval?.blocked === false
  ) {
    await renderRosterDraftApproval({
      api, signal, status: focusedStatus, host, report,
    });
    return;
  }
  if (!fullResult.ok || !focusedResult.ok) {
    host.replaceChildren(UI.notice({
      tone: 'error', title: 'Roster review could not load',
      body: fullResult.error || focusedResult.error, live: true,
    }));
    return;
  }
  const full = fullResult.data || {};
  const focused = candidate.reconciliation
    || focusedResult.data?.pending_import
    || {};
  let packageSummary = candidate.cast_dossier_package
    || focusedResult.data?.cast_dossier_package
    || null;
  const summary = full.summary || focused.summary || {};
  const issues = focused.issues || [];
  const issueById = new Map(issues.map((item) => [item.import_id, item]));
  const root = document.createElement('section');
  root.className = 'roster-import-review';
  root.dataset.rosterImportReview = '';
  const header = document.createElement('header');
  header.className = 'roster-import-review__header';
  header.append(
    text('span', 'metadata task-import-surface__eyebrow', packageSummary
      ? 'Complete Cast dossier · roster review' : 'Native review'),
    text('h2', '', 'Review imported Cast roster'),
    text('p', 'support-status-copy', packageSummary
      ? 'Approve the identity foundation first. The included ChatGPT Voice and visual sections remain attached to this dossier and cannot enter native review until these identities are resolved.'
      : 'Confirm identity decisions and inspect the imported relationships and evidence before creating a roster draft.'),
  );
  const repairWarnings = packageSummary?.repair_warnings || [];
  const repairNote = repairWarnings.length
    ? text(
      'p',
      'roster-import-repair-note',
      `Import repair: ${repairWarnings.join(' ')}`,
    )
    : null;
  const allObservations = full.observations || [];
  const issueIds = new Set(issues.map((item) => item.import_id));
  const decisionObservations = allObservations.filter((item) => issueIds.has(item.import_id));
  const safeObservations = allObservations.filter((item) => !issueIds.has(item.import_id));
  const metrics = document.createElement('div');
  metrics.className = 'roster-import-metrics';
  metrics.append(
    metric('Decisions', issues.length),
    metric('Safe changes', safeObservations.length),
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
  decisionHeader.className = 'roster-import-decision-header';
  decisionHeader.append(
    text('h3', '', `Decisions required (${issues.length})`),
    text('p', 'metadata', 'Review only the identities Alexandria cannot apply safely on its own.'),
    text(
      'p',
      'metadata roster-import-decision-help',
      'Add as unresolved identity creates a provisional Cast entry with its evidence intact and keeps it flagged for later review. Do not add to Cast omits it from the active roster while preserving the task evidence; Script and source text are unchanged.',
    ),
  );
  const list = document.createElement('div');
  list.className = 'roster-import-list roster-import-list--decisions';
  decisionObservations.forEach((item) => list.append(observationRow(
    item, issueById.get(item.import_id), full.current_entries || [],
  )));
  const safeChanges = safeChangesDisclosure(safeObservations);
  const attachedDossiers = dossierAttachment(packageSummary);
  const enrich = enrichmentChoices(packageSummary);
  const feedback = document.createElement('div');
  feedback.className = 'roster-import-feedback';
  feedback.setAttribute('aria-live', 'polite');
  const apply = UI.button({
    label: 'Create roster draft', variant: 'primary',
    attributes: { 'data-apply-roster-import': '' },
  });
  const footerStatus = text('span', 'metadata roster-import-review__readiness', 'Checking decisions…');
  const footer = document.createElement('footer');
  footer.className = 'roster-import-review__footer';
  footer.append(footerStatus, apply);
  root.append(
    header,
    ...(repairNote ? [repairNote] : []),
    metrics,
    ...(attachedDossiers ? [attachedDossiers] : []),
    decisionHeader,
    list,
    safeChanges,
    enrich.section,
    feedback,
    footer,
  );
  host.replaceChildren(root);

  const syncReadiness = () => {
    const incomplete = incompleteDecisions(root);
    apply.disabled = incomplete.length > 0;
    footerStatus.textContent = incomplete.length
      ? `${incomplete.length} decision${incomplete.length === 1 ? '' : 's'} still need a valid choice.`
      : `${issues.length} decisions ready · ${safeObservations.length} safe changes will apply automatically.`;
    return incomplete;
  };
  root.querySelectorAll('[data-roster-import-decision], [data-roster-import-target]')
    .forEach((control) => control.addEventListener('change', syncReadiness));
  syncReadiness();

  apply.addEventListener('click', async () => {
    const incomplete = syncReadiness();
    if (incomplete.length) {
      const first = incomplete[0];
      const row = first.closest('.roster-import-row');
      row?.classList.add('roster-import-row--incomplete');
      feedback.replaceChildren(UI.notice({
        tone: 'warning', title: 'A roster decision still needs attention',
        body: 'Choose an action and, for a merge, confirm the existing Cast identity.',
        live: true,
      }));
      row?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      window.setTimeout(() => first.focus(), 180);
      return;
    }
    const decisions = issues.map((issue) => {
      const action = root.querySelector(`[data-roster-import-decision="${CSS.escape(issue.import_id)}"]`)?.value;
      const currentEntry = root.querySelector(`[data-roster-import-target="${CSS.escape(issue.import_id)}"]`)?.value || null;
      return { import_id: issue.import_id, action, current_entry_id: action === 'merge' ? currentEntry : null };
    });
    apply.disabled = true;
    apply.textContent = 'Creating draft…';
    footerStatus.textContent = 'Creating a reviewable roster draft…';
    const response = await api.post('/api/character_roster/reconciliation/apply', {
      candidate_id: focused.candidate_id,
      result_fingerprint: focused.result_fingerprint,
      current_kind: focused.current_kind,
      current_fingerprint: focused.current_fingerprint,
      decisions,
      create_designed_voice_profiles: enrich.voices.querySelector('input')?.checked !== false,
      discover_visual_details: enrich.visuals.querySelector('input')?.checked !== false,
    }, { signal });
    apply.textContent = 'Create roster draft';
    apply.disabled = false;
    if (!response.ok) {
      feedback.replaceChildren(UI.notice({
        tone: 'error', title: 'Roster draft was not created', body: response.error, live: true,
      }));
      footerStatus.textContent = 'No roster changes were applied.';
      feedback.scrollIntoView({ block: 'nearest' });
      return;
    }
    const reconciliation = response.data?.reconciliation || {};
    packageSummary = response.data?.cast_dossier_package || packageSummary;
    await renderRosterDraftApproval({
      api,
      signal,
      host,
      report,
      status: {
        ...reconciliation,
        cast_dossier_package: packageSummary,
      },
    });
  });
}
