'use strict';

const UI = globalThis.AlexandriaUI;
const STAGES = Object.freeze(['script', 'cast', 'produce', 'export']);

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function displayProjectTitle(project) {
  return project.name || project.source_title || 'Untitled project';
}

function activityLabel(project) {
  const raw = project.latest_meaningful_activity || project.last_activity_at || project.updated_at;
  if (!raw) return 'Activity time not recorded';
  const value = new Date(raw);
  if (Number.isNaN(value.getTime())) return 'Activity time not recorded';
  return `Last activity: ${new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', year: value.getFullYear() === new Date().getFullYear() ? undefined : 'numeric',
  }).format(value)}`;
}

function projectCover(project, variant) {
  const coverUrl = project.cover_url || project.cover?.url
    || (project.cover?.exists ? `/api/projects/${encodeURIComponent(project.id)}/cover` : '');
  const cover = UI.sourceCover({
    src: coverUrl || null,
    alt: coverUrl ? `Cover for ${displayProjectTitle(project)}` : '',
    label: `No source cover is available for ${displayProjectTitle(project)}`,
    emptyLabel: 'No cover',
  });
  cover.classList.add('project-cover', `project-cover--${variant}`);
  return cover;
}

function normalizedStageState(project, stage) {
  const raw = String(project.stage_states?.[stage] || '').toLowerCase();
  if (stage === project.current_recommended_stage) return 'current';
  if (['complete', 'completed', 'accepted', 'current'].includes(raw)) return 'complete';
  if (raw === 'blocked') return 'blocked';
  return 'future';
}

function compactTracker(project) {
  const tracker = UI.stageTracker({
    label: `${displayProjectTitle(project)} stages`,
    stages: STAGES.map((stage) => ({
      label: `${stage[0].toUpperCase()}${stage.slice(1)}`,
      state: normalizedStageState(project, stage),
    })),
  });
  tracker.classList.add('project-continue__tracker');
  return tracker;
}

function projectStatus(project) {
  const exportState = String(project.stage_states?.export || '').toLowerCase();
  if (['complete', 'completed', 'current'].includes(exportState)) {
    return { label: 'Completed', tone: 'success', action: 'View' };
  }
  if (Number(project.blocker_count) > 0) {
    const count = Number(project.blocker_count);
    return {
      label: `${count} item${count === 1 ? '' : 's'} need attention`,
      tone: 'warning',
      action: 'Resolve',
    };
  }
  if (project.current || project.selected) {
    return { label: 'Next up', tone: 'information', action: 'Resume' };
  }
  return { label: 'Available', tone: 'neutral', action: 'Open Project' };
}

export function projectDetails(project) {
  const detail = document.createElement('div');
  detail.className = 'project-home__details';
  detail.append(
    text('div', 'metadata', 'Project'),
    text('h3', 'entity-title', displayProjectTitle(project)),
    UI.flatSection({
      title: 'Source',
      body: [project.source_author, project.source_filename].filter(Boolean).join(' · ')
        || 'Source details are not available.',
    }),
    UI.flatSection({
      title: 'Next step',
      body: project.stage_summary || `Continue in ${project.current_recommended_stage || 'Script'}.`,
    }),
  );
  return detail;
}

export function projectRow(project, openProject, showDetails, projectOpenAttribute) {
  const row = document.createElement('li');
  row.className = 'project-list__row';
  if (project.current || project.selected) row.dataset.current = '';
  const identity = document.createElement('div');
  identity.className = 'project-list__identity';
  identity.append(
    text('strong', 'entity-title', displayProjectTitle(project)),
    text('span', 'metadata', [project.source_author, project.source_filename].filter(Boolean).join(' · ')
      || 'Source details not available'),
  );
  const state = projectStatus(project);
  const status = document.createElement('div');
  status.className = 'project-list__status';
  status.append(
    UI.status({ tone: state.tone, label: state.label }),
    text('span', 'metadata', project.stage_summary || `Continue in ${project.current_recommended_stage || 'Script'}.`),
  );
  const button = UI.button({
    label: state.action, variant: 'secondary', onClick: () => openProject(project, button),
  });
  button.dataset[projectOpenAttribute] = '';
  const details = UI.iconButton({
    name: 'more',
    label: `Project details for ${displayProjectTitle(project)}`,
    tooltip: 'Project details',
    onClick: () => showDetails(project),
  });
  details.classList.add('project-list__more');
  row.append(projectCover(project, 'row'), identity, status, button, details);
  return row;
}

export function continuationPanel(project, openProject) {
  const section = document.createElement('section');
  section.className = 'project-continue';
  section.dataset.projectContinue = '';
  section.setAttribute('aria-labelledby', 'continue-heading');
  const heading = text('h2', 'section-title', 'Continue where you left off');
  heading.id = 'continue-heading';
  const panel = document.createElement('div');
  panel.className = 'project-continue__panel';
  const identity = document.createElement('div');
  identity.className = 'project-continue__identity';
  identity.append(
    text('h3', 'entity-title', displayProjectTitle(project)),
    text('p', 'metadata', project.source_author ? `by ${project.source_author}` : project.source_filename || 'Source attached'),
    compactTracker(project),
  );
  const next = document.createElement('div');
  next.className = 'project-continue__next';
  const stage = project.current_recommended_stage || 'script';
  next.append(
    text('div', 'utility-heading', 'Next up'),
    text('strong', '', `${stage[0].toUpperCase()}${stage.slice(1)}`),
    text('p', 'metadata', project.stage_summary || 'Continue the current audiobook workflow.'),
    text('span', 'metadata', activityLabel(project)),
  );
  const resume = UI.button({
    label: 'Resume Project', variant: 'secondary', onClick: () => openProject(project, resume),
  });
  resume.dataset.projectResume = '';
  panel.append(projectCover(project, 'continue'), identity, next, resume);
  section.append(heading, panel);
  return section;
}
