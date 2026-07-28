'use strict';

const UI = globalThis.AlexandriaUI;

function context(route) {
  return {
    project_id: route.context.project || null,
    character_id: route.context.character || null,
    return_route: route.hash || '#/library',
  };
}

function dependencyList(blockers = []) {
  const list = document.createElement('ul');
  list.className = 'divider-list';
  blockers.slice(0, 8).forEach((blocker) => {
    const item = document.createElement('li');
    item.textContent = blocker.character_id
      ? `Used by character ${blocker.character_id}`
      : blocker.source ? `Referenced by ${blocker.source}` : 'Referenced by another workflow artifact';
    list.append(item);
  });
  if (blockers.length > 8) {
    const item = document.createElement('li');
    item.textContent = `${blockers.length - 8} additional dependencies`;
    list.append(item);
  }
  return list;
}

function message(result, fallback) {
  const detail = result?.data?.detail;
  return (detail && typeof detail === 'object' ? detail.message : detail)
    || result?.error || fallback;
}

export function libraryDeleteAction({
  artifact, inventoryFingerprint, route, api, signal, onDeleted,
}) {
  if (!artifact.delete?.supported) return null;
  const opener = UI.button({
    label: 'Review deletion',
    variant: 'quiet',
    size: 'compact',
    attributes: { 'data-library-delete-review': artifact.artifact_id },
  });
  opener.addEventListener('click', async () => {
    if (signal.aborted) return;
    opener.disabled = true;
    const impactResult = await api.post(
      `/api/library/artifacts/${encodeURIComponent(artifact.artifact_id)}/delete-impact`,
      context(route),
      { signal },
    );
    opener.disabled = false;
    if (!impactResult.ok || signal.aborted) return;
    const impact = impactResult.data || {};
    const content = document.createElement('div');
    content.className = 'library-delete-impact';
    if (impact.blockers?.length) {
      content.append(
        UI.notice({
          tone: impact.safe_to_delete ? 'information' : 'warning',
          title: impact.safe_to_delete ? 'Dependencies reviewed' : 'Deletion is blocked',
          body: impact.reason || `${impact.blockers.length} dependencies currently reference this artifact.`,
        }),
        dependencyList(impact.blockers),
      );
    }
    if (!impact.safe_to_delete) {
      const dialog = UI.dialog({
        title: `Cannot delete ${impact.name || artifact.name}`,
        body: impact.reason || 'Repair or detach the listed dependencies before deleting this artifact.',
        content,
        confirmLabel: 'Close',
      });
      dialog.open(opener);
      return;
    }
    const confirmation = UI.field({
      id: `library-delete-${artifact.artifact_id}`,
      label: `Type ${impact.confirm_name} to delete`,
      description: 'Deletion uses the current reviewed inventory and artifact fingerprints.',
    });
    const input = confirmation.querySelector('input');
    const feedback = document.createElement('div');
    feedback.className = 'transaction-status';
    feedback.setAttribute('role', 'status');
    feedback.setAttribute('aria-live', 'polite');
    content.append(confirmation, feedback);
    const dialog = UI.dialog({
      title: `Delete ${impact.name}?`,
      body: 'This removes the reusable Library artifact. Project Script, Cast, and generated audio are not silently rewritten.',
      content,
      confirmLabel: 'Delete artifact',
      destructive: true,
      onConfirm: async () => {
        const result = await api.delete(
          `/api/library/artifacts/${encodeURIComponent(artifact.artifact_id)}`,
          {
            signal,
            body: {
              ...context(route),
              expected_inventory_fingerprint: inventoryFingerprint,
              expected_artifact_fingerprint: impact.artifact_fingerprint,
              confirm_name: input.value,
            },
          },
        );
        if (signal.aborted) return;
        if (!result.ok) {
          feedback.textContent = message(result, 'The artifact was not deleted.');
          return;
        }
        await onDeleted?.();
      },
    });
    dialog.open(opener);
    const confirm = dialog.layer?.querySelector('.dialog__footer .ui-button[data-variant="destructive"]');
    const sync = () => { if (confirm) confirm.disabled = input.value !== impact.confirm_name; };
    input.addEventListener('input', sync);
    sync();
    input.focus();
  });
  return opener;
}
