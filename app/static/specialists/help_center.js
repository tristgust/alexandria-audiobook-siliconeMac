'use strict';

import {
  resultMessage,
  supportOwner,
  supportReturn,
  textNode,
} from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

function appendInline(parent, value) {
  const source = String(value || '');
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let start = 0;
  for (const match of source.matchAll(pattern)) {
    if (match.index > start) {
      parent.append(document.createTextNode(source.slice(start, match.index)));
    }
    const token = match[0];
    const node = document.createElement(token.startsWith('`') ? 'code' : 'strong');
    node.textContent = token.startsWith('`') ? token.slice(1, -1) : token.slice(2, -2);
    parent.append(node);
    start = match.index + token.length;
  }
  if (start < source.length) {
    parent.append(document.createTextNode(source.slice(start)));
  }
}

function renderMarkdown(markdown) {
  const article = document.createElement('article');
  article.className = 'help-article';
  let list = null;
  let listKind = '';
  const endList = () => {
    list = null;
    listKind = '';
  };
  String(markdown || '').split(/\r?\n/).forEach((line) => {
    if (!line.trim()) {
      endList();
      return;
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      endList();
      const level = Math.min(3, heading[1].length + 1);
      const node = document.createElement(`h${level}`);
      appendInline(node, heading[2]);
      article.append(node);
      return;
    }
    const ordered = /^\d+\.\s+(.+)$/.exec(line);
    const unordered = /^[-*]\s+(.+)$/.exec(line);
    if (ordered || unordered) {
      const kind = ordered ? 'ol' : 'ul';
      if (!list || listKind !== kind) {
        list = document.createElement(kind);
        listKind = kind;
        article.append(list);
      }
      const item = document.createElement('li');
      appendInline(item, (ordered || unordered)[1]);
      list.append(item);
      return;
    }
    endList();
    const paragraph = document.createElement('p');
    appendInline(paragraph, line);
    article.append(paragraph);
  });
  return article;
}

function topicNavigation(topics, activeSlug, route, shell) {
  const nav = document.createElement('nav');
  nav.className = 'help-topic-list';
  nav.setAttribute('aria-label', 'Help topics');
  nav.setAttribute('role', 'listbox');
  nav.setAttribute('aria-activedescendant', activeSlug ? `help-topic-${activeSlug}` : '');
  topics.forEach((topic) => {
    const target = shell.routes.withContext(route, { topic: topic.slug });
    const link = document.createElement('a');
    link.id = `help-topic-${topic.slug}`;
    link.href = target.hash;
    link.textContent = topic.title;
    link.setAttribute('role', 'option');
    link.setAttribute('aria-selected', String(topic.slug === activeSlug));
    if (topic.slug === activeSlug) link.setAttribute('aria-current', 'page');
    link.addEventListener('click', (event) => {
      event.preventDefault();
      shell.navigate(target.hash);
    });
    nav.append(link);
  });
  return nav;
}

async function inventory(api, route, signal) {
  const search = String(route.context.search || '').trim();
  if (!search) return api.get("/api/help", { signal });
  return api.get(`/api/help?search=${encodeURIComponent(search)}`, { signal });
}

export async function mount({ root, route, shell, api, signal }) {
  const dataRouteOwner = route.path;
  const { owner, stateRegion } = supportOwner(root, route, {
    shell,
    page: 'help-center',
    title: 'Help Center',
    subtitle: 'Versioned guidance bundled with Alexandria for offline use.',
    className: 'specialist-workspace',
  });
  owner.dataset.routeOwner = dataRouteOwner;
  stateRegion.setAttribute('data-state-region', '');
  const inventoryResult = await inventory(api, route, signal);
  if (signal.aborted) return () => {};
  const toolbar = document.createElement('div');
  toolbar.className = 'support-toolbar';
  const returnButton = supportReturn(route, shell);
  returnButton.setAttribute('data-support-return', '');
  const search = UI.searchField({
    label: 'Search bundled help',
    placeholder: 'Search Help Center',
  });
  const searchInput = search.querySelector('input');
  searchInput.value = route.context.search || '';
  searchInput.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    const target = shell.routes.withContext(route, {
      search: searchInput.value.trim(),
      topic: '',
    });
    shell.navigate(target.hash, { historyMode: 'replace' });
  });
  toolbar.append(returnButton, search);
  if (!inventoryResult.ok) {
    owner.dataset.viewState = 'error';
    stateRegion.replaceChildren(toolbar, UI.notice({
      tone: 'error',
      title: 'Help Center could not be loaded',
      body: resultMessage(inventoryResult, 'Bundled guidance remains unchanged.'),
      live: true,
    }));
    return () => {};
  }
  const topics = inventoryResult.data?.topics || [];
  const requested = route.context.topic;
  const active = topics.find((topic) => topic.slug === requested) || topics[0];
  if (!active) {
    owner.dataset.viewState = 'empty';
    stateRegion.replaceChildren(toolbar, UI.emptyState({
      title: 'No help topic matches',
      body: 'Clear the search to browse all bundled guidance.',
    }));
    return () => {};
  }
  const topicResult = await api.get(`/api/help/${encodeURIComponent(active.slug)}`, { signal });
  if (signal.aborted) return () => {};
  if (!topicResult.ok) {
    owner.dataset.viewState = 'error';
    stateRegion.replaceChildren(toolbar, UI.notice({
      tone: 'error',
      title: 'Help topic could not be loaded',
      body: resultMessage(topicResult, 'Choose another bundled topic.'),
      live: true,
    }));
    return () => {};
  }
  const layout = document.createElement('div');
  layout.className = 'help-layout';
  layout.append(
    topicNavigation(topics, active.slug, route, shell),
    renderMarkdown(topicResult.data.markdown),
  );
  stateRegion.replaceChildren(toolbar, layout);
  owner.dataset.viewState = 'ready';
  return () => {};
}
