'use strict';

function row(text, label, value) {
  const node = document.createElement('div');
  node.className = 'stable-task-card stable-task-card--audit';
  node.append(text('strong', label), text('span', value || 'Not applied', 'stable-task-muted'));
  return node;
}

function disclosure(text, title, values) {
  const details = document.createElement('details');
  details.className = 'stable-individual-tasks';
  details.append(text('summary', `${title} (${values.length.toLocaleString()})`));
  const list = document.createElement('ul');
  values.forEach((value) => list.append(text('li', value, 'stable-task-muted')));
  details.append(list);
  return details;
}

export function renderStableCompletedCast({ reconciliation, text }) {
  const packageSummary = reconciliation.cast_dossier_package || reconciliation;
  const selected = packageSummary.selected_sections || {};
  const applications = packageSummary.applications || {};
  const voice = applications.voice_dossiers?.application || applications.voice_dossiers;
  const visual = applications.visual_dossiers?.application || applications.visual_dossiers;
  const panel = document.createElement('section');
  panel.className = 'stable-task-section';
  panel.dataset.completeCastResume = 'completed';
  panel.append(
    text('span', 'Completed whole-book workflow', 'stable-task-eyebrow'),
    text('h3', 'Complete Cast dossier imported'),
    text('p', 'The selected dossier sections are already in their native review areas. This is the saved audit record; no re-import is needed.', 'stable-task-muted'),
  );
  const grid = document.createElement('div');
  grid.className = 'stable-task-grid';
  grid.append(row(text, 'Roster & relationships', selected.roster_and_relationships
    ? `Selected · approved roster ${packageSummary.activation?.approved_roster_fingerprint || 'fingerprint retained'}` : 'Not selected'));
  grid.append(row(text, 'Voice personas & designs', !selected.voice_personas_and_designs
    ? 'Not selected' : voice
      ? `Applied · ${packageSummary.summary?.voice_dossier_count || 0} dossiers · ${voice.persona_count || 0} personas · ${voice.identity_project_count || 0} identity projects · Destination: ${voice.destination || 'Voice review'}`
      : 'Selected · not applied'));
  grid.append(row(text, 'Visual dossiers', !selected.visual_dossiers
    ? 'Not selected' : visual
      ? `Applied · ${visual.character_count ?? packageSummary.summary?.visual_dossier_count ?? 0} characters · ${visual.observation_count ?? packageSummary.summary?.visual_observation_count ?? 0} observations · Destination: ${visual.destination || 'Visual review'}`
      : 'Selected · not applied'));
  panel.append(grid);
  panel.append(row(text, 'Provenance', [
    `Parent ${packageSummary.parent_candidate_id || 'not recorded'}`,
    packageSummary.components?.roster_candidate_id && `Roster ${packageSummary.components.roster_candidate_id}`,
    packageSummary.components?.persona_candidate_id && `Voice ${packageSummary.components.persona_candidate_id}`,
  ].filter(Boolean).join(' · ')));
  const crosswalk = visual?.identity_crosswalk || {};
  const exclusions = visual?.excluded_identity_keys || [];
  const warnings = [...new Set([...(packageSummary.review_warnings || []), ...(packageSummary.repair_warnings || [])])];
  if (Object.keys(crosswalk).length) panel.append(disclosure(text, 'Visual identity crosswalk',
    Object.entries(crosswalk).map(([key, value]) => `${key} → ${value}`)));
  if (exclusions.length) panel.append(disclosure(text, 'Retained visual exclusions', exclusions));
  if (warnings.length) panel.append(disclosure(text, 'Review and repair notes', warnings));
  panel.append(row(text, 'History and rollback',
    `${Number(reconciliation.revision_count || 0).toLocaleString()} revisions · ${reconciliation.rollback?.available ? 'Rollback available' : 'No rollback revision currently available'}`));
  return panel;
}
