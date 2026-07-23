'use strict';

import { createNewProjectController } from './new_project.js';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']);
const dataProjectOpen = 'projectOpen';
const dataNewProjectOpen = 'newProjectOpen';

function text(tag, className, value) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function pageOwner(route) {
  const owner = document.createElement('article');
  owner.className = 'project-flow';
  owner.dataset.routeOwner = route.path;
  owner.dataset.page = route.path;
  const title = UI.pageTitleBlock({
    title: 'Project Home',
    subtitle: 'Continue a book or begin a new audiobook project.',
  });
  title.querySelector('h1').dataset.pageHeading = '';
  owner.append(title);
  return owner;
}

function failureNode(error, retry) {
  return UI.notice({
    tone: 'error',
    title: 'Projects could not load',
    body: error || 'Alexandria could not read the project catalog.',
    live: true,
    action: UI.button({ label: 'Retry', onClick: retry }),
  });
}

function projectRow(project, openProject) {
  const row = document.createElement('li');
  row.className = 'project-list__row';
  if (project.current) row.dataset.current = '';
  const identity = document.createElement('div');
  identity.className = 'project-list__identity';
  identity.append(
    text('strong', 'entity-title', project.name || project.source_title || 'Untitled project'),
    text('span', 'metadata', [project.source_author, project.source_filename].filter(Boolean).join(' · ')),
  );
  const progress = document.createElement('div');
  progress.className = 'project-list__progress';
  progress.append(
    UI.status({
      tone: project.blocker_count ? 'warning' : project.current ? 'success' : 'neutral',
      label: project.current ? 'Current project' : (project.stage_summary || 'Available'),
    }),
    text('span', 'metadata', `Next: ${project.current_recommended_stage || 'script'}`),
  );
  const button = UI.button({
    label: project.current ? 'Continue' : 'Open',
    variant: project.current ? 'primary' : 'secondary',
    onClick: () => openProject(project, button),
  });
  button.dataset[dataProjectOpen] = '';
  row.append(identity, progress, button);
  return row;
}

export async function mount({ root, route, shell, api, signal }) {
  const owner = pageOwner(route);
  const newButton = UI.button({ label: 'New Project', variant: 'primary' });
  newButton.dataset[dataNewProjectOpen] = '';
  owner.querySelector('.page-title-block').append(newButton);
  const toolbar = document.createElement('div');
  toolbar.className = 'page-toolbar';
  const search = UI.searchField({ label: 'Search projects', placeholder: 'Search projects' });
  const filter = UI.field({
    kind: 'select',
    label: 'Show',
    options: [
      { value: 'active', label: 'Active projects' },
      { value: 'all', label: 'All projects' },
      { value: 'archived', label: 'Archived projects' },
    ],
    value: route.context.filter || 'active',
  });
  toolbar.append(search, filter);
  const content = document.createElement('section');
  content.className = 'content-state';
  content.dataset.state = STATES[0];
  content.append(UI.skeleton({ label: 'Loading projects' }), UI.skeleton());
  owner.append(toolbar, content);
  root.replaceChildren(owner);
  shell.player.set({ state: 'inactive', title: 'No audio selected' });
  shell.inspector.set({ state: 'collapsed', title: 'Project details', content: null });

  let catalog = { catalog_fingerprint: '', projects: [] };
  let disposed = false;
  let newProject = null;

  const render = () => {
    if (disposed || signal.aborted) return;
    const query = search.querySelector('input').value.trim().toLocaleLowerCase();
    const mode = filter.querySelector('select').value;
    const projects = (catalog.projects || []).filter((project) => {
      const archived = project.archive_state === 'archived';
      const included = mode === 'all' || (mode === 'archived' ? archived : !archived);
      const haystack = `${project.name || ''} ${project.source_title || ''} ${project.source_author || ''}`.toLocaleLowerCase();
      return included && (!query || haystack.includes(query));
    });
    content.replaceChildren();
    content.dataset.state = projects.length > 8 ? STATES[4] : STATES[3];
    if (!projects.length) {
      content.dataset.state = STATES[1];
      content.append(UI.emptyState({
        title: query ? 'No matching projects' : 'No projects yet',
        body: query ? 'Try another search or filter.' : 'Create a project from an EPUB, text file, or existing Script.',
        action: UI.button({ label: 'New Project', variant: 'primary', onClick: () => newProject?.open(newButton) }),
      }));
      return;
    }
    const list = document.createElement('ul');
    list.className = 'project-list';
    projects.forEach((project) => list.append(projectRow(project, openProject)));
    content.append(list);
  };

  const load = async () => {
    content.dataset.state = STATES[0];
    content.replaceChildren(UI.skeleton({ label: 'Loading projects' }), UI.skeleton());
    const result = await api.get('/api/projects', { signal });
    if (disposed || signal.aborted) return;
    if (!result.ok) {
      content.dataset.state = STATES[2];
      content.replaceChildren(failureNode(result.error, load));
      return;
    }
    catalog = result.data || catalog;
    render();
  };

  async function openProject(project, button) {
    button.disabled = true;
    const result = await api.post(`/api/projects/${encodeURIComponent(project.id)}/open`, {
      expected_catalog_fingerprint: catalog.catalog_fingerprint,
    }, { signal });
    if (disposed || signal.aborted) return;
    button.disabled = false;
    if (!result.ok) {
      content.prepend(UI.notice({
        tone: 'error', title: 'Project could not open', body: result.error, live: true,
      }));
      return;
    }
    const destination = result.data?.activation?.native_destination
      || result.data?.native_destination || project.current_recommended_stage || 'script';
    shell.navigate(shell.routes.routeForPath(destination, { project: project.id }).hash);
  }

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
  newButton.addEventListener('click', openNew);
  search.querySelector('input').addEventListener('input', render);
  filter.querySelector('select').addEventListener('change', render);
  await load();
  if (route.context.mode === 'new' && !signal.aborted) openNew();

  return () => {
    if (disposed) return;
    disposed = true;
    newButton.removeEventListener('click', openNew);
    newProject?.cleanup();
  };
}
