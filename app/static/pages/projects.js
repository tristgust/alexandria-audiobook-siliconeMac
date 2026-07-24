'use strict';

import { createNewProjectController } from './new_project.js';
import { createProjectHomeActions } from './project_home_actions.js';
import {
  continuationPanel, displayProjectTitle, projectDetails, projectHomeFailure,
  projectHomeOwner, projectRow,
} from './project_home_components.js';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);
const dataProjectOpen = 'projectOpen', dataNewProjectOpen = 'newProjectOpen';
const PROJECT_SORT_KEY = 'alexandria.projects.sort', PROJECT_FILTER_KEY = 'alexandria.projects.filter';
const SEARCH_DEBOUNCE_MS = 250;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export async function mount({ root, route, shell, api, signal }) {
  const owner = projectHomeOwner(route);
  const newButton = UI.button({ label: 'New Project', variant: 'primary' });
  newButton.dataset[dataNewProjectOpen] = '';
  const search = UI.searchField({ label: 'Search projects', placeholder: 'Search projects…' });
  search.classList.add('project-home__search');
  search.querySelector('.field__label')?.classList.add('visually-hidden');
  shell.globalHeader.set({
    eyebrow: 'Alexandria',
    title: 'Project Home',
    subtitle: 'Open an existing project or create a new one.',
    actions: [search, newButton],
  });

  const continuation = document.createElement('div');
  continuation.className = 'project-home__continuation';
  const allProjects = document.createElement('section');
  allProjects.className = 'project-home__all';
  allProjects.setAttribute('aria-labelledby', 'all-projects-heading');
  const sectionHeader = document.createElement('header');
  sectionHeader.className = 'project-home__section-header';
  const sectionTitle = text('h2', 'section-title', 'All projects');
  sectionTitle.id = 'all-projects-heading';
  const controls = document.createElement('div');
  controls.className = 'project-home__controls';
  const sort = UI.field({
    kind: 'select',
    label: 'Sort by',
    options: [
      { value: 'activity', label: 'Last activity' },
      { value: 'title', label: 'Title' },
    ],
    value: route.context.sort || sessionStorage.getItem(PROJECT_SORT_KEY) || 'activity',
  });
  const filter = UI.field({
    kind: 'select',
    label: 'Filter',
    options: [
      { value: 'all', label: 'All' },
      { value: 'attention', label: 'Needs attention' },
      { value: 'complete', label: 'Completed' },
      { value: 'archived', label: 'Archived' },
    ],
    value: route.context.filter || sessionStorage.getItem(PROJECT_FILTER_KEY) || 'all',
  });
  controls.append(sort, filter);
  sectionHeader.append(sectionTitle, controls);
  const resultsStatus = text('p', 'metadata project-results-status', 'Loading projects…');
  resultsStatus.setAttribute('role', 'status');
  resultsStatus.setAttribute('aria-live', 'polite');
  const content = document.createElement('div');
  content.className = 'content-state';
  content.dataset.state = STATES[0];
  content.append(UI.skeleton({ label: 'Loading projects' }), UI.skeleton());
  allProjects.append(sectionHeader, resultsStatus, content);
  owner.append(continuation, allProjects);
  root.replaceChildren(owner);
  shell.player.set({ state: 'inactive', title: 'No track selected', subtitle: 'Choose a chapter, voice, or audio take to begin' });
  shell.inspector.set({ state: 'hidden', title: 'Project details', content: null });

  let catalog = { catalog_fingerprint: '', projects: [] }, newProject = null;
  let disposed = false, searchTimer = null;
  const searchInput = search.querySelector('input'), sortSelect = sort.querySelector('select');
  const filterSelect = filter.querySelector('select');

  const resumableProject = () => {
    const projects = catalog.projects || [];
    const preferredId = catalog.current_project_id || catalog.last_selected_project_id;
    return projects.find((project) => project.id === preferredId && project.archive_state !== 'archived')
      || projects.find((project) => (project.current || project.selected) && project.archive_state !== 'archived')
      || null;
  };

  const showDetails = (project) => {
    shell.inspector.set({ state: 'open', title: 'Project details', content: projectDetails(project) });
  };
  const cleanupProjectRows = () => {
    content.querySelectorAll('.popover-controller').forEach((node) => node.popoverCleanup?.());
  };
  const reportError = (title, body) => {
    owner.querySelector('[data-project-action-error]')?.remove();
    const notice = UI.notice({ tone: 'error', title, body, live: true });
    notice.dataset.projectActionError = '';
    allProjects.prepend(notice);
    notice.focus?.();
  };

  const render = () => {
    if (disposed || signal.aborted) return;
    const query = searchInput.value.trim().toLocaleLowerCase();
    const mode = filterSelect.value;
    const projects = [...(catalog.projects || [])].filter((project) => {
      const archived = project.archive_state === 'archived';
      const complete = ['complete', 'completed', 'current'].includes(
        String(project.stage_states?.export || '').toLowerCase(),
      );
      const included = mode === 'all' || (mode === 'archived' && archived)
        || (mode === 'attention' && !archived && Number(project.blocker_count) > 0)
        || (mode === 'complete' && !archived && complete);
      const haystack = `${project.name || ''} ${project.source_title || ''} ${project.source_author || ''} ${project.source_filename || ''}`.toLocaleLowerCase();
      return included && (!query || haystack.includes(query));
    });
    projects.sort((left, right) => {
      if (sortSelect.value === 'title') return displayProjectTitle(left).localeCompare(displayProjectTitle(right));
      const leftActivity = Date.parse(left.latest_meaningful_activity || left.last_activity_at || left.updated_at || '') || 0;
      const rightActivity = Date.parse(right.latest_meaningful_activity || right.last_activity_at || right.updated_at || '') || 0;
      return rightActivity - leftActivity || displayProjectTitle(left).localeCompare(displayProjectTitle(right));
    });

    continuation.replaceChildren();
    const current = resumableProject();
    if (current) continuation.append(continuationPanel(current, openProject));

    cleanupProjectRows();
    content.replaceChildren();
    content.dataset.state = projects.length > 8 ? STATES[4] : STATES[3];
    resultsStatus.textContent = `${projects.length} project${projects.length === 1 ? '' : 's'}`;
    if (!projects.length) {
      content.dataset.state = STATES[1];
      content.append(UI.emptyState({
        title: query ? 'No projects match this search' : 'No projects yet',
        body: query ? 'Clear the search or choose another filter.' : 'Create your first project to start building an audiobook.',
        action: query
          ? UI.button({
            label: 'Clear search', variant: 'quiet', onClick: () => {
              searchInput.value = '';
              render();
              searchInput.focus();
            },
          })
          : UI.button({ label: 'New Project', variant: 'primary', onClick: () => newProject?.open(newButton) }),
      }));
      return;
    }
    const list = document.createElement('ul');
    list.className = 'project-list';
    const actions = {
      open: openProject,
      details: showDetails,
      duplicate: projectActions.duplicate,
      archive: projectActions.archive,
      remove: projectActions.remove,
    };
    projects.forEach((project) => list.append(
      projectRow(project, actions, dataProjectOpen),
    ));
    content.append(list);
  };

  const load = async () => {
    content.dataset.state = STATES[0];
    resultsStatus.textContent = 'Loading projects…';
    content.replaceChildren(UI.skeleton({ label: 'Loading projects' }), UI.skeleton());
    const result = await api.get('/api/projects', { signal });
    if (disposed || signal.aborted) return;
    if (!result.ok) {
      content.dataset.state = STATES[2];
      resultsStatus.textContent = 'Projects unavailable';
      content.replaceChildren(projectHomeFailure(result.error, load));
      return;
    }
    catalog = result.data || catalog;
    render();
  };

  async function openProject(project, button, destinationOverride = '') {
    button.disabled = true;
    const originalLabel = button.textContent;
    button.textContent = 'Opening…';
    const result = await api.post(`/api/projects/${encodeURIComponent(project.id)}/open`, {
      expected_catalog_fingerprint: catalog.catalog_fingerprint,
    }, { signal });
    if (disposed || signal.aborted) return;
    button.disabled = false;
    button.textContent = originalLabel;
    if (!result.ok) {
      content.prepend(UI.notice({
        tone: 'error', title: 'Could not open project', body: result.error, live: true,
      }));
      return;
    }
    const destination = destinationOverride || result.data?.activation?.native_destination
      || result.data?.native_destination || project.current_recommended_stage || 'script';
    shell.navigate(shell.routes.routeForPath(destination, { project: project.id }).hash);
  }

  const projectActions = createProjectHomeActions({
    api, shell, signal,
    getCatalog: () => catalog,
    reload: load,
    reportError,
  });

  newProject = createNewProjectController({
    shell, api, signal,
    templateId: route.context.mode === 'new' ? route.context.source : '',
    getCatalogFingerprint: () => catalog.catalog_fingerprint,
    onCreated: (project, destination) => {
      if (project) catalog.projects = [project, ...(catalog.projects || []).filter((item) => item.id !== project.id)];
      render();
      shell.navigate(shell.routes.routeForPath(destination || 'script', { project: project?.id }).hash);
    },
  });
  const openNew = () => newProject.open(newButton);
  const onSearchInput = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(render, SEARCH_DEBOUNCE_MS);
  };
  const onSortChange = () => {
    sessionStorage.setItem(PROJECT_SORT_KEY, sortSelect.value);
    render();
  };
  const onFilterChange = () => {
    sessionStorage.setItem(PROJECT_FILTER_KEY, filterSelect.value);
    render();
  };
  const onSearchKeydown = (event) => {
    if (event.key !== 'Escape' || !searchInput.value) return;
    event.preventDefault();
    searchInput.value = '';
    render();
  };
  newButton.addEventListener('click', openNew);
  searchInput.addEventListener('input', onSearchInput);
  searchInput.addEventListener('keydown', onSearchKeydown);
  sortSelect.addEventListener('change', onSortChange);
  filterSelect.addEventListener('change', onFilterChange);
  await load();
  if (route.context.mode === 'new' && !signal.aborted) openNew();

  return () => {
    if (disposed) return;
    disposed = true;
    newButton.removeEventListener('click', openNew);
    clearTimeout(searchTimer);
    searchInput.removeEventListener('input', onSearchInput);
    searchInput.removeEventListener('keydown', onSearchKeydown);
    sortSelect.removeEventListener('change', onSortChange);
    filterSelect.removeEventListener('change', onFilterChange);
    cleanupProjectRows();
    newProject?.cleanup();
    shell.inspector.hide();
  };
}
