import { castStyle } from "./cast_model.js";
import {
  createCastPage, renderCastEmpty, renderCastError, renderCastLoading,
  renderCastSelectionLoading,
} from "./cast_page_view.js";
import { createCastProfile } from "./cast_profile.js";
import { createCastRoster } from "./cast_roster.js";
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
  let searchTimer = null;
  let disposed = false;
  let requestController = null;
  let saveController = null;
  const roster = createCastRoster({
    master,
    getAggregate: () => aggregate,
    getSelected: () => selected,
    getFilter: () => activeFilter,
    getSearch: () => search,
    onSearch: (value) => { search = value; scheduleReload(); },
    onFilter: (value) => { activeFilter = value; scheduleReload(); },
    onSelect: (characterId, opener) => {
      const changing = characterId !== selected?.character_id;
      saveController?.requestSelection(characterId, opener);
      return !(changing && saveController?.dirty);
    },
    onReviewScript: () => shell.navigate(shell.routes.routeForPath("script",
      projectId ? { project: projectId } : {}).hash),
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
  const profileView = createCastProfile({
    profile, api, signal, shell,
    getSelected: () => selected,
    getDirty: () => saveController?.dirty || false,
    getSaveState: () => saveController?.saveState || "saved",
    onDirty: () => saveController?.markDirty(true),
    onSave: () => saveController?.saveProfile(),
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
    renderProfile,
    loadSelection,
    refreshAggregate,
  });

  function beginRequest() {
    requestController?.abort("superseded");
    requestController = new AbortController();
    const abort = () => requestController?.abort(signal.reason);
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
    return requestController.signal;
  }

  function header() {
    const summary = aggregate?.summary || {};
    const complete = Boolean(summary.complete);
    const blockers = Number(summary.blocker_count) || 0;
    const saveState = saveController?.saveState || "saved";
    shell.header.set({
      projectTitle: route.projectTitle || projectId || "Project workspace",
      save: {
        state: saveState === "error" ? "recoverable error" : saveState,
        label: saveState === "dirty" ? "Unsaved changes"
          : saveState === "saving" ? "Saving…"
            : saveState === "error" ? "Save failed" : "Saved",
      },
      status: {
        tone: complete ? "success" : blockers ? "warning" : "information",
        label: complete ? "Cast ready"
          : blockers ? `${blockers} item${blockers === 1 ? "" : "s"} need attention`
            : "Cast in progress",
      },
      primaryAction: {
        label: "Continue to Produce",
        disabled: !complete,
        onClick: () => shell.navigate(shell.routes.routeForPath("produce",
          projectId ? { project: projectId } : {}).hash),
      },
    });
    shell.tracker.set({ script: "complete", cast: "current", produce: "future", export: "future" });
  }

  const showLoading = () => renderCastLoading({ roster, profile, page });
  const showError = (message) => renderCastError({
    master, profile, page, onRetry: loadCast, message,
  });
  const showEmpty = () => renderCastEmpty({ roster, profile, page });

  function scheduleReload() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadCast, 180);
  }

  function renderProfile() {
    profileView.render();
    page.dataset.castState = selected ? "ready" : "empty";
  }

  function moreContext(path) {
    return shell.routes.routeForPath(path, {
      ...(projectId ? { project: projectId } : {}),
      character: selected.character_id,
      source: `cast:character:${selected.character_id}`,
      return: shell.routes.routeForPath("cast", {
        ...(projectId ? { project: projectId } : {}),
        character: selected.character_id,
      }).hash,
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
    if (disposed || signal.aborted || !response.ok) return;
    aggregate = response.data || {};
    roster.render();
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
    header();
    if (!(aggregate.characters || []).length && !aggregate.selected_character) {
      showEmpty();
      return;
    }
    selected = aggregate.selected_character || null;
    roster.render();
    const characterId = aggregate.selected_character_id
      || selected?.character_id
      || aggregate.characters?.[0]?.character_id;
    if (characterId) await loadSelection(characterId);
    else renderProfile();
  }

  const cleanup = () => {
    if (disposed) return;
    disposed = true;
    clearTimeout(searchTimer);
    requestController?.abort("cleanup");
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
  return cleanup;
}
