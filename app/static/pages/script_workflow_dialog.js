'use strict';

const UI = globalThis.AlexandriaUI;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function createScriptWorkflowDialog({ content, signal, onOpen }) {
  const launcher = document.createElement('section');
  launcher.className = 'script-workflow-launcher';
  launcher.dataset.scriptWorkflow = 'generation';
  const copy = document.createElement('div');
  copy.className = 'script-workflow-launcher__copy';
  copy.append(
    text('h2', 'entity-title', 'Generation options'),
    text(
      'p',
      'metadata',
      'Create or import the Script task, approve it, then add Qwen and Fish delivery directions.',
    ),
  );
  const opener = UI.button({
    label: 'Open generation options',
    variant: 'secondary',
    attributes: { 'data-script-generation-open': '' },
  });
  launcher.append(copy, opener);

  const dialog = UI.dialog({
    kind: 'modal',
    title: 'Generation options',
    body: 'Complete the Script and delivery-plan round trips without leaving the Script workspace.',
    content,
    footer: false,
  });

  async function open(trigger = opener) {
    dialog.open(trigger);
    dialog.layer?.classList.add('script-generation-dialog-layer');
    await onOpen?.();
  }

  const onClick = () => { void open(opener); };
  const cleanup = () => dialog.forceClose();
  opener.addEventListener('click', onClick);
  signal.addEventListener('abort', cleanup, { once: true });

  return Object.freeze({
    launcher,
    open,
    cleanup() {
      opener.removeEventListener('click', onClick);
      signal.removeEventListener('abort', cleanup);
      cleanup();
    },
  });
}
