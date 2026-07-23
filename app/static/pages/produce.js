const UI = globalThis.AlexandriaUI;
const INITIAL_CHUNK_LIMIT = 150;
const CHUNK_BATCH_SIZE = 150;
const FILTERS = Object.freeze([
  ["all", "All"],
  ["needs_attention", "Needs attention"],
  ["ready", "Ready"],
  ["current", "Current"],
  ["stale", "Stale"],
  ["failed", "Failed"],
  ["needs_listening", "Needs listening"],
  ["missing_voice", "Blocked"],
]);

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null || value === "" ? "Not available" : String(value);
  return node;
}

function words(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function initials(value) {
  const parts = String(value || "Narrator").trim().split(/\s+/).filter(Boolean);
  return parts.slice(0, 2).map((part) => part[0]?.toUpperCase() || "").join("") || "N";
}

function duration(milliseconds) {
  if (!Number.isFinite(Number(milliseconds))) return "Not generated";
  const seconds = Math.max(0, Math.round(Number(milliseconds) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function readableState(state) {
  return {
    ready: { label: "Ready to generate", tone: "information" },
    generating: { label: "Generating", tone: "information" },
    needs_listening: { label: "Needs listening", tone: "warning" },
    needs_review: { label: "Needs review", tone: "warning" },
    current: { label: "Current", tone: "success" },
    stale: { label: "Stale", tone: "warning" },
    failed: { label: "Failed", tone: "error" },
    missing_voice: { label: "Blocked", tone: "error" },
  }[state] || { label: words(state || "Not started"), tone: "neutral" };
}

function addStyle() {
  const existing = document.querySelector('link[data-page-style="produce-export"]');
  if (existing) return { node: existing, owned: false };
  const node = document.createElement("link");
  node.rel = "stylesheet";
  node.href = "/static/styles/pages/produce_export.css";
  node.dataset.pageStyle = "produce-export";
  document.head.append(node);
  return { node, owned: true };
}

function waitForStyle(node, signal) {
  if (node.sheet) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      node.removeEventListener("load", loaded);
      node.removeEventListener("error", failed);
      signal.removeEventListener("abort", aborted);
    };
    const loaded = () => { cleanup(); resolve(); };
    const failed = () => { cleanup(); reject(new Error("Produce and Export styles could not load.")); };
    const aborted = () => { cleanup(); reject(signal.reason || new DOMException("Navigation canceled", "AbortError")); };
    node.addEventListener("load", loaded, { once: true });
    node.addEventListener("error", failed, { once: true });
    signal.addEventListener("abort", aborted, { once: true });
  });
}

function pageOwner(route) {
  const owner = document.createElement("article");
  owner.className = "produce-page";
  owner.dataset.routeOwner = route.path;
  owner.dataset.producePage = "";
  owner.dataset.pageState = "loading";
  const title = UI.pageTitleBlock({
    title: "Produce",
    subtitle: "Generate, review, and keep every spoken chunk aligned with the current Script and Cast.",
  });
  title.querySelector("h1").dataset.pageHeading = "";
  owner.append(title);
  return owner;
}

export async function mount({ root, route, shell, api, signal }) {
  if (!UI) throw new Error("Produce requires Alexandria UI primitives.");
  const projectId = route.projectId || route.context.project || "";
  const style = addStyle();
  const owner = pageOwner(route);
  const summary = document.createElement("section");
  summary.className = "produce-summary";
  summary.setAttribute("aria-label", "Production summary");
  const toolbar = document.createElement("div");
  toolbar.className = "produce-toolbar";
  const content = document.createElement("section");
  content.className = "produce-content";
  content.setAttribute("aria-label", "Audio chunks");
  owner.append(summary, toolbar, content);
  root.replaceChildren(owner);

  let aggregate = null;
  let selected = null;
  let disposed = false;
  let loadEpoch = 0;
  let actionBusy = false;
  let actionMessage = null;
  let pollTimer = null;
  let popover = null;
  let regenerateDialog = null;
  let inspectorRequested = false;
  let visibleChunkLimit = INITIAL_CHUNK_LIMIT;
  const activeFilters = new Set();

  function inspectorState() {
    const breakpoint = Number.parseFloat(getComputedStyle(document.documentElement)
      .getPropertyValue("--breakpoint-inspector"));
    return inspectorRequested || innerWidth >= breakpoint ? "open" : "collapsed";
  }

  function tracker() {
    shell.tracker.set({
      script: "complete",
      cast: "complete",
      produce: "current",
      export: aggregate?.summary?.complete ? "future" : "blocked",
    });
  }

  function header() {
    const counts = aggregate?.counts || {};
    const running = Boolean(aggregate?.process?.running);
    const complete = Boolean(aggregate?.summary?.complete);
    const blockers = Number(aggregate?.summary?.blocker_count) || 0;
    let primaryAction = null;
    if (running) {
      primaryAction = {
        label: "Cancel generation",
        attributes: { "data-produce-primary": "" },
        disabled: actionBusy,
        state: actionBusy ? "loading" : "default",
        onClick: cancelGeneration,
      };
    } else if (aggregate?.primary_action && (Number(counts.ready) + Number(counts.stale) > 0)) {
      primaryAction = {
        label: aggregate.primary_action.label || "Generate audio",
        attributes: { "data-produce-primary": "" },
        disabled: actionBusy,
        state: actionBusy ? "loading" : "default",
        onClick: () => executePlan("missing_stale"),
      };
    }
    shell.header.set({
      projectTitle: route.projectTitle || projectId || "Project workspace",
      save: { state: "saved", label: "Saved" },
      status: {
        tone: running ? "information" : complete ? "success" : blockers ? "warning" : "information",
        label: running ? "Generating" : complete ? "Produce complete" : blockers ? `${blockers} need attention` : "Ready to produce",
      },
      primaryAction,
    });
    tracker();
  }

  function renderLoading() {
    owner.dataset.pageState = "loading";
    summary.replaceChildren(UI.skeleton({ label: "Loading production summary" }));
    toolbar.replaceChildren(UI.skeleton({ label: "Loading audio filters" }));
    content.replaceChildren(
      UI.skeleton({ label: "Loading audio chunk" }),
      UI.skeleton({ label: "Loading audio chunk" }),
      UI.skeleton({ label: "Loading audio chunk" }),
    );
    shell.inspector.set({
      state: inspectorState(),
      title: "Selected chunk",
      content: UI.skeleton({ label: "Loading selected audio chunk" }),
    });
  }

  function renderError(message = "Alexandria could not load production status.") {
    owner.dataset.pageState = "error";
    summary.replaceChildren();
    toolbar.replaceChildren();
    content.replaceChildren(UI.notice({
      tone: "error",
      title: "Produce unavailable",
      body: message,
      live: true,
      action: UI.button({ label: "Retry", variant: "secondary", onClick: loadProduce }),
    }));
    shell.inspector.set({ state: "collapsed", title: "Selected chunk", content: null });
    shell.header.set({
      projectTitle: route.projectTitle || projectId || "Project workspace",
      save: { state: "saved", label: "Saved" },
      status: { tone: "error", label: "Unavailable" },
      primaryAction: null,
    });
    tracker();
  }

  function countNeedsAttention(counts = {}) {
    return ["ready", "stale", "failed", "needs_listening", "needs_review", "missing_voice"]
      .reduce((total, key) => total + (Number(counts[key]) || 0), 0);
  }

  function stat(label, value, tone = "neutral") {
    const node = document.createElement("div");
    node.className = "produce-stat";
    node.append(text("strong", "produce-stat__value", value), text("span", "metadata", label));
    node.dataset.tone = tone;
    return node;
  }

  function renderSummary() {
    const counts = aggregate.counts || {};
    summary.replaceChildren(
      stat("Current", Number(counts.current) || 0, "success"),
      stat("Need attention", countNeedsAttention(counts), "warning"),
      stat("Ready to generate", Number(counts.ready) || 0),
      stat("Stale", Number(counts.stale) || 0, "warning"),
      stat("Failed", Number(counts.failed) || 0, "error"),
    );
    if (aggregate.process?.running) {
      const total = Number(aggregate.process.total_count) || 0;
      const completed = Number(aggregate.process.completed_count) || 0;
      const progress = UI.progress({
        label: "Audio generation",
        state: total ? "running" : "indeterminate",
        value: total ? Math.round((completed / total) * 100) : 0,
        message: total ? `${completed} of ${total} chunks finished.` : "Preparing audio generation.",
      });
      progress.classList.add("produce-summary__progress");
      summary.append(progress);
    }
  }

  function matchesFilters(chunk) {
    if (!activeFilters.size) return true;
    return [...activeFilters].some((filter) => {
      if (filter === "needs_attention") {
        return ["ready", "stale", "failed", "needs_listening", "needs_review", "missing_voice"].includes(chunk.state);
      }
      return chunk.state === filter;
    });
  }

  function renderToolbar() {
    const filters = document.createElement("div");
    filters.className = "produce-filters";
    filters.setAttribute("aria-label", "Filter audio chunks");
    FILTERS.forEach(([value, label]) => {
      const counts = aggregate.counts || {};
      const count = value === "all" ? (aggregate.chunks?.length || 0)
        : value === "needs_attention" ? countNeedsAttention(counts)
          : Number(counts[value]) || 0;
      const chip = UI.filterChip({
        label: `${label} ${count.toLocaleString()}`,
        pressed: value === "all" ? activeFilters.size === 0 : activeFilters.has(value),
        multiple: value !== "all",
      });
      chip.querySelector("button").addEventListener("click", () => {
        if (value === "all") activeFilters.clear();
        else if (activeFilters.has(value)) activeFilters.delete(value);
        else activeFilters.add(value);
        visibleChunkLimit = INITIAL_CHUNK_LIMIT;
        renderToolbar();
        renderRows();
      });
      filters.append(chip);
    });
    const actions = document.createElement("div");
    actions.className = "produce-toolbar__actions";
    if (Number(aggregate.summary?.failed_count) > 0 || Number(aggregate.counts?.failed) > 0) {
      actions.append(UI.button({
        label: "Retry failed",
        variant: "secondary",
        size: "compact",
        attributes: { "data-produce-action": "retry" },
        disabled: actionBusy || aggregate.process?.running,
        onClick: () => executePlan("retry_failed", [], "/api/produce/retry-failed"),
      }));
    }
    const more = UI.button({
      label: "More actions",
      variant: "quiet",
      size: "compact",
      disabled: actionBusy || aggregate.process?.running,
    });
    popover?.popoverCleanup?.();
    popover = UI.popover({
      opener: more,
      label: "Produce actions",
      items: [{
        label: "Regenerate all audio",
        onSelect: () => regenerateDialog?.open(more),
      }],
    });
    regenerateDialog = UI.dialog({
      title: "Regenerate all audio?",
      body: "Every current audio file will become obsolete and be generated again from the latest Script, Cast, and direction.",
      confirmLabel: "Regenerate all",
      destructive: true,
      onConfirm: () => executePlan("regenerate_all", [], "/api/produce/generate", true),
    });
    actions.append(popover);
    toolbar.replaceChildren(filters, actions);
  }

  function selectChunk(chunk, row) {
    selected = chunk;
    inspectorRequested = true;
    aggregate.selected_chunk_id = chunk.chunk_id;
    content.querySelectorAll("[data-audio-row]").forEach((item) => {
      const current = item === row;
      item.setAttribute("aria-selected", String(current));
      item.tabIndex = current ? 0 : -1;
    });
    renderInspector();
  }

  function audioTransport(chunk, detailed = false) {
    const available = Boolean(chunk.audio?.available || chunk.audio?.stale_audio_available);
    const stale = chunk.state === "stale";
    const transport = document.createElement("div");
    transport.className = detailed ? "produce-inspector__transport" : "audio-row__transport";
    const play = UI.compactPlay({
      state: available ? "ready" : chunk.state === "failed" ? "failed" : "disabled",
      label: available
        ? `Play ${stale ? "stale " : ""}audio for chunk ${chunk.index}`
        : `Audio unavailable for chunk ${chunk.index}`,
    });
    if (available) {
      play.addEventListener("click", (event) => {
        event.stopPropagation();
        shell.player.set({
          state: "playing",
          title: `${chunk.character_name || chunk.speaker || "Narrator"} · Chunk ${chunk.index}`,
          subtitle: stale ? "Stale sample · regenerate before export" : `${readableState(chunk.state).label} audio`,
        });
      });
    }
    transport.append(
      play,
      UI.waveform({
        value: 0,
        maximum: Math.max(1, Math.round((Number(chunk.duration_ms) || 1000) / 1000)),
        label: `Audio position for chunk ${chunk.index}`,
        disabled: !available,
      }),
    );
    if (detailed) transport.append(text("span", "metadata", available
      ? stale ? "Stale sample" : `Duration ${duration(chunk.duration_ms)}`
      : "No generated audio"));
    return transport;
  }

  function chunkRow(chunk, index) {
    const row = document.createElement("li");
    row.className = "audio-row";
    row.dataset.audioRow = "";
    row.dataset.audioState = chunk.state || "";
    row.setAttribute("aria-selected", String(chunk.chunk_id === selected?.chunk_id));
    row.tabIndex = chunk.chunk_id === selected?.chunk_id || (!selected && index === 0) ? 0 : -1;
    const identity = document.createElement("div");
    identity.className = "audio-row__identity";
    const characterName = chunk.character_name || chunk.speaker || "Narrator";
    const identityCopy = document.createElement("div");
    identityCopy.className = "audio-row__identity-copy";
    identityCopy.append(
      text("strong", "audio-row__speaker", characterName),
      text("span", "metadata", `Chunk ${chunk.index}`),
    );
    identity.append(UI.monogram({
      initials: initials(characterName),
      label: `Monogram for ${characterName}`,
    }), identityCopy);
    const excerpt = document.createElement("div");
    excerpt.className = "audio-row__excerpt";
    excerpt.append(
      text("span", "", chunk.text_excerpt || chunk.text || "No script text"),
      text("span", "metadata audio-row__direction-inline", chunk.delivery_direction || "No delivery direction"),
    );
    const direction = text("span", "audio-row__direction", chunk.delivery_direction || "No delivery direction");
    const chunkDuration = text("span", "timecode audio-row__duration", duration(chunk.duration_ms));
    const state = readableState(chunk.state);
    const status = UI.status({ ...state, domain: "audio", value: chunk.state });
    const action = UI.button({
      label: chunk.regenerate_action?.label || "Unavailable",
      variant: "quiet",
      size: "compact",
      disabled: actionBusy || !chunk.regenerate_action || aggregate.process?.running,
      onClick: (event) => {
        event.stopPropagation();
        executePlan("selected", [chunk.chunk_id]);
      },
    });
    row.append(identity, excerpt, direction, chunkDuration, audioTransport(chunk), status, action);
    row.addEventListener("click", () => selectChunk(chunk, row));
    row.addEventListener("keydown", (event) => {
      if (!["ArrowUp", "ArrowDown", "Home", "End", "Enter", " "].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Enter" || event.key === " ") {
        selectChunk(chunk, row);
        return;
      }
      const rows = [...content.querySelectorAll("[data-audio-row]")];
      const current = rows.indexOf(row);
      const target = event.key === "Home" ? rows[0] : event.key === "End" ? rows.at(-1)
        : rows[(current + (event.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length];
      target?.click();
      target?.focus();
    });
    return row;
  }

  function renderRows() {
    content.replaceChildren();
    if (actionMessage) {
      content.append(UI.notice({
        tone: actionMessage.tone,
        title: actionMessage.title,
        body: actionMessage.body,
        live: true,
      }));
    }
    const chunks = (aggregate.chunks || []).filter(matchesFilters);
    if (!chunks.length) {
      const empty = UI.emptyState({
        title: aggregate.chunks?.length ? "No chunks match these filters" : "No audio chunks yet",
        body: aggregate.chunks?.length
          ? "Choose All or remove a filter to see the production list."
          : "Review Script and Cast before generating audio.",
        action: aggregate.chunks?.length ? null : UI.button({
          label: "Review Script",
          variant: "secondary",
          onClick: () => shell.navigate(shell.routes.routeForPath("script",
            projectId ? { project: projectId } : {}).hash),
        }),
      });
      content.append(empty);
      owner.dataset.pageState = aggregate.chunks?.length ? "ready" : "empty";
      return;
    }
    const visibleChunks = chunks.slice(0, visibleChunkLimit);
    if (selected && matchesFilters(selected)
      && !visibleChunks.some((chunk) => chunk.chunk_id === selected.chunk_id)) {
      const selectedChunk = chunks.find((chunk) => chunk.chunk_id === selected.chunk_id);
      if (selectedChunk) visibleChunks.push(selectedChunk);
    }
    const headerRow = document.createElement("div");
    headerRow.className = "audio-table__header";
    headerRow.setAttribute("aria-hidden", "true");
    ["Character", "Text excerpt", "Delivery direction", "Duration", "Audio", "State", "Action"].forEach((label) => {
      headerRow.append(text("span", "", label));
    });
    const rows = document.createElement("ul");
    rows.className = "audio-table";
    rows.setAttribute("aria-label", "Audio chunks");
    visibleChunks.forEach((chunk, index) => rows.append(chunkRow(chunk, index)));
    const footer = document.createElement("div");
    footer.className = "collection-footer";
    footer.dataset.produceCollectionFooter = "";
    footer.append(text("span", "metadata",
      `Showing ${visibleChunks.length.toLocaleString()} of ${chunks.length.toLocaleString()} chunks`));
    if (visibleChunkLimit < chunks.length) {
      const remaining = chunks.length - visibleChunkLimit;
      footer.append(UI.button({
        label: `Load ${Math.min(CHUNK_BATCH_SIZE, remaining).toLocaleString()} more`,
        variant: "secondary",
        size: "compact",
        attributes: { "data-produce-load-more": "" },
        onClick: () => {
          visibleChunkLimit += CHUNK_BATCH_SIZE;
          renderRows();
        },
      }));
    }
    content.append(headerRow, rows, footer);
    owner.dataset.pageState = aggregate.process?.running ? "running"
      : aggregate.state === "blocked" ? "blocked" : chunks.length > 20 ? "dense" : "ready";
  }

  function blockerContent(blockers) {
    const list = document.createElement("div");
    list.className = "produce-inspector__blockers";
    blockers.forEach((blocker) => {
      const action = blocker.native_destination ? UI.button({
        label: blocker.native_destination === "cast" ? "Open Cast" : `Open ${words(blocker.native_destination)}`,
        variant: "secondary",
        size: "compact",
        onClick: () => shell.navigate(shell.routes.routeForPath(blocker.native_destination, {
          ...(projectId ? { project: projectId } : {}),
          source: blocker.target_id || selected.chunk_id,
        }).hash),
      }) : null;
      list.append(UI.notice({
        tone: blocker.blocking === false ? "information" : "warning",
        title: blocker.title || "Production blocker",
        body: blocker.explanation || "Resolve this item before generating audio.",
        action,
      }));
    });
    return list;
  }

  function renderInspector() {
    if (!selected) {
      shell.inspector.set({
        state: "collapsed",
        title: "Selected chunk",
        content: text("p", "metadata", "Choose an audio chunk to review its Script, Voice, and sample."),
      });
      return;
    }
    const body = document.createElement("div");
    body.className = "produce-inspector";
    const state = readableState(selected.state);
    const identity = document.createElement("header");
    identity.className = "produce-inspector__header";
    identity.append(
      text("div", "metadata", `Chunk ${selected.index}`),
      text("h3", "entity-title", selected.character_name || selected.speaker || "Narrator"),
      UI.status({ ...state, domain: "audio", value: selected.state }),
    );
    const script = UI.flatSection({
      title: "Script and direction",
      content: [
        text("p", "produce-inspector__script", selected.text || selected.text_excerpt || "No script text"),
        text("p", "metadata", selected.delivery_direction || "No delivery direction"),
        text("p", "metadata", `Pause after: ${Number(selected.pause_after_ms) || 0} ms`),
      ],
    });
    const voice = UI.flatSection({
      title: "Production Voice",
      content: text("p", "", selected.voice?.configuration_key
        || (selected.voice?.valid ? "Assigned in Cast" : "No valid Voice assigned")),
    });
    const audio = UI.flatSection({
      title: "Audio sample",
      content: audioTransport(selected, true),
    });
    const history = UI.flatSection({
      title: "Generation history",
      body: selected.reason
        ? `Current sample status: ${String(selected.reason).replaceAll("_", " ")}`
        : "No earlier generation receipt is available for this chunk.",
    });
    body.append(identity, script, voice, audio, history);
    if (selected.blockers?.length) body.append(blockerContent(selected.blockers));
    if (selected.regenerate_action && !aggregate.process?.running) {
      body.append(UI.button({
        label: selected.regenerate_action.label || "Regenerate chunk",
        variant: "primary",
        attributes: { "data-produce-selected-action": "" },
        disabled: actionBusy,
        onClick: () => executePlan("selected", [selected.chunk_id]),
      }));
    }
    shell.inspector.set({ state: inspectorState(), title: "Selected chunk", content: body });
  }

  function render() {
    header();
    renderSummary();
    renderToolbar();
    renderRows();
    renderInspector();
    if (aggregate.process?.running) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(loadProduce, 1500);
    }
  }

  async function executePlan(mode, selectedChunkIds = [], endpoint = "/api/produce/generate", confirm = false) {
    if (actionBusy || disposed || signal.aborted) return;
    actionBusy = true;
    actionMessage = null;
    render();
    const planResponse = await api.post("/api/produce/plan", {
      mode,
      selected_chunk_ids: selectedChunkIds,
    }, { signal });
    if (disposed || signal.aborted) return;
    if (!planResponse.ok) {
      actionBusy = false;
      actionMessage = { tone: "error", title: "Audio plan unavailable", body: planResponse.error };
      render();
      return;
    }
    const plan = planResponse.data || {};
    if (!plan.safe_to_execute) {
      actionBusy = false;
      actionMessage = {
        tone: "warning",
        title: "Generation is blocked",
        body: plan.empty_reason || plan.blockers?.[0]?.explanation || "Resolve the listed blockers before generating audio.",
      };
      render();
      return;
    }
    const executeResponse = await api.post(endpoint, {
      mode,
      selected_chunk_ids: selectedChunkIds,
      plan_fingerprint: plan.plan_fingerprint,
      chunks_fingerprint: plan.chunks_fingerprint,
      confirm_regenerate_all: confirm,
    }, { signal });
    if (disposed || signal.aborted) return;
    actionBusy = false;
    if (!executeResponse.ok) {
      actionMessage = { tone: "error", title: "Audio generation did not start", body: executeResponse.error };
      render();
      return;
    }
    actionMessage = { tone: "success", title: "Audio generation started", body: "Alexandria accepted the reviewed generation plan." };
    await loadProduce(false);
  }

  async function cancelGeneration() {
    if (actionBusy || disposed || signal.aborted) return;
    actionBusy = true;
    header();
    const response = await api.post("/api/produce/cancel", {}, { signal });
    if (disposed || signal.aborted) return;
    actionBusy = false;
    actionMessage = response.ok
      ? { tone: "information", title: "Cancellation requested", body: "Running audio work will stop at the next safe boundary." }
      : { tone: "error", title: "Could not cancel generation", body: response.error };
    await loadProduce(false);
  }

  async function loadProduce(showLoading = true) {
    const epoch = ++loadEpoch;
    clearTimeout(pollTimer);
    if (showLoading) renderLoading();
    const query = new URLSearchParams();
    const selectedId = selected?.chunk_id || route.context.chunk;
    if (selectedId) query.set("selected_chunk_id", selectedId);
    const response = await api.get(`/api/produce${query.size ? `?${query}` : ""}`, { signal });
    if (disposed || signal.aborted || epoch !== loadEpoch) return;
    if (!response.ok) {
      if (response.kind !== "canceled") renderError(response.error);
      return;
    }
    aggregate = response.data || {};
    selected = aggregate.selected_chunk
      || aggregate.chunks?.find((chunk) => chunk.chunk_id === aggregate.selected_chunk_id)
      || aggregate.chunks?.[0]
      || null;
    render();
  }

  const cleanup = () => {
    if (disposed) return;
    disposed = true;
    loadEpoch += 1;
    clearTimeout(pollTimer);
    popover?.popoverCleanup?.();
    regenerateDialog?.forceClose?.();
    shell.inspector.close();
    if (style.owned) style.node.remove();
    signal.removeEventListener("abort", cleanup);
  };
  signal.addEventListener("abort", cleanup, { once: true });

  shell.player.set({ state: "inactive", title: "No active production audio" });
  renderLoading();
  header();
  await waitForStyle(style.node, signal);
  await loadProduce(false);
  return cleanup;
}
