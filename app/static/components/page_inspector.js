'use strict';

const UI = globalThis.AlexandriaUI;
const FOCUSABLE = [
  'button:not(:disabled)',
  'a[href]',
  'input:not(:disabled)',
  'select:not(:disabled)',
  'textarea:not(:disabled)',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function createPageInspector({
  className,
  label,
  emptyContent = null,
  onClose = null,
}) {
  if (!UI?.iconButton) throw new Error('Page inspector requires Alexandria UI primitives.');

  const node = document.createElement('aside');
  node.className = `page-inspector ${className}`;
  node.dataset.pageInspector = '';
  node.setAttribute('aria-label', label);

  const closeButton = UI.iconButton({
    name: 'close',
    label: `Close ${label}`,
    tooltip: 'Close inspector',
  });
  closeButton.classList.add('page-inspector__close');
  closeButton.dataset.pageInspectorClose = '';

  const body = document.createElement('div');
  body.className = 'page-inspector__body';
  body.dataset.pageInspectorBody = '';
  if (emptyContent) body.append(emptyContent);

  const scrim = document.createElement('div');
  scrim.className = 'page-inspector__scrim';
  scrim.dataset.pageInspectorScrim = '';
  scrim.setAttribute('aria-hidden', 'true');
  scrim.hidden = true;

  node.append(closeButton, body);

  let requestedOpen = false;
  let returnFocus = null;
  let modalActive = false;
  let bodyOverflow = '';
  const anchor = document.createComment('page inspector');
  const inertState = new Map();
  const breakpoint = () => Number.parseFloat(getComputedStyle(document.documentElement)
    .getPropertyValue('--breakpoint-inspector')) || 1180;
  const overlayMode = () => innerWidth <= breakpoint();
  const visibleFocusables = () => [...node.querySelectorAll(FOCUSABLE)]
    .filter((item) => !item.hidden && item.getClientRects().length);

  const protectBackground = (overlayRoot) => {
    if (inertState.size) return;
    [...document.body.children].forEach((item) => {
      if (item === overlayRoot) return;
      inertState.set(item, item.inert);
      item.inert = true;
    });
    bodyOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
  };

  const releaseBackground = () => {
    inertState.forEach((value, item) => { item.inert = value; });
    inertState.clear();
    document.body.style.overflow = bodyOverflow;
  };

  const activateModal = () => {
    const overlayRoot = document.querySelector('[data-overlay-root]');
    if (!overlayRoot) return;
    if (!anchor.parentNode && node.parentNode) node.before(anchor);
    if (node.parentNode !== overlayRoot) overlayRoot.append(scrim, node);
    scrim.hidden = false;
    node.setAttribute('role', 'dialog');
    node.setAttribute('aria-modal', 'true');
    protectBackground(overlayRoot);
    modalActive = true;
  };

  const deactivateModal = () => {
    if (!modalActive) return;
    node.removeAttribute('role');
    node.removeAttribute('aria-modal');
    scrim.hidden = true;
    scrim.remove();
    if (anchor.parentNode) anchor.replaceWith(node);
    releaseBackground();
    modalActive = false;
  };

  const sync = () => {
    const overlay = overlayMode();
    const modalOpen = overlay && requestedOpen;
    const activateFocus = modalOpen && !modalActive;
    if (modalOpen) activateModal();
    else deactivateModal();
    node.dataset.inspectorMode = overlay ? 'overlay' : 'inline';
    node.classList.toggle('is-open', modalOpen);
    node.hidden = overlay && !requestedOpen;
    if (activateFocus && modalActive) {
      requestAnimationFrame(() => closeButton.focus({ preventScroll: true }));
    }
  };

  const setContent = (content) => {
    body.replaceChildren();
    if (content) body.append(content);
    sync();
  };

  const open = (opener = document.activeElement) => {
    if (opener instanceof HTMLElement) returnFocus = opener;
    requestedOpen = true;
    sync();
  };

  const close = ({ restoreFocus = true } = {}) => {
    requestedOpen = false;
    sync();
    onClose?.();
    if (restoreFocus && overlayMode()) returnFocus?.focus?.({ preventScroll: true });
  };

  const onResize = () => sync();
  const onCloseClick = () => close();
  const onScrimClick = () => close();
  const onKeydown = (event) => {
    if (!modalActive) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = visibleFocusables();
    const first = focusable[0];
    const last = focusable.at(-1);
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus({ preventScroll: true });
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus({ preventScroll: true });
    }
  };
  closeButton.addEventListener('click', onCloseClick);
  scrim.addEventListener('click', onScrimClick);
  node.addEventListener('keydown', onKeydown);
  window.addEventListener('resize', onResize);
  sync();

  return Object.freeze({
    node,
    body,
    setContent,
    open,
    close,
    isOverlay: overlayMode,
    isOpen: () => !overlayMode() || requestedOpen,
    cleanup() {
      requestedOpen = false;
      deactivateModal();
      window.removeEventListener('resize', onResize);
      closeButton.removeEventListener('click', onCloseClick);
      scrim.removeEventListener('click', onScrimClick);
      node.removeEventListener('keydown', onKeydown);
    },
  });
}
