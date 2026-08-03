'use strict';

export function projectText(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? '' : String(value);
  return node;
}

export function bindProjectHomeControls({
  newButton,
  openNew,
  searchInput,
  sortSelect,
  filterSelect,
  render,
  debounceMs,
  sortKey,
  filterKey,
}) {
  let searchTimer = null;
  const onSearchInput = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(render, debounceMs);
  };
  const onSortChange = () => {
    sessionStorage.setItem(sortKey, sortSelect.value);
    render();
  };
  const onFilterChange = () => {
    sessionStorage.setItem(filterKey, filterSelect.value);
    render();
  };
  const onSearchKeydown = (event) => {
    if (event.key !== 'Escape' || !searchInput.value) return;
    event.preventDefault();
    searchInput.value = '';
    render();
  };
  newButton.addEventListener('click', openNew);
  searchInput.addEventListener('input', onSearchInput);
  searchInput.addEventListener('keydown', onSearchKeydown);
  sortSelect.addEventListener('change', onSortChange);
  filterSelect.addEventListener('change', onFilterChange);
  return () => {
    clearTimeout(searchTimer);
    newButton.removeEventListener('click', openNew);
    searchInput.removeEventListener('input', onSearchInput);
    searchInput.removeEventListener('keydown', onSearchKeydown);
    sortSelect.removeEventListener('change', onSortChange);
    filterSelect.removeEventListener('change', onFilterChange);
  };
}

export function beginProjectCatalogLoad(
  content,
  resultsStatus,
  loadingNode,
  showLoading,
) {
  if (showLoading) {
    content.dataset.state = 'loading';
    resultsStatus.textContent = '';
    content.replaceChildren(loadingNode);
    return;
  }
  resultsStatus.textContent = `${resultsStatus.textContent} · Refreshing`;
}

export function markProjectCatalogRefreshUnavailable(resultsStatus) {
  resultsStatus.textContent = resultsStatus.textContent.replace(
    ' · Refreshing',
    ' · Refresh unavailable',
  );
}

export function publishProjectCatalog(
  shell,
  catalog,
  currentProject = null,
  { prepend = false } = {},
) {
  if (currentProject?.id) {
    catalog.current_project_id = currentProject.id;
    catalog.last_selected_project_id = currentProject.id;
    const current = { ...currentProject, current: true, selected: true };
    const others = (catalog.projects || [])
      .filter((item) => item.id !== currentProject.id)
      .map((item) => ({ ...item, current: false, selected: false }));
    catalog.projects = prepend
      ? [current, ...others]
      : (catalog.projects || []).some((item) => item.id === currentProject.id)
        ? (catalog.projects || []).map((item) => (
          item.id === currentProject.id
            ? { ...item, current: true, selected: true }
            : { ...item, current: false, selected: false }
        ))
        : [current, ...others];
  }
  shell.rememberProjectCatalog?.(catalog);
}
