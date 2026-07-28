'use strict';

const UI = globalThis.AlexandriaUI;

function message(result, fallback) {
  const detail = result?.data?.detail;
  return (detail && typeof detail === 'object' ? detail.message : detail)
    || result?.error || fallback;
}

export function createTemplateActions({
  template, getCatalog, api, signal, onChanged, onEdit, onUse,
}) {
  const root = document.createElement('div');
  root.className = 'template-actions';
  const feedback = document.createElement('div');
  feedback.className = 'transaction-status';
  feedback.setAttribute('role', 'status');
  feedback.setAttribute('aria-live', 'polite');

  root.append(UI.button({
    label: 'Use Template',
    variant: 'primary',
    onClick: () => onUse(template),
  }));
  if (template.editable) {
    root.append(UI.button({
      label: 'Edit',
      variant: 'secondary',
      onClick: (event) => onEdit(template, event.currentTarget),
    }));
  }
  if (template.duplicable) {
    const duplicate = UI.button({ label: 'Duplicate', variant: 'secondary' });
    duplicate.addEventListener('click', () => {
      const name = UI.field({
        id: `template-duplicate-${template.id}`,
        label: 'Copy name',
        value: `${template.name} copy`,
      });
      const input = name.querySelector('input');
      const dialog = UI.dialog({
        title: `Duplicate ${template.name}`,
        body: 'The copy starts with the same production intent and can be edited independently.',
        content: name,
        confirmLabel: 'Duplicate Template',
        onConfirm: async () => {
          const result = await api.post(`/api/templates/${encodeURIComponent(template.id)}/duplicate`, {
            expected_catalog_fingerprint: getCatalog().catalog_fingerprint,
            name: input.value.trim(),
          }, { signal });
          if (signal.aborted) return;
          feedback.textContent = result.ok
            ? 'Template duplicated.' : message(result, 'The template was not duplicated.');
          if (result.ok) await onChanged(result.data, result.data?.template || null);
        },
      });
      dialog.open(duplicate);
      input.focus();
    });
    root.append(duplicate);
  }
  if (!template.default) {
    const makeDefault = UI.button({ label: 'Set as Default', variant: 'quiet' });
    makeDefault.addEventListener('click', async () => {
      makeDefault.disabled = true;
      const result = await api.post(`/api/templates/${encodeURIComponent(template.id)}/default`, {
        expected_catalog_fingerprint: getCatalog().catalog_fingerprint,
      }, { signal });
      makeDefault.disabled = false;
      if (signal.aborted) return;
      feedback.textContent = result.ok
        ? 'Default template updated.' : message(result, 'The default template was not changed.');
      if (result.ok) await onChanged(result.data, result.data?.templates?.find((item) => item.id === template.id));
    });
    root.append(makeDefault);
  }
  if (template.deletable) {
    const remove = UI.button({ label: 'Review deletion', variant: 'quiet' });
    remove.addEventListener('click', async () => {
      remove.disabled = true;
      const impactResult = await api.get(`/api/templates/${encodeURIComponent(template.id)}/delete-impact`, { signal });
      remove.disabled = false;
      if (!impactResult.ok || signal.aborted) {
        feedback.textContent = message(impactResult, 'Delete impact could not be loaded.');
        return;
      }
      const impact = impactResult.data || {};
      const content = document.createElement('div');
      content.className = 'template-delete-impact';
      if (impact.usage_count) {
        content.append(UI.notice({
          tone: 'information',
          title: `${impact.usage_count} existing project${impact.usage_count === 1 ? '' : 's'} used this template`,
          body: impact.message,
        }));
      }
      if (!impact.safe_to_delete) {
        const dialog = UI.dialog({
          title: `Cannot delete ${template.name}`,
          body: impact.blocking_reasons?.[0]?.message || 'Choose another default template first.',
          content,
          confirmLabel: 'Close',
        });
        dialog.open(remove);
        return;
      }
      const confirmation = UI.field({
        id: `template-delete-${template.id}`,
        label: `Type ${impact.confirmation_text} to delete`,
      });
      const input = confirmation.querySelector('input');
      content.append(confirmation);
      let acknowledge = null;
      if (impact.requires_usage_acknowledgement) {
        acknowledge = UI.checkbox({
          label: 'I understand existing projects keep their historical template reference.',
          checked: false,
        });
        content.append(acknowledge);
      }
      const dialog = UI.dialog({
        title: `Delete ${template.name}?`,
        body: 'This removes the reusable template. Existing projects are not rewritten.',
        content,
        confirmLabel: 'Delete Template',
        destructive: true,
        onConfirm: async () => {
          const result = await api.delete(`/api/templates/${encodeURIComponent(template.id)}`, {
            signal,
            body: {
              expected_catalog_fingerprint: impact.catalog_fingerprint,
              expected_template_fingerprint: impact.template?.fingerprint || template.fingerprint,
              confirmation_text: input.value,
              acknowledge_usage: Boolean(acknowledge?.querySelector('input')?.checked),
            },
          });
          if (signal.aborted) return;
          feedback.textContent = result.ok
            ? 'Template deleted.' : message(result, 'The template was not deleted.');
          if (result.ok) await onChanged(result.data, null);
        },
      });
      dialog.open(remove);
      const confirm = dialog.layer?.querySelector('.dialog__footer .ui-button[data-variant="destructive"]');
      const sync = () => {
        if (!confirm) return;
        const acknowledged = !acknowledge || acknowledge.querySelector('input')?.checked;
        confirm.disabled = input.value !== impact.confirmation_text || !acknowledged;
      };
      input.addEventListener('input', sync);
      acknowledge?.querySelector('input')?.addEventListener('change', sync);
      sync();
      input.focus();
    });
    root.append(remove);
  }
  root.append(feedback);
  return root;
}
