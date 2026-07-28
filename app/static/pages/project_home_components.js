'use strict';

const UI = globalThis.AlexandriaUI;
const STAGES = Object.freeze(['script', 'cast', 'produce', 'export']);

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function projectHomeOwner(route) {
  const owner = document.createElement('article');
  owner.className = 'project-flow project-home';
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = route.path;
  const heading = text('h1', 'visually-hidden', 'Project Home');
  heading.dataset.pageHeading = '';
  owner.append(heading);
  return owner;
}

export function projectHomeFailure(error, retry) {
  return UI.notice({
    tone: 'error',
    title: 'Projects could not load',
    body: error || 'Alexandria could not read the project catalog.',
    live: true,
    action: UI.button({ label: 'Retry', variant: 'secondary', onClick: retry }),
  });
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

function stageLabel(stage) {
  return `${stage[0].toUpperCase()}${stage.slice(1)}`;
}

function compactTracker(project) {
  const tracker = UI.stageTracker({
    label: `${displayProjectTitle(project)} stages`,
    stages: STAGES.map((stage) => ({
      label: stageLabel(stage),
      state: normalizedStageState(project, stage),
    })),
  });
  tracker.classList.add('project-continue__tracker');
  return tracker;
}

function miniTracker(project) {
  const tracker = document.createElement('ol');
  tracker.className = 'project-mini-tracker';
  tracker.setAttribute('aria-label', `${displayProjectTitle(project)} stages`);
  STAGES.forEach((stage) => {
    const state = normalizedStageState(project, stage);
    const item = document.createElement('li');
    item.dataset.state = state;
    const marker = document.createElement('span');
    marker.setAttribute('aria-hidden', 'true');
    marker.append(UI.icon(state === 'complete' ? 'check' : state === 'blocked' ? 'stage-blocked' : 'future'));
    const label = text('small', '', stageLabel(stage));
    item.append(marker, label);
    tracker.append(item);
  });
  return tracker;
}

function projectStatus(project) {
  const safeAction = project.safe_next_action || {};
  const stage = safeAction.native_destination || project.current_recommended_stage || 'script';
  const contextLabel = safeAction.label || `Open ${stage[0].toUpperCase()}${stage.slice(1)}`;
  const exportState = String(project.stage_states?.export || '').toLowerCase();
  if (['complete', 'completed', 'current'].includes(exportState)) {
    return {
      label: 'Completed', tone: 'success', action: 'View',
      contextLabel: 'Open in Library', destination: 'library',
    };
  }
  if (Number(project.blocker_count) > 0) {
    const count = Number(project.blocker_count);
    return {
      label: `${count} item${count === 1 ? '' : 's'} need attention`,
      tone: 'warning', action: 'Resolve', contextLabel, destination: stage,
    };
  }
  if (project.current || project.selected) {
    return {
      label: 'Next up', tone: 'information', action: 'Resume',
      contextLabel, destination: stage,
    };
  }
  return {
    label: project.completion_state === 'recently_created' ? 'Recently created' : 'Available',
    tone: 'neutral', action: 'Open Project', contextLabel, destination: stage,
  };
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

export function projectRow(project, actions, projectOpenAttribute) {
  const row = document.createElement('li');
  row.className = 'project-list__row';
  if (project.current || project.selected) row.dataset.current = '';
  const state = projectStatus(project);
  const coverButton = document.createElement('button');
  coverButton.type = 'button';
  coverButton.className = 'project-list__cover-action';
  coverButton.setAttribute('aria-label', `Open ${displayProjectTitle(project)}`);
  coverButton.append(projectCover(project, 'row'));
  coverButton.addEventListener('click', () => actions.open(project, coverButton, state.destination));
  const identity = document.createElement('div');
  identity.className = 'project-list__identity';
  const titleButton = document.createElement('button');
  titleButton.type = 'button';
  titleButton.className = 'project-list__title entity-title';
  titleButton.textContent = displayProjectTitle(project);
  titleButton.addEventListener('click', () => actions.open(project, titleButton, state.destination));
  identity.append(
    titleButton,
    text('span', 'metadata', [project.source_author, project.source_filename].filter(Boolean).join(' · ')
      || 'Source details not available'),
    text('span', 'metadata project-list__activity', activityLabel(project)),
    miniTracker(project),
  );
  const status = document.createElement('div');
  status.className = 'project-list__status';
  status.dataset.state = state.tone;
  const contextLink = document.createElement('button');
  contextLink.type = 'button';
  contextLink.className = 'project-list__context-link';
  contextLink.textContent = state.contextLabel;
  contextLink.addEventListener('click', () => actions.open(project, contextLink, state.destination));
  status.append(
    UI.status({ tone: state.tone, label: state.label }),
    text('span', 'metadata', project.stage_summary || `Continue in ${project.current_recommended_stage || 'Script'}.`),
    contextLink,
  );
  const next = document.createElement('div');
  next.className = 'project-list__next';
  next.append(
    text('span', 'utility-heading', 'Next'),
    text('strong', '', stageLabel(state.destination === 'library' ? 'export' : state.destination)),
  );
  const button = UI.button({
    label: state.action,
    variant: 'secondary',
    onClick: () => actions.open(project, button, state.destination),
  });
  button.dataset[projectOpenAttribute] = '';
  const opener = UI.iconButton({
    name: 'more',
    label: `More actions for ${displayProjectTitle(project)}`,
    tooltip: 'More actions',
  });
  opener.classList.add('project-list__more');
  const menuItems = [
    { label: 'Project details', onSelect: () => actions.details(project) },
    { label: 'Duplicate project', onSelect: () => actions.duplicate(project, opener) },
  ];
  if (!(project.current || project.selected) || project.archive_state === 'archived') {
    menuItems.push({
      label: project.archive_state === 'archived' ? 'Restore project' : 'Archive project',
      onSelect: () => actions.archive(project, opener),
    });
  }
  if (project.archive_state === 'archived') {
    menuItems.push({ label: 'Move to Trash…', onSelect: () => actions.remove(project, opener) });
  }
  const overflow = UI.popover({
    opener,
    label: `Project actions for ${displayProjectTitle(project)}`,
    items: menuItems,
  });
  overflow.classList.add('project-list__overflow');
  row.append(coverButton, identity, status, next, button, overflow);
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
    text('div', 'utility-heading', 'Current audiobook'),
    text('h3', 'entity-title', displayProjectTitle(project)),
    text('p', 'metadata', project.source_author ? `by ${project.source_author}` : project.source_filename || 'Source attached'),
    compactTracker(project),
    text('span', 'metadata project-continue__activity', activityLabel(project)),
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
