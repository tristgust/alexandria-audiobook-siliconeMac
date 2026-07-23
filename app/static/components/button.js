'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};

  function setAttributes(node, attributes = {}) {
    Object.entries(attributes).forEach(([name, value]) => {
      if (value === false || value == null) return;
      if (value === true) node.setAttribute(name, '');
      else node.setAttribute(name, String(value));
    });
  }

  UI.button = function button(options = {}) {
    const {
      label = 'Button', variant = 'secondary', size = 'default', state = 'default',
      disabled = false, type = 'button', attributes = {}, onClick,
    } = options;
    const node = document.createElement('button');
    node.type = type;
    node.className = `ui-button ui-button--${variant}`;
    if (size !== 'default') node.classList.add(`ui-button--${size}`);
    node.dataset.primitive = 'button';
    node.dataset.state = state;
    node.disabled = disabled || state === 'disabled' || state === 'loading';
    if (state === 'loading') {
      node.setAttribute('aria-busy', 'true');
      const spinner = document.createElement('span');
      spinner.className = 'ui-button__spinner';
      spinner.setAttribute('aria-hidden', 'true');
      node.append(spinner);
    }
    const text = document.createElement('span');
    text.textContent = label;
    node.append(text);
    setAttributes(node, attributes);
    if (typeof onClick === 'function') node.addEventListener('click', onClick);
    return node;
  };
})();
