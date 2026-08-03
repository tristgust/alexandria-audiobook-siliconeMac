'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  let nextId = 0;
  const mark = (node, primitive, factory) => {
    node.dataset.primitive = primitive;
    node.dataset.productionFactory = factory;
    return node;
  };
  const textNode = (tag, className, text) => {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  };
  const append = (parent, value) => {
    (Array.isArray(value) ? value : [value]).filter(Boolean).forEach((node) => parent.append(node));
  };

  UI.appShell = function appShell(options = {}) {
    const root = mark(document.createElement('div'), 'app-shell', 'appShell');
    root.className = 'app-shell';
    const frame = document.createElement('div');
    frame.className = 'app-frame';
    append(frame, options.header);
    append(frame, options.main);
    append(root, options.navigation);
    root.append(frame);
    append(root, options.player);
    const token = (name) => Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name));
    const syncLayout = () => {
      const narrow = token('--breakpoint-narrow');
      const compact = token('--breakpoint-compact');
      const inspector = token('--breakpoint-inspector');
      root.dataset.layout = innerWidth < narrow ? 'narrow' : innerWidth < compact ? 'compact' : 'wide';
      root.dataset.inspectorLayout = innerWidth < inspector ? 'overlay' : 'inline';
      document.body.dataset.layout = root.dataset.layout;
      document.body.dataset.inspectorLayout = root.dataset.inspectorLayout;
    };
    root.syncLayout = syncLayout;
    syncLayout();
    window.addEventListener('resize', syncLayout);
    root.layoutCleanup = () => window.removeEventListener('resize', syncLayout);
    return root;
  };

  UI.shellInspector = function shellInspector(options = {}) {
    const states = ['hidden', 'collapsed', 'open', 'overlay'];
    const label = options.label || 'Context inspector';
    const root = mark(document.createElement('aside'), 'shell-inspector', 'shellInspector');
    root.className = 'shell-inspector';
    root.dataset.shellInspector = '';
    root.setAttribute('aria-label', label);
    const body = document.createElement('div');
    body.id = options.bodyId || `shell-inspector-${++nextId}`;
    body.dataset.inspectorBody = '';
    if (options.title) body.append(textNode('h2', 'entity-title', options.title));
    if (typeof options.content === 'string') body.append(textNode('p', 'flat-section__body', options.content));
    else append(body, options.content);
    const trigger = UI.iconButton({
      name: 'chevron',
      label: `Open ${label}`,
      tooltip: `Open ${label}`,
    });
    trigger.dataset.inspectorTrigger = '';
    trigger.setAttribute('aria-controls', body.id);
    root.append(trigger, body);
    let state = 'collapsed';
    let inlineSlot = null;
    const setState = (value, notify = true) => {
      const nextState = states.includes(value) ? value : 'collapsed';
      const hidden = nextState === 'hidden';
      const expanded = nextState === 'open' || nextState === 'overlay';
      const action = nextState === 'overlay' ? 'Close' : expanded ? 'Collapse' : 'Open';
      const changed = state !== nextState;
      state = nextState;
      root.dataset.state = state;
      root.hidden = hidden;
      trigger.setAttribute('aria-expanded', String(expanded));
      trigger.setAttribute('aria-label', `${action} ${label}`);
      trigger.dataset.tooltip = `${action} ${label}`;
      trigger.replaceChildren(UI.icon(nextState === 'overlay' ? 'close' : 'chevron'));
      body.hidden = !expanded;
      if (inlineSlot) inlineSlot.dataset.inspectorState = hidden ? 'hidden' : expanded ? 'open' : 'collapsed';
      if (changed && notify) {
        if (typeof options.onStateChange === 'function') options.onStateChange(state);
        root.dispatchEvent(new CustomEvent('shellinspectorchange', { detail: { state } }));
      }
      return state;
    };
    trigger.addEventListener('click', () => {
      const expandedState = root.parentElement?.matches('[data-overlay-root]') ? 'overlay' : 'open';
      setState(state === 'collapsed' ? expandedState : 'collapsed');
    });
    root.setState = (value) => setState(value);
    root.getState = () => state;
    root.mountInline = (slot) => {
      inlineSlot = slot;
      slot.dataset.shellInspectorSlot = '';
      setState(state === 'overlay' ? 'open' : state, false);
      slot.append(root);
      return root;
    };
    root.mountOverlay = (overlay) => {
      inlineSlot = null;
      if (state !== 'collapsed') setState('overlay', false);
      overlay.replaceChildren(root);
      return root;
    };
    setState(options.state, false);
    return root;
  };

  UI.navRail = function navRail(options = {}) {
    const nav = mark(document.createElement('nav'), 'nav-rail', 'navRail');
    nav.className = 'nav-rail';
    nav.setAttribute('aria-label', options.label || 'Primary navigation');
    const brand = document.createElement('a');
    brand.className = 'nav-brand';
    brand.href = options.brandHref || '#foundation';
    brand.setAttribute('aria-label', options.brandLabel || 'Alexandria home');
    const book = document.createElement('span');
    book.className = 'book-mark';
    book.setAttribute('aria-hidden', 'true');
    book.innerHTML = `
      <svg viewBox="0 0 72 58" focusable="false">
        <path d="M36 50c-7.5-5.7-16.5-8.5-27-8.5V7.8C19.7 7.8 28.7 10.7 36 16.4Z"></path>
        <path d="M36 50c7.5-5.7 16.5-8.5 27-8.5V7.8C52.3 7.8 43.3 10.7 36 16.4Z"></path>
        <path d="M36 16.4V50"></path>
        <path d="M9 13.5H4.5V47c12 0 22.5 2.2 31.5 6.7 9-4.5 19.5-6.7 31.5-6.7V13.5H63"></path>
      </svg>`;
    const brandName = document.createElement('span');
    brandName.className = 'nav-brand__name';
    brandName.textContent = options.brand || 'Alexandria';
    brand.append(book, brandName);
    nav.append(brand);
    (options.groups || []).forEach((group) => {
      const section = document.createElement('section');
      section.className = 'nav-group';
      const id = `nav-group-${++nextId}`;
      section.setAttribute('aria-labelledby', id);
      const heading = textNode('h2', 'utility-heading', group.label || 'Navigation');
      heading.id = id;
      const list = document.createElement('ul');
      list.className = 'nav-list';
      (group.items || []).forEach((item) => {
        const row = document.createElement('li');
        const link = document.createElement('a');
        link.className = 'nav-item';
        link.href = item.href || '#';
        if (item.current) link.setAttribute('aria-current', 'page');
        const icon = document.createElement('span');
        icon.className = 'nav-icon';
        if (item.iconClass) {
          const stableIcon = document.createElement('i');
          stableIcon.className = item.iconClass;
          stableIcon.setAttribute('aria-hidden', 'true');
          icon.append(stableIcon);
        } else {
          icon.append(UI.icon(item.icon || 'grid'));
        }
        const label = textNode('span', 'nav-item__label', item.label || 'Destination');
        link.setAttribute('aria-label', item.label || 'Destination');
        link.append(icon, label);
        row.append(link);
        list.append(row);
      });
      section.append(heading, list);
      nav.append(section);
    });
    return nav;
  };

  UI.globalHeader = function globalHeader(options = {}) {
    const header = mark(document.createElement('header'), 'global-header', 'globalHeader');
    header.className = 'app-header app-header--global';
    const context = document.createElement('div');
    context.className = 'global-context';
    const eyebrow = textNode('div', 'metadata', options.eyebrow || 'Application');
    eyebrow.dataset.globalEyebrow = '';
    const title = textNode('h1', 'global-title', options.title || 'Alexandria');
    title.dataset.globalTitle = '';
    title.dataset.pageHeading = '';
    const subtitle = textNode('p', 'global-subtitle', options.subtitle || '');
    subtitle.dataset.globalSubtitle = '';
    subtitle.hidden = !options.subtitle;
    context.append(eyebrow, title, subtitle);
    const actions = document.createElement('div');
    actions.className = 'header-actions';
    actions.dataset.globalActions = '';
    append(actions, options.actions);
    header.append(context, actions);
    return header;
  };

  UI.projectHeader = function projectHeader(options = {}) {
    const header = mark(document.createElement('header'), 'project-header', 'projectHeader');
    header.className = `app-header app-header--project${options.className ? ` ${options.className}` : ''}`;
    const context = document.createElement('div');
    context.className = 'project-context';
    context.append(textNode('div', 'metadata', options.eyebrow || 'Current project'));
    context.append(textNode('h2', 'project-title', options.title || 'Project workspace'));
    const actions = document.createElement('div');
    actions.className = 'header-actions';
    append(actions, options.actions);
    header.append(context, UI.stageTracker({ stages: options.stages }), actions);
    return header;
  };

  UI.pageTitleBlock = function pageTitleBlock(options = {}) {
    const header = mark(document.createElement('header'), 'page-title', 'pageTitleBlock');
    header.className = 'page-title-block';
    const title = textNode('h1', 'page-title', options.title || 'Page title');
    if (options.id) title.id = options.id;
    header.append(title);
    if (options.subtitle) header.append(textNode('p', 'page-subtitle', options.subtitle));
    append(header, options.actions);
    return header;
  };

  UI.flatSection = function flatSection(options = {}) {
    const section = mark(document.createElement(options.tag || 'section'), 'flat-section', 'flatSection');
    section.className = `flat-section${options.className ? ` ${options.className}` : ''}`;
    if (options.id) section.id = options.id;
    if (options.eyebrow) section.append(textNode('div', 'metadata', options.eyebrow));
    if (options.title) section.append(textNode(options.headingTag || 'h3', 'entity-title', options.title));
    if (options.body) section.append(textNode('p', 'flat-section__body', options.body));
    append(section, options.content);
    return section;
  };

  UI.dividerList = function dividerList(options = {}) {
    const list = mark(document.createElement('ul'), 'divider-list', 'dividerList');
    list.className = 'divider-list';
    (options.items || []).forEach((item) => {
      const row = document.createElement('li');
      if (item instanceof Node) row.append(item); else row.textContent = String(item);
      list.append(row);
    });
    return list;
  };

  UI.masterDetail = function masterDetail(options = {}) {
    const root = mark(document.createElement('div'), 'master-detail', 'masterDetail');
    root.className = 'master-detail';
    append(root, [options.master, options.detail]);
    return root;
  };

  UI.monogram = function monogram(options = {}) {
    const node = mark(document.createElement('span'), 'monogram', 'monogram');
    node.className = 'monogram';
    node.setAttribute('role', 'img');
    node.setAttribute('aria-label', options.label || 'Profile monogram');
    node.textContent = options.initials || 'AL';
    return node;
  };

  UI.portrait = function portrait(options = {}) {
    const node = mark(document.createElement(options.src ? 'img' : 'span'), 'portrait', 'portrait');
    node.className = `portrait${options.src ? '' : ' portrait-placeholder'}`;
    if (options.src) { node.src = options.src; node.alt = options.alt || ''; }
    else { node.setAttribute('role', 'img'); node.setAttribute('aria-label', options.label || 'Portrait evidence unavailable'); node.append(UI.icon('user')); }
    return node;
  };

  UI.sourceCover = function sourceCover(options = {}) {
    const node = mark(document.createElement(options.src ? 'img' : 'span'), 'source-cover', 'sourceCover');
    node.className = `source-cover${options.src ? '' : ' source-cover--placeholder'}`;
    if (options.src) {
      node.src = options.src;
      node.alt = options.alt || '';
    } else {
      node.setAttribute('role', 'img');
      node.setAttribute('aria-label', options.label || 'Source cover evidence unavailable');
      if (options.iconClass) {
        node.append(UI.iconFromClass(options.iconClass, options.icon || 'book-open'));
      } else if (options.icon) {
        node.append(UI.icon(options.icon));
      } else {
        node.textContent = options.emptyLabel || 'Source cover not provided';
      }
    }
    return node;
  };

  UI.notice = function notice(options = {}) {
    const tone = options.tone || 'information';
    const root = mark(document.createElement('section'), 'notice', 'notice');
    root.className = 'notice';
    root.dataset.tone = tone;
    if (options.live || options.blocking) {
      root.setAttribute('role', options.blocking || tone === 'error' ? 'alert' : 'status');
      root.setAttribute('aria-live', options.blocking || tone === 'error' ? 'assertive' : 'polite');
    }
    const marker = document.createElement('span');
    marker.className = 'notice__marker';
    marker.append(UI.icon(
      tone === 'success' ? 'check'
        : tone === 'warning' ? 'warning'
          : tone === 'error' || options.blocking ? 'error'
            : tone === 'information' ? 'info'
              : 'current',
    ));
    const content = document.createElement('div');
    content.append(textNode('h3', 'notice__title', options.title || 'Information'));
    content.append(textNode('p', 'notice__body', options.body || 'Additional context is available.'));
    root.append(marker, content);
    append(root, options.action);
    return root;
  };
})();
