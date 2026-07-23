'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  let nextId = 0;
  const FOCUSABLE = 'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

  function actionButton(label, variant = 'secondary') {
    if (UI.button) return UI.button({ label, variant });
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    return button;
  }

  UI.dialog = function dialog(options = {}) {
    nextId += 1;
    const kind = options.kind === 'drawer' ? 'drawer' : 'modal';
    const titleId = `dialog-title-${nextId}`;
    let layer = null;
    let returnFocus = null;

    function close() {
      if (!layer) return;
      layer.remove();
      layer = null;
      returnFocus?.focus();
      options.onClose?.();
    }

    function open(opener = options.opener) {
      if (layer) return;
      returnFocus = opener || document.activeElement;
      layer = document.createElement('div');
      layer.className = 'dialog-layer';
      layer.dataset.kind = kind;
      layer.dataset.primitive = kind;
      layer.setAttribute('role', 'dialog');
      layer.setAttribute('aria-modal', 'true');
      layer.setAttribute('aria-labelledby', titleId);
      const surface = document.createElement('section');
      surface.className = 'dialog-surface';
      const header = document.createElement('header');
      header.className = 'dialog__header';
      const title = document.createElement('h2');
      title.id = titleId;
      title.textContent = options.title || (kind === 'drawer' ? 'Inspector' : 'Dialog');
      const closeButton = UI.iconButton
        ? UI.iconButton({ label: `Close ${title.textContent}`, name: 'close', tooltip: '', onClick: close })
        : actionButton('Close');
      if (!UI.iconButton) closeButton.addEventListener('click', close);
      header.append(title, closeButton);
      const body = document.createElement('div');
      const description = document.createElement('p');
      description.textContent = options.body || 'Review the details before continuing.';
      body.append(description);
      if (options.content) body.append(options.content);
      const footer = document.createElement('footer');
      footer.className = 'dialog__footer';
      const cancel = actionButton('Cancel');
      cancel.addEventListener('click', close);
      const confirm = actionButton(options.confirmLabel || 'Continue', options.destructive ? 'destructive' : 'primary');
      confirm.addEventListener('click', () => {
        options.onConfirm?.();
        close();
      });
      footer.append(cancel, confirm);
      surface.append(header, body, footer);
      layer.append(surface);
      layer.addEventListener('mousedown', (event) => {
        if (event.target === layer && !options.dirty) close();
      });
      layer.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !options.dirty) {
          event.preventDefault();
          close();
          return;
        }
        if (event.key !== 'Tab') return;
        const focusable = [...layer.querySelectorAll(FOCUSABLE)];
        const first = focusable[0];
        const last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      });
      document.body.append(layer);
      closeButton.focus();
    }

    if (options.opener) options.opener.addEventListener('click', () => open(options.opener));
    return { open, close };
  };
})();
