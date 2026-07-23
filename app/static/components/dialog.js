'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  let nextId = 0;
  const FOCUSABLE = 'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

  UI.dialog = function dialog(options = {}) {
    const kind = options.kind === 'drawer' ? 'drawer' : 'modal';
    const titleId = `dialog-title-${++nextId}`;
    let layer = null;
    let returnFocus = null;
    let dirtyReturnFocus = null;

    if (options.opener) {
      options.opener.dataset.primitive = kind;
      options.opener.dataset.productionFactory = 'dialog';
    }

    function forceClose(resolution = 'close') {
      if (!layer) return;
      layer.remove();
      layer = null;
      options.onClose?.(resolution);
      returnFocus?.focus();
    }

    function showDirtyConfirmation() {
      if (!layer || layer.querySelector('[data-dirty-confirmation]')) return;
      dirtyReturnFocus = document.activeElement;
      const confirmation = document.createElement('section');
      confirmation.className = 'dirty-confirmation';
      confirmation.dataset.dirtyConfirmation = 'true';
      confirmation.setAttribute('aria-label', 'Unsaved changes');
      const title = document.createElement('h3');
      title.className = 'entity-title';
      title.textContent = 'Save changes before closing?';
      const body = document.createElement('p');
      body.textContent = 'Choose Save, Discard, or Cancel. Nothing is discarded silently.';
      const actions = document.createElement('div');
      actions.className = 'dialog__footer';
      const cancel = UI.button({ label: 'Cancel', variant: 'secondary', attributes: { 'data-dirty-action': 'cancel' } });
      const discard = UI.button({ label: 'Discard', variant: 'destructive', attributes: { 'data-dirty-action': 'discard' } });
      const save = UI.button({ label: 'Save', variant: 'primary', attributes: { 'data-dirty-action': 'save' } });
      const baseFooter = layer.querySelector('.dialog-surface > .dialog__footer');
      baseFooter.hidden = true;
      cancel.addEventListener('click', () => { confirmation.remove(); baseFooter.hidden = false; dirtyReturnFocus?.focus(); });
      discard.addEventListener('click', () => { options.onDiscard?.(); forceClose('discard'); });
      save.addEventListener('click', () => { options.onSave?.(); forceClose('save'); });
      actions.append(cancel, discard, save);
      confirmation.append(title, body, actions);
      layer.querySelector('.dialog-surface').append(confirmation);
      cancel.focus();
    }

    function requestClose() {
      if (options.dirty) showDirtyConfirmation(); else forceClose();
    }

    function open(opener = options.opener) {
      if (layer) return;
      returnFocus = opener || document.activeElement;
      layer = document.createElement('div');
      layer.className = 'dialog-layer';
      layer.dataset.kind = kind;
      layer.dataset.primitive = kind;
      layer.dataset.productionFactory = 'dialog';
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
      const closeButton = UI.iconButton({ label: `Close ${title.textContent}`, name: 'close', tooltip: '', onClick: requestClose });
      header.append(title, closeButton);
      const body = document.createElement('div');
      const description = document.createElement('p');
      description.textContent = options.body || 'Review the details before continuing.';
      body.append(description);
      if (options.content) body.append(options.content);
      const footer = document.createElement('footer');
      footer.className = 'dialog__footer';
      const cancel = UI.button({ label: 'Cancel', variant: 'secondary', onClick: requestClose });
      const confirm = UI.button({ label: options.confirmLabel || 'Continue', variant: options.destructive ? 'destructive' : 'primary', onClick: () => { options.onConfirm?.(); forceClose('confirm'); } });
      footer.append(cancel, confirm);
      surface.append(header, body, footer);
      layer.append(surface);
      layer.addEventListener('mousedown', (event) => { if (event.target === layer) requestClose(); });
      layer.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') { event.preventDefault(); requestClose(); return; }
        if (event.key !== 'Tab') return;
        const focusable = [...layer.querySelectorAll(FOCUSABLE)];
        const first = focusable[0];
        const last = focusable.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      });
      document.body.append(layer);
      closeButton.focus();
    }

    if (options.opener) options.opener.addEventListener('click', () => open(options.opener));
    return { open, close: requestClose, forceClose, get layer() { return layer; } };
  };
})();
