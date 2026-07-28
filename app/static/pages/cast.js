import { castStyle } from "./cast_model.js";
import {
  createCastPage, renderCastEmpty, renderCastError, renderCastLoading,
  renderCastSelectionLoading,
} from "./cast_page_view.js";
import { createCastProfile } from "./cast_profile.js";
import { createCastRoster } from "./cast_roster.js";
import { createCastDiscovery } from "./cast_discovery.js";
import { createCastHeader } from "./cast_header.js";
import { createCastVoiceLibrary } from "./cast_voice_library.js";
import { createCastVoiceSave } from "./cast_voice_save.js";
import { createCastWorkflows } from "./cast_workflows.js";

const UI = globalThis.AlexandriaUI;

export async function mount({ root, route, shell, api, signal }) {
  if (!UI) throw new Error("Cast requires Alexandria UI primitives.");
  const projectId = route.projectId || route.context.project || "";
  const style = castStyle();
  const { page, master, profile } = createCastPage(root);

  let aggregate = null;
  let selected = null;
  let activeFilter = route.context.filter || "all";
  let search = route.context.search || "";
  let rosterSort = "script_order";
  let searchTimer = null;
  let disposed = false;
  let requestController = null;
  let saveController = null;
  let discovery = null;
  let profileView = null;
  const header = createCastHeader({
    shell, route, projectId,
    getAggregate: () => aggregate,
    getDiscoveryState: () => discovery?.isRunning() ? 'running' : '',
    getSaveState: () => saveController?.saveState || 'saved',
  });
  const roster = createCastRoster({
    master,
    getAggregate: () => aggregate,
    getSelected: () => selected,
    getFilter: () => activeFilter,
    getSearch: () => search,
    getSort: () => rosterSort,
    onSearch: (value) => { search = value; scheduleReload(); },
    onFilter: (value) => { activeFilter = value; scheduleReload(); },
    onSort: (value) => { rosterSort = value; roster.render(); },
    onSelect: (characterId, opener) => {
      const changing = characterId !== selected?.character_id;
      saveController?.requestSelection(characterId, opener);
      return !(changing && saveController?.dirty);
    },
    onReviewScript: () => shell.navigate(shell.routes.routeForPath("script",
      projectId ? { project: projectId } : {}).hash),
    onOpenFullCastTasks: (opener) => workflows.open(
      'advanced-character-operations', opener,
    ),
  });
  const workflows = createCastWorkflows({
    shell, api, signal, projectId,
    getSelected: () => selected,
    routeForTool: moreContext,
    onRefresh: async () => {
      if (selected?.character_id) await loadSelection(selected.character_id, false);
      await refreshAggregate();
    },
  });
  const voiceLibrary = createCastVoiceLibrary({
    api, signal, projectId,
    routeForCast: () => shell.routes.routeForPath('cast',
      projectId ? { project: projectId } : {}).hash,
    onStateChange: () => profileView?.syncVoiceLibraryState(),
  });
  profileView = createCastProfile({
    profile, api, signal, shell,
    getSelected: () => selected,
    getVoiceLibrary: voiceLibrary.getData,
    getVoiceLibraryState: voiceLibrary.getState,
    onRetryVoiceLibrary: () => { void voiceLibrary.load(); },
    getDirty: () => saveController?.dirty || false,
    getSaveState: () => saveController?.saveState || "saved",
    onDirty: () => saveController?.markDirty(true),
    onSave: () => saveController?.saveProfile(),
    onCancelEdit: () => {
      saveController?.reset();
      header();
    },
    onOpenWorkflow: workflows.open,
    onControlledCloneApplied: async () => {
      if (selected?.character_id) await loadSelection(selected.character_id, false);
      await refreshAggregate();
    },
    routeForTool: moreContext,
  });
  saveController = createCastVoiceSave({
    api, signal, page, profile, profileView, beginRequest,
    getSelected: () => selected,
    setSelected: (value) => { selected = value; },
    renderHeader: header,
    loadSelection,
    refreshAggregate,
    reloadVoiceLibrary: voiceLibrary.load,
  });

  function beginRequest() {
    requestController?.abort("superseded");
    requestController = new AbortController();
    const abort = () => requestController?.abort(signal.reason);
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
    return requestController.signal;
  }

  const showLoading = () => renderCastLoading({ roster, profile, page });
  const showError = (message) => renderCastError({
    master, profile, page, onRetry: loadCast, message,
  });
  const showEmpty = () => renderCastEmpty({
    roster,
    profile,
    page,
    discovering: discovery?.isRunning() || false,
    process: aggregate?.process || {},
    progress: aggregate?.progress || {},
    state: aggregate?.summary?.state || "not_started",
    onDiscover: () => discovery?.start(),
    onCancel: () => discovery?.cancel(),
  });

  discovery = createCastDiscovery({
    api, signal, beginRequest,
    isDisposed: () => disposed,
    setAggregate: (value) => { aggregate = value; },
    renderEmpty: showEmpty,
    renderError: showError,
    renderHeader: header,
    reloadCast: loadCast,
  });

  function scheduleReload() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadCast, 180);
  }

  function renderProfile() {
    profileView.render();
    page.dataset.castState = selected ? "ready" : "empty";
  }

  function moreContext(path, context = {}) {
    return shell.routes.routeForPath(path, {
      ...(projectId ? { project: projectId } : {}),
      character: selected.character_id,
      source: `cast:character:${selected.character_id}`,
      return: shell.routes.routeForPath("cast", {
        ...(projectId ? { project: projectId } : {}),
        character: selected.character_id,
      }).hash,
      ...context,
    });
  }

  async function loadSelection(characterId, showLoading = true) {
    if (!characterId || disposed) return;
    if (showLoading) renderCastSelectionLoading(profile);
    const response = await api.get(
      `/api/cast/characters/${encodeURIComponent(characterId)}`,
      { signal: beginRequest() },
    );
    if (disposed || signal.aborted) return;
    if (!response.ok) {
      if (response.kind !== "canceled") showError("Alexandria could not load the selected character.");
      return;
    }
    selected = response.data?.character_id
      ? response.data
      : response.data?.selected_character || response.data?.character || response.data;
    aggregate.selected_character_id = selected.character_id;
    aggregate.selected_character = selected;
    aggregate.selection_visible = (aggregate.characters || []).some(
      (character) => character.character_id === selected.character_id,
    );
    saveController.reset();
    roster.render();
    renderProfile();
    header();
  }

  async function refreshAggregate() {
    const query = new URLSearchParams({ filter: activeFilter });
    if (search.trim()) query.set("search", search.trim());
    if (selected?.character_id) query.set("selected_character_id", selected.character_id);
    const response = await api.get(`/api/cast?${query}`, { signal: beginRequest() });
    if (disposed || signal.aborted || !response.ok) return false;
    aggregate = response.data || {};
    roster.render();
    return true;
  }

  async function loadCast() {
    showLoading();
    const query = new URLSearchParams({ filter: activeFilter });
    if (search.trim()) query.set("search", search.trim());
    if (route.context.character) query.set("selected_character_id", route.context.character);
    const response = await api.get(`/api/cast?${query}`, { signal: beginRequest() });
    if (disposed || signal.aborted) return;
    if (!response.ok) {
      if (response.kind !== "canceled") showError();
      return;
    }
    aggregate = response.data || {};
    discovery.sync(aggregate);
    header();
    if (!(aggregate.characters || []).length && !aggregate.selected_character) {
      showEmpty();
      if (discovery.isRunning()) discovery.schedule(800);
      return;
    }
    selected = aggregate.selected_character || null;
    roster.render();
    const characterId = aggregate.selected_character_id
      || selected?.character_id
      || aggregate.characters?.[0]?.character_id;
    if (selected) renderProfile();
    else if (characterId) await loadSelection(characterId);
    else renderProfile();
  }

  const cleanup = () => {
    if (disposed) return;
    disposed = true;
    clearTimeout(searchTimer);
    requestController?.abort("cleanup");
    discovery.cleanup();
    voiceLibrary.cleanup();
    workflows.cleanup();
    profileView.cleanup();
    if (style.owned) style.node.remove();
    signal.removeEventListener("abort", cleanup);
  };
  signal.addEventListener("abort", cleanup, { once: true });

  shell.player.set({ state: "inactive", title: "No active Cast preview" });
  showLoading();
  header();
  await loadCast();
  if (!disposed && !signal.aborted) void voiceLibrary.load();
  return cleanup;
}
