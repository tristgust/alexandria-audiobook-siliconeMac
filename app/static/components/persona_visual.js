const UI = globalThis.AlexandriaUI;
const STATE_ATTRIBUTE = "data-persona-state";
const POLL_INTERVAL = 1200;

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null || value === "" ? "Not yet described" : String(value);
  return node;
}

function list(values, emptyCopy) {
  const items = Array.isArray(values) ? values.filter((value) => value != null && value !== "") : [];
  if (!items.length) return text("p", "persona-visual__muted", emptyCopy);
  const node = document.createElement("ul");
  node.className = "persona-visual__list";
  items.forEach((value) => node.append(text("li", "", typeof value === "string"
    ? value : value.summary || value.description || value.label || "Supporting detail")));
  return node;
}

function stateFor(status, entry, failed) {
  if (failed || entry?.status === "invalid") return "error";
  if (status?.context_error || !status?.approved_roster_available) return "disabled";
  if (status?.process?.running || status?.progress?.status === "running") return "running";
  if (entry?.status === "complete") return "completed";
  return "idle";
}

export function createPersonaVisual({ api, character, signal }) {
  if (!UI) throw new Error("Persona Visual requires Alexandria UI primitives.");
  const root = document.createElement("div");
  root.className = "persona-visual";
  root.dataset.personaVisual = "";
  root.setAttribute(STATE_ATTRIBUTE, "idle");
  root.setAttribute("aria-live", "polite");

  const entryId = character?.appearance?.entry_id
    || character?.character_id
    || character?.canonical_name;
  let timer = null;
  let disposed = false;
  let status = null;
  let dossier = null;
  let failed = false;
  let enabled = false;

  const stopPolling = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };

  const schedule = () => {
    stopPolling();
    if (!disposed && !signal.aborted) timer = setTimeout(refresh, POLL_INTERVAL);
  };

  function selectedEntry() {
    return (status?.entries || []).find((entry) => (
      String(entry.entry_id) === String(entryId)
      || entry.canonical_name === character?.canonical_name
      || entry.display_name === character?.display_name
    )) || null;
  }

  function renderDisabled() {
    const content = document.createDocumentFragment();
    content.append(
      text("p", "persona-visual__muted",
        status?.context_error
          ? "Appearance details are unavailable until the character roster can be read."
          : "Approve the character roster before collecting optional appearance details."),
    );
    root.replaceChildren(content);
  }

  function renderIdle() {
    const intro = text(
      "p",
      "persona-visual__muted",
      "Optional. Alexandria can collect script-supported appearance details for this character.",
    );
    const checkbox = UI.checkbox({
      checked: enabled,
      label: "Enable appearance collection for this character",
    });
    const input = checkbox.querySelector("input");
    input.dataset.personaEnable = "";
    input.addEventListener("change", () => {
      enabled = input.checked;
      render();
    });
    const collect = UI.button({
      label: "Collect appearance details",
      variant: "secondary",
      disabled: !enabled,
      attributes: { "data-persona-collect": "" },
      onClick: startCollection,
    });
    root.replaceChildren(intro, checkbox, collect);
  }

  function renderRunning() {
    const completed = Number(status?.progress?.completed_passages) || 0;
    const total = Number(status?.progress?.total_passages) || 0;
    const value = total > 0 ? Math.round((completed / total) * 100) : 0;
    const progress = UI.progress({
      label: "Appearance collection",
      state: total > 0 ? "running" : "indeterminate",
      value,
      message: total > 0 ? `${completed} of ${total} script passages reviewed.` : "Reviewing script evidence.",
    });
    const cancel = UI.button({
      label: "Cancel",
      variant: "secondary",
      attributes: { "data-persona-cancel": "" },
      onClick: cancelCollection,
    });
    root.replaceChildren(
      text("p", "persona-visual__muted", "Reviewing script evidence for this character."),
      progress,
      cancel,
    );
  }

  function renderError() {
    const retry = UI.button({
      label: "Retry",
      variant: "secondary",
      attributes: { "data-persona-retry": "" },
      onClick: () => {
        failed = false;
        refresh();
      },
    });
    root.replaceChildren(UI.notice({
      tone: "error",
      title: "Appearance details unavailable",
      body: "Alexandria could not load these optional details. The Cast profile is still safe to use.",
      action: retry,
      live: true,
    }));
  }

  function renderCompleted() {
    const visual = dossier?.visual || {};
    const summary = visual.image_prompt_summary
      || visual.summary
      || selectedEntry()?.image_prompt_summary
      || character?.appearance?.summary;
    const body = document.createElement("div");
    body.className = "persona-visual__dossier";
    if (summary && summary !== character?.appearance?.summary) {
      body.append(text("p", "persona-visual__summary", summary));
    }
    body.append(
      text("h4", "persona-visual__heading", "Stable traits"),
      list(visual.stable_traits || visual.profile?.stable_traits, "No stable traits were stated in the script."),
    );
    const variants = visual.variants || visual.profile?.variants;
    if (Array.isArray(variants) && variants.length) {
      body.append(text("h4", "persona-visual__heading", "Scene variants"), list(variants, ""));
    }
    const conflicts = visual.conflicts || visual.profile?.conflicts;
    if (Array.isArray(conflicts) && conflicts.length) {
      body.append(text("h4", "persona-visual__heading", "Conflicting evidence"), list(conflicts, ""));
    }
    root.replaceChildren(body);
  }

  function render() {
    const state = stateFor(status, selectedEntry(), failed);
    root.setAttribute(STATE_ATTRIBUTE, state);
    root.dataset.personaState = state;
    if (state === "disabled") renderDisabled();
    else if (state === "running") renderRunning();
    else if (state === "error") renderError();
    else if (state === "completed") renderCompleted();
    else renderIdle();
  }

  async function loadDossier() {
    if (!entryId) return;
    const response = await api.get(
      `/api/character_visuals/${encodeURIComponent(entryId)}`,
      { signal },
    );
    if (disposed || signal.aborted) return;
    if (!response.ok) {
      failed = true;
      return;
    }
    dossier = response.data;
  }

  async function refresh() {
    stopPolling();
    if (disposed || signal.aborted) return;
    const response = await api.get("/api/character_visuals/status", { signal });
    if (disposed || signal.aborted) return;
    if (!response.ok) {
      failed = response.kind !== "canceled";
      render();
      return;
    }
    failed = false;
    status = response.data || {};
    const entry = selectedEntry();
    if (entry?.status === "complete") await loadDossier();
    if (disposed || signal.aborted) return;
    render();
    if (stateFor(status, entry, failed) === "running") schedule();
  }

  async function startCollection() {
    if (!enabled || !entryId || disposed || signal.aborted) return;
    const response = await api.post("/api/character_visuals/discover", {
      enabled: true,
      entry_ids: [entryId],
    }, { signal });
    if (disposed || signal.aborted) return;
    if (!response.ok) {
      failed = response.kind !== "canceled";
      render();
      return;
    }
    status = {
      ...(status || {}),
      process: { ...(status?.process || {}), running: true },
      progress: { ...(status?.progress || {}), status: "running" },
    };
    render();
    schedule();
  }

  async function cancelCollection() {
    const response = await api.post("/api/character_visuals/cancel", {}, { signal });
    if (disposed || signal.aborted) return;
    if (!response.ok) {
      failed = response.kind !== "canceled";
      render();
      return;
    }
    await refresh();
  }

  const cleanup = () => {
    if (disposed) return;
    disposed = true;
    stopPolling();
    signal.removeEventListener("abort", cleanup);
  };
  signal.addEventListener("abort", cleanup, { once: true });
  root.cleanup = cleanup;
  root.refresh = refresh;
  render();
  refresh();
  return root;
}
