(() => {
  "use strict";

  const data = window.ALEXANDRIA_ROUND1_DATA;
  const modules = window.AlexandriaRound1;
  if (!data || !modules?.core || !modules?.content || !modules?.navigation || !modules?.io) {
    document.body.innerHTML = "<p>Round 1 review assets could not be loaded.</p>";
    return;
  }

  const core = modules.core;
  const query = new URLSearchParams(window.location.search);
  const reviewerProfile = query.get("reviewer")?.trim().slice(0, 80) || "default";
  const reviewSession = query.get("session")?.trim().slice(0, 80) || "default";
  const storageKey = core.storageKey(data, reviewerProfile, reviewSession);
  const legacyStorageKey = `alexandria-round1-review:${data.round_id}`;
  const allowLegacy = reviewerProfile === "default" && reviewSession === "default";
  const byId = new Map(data.samples.map((sample) => [sample.sample_id, sample]));
  const stylesByKey = new Map(data.styles.map((style) => [style.key, style]));
  const groupKeys = Object.keys(data.groups);
  const readySamples = data.samples.filter((sample) => sample.status === "ready" && sample.audio);
  const state = {
    saved: core.loadSaved(storageKey, legacyStorageKey, allowLegacy),
    activeGroup: null,
    activeStyle: null,
    identityFilter: "all",
    searchQuery: "",
    incompleteOnly: false,
    saveTimer: null,
  };
  const els = collectElements();

  function collectElements() {
    const entries = [
      ["groupNavigation", "group-navigation"], ["styleNavigation", "style-navigation"],
      ["identityFilter", "identity-filter"], ["search", "search"], ["incompleteOnly", "incomplete-only"],
      ["groupLabel", "group-label"], ["styleTitle", "style-title"], ["styleInstruction", "style-instruction"],
      ["styleProgressText", "style-progress-text"], ["styleCoverageText", "style-coverage-text"],
      ["groupProgressCompact", "group-progress-compact"], ["referenceList", "reference-list"],
      ["referencePanel", "reference-panel"], ["referenceToggle", "reference-toggle"],
      ["closeReferenceDrawer", "close-reference-drawer"], ["sampleList", "sample-list"],
      ["notice", "notice"], ["overallProgress", "overall-progress"], ["overallGenerated", "overall-generated"],
      ["followupCount", "followup-count"], ["previousStyle", "previous-style"], ["nextStyle", "next-style"],
      ["nextIncomplete", "next-incomplete"], ["exportStyle", "export-style"], ["exportGroup", "export-group"],
      ["exportAll", "export-all"], ["importResults", "import-results"], ["importDialog", "import-dialog"],
      ["importSummary", "import-summary"],
    ];
    return Object.fromEntries(entries.map(([key, id]) => [key, document.getElementById(id)]));
  }

  function samplesForGroup(groupKey) {
    return data.samples.filter((sample) => sample.group === groupKey);
  }

  function samplesForStyle(styleKey) {
    return data.samples.filter((sample) => sample.style === styleKey);
  }

  function persistSelection(kind, value) {
    localStorage.setItem(`${storageKey}:${kind}`, value);
  }

  function showNotice(message) {
    els.notice.textContent = message;
    els.notice.hidden = false;
    setTimeout(() => { els.notice.hidden = true; }, 5000);
  }

  function scheduleSave() {
    clearTimeout(state.saveTimer);
    document.querySelectorAll(".saved-indicator").forEach((node) => { node.textContent = "Saving…"; });
    state.saveTimer = setTimeout(() => {
      localStorage.setItem(storageKey, JSON.stringify(state.saved));
      document.querySelectorAll(".saved-indicator").forEach((node) => { node.textContent = "Saved"; });
    }, 300);
  }

  function setValue(sampleId, field, value) {
    const existing = state.saved[sampleId];
    const row = existing && typeof existing === "object" && !Array.isArray(existing)
      ? existing
      : { sample_id: sampleId, updated_at: null, revision: 0 };
    state.saved[sampleId] = row;
    row[field] = value;
    row.updated_at = new Date().toISOString();
    row.revision = Math.max(0, Number(row.revision) || 0) + 1;
    scheduleSave();
    context.content.updateCardStatus(sampleId);
    context.navigation.updateProgressOnly();
  }

  const context = {
    byId,
    core,
    data,
    els,
    groupKeys,
    readySamples,
    state,
    storageKey,
    stylesByKey,
    persistSelection,
    samplesForGroup,
    samplesForStyle,
    setValue,
    showNotice,
  };
  context.content = modules.content.create(context);
  context.navigation = modules.navigation.create(context);
  context.io = modules.io.create(context);
  context.render = render;

  const firstGroup = context.navigation.firstGeneratedGroup();
  state.activeGroup = core.restoreSelection(
    `${storageKey}:group`, `${legacyStorageKey}:group`, allowLegacy, firstGroup,
  );
  const firstStyle = context.navigation.firstStyleForGroup(state.activeGroup);
  state.activeStyle = core.restoreSelection(
    `${storageKey}:style`, `${legacyStorageKey}:style`, allowLegacy, firstStyle,
  );

  function render() {
    if (!data.groups[state.activeGroup]) state.activeGroup = context.navigation.firstGeneratedGroup();
    if (!data.groups[state.activeGroup].styles.includes(state.activeStyle)) {
      state.activeStyle = context.navigation.firstStyleForGroup(state.activeGroup);
    }
    context.navigation.renderGroupNavigation();
    context.navigation.renderStyleNavigation();
    context.navigation.renderIdentityFilter();
    context.navigation.renderStyleHeader();
    context.content.renderReferences(context.navigation.filteredStyleSamples({
      ignoreSearch: true, ignoreIncomplete: true,
    }));
    context.content.renderSamples(context.navigation.filteredStyleSamples());
    context.navigation.updateProgressOnly();
    context.navigation.updatePreviousNextButtons();
  }

  const toolbar = document.querySelector(".toolbar");
  let chromeFrame = null;
  function updateChromeOffsets() {
    const rectangle = toolbar.getBoundingClientRect();
    document.documentElement.style.setProperty("--toolbar-offset", `${toolbar.offsetHeight}px`);
    document.documentElement.style.setProperty("--drawer-top", `${Math.max(10, rectangle.bottom + 10)}px`);
  }
  function scheduleChromeOffsets() {
    if (chromeFrame !== null) return;
    chromeFrame = requestAnimationFrame(() => {
      chromeFrame = null;
      updateChromeOffsets();
    });
  }

  els.previousStyle.addEventListener("click", () => context.navigation.moveStyle(-1));
  els.nextStyle.addEventListener("click", () => context.navigation.moveStyle(1));
  els.nextIncomplete.addEventListener("click", context.navigation.goToNextIncomplete);
  els.referenceToggle.addEventListener("click", () => {
    updateChromeOffsets();
    els.referencePanel.open = true;
    els.referencePanel.classList.add("docked");
    els.referenceToggle.setAttribute("aria-expanded", "true");
    els.closeReferenceDrawer.focus();
  });
  els.closeReferenceDrawer.addEventListener("click", () => {
    els.referencePanel.classList.remove("docked");
    els.referenceToggle.setAttribute("aria-expanded", "false");
    els.referenceToggle.focus();
  });
  els.exportStyle.addEventListener("click", () => context.io.exportRows("style", state.activeStyle));
  els.exportGroup.addEventListener("click", () => context.io.exportRows("group", state.activeGroup));
  els.exportAll.addEventListener("click", () => context.io.exportRows("cumulative", "all"));
  els.importResults.addEventListener("change", async (event) => {
    await context.io.importFiles([...event.target.files]);
    event.target.value = "";
  });
  els.identityFilter.addEventListener("change", () => {
    state.identityFilter = els.identityFilter.value;
    context.content.renderReferences(context.navigation.filteredStyleSamples({
      ignoreSearch: true, ignoreIncomplete: true,
    }));
    context.content.renderSamples(context.navigation.filteredStyleSamples());
  });
  els.search.addEventListener("input", () => {
    state.searchQuery = els.search.value.trim();
    context.content.renderSamples(context.navigation.filteredStyleSamples());
  });
  els.incompleteOnly.addEventListener("change", () => {
    state.incompleteOnly = els.incompleteOnly.checked;
    context.content.renderSamples(context.navigation.filteredStyleSamples());
  });

  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (event.target instanceof Element && event.target.closest("audio, input, textarea, select, button, a[href], summary, [contenteditable], [role='button'], [role='link']")) return;
    if (event.key === "ArrowLeft") context.navigation.moveStyle(-1);
    if (event.key === "ArrowRight") context.navigation.moveStyle(1);
    if (event.key.toLowerCase() === "n") context.navigation.goToNextIncomplete();
  });

  render();
  updateChromeOffsets();
  window.addEventListener("scroll", scheduleChromeOffsets, { passive: true });
  window.addEventListener("resize", scheduleChromeOffsets);
  if (typeof ResizeObserver === "function") new ResizeObserver(scheduleChromeOffsets).observe(toolbar);
})();
