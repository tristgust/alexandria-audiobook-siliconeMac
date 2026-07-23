'use strict';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

function projectFingerprint(project) {
  return project.technical_details?.manifest_fingerprint || project.project_fingerprint || '';
}

function impactContent(project, impact) {
  const content = document.createElement('div');
  content.className = 'project-action-dialog__content';
  content.append(
    UI.flatSection({
      title: 'Project',
      body: project.name || project.source_title || project.id,
    }),
    UI.flatSection({
      title: 'What moves to Trash',
      body: impact.summary || impact.message
        || 'The managed project and its project-local artifacts will be moved to Trash and remain recoverable.',
    }),
  );
  const facts = document.createElement('dl');
  facts.className = 'project-action-dialog__facts';
  [
    ['Recoverable', impact.recoverable_delete === false ? 'No' : 'Yes'],
    ['Library dependencies', String(impact.library_dependency_count ?? impact.dependencies?.length ?? 0)],
  ].forEach(([label, value]) => {
    const row = document.createElement('div');
    row.append(text('dt', '', label), text('dd', '', value));
    facts.append(row);
  });
  content.append(facts);
  return content;
}

export function createProjectHomeActions({
  api, shell, signal, getCatalog, reload, reportError,
}) {
  const duplicate = (project, opener) => {
    const field = UI.field({
      label: 'Duplicate project name',
      value: `${project.name || project.source_title || 'Project'} copy`,
      required: true,
    });
    const input = field.querySelector('input');
    const dialog = UI.dialog({
      title: 'Duplicate project',
      body: 'Create a separate project with the current project artifacts and settings.',
      content: field,
      confirmLabel: 'Duplicate',
      onConfirm: async () => {
        const name = input.value.trim();
        if (!name) return;
        const result = await api.post(`/api/projects/${encodeURIComponent(project.id)}/duplicate`, {
          name,
          expected_catalog_fingerprint: getCatalog().catalog_fingerprint,
        }, { signal });
        if (!result.ok) reportError('Project could not be duplicated', result.error);
        else await reload();
      },
    });
    dialog.open(opener);
    requestAnimationFrame(() => { input.focus(); input.select(); });
  };

  const archive = (project, opener) => {
    const archived = project.archive_state === 'archived';
    const dialog = UI.dialog({
      title: archived ? 'Restore project' : 'Archive project',
      body: archived
        ? 'Return this project to the active project list.'
        : 'Archive this project. Its files and generated artifacts will remain intact.',
      confirmLabel: archived ? 'Restore' : 'Archive',
      onConfirm: async () => {
        const result = await api.post(`/api/projects/${encodeURIComponent(project.id)}/archive`, {
          archived: !archived,
          expected_catalog_fingerprint: getCatalog().catalog_fingerprint,
          expected_project_fingerprint: projectFingerprint(project),
        }, { signal });
        if (!result.ok) reportError(archived ? 'Project could not be restored' : 'Project could not be archived', result.error);
        else await reload();
      },
    });
    dialog.open(opener);
  };

  const remove = async (project, opener) => {
    const impact = await api.get(`/api/projects/${encodeURIComponent(project.id)}/delete-impact`, { signal });
    if (!impact.ok) {
      reportError('Project delete impact could not be loaded', impact.error);
      return;
    }
    const dialog = UI.dialog({
      title: 'Move project to Trash',
      body: 'Review the impact before moving this archived project to Trash.',
      content: impactContent(project, impact.data || {}),
      confirmLabel: 'Move to Trash',
      destructive: true,
      onConfirm: async () => {
        const result = await api.post(`/api/projects/${encodeURIComponent(project.id)}/delete`, {
          confirm_project_id: project.id,
          expected_catalog_fingerprint: getCatalog().catalog_fingerprint,
          expected_project_fingerprint: projectFingerprint(project),
          confirm_dependencies: true,
        }, { signal });
        if (!result.ok) reportError('Project could not be moved to Trash', result.error);
        else await reload();
      },
    });
    dialog.open(opener);
  };

  return Object.freeze({ duplicate, archive, remove });
}
