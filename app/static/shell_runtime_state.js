'use strict';

export function createShellRuntimeState({
  api,
  assetVersion,
  projectDisplayTitle,
}) {
  const projectTitleCache = new Map();
  const projectRecordCache = new Map();
  const pageModuleCache = new Map();
  let cachedProjectCatalog = null;
  let cachedCurrentProjectId = '';
  let cachedSelectedProjectId = '';
  let lastAssetCheckAt = 0;
  let assetCheckPromise = null;

  async function ensureCurrentAssets(force = false) {
    if (!assetVersion) return true;
    const now = Date.now();
    if (!force && now - lastAssetCheckAt < 2000) return true;
    if (assetCheckPromise) return assetCheckPromise;
    lastAssetCheckAt = now;
    assetCheckPromise = api.get('/api/runtime_status', { timeout: 2500 })
      .then((result) => {
        const current = String(result.data?.static_asset_version || '');
        if (result.ok && current && current !== assetVersion) {
          location.reload();
          return false;
        }
        return true;
      })
      .finally(() => { assetCheckPromise = null; });
    return assetCheckPromise;
  }

  function rememberProjectCatalog(catalog) {
    cachedProjectCatalog = catalog && typeof catalog === 'object'
      ? structuredClone(catalog)
      : null;
    const projects = Array.isArray(catalog?.projects) ? catalog.projects : [];
    projectRecordCache.clear();
    projects.forEach((project) => {
      const identifier = String(project?.id || '').trim();
      if (!identifier) return;
      projectRecordCache.set(identifier, project);
      const title = typeof projectDisplayTitle === 'function'
        ? projectDisplayTitle(project, '')
        : project?.name || project?.source_title || '';
      if (title && title !== 'Project workspace') {
        projectTitleCache.set(identifier, title);
      }
    });
    cachedCurrentProjectId = String(catalog?.current_project_id || '').trim();
    cachedSelectedProjectId = String(catalog?.last_selected_project_id || '').trim();
  }

  function projectCatalog() {
    return cachedProjectCatalog ? structuredClone(cachedProjectCatalog) : null;
  }

  async function resolveProjectRoute(route, signal) {
    if (route.shellMode !== 'project') return route;
    const requestedProjectId = route.context.project || '';
    const cachedProjectId = requestedProjectId
      || cachedCurrentProjectId || cachedSelectedProjectId;
    const cachedProject = projectRecordCache.get(cachedProjectId) || null;
    if (cachedProject) {
      const cachedTitle = typeof projectDisplayTitle === 'function'
        ? projectDisplayTitle(cachedProject, '')
        : cachedProject.name || cachedProject.source_title || 'Project workspace';
      return Object.freeze({
        ...route,
        projectId: cachedProjectId,
        projectTitle: cachedTitle || projectTitleCache.get(cachedProjectId)
          || 'Project workspace',
        project: cachedProject,
      });
    }
    const result = await api.get('/api/projects', { signal, timeout: 5000 });
    if (!result.ok || signal.aborted) {
      return Object.freeze({
        ...route,
        projectId: requestedProjectId,
        projectTitle: projectTitleCache.get(requestedProjectId)
          || 'Project workspace',
        project: null,
      });
    }
    const catalog = result.data || {};
    rememberProjectCatalog(catalog);
    const projects = Array.isArray(catalog.projects) ? catalog.projects : [];
    const selectedId = requestedProjectId || catalog.current_project_id
      || catalog.last_selected_project_id || '';
    const project = projects.find((item) => item.id === selectedId)
      || projects.find((item) => item.current)
      || projects.find((item) => item.selected)
      || projects[0]
      || null;
    const resolvedId = project?.id || selectedId;
    const resolvedTitle = typeof projectDisplayTitle === 'function'
      ? projectDisplayTitle(project, '')
      : project?.name || project?.source_title || 'Project workspace';
    if (resolvedId && resolvedTitle && resolvedTitle !== 'Project workspace') {
      projectTitleCache.set(resolvedId, resolvedTitle);
    }
    return Object.freeze({
      ...route,
      projectId: resolvedId,
      projectTitle: resolvedTitle || projectTitleCache.get(resolvedId)
        || 'Project workspace',
      project,
    });
  }

  async function loadPage(modulePath, signal, RouteFailure) {
    const cached = pageModuleCache.get(modulePath);
    if (cached) return cached;
    let response;
    try {
      response = await fetch(modulePath, {
        method: 'HEAD',
        cache: 'no-store',
        signal,
      });
      await response.text();
    } catch (error) {
      if (signal.aborted) {
        throw new RouteFailure('canceled', 'Navigation canceled.', error);
      }
      throw new RouteFailure(
        'network',
        'Module availability request failed.',
        error,
      );
    }
    if (!response.ok) {
      throw new RouteFailure(
        'missing',
        `Module unavailable (${response.status}).`,
      );
    }
    try {
      const page = await import(modulePath);
      pageModuleCache.set(modulePath, page);
      return page;
    } catch (error) {
      if (signal.aborted) {
        throw new RouteFailure('canceled', 'Navigation canceled.', error);
      }
      throw new RouteFailure('module', 'Module evaluation failed.', error);
    }
  }

  return Object.freeze({
    ensureCurrentAssets,
    loadPage,
    projectCatalog,
    projectTitleCache,
    rememberProjectCatalog,
    resolveProjectRoute,
  });
}
