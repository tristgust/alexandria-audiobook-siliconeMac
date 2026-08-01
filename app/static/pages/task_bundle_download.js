'use strict';

function filenameFrom(response, taskType) {
  const disposition = response.headers.get('content-disposition') || '';
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  try {
    if (encoded) return decodeURIComponent(encoded);
  } catch (_error) {}
  return plain || `alexandria-${taskType}.alexandria-task.zip`;
}

export async function downloadTaskBundle({
  api,
  signal,
  button,
  taskType,
  target = null,
  options = null,
  pendingLabel = 'Preparing download…',
  onError,
  onDownloaded,
}) {
  const prior = button.textContent;
  button.disabled = true;
  button.textContent = pendingLabel;
  try {
    const payload = {
      task_type: taskType,
      target,
    };
    if (options && typeof options === 'object') payload.options = options;
    const exported = await api.post('/api/tasks/export', payload, { signal });
    if (!exported.ok) {
      onError?.(exported.error);
      return null;
    }
    const downloadUrl = exported.data?.download_url;
    if (!downloadUrl) {
      onError?.('Alexandria did not return a task-bundle download URL.');
      return null;
    }
    const response = await fetch(downloadUrl, {
      credentials: 'same-origin',
      signal,
    });
    if (!response.ok) {
      onError?.(`Task bundle download failed (${response.status}).`);
      return null;
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filenameFrom(response, taskType);
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
    onDownloaded?.(exported.data);
    return exported.data;
  } catch (error) {
    if (signal.aborted) return null;
    onError?.(String(error?.message || error));
    return null;
  } finally {
    button.disabled = false;
    button.textContent = prior;
  }
}
