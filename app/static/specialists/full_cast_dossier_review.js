'use strict';

import { textNode } from '/static/pages/more.js';

function auditRow(label, value) {
  const row = document.createElement('div');
  row.className = 'full-cast-task-card full-cast-task-card--audit';
  row.append(
    textNode('strong', '', label),
    textNode('span', 'metadata', value || 'Not applied'),
  );
  return row;
}

function applicationCopy(application, countCopy) {
  if (!application) return 'Selected · not applied';
  const native = application.application || application;
  return [
    'Applied',
    countCopy,
    native.destination && `Destination: ${native.destination}`,
    native.tab && `Tab: ${native.tab}`,
    native.status && `Status: ${native.status}`,
  ].filter(Boolean).join(' · ');
}

function disclosure(title, values) {
  const details = document.createElement('details');
  details.className = 'full-cast-task-advanced';
  details.append(textNode('summary', '', `${title} (${values.length.toLocaleString()})`));
  const list = document.createElement('ul');
  values.forEach((value) => list.append(textNode('li', 'support-status-copy', value)));
  details.append(list);
  return details;
}

export function renderCompletedCastAudit(reconciliation = {}) {
  const packageSummary = reconciliation.cast_dossier_package || reconciliation;
  const selected = packageSummary.selected_sections || {};
  const applications = packageSummary.applications || {};
  const voice = applications.voice_dossiers;
  const visual = applications.visual_dossiers;
  const voiceNative = voice?.application || voice || {};
  const visualNative = visual?.application || visual || {};
  const exclusions = visualNative.excluded_identity_keys || [];
  const crosswalk = visualNative.identity_crosswalk || {};
  const warnings = [...new Set([
    ...(packageSummary.review_warnings || []),
    ...(packageSummary.repair_warnings || []),
  ])];
  const panel = document.createElement('section');
  panel.className = 'complete-cast-bundle complete-cast-bundle--activation';
  panel.dataset.completeCastResume = 'completed';
  panel.append(
    textNode('span', 'metadata task-import-surface__eyebrow', 'Completed whole-book workflow'),
    textNode('h3', '', 'Complete Cast dossier imported'),
    textNode('p', 'support-status-copy',
      'The selected dossier sections are already in their native review areas. This is the saved audit record; no re-import is needed.'),
  );
  const grid = document.createElement('div');
  grid.className = 'full-cast-task-grid';
  grid.append(auditRow(
    'Roster & relationships',
    selected.roster_and_relationships
      ? `Selected · approved roster ${packageSummary.activation?.approved_roster_fingerprint || 'fingerprint retained'}`
      : 'Not selected',
  ));
  grid.append(auditRow(
    'Voice personas & designs',
    selected.voice_personas_and_designs
      ? applicationCopy(voice, `${packageSummary.summary?.voice_dossier_count || 0} dossiers · ${voiceNative.persona_count || 0} personas · ${voiceNative.identity_project_count || 0} identity projects`)
      : 'Not selected',
  ));
  grid.append(auditRow(
    'Visual dossiers',
    selected.visual_dossiers
      ? applicationCopy(visual, `${visualNative.character_count ?? packageSummary.summary?.visual_dossier_count ?? 0} characters · ${visualNative.observation_count ?? packageSummary.summary?.visual_observation_count ?? 0} observations`)
      : 'Not selected',
  ));
  panel.append(grid);
  const components = packageSummary.components || {};
  panel.append(auditRow('Provenance', [
    `Parent ${packageSummary.parent_candidate_id || 'not recorded'}`,
    components.roster_candidate_id && `Roster ${components.roster_candidate_id}`,
    components.persona_candidate_id && `Voice ${components.persona_candidate_id}`,
  ].filter(Boolean).join(' · ')));
  if (Object.keys(crosswalk).length) panel.append(disclosure(
    'Visual identity crosswalk', Object.entries(crosswalk).map(([key, value]) => `${key} → ${value}`),
  ));
  if (exclusions.length) panel.append(disclosure('Retained visual exclusions', exclusions));
  if (warnings.length) panel.append(disclosure('Review and repair notes', warnings));
  const rollback = reconciliation.rollback || {};
  panel.append(auditRow(
    'History and rollback',
    `${Number(reconciliation.revision_count || 0).toLocaleString()} revisions · ${rollback.available ? 'Rollback available' : 'No rollback revision currently available'}${rollback.revision?.revision_id ? ` · ${rollback.revision.revision_id}` : ''}`,
  ));
  return panel;
}
