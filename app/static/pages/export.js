const UI = globalThis.AlexandriaUI;
const FORMATS = Object.freeze([
  { value: "m4b", label: "M4B audiobook", extension: ".m4b" },
  { value: "mp3", label: "MP3 audio file", extension: ".mp3" },
  { value: "audacity", label: "Audacity project package", extension: ".zip" },
  { value: "chapter_separated", label: "Separate chapter files", extension: "", disabled: true },
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

function clock(milliseconds) {
  const total = Math.max(0, Math.round((Number(milliseconds) || 0) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function bytes(value) {
  const size = Number(value);
  if (!Number.isFinite(size) || size <= 0) return "Calculated during build";
  if (size >= 1_000_000_000) return `${(size / 1_000_000_000).toFixed(1)} GB`;
  if (size >= 1_000_000) return `${Math.round(size / 1_000_000)} MB`;
  return `${Math.round(size / 1000)} KB`;
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
  owner.className = "export-page";
  owner.dataset.routeOwner = route.path;
  owner.dataset.exportPage = "";
  owner.dataset.pageState = "loading";
  const title = UI.pageTitleBlock({
    title: "Export",
    subtitle: "Review publication details before building the final output.",
  });
  title.querySelector("h1").dataset.pageHeading = "";
  owner.append(title);
  return owner;
}

function panel(className, title, metadata = "") {
  const node = document.createElement("section");
  node.className = `export-panel ${className}`;
  const heading = document.createElement("header");
  heading.className = "export-panel__heading";
  heading.append(text("h2", "section-title", title));
  if (metadata) heading.append(text("span", "metadata", metadata));
  node.append(heading);
  return node;
}

export async function mount({ root, route, shell, api, signal }) {
  if (!UI) throw new Error("Export requires Alexandria UI primitives.");
  const projectId = route.projectId || route.context.project || "";
  const style = addStyle();
  const owner = pageOwner(route);
  const readiness = document.createElement("section");
  readiness.className = "export-readiness";
  readiness.setAttribute("aria-label", "Export preflight");
  const workspace = document.createElement("div");
  workspace.className = "export-grid";
  owner.append(readiness, workspace);
  root.replaceChildren(owner);

  let aggregate = null;
  let disposed = false;
  let actionBusy = false;
  let actionMessage = null;
  let loadEpoch = 0;
  let pollTimer = null;
  let selectedFormat = "m4b";
  let controls = {};

  function selectedOutput() {
    return aggregate?.outputs?.[selectedFormat]
      || aggregate?.selected_outputs?.find((output) => output.format === selectedFormat)
      || aggregate?.selected_outputs?.[0]
      || null;
  }

  function hardBlockers() {
    return (aggregate?.blockers || []).filter((blocker) => blocker.code !== "export_metadata_missing");
  }

  function formMetadata() {
    return {
      title: controls.title?.value?.trim() || "",
      author: controls.author?.value?.trim() || "",
      narrator: controls.narrator?.value?.trim() || "",
      year: controls.year?.value?.trim() || "",
      description: controls.description?.value?.trim() || "",
    };
  }

  function canBuild() {
    const metadata = formMetadata();
    return !aggregate?.process?.running
      && hardBlockers().length === 0
      && Boolean(metadata.title && metadata.author)
      && Boolean(aggregate?.chapters?.length);
  }

  function tracker() {
    shell.tracker.set({
      script: "complete",
      cast: "complete",
      produce: "complete",
      export: aggregate?.summary?.complete ? "complete" : "current",
    });
  }

  function header() {
    const running = Boolean(aggregate?.process?.running);
    const complete = Boolean(aggregate?.summary?.complete);
    const blockerCount = Number(aggregate?.summary?.blocker_count) || 0;
    let primaryAction = null;
    if (running) {
      primaryAction = {
        label: "Cancel build",
        attributes: { "data-export-primary": "" },
        disabled: actionBusy,
        state: actionBusy ? "loading" : "default",
        onClick: cancelBuild,
      };
    } else if (canBuild()) {
      primaryAction = {
        label: complete ? "Build again" : "Build Audiobook",
        attributes: { "data-export-primary": "" },
        disabled: actionBusy,
        state: actionBusy ? "loading" : "default",
        onClick: buildExport,
      };
    }
    shell.header.set({
      projectTitle: route.projectTitle || projectId || "Project workspace",
      save: { state: "saved", label: "Saved" },
      status: {
        tone: running ? "information" : complete ? "success" : canBuild() ? "success" : blockerCount ? "warning" : "information",
        label: running ? "Building audiobook…" : complete ? "Built" : canBuild() ? "Ready to build"
          : `${blockerCount} item${blockerCount === 1 ? "" : "s"} need attention`,
      },
      primaryAction,
    });
    tracker();
  }

  function renderLoading() {
    owner.dataset.pageState = "loading";
    readiness.replaceChildren(UI.skeleton({ label: "Checking final output" }));
    workspace.replaceChildren(
      UI.skeleton({ label: "Loading publication details" }),
      UI.skeleton({ label: "Loading chapters" }),
      UI.skeleton({ label: "Loading output validation" }),
    );
    shell.inspector.set({ state: "collapsed", title: "Export details", content: null });
  }

  function renderError(message = "Alexandria could not load Export status.") {
    owner.dataset.pageState = "error";
    readiness.replaceChildren();
    workspace.replaceChildren(UI.notice({
      tone: "error",
      title: "Export unavailable",
      body: message,
      live: true,
      action: UI.button({ label: "Retry", variant: "secondary", onClick: loadExport }),
    }));
    shell.header.set({
      projectTitle: route.projectTitle || projectId || "Project workspace",
      save: { state: "saved", label: "Saved" },
      status: { tone: "error", label: "Unavailable" },
      primaryAction: null,
    });
    tracker();
  }

  function blockerAction(blocker) {
    if (!blocker.native_destination) return null;
    return UI.button({
      label: `Open ${words(blocker.native_destination)}`,
      variant: "secondary",
      size: "compact",
      onClick: () => shell.navigate(shell.routes.routeForPath(blocker.native_destination, {
        ...(projectId ? { project: projectId } : {}),
        source: blocker.target_id || "export:preflight",
      }).hash),
    });
  }

  function renderReadiness() {
    readiness.replaceChildren();
    if (actionMessage) {
      readiness.append(UI.notice({
        tone: actionMessage.tone,
        title: actionMessage.title,
        body: actionMessage.body,
        live: true,
      }));
    }
    if (aggregate.process?.running) {
      const total = Number(aggregate.process.total_count) || 0;
      const completed = Number(aggregate.process.completed_count) || 0;
      const progress = UI.progress({
        label: "Building audiobook…",
        state: total ? "running" : "indeterminate",
        value: total ? Math.round((completed / total) * 100) : 0,
        message: total ? `${completed} of ${total} output steps finished.` : "Preparing the final output.",
      });
      progress.classList.add("export-progress");
      readiness.append(progress);
      return;
    }
    if (aggregate.summary?.complete) {
      const output = selectedOutput();
      readiness.append(UI.notice({
        tone: "success",
        title: "Audiobook built",
        body: output?.filename
          ? `${output.filename} is the verified current output.`
          : "The selected output was built and verified.",
        live: true,
      }));
      return;
    }
    if (aggregate.blockers?.length) {
      const hard = hardBlockers();
      const metadataCount = aggregate.blockers.length - hard.length;
      const parts = [];
      if (hard.length) parts.push(`${hard.length} production issue${hard.length === 1 ? "" : "s"}`);
      if (metadataCount) parts.push(`${metadataCount} publication detail${metadataCount === 1 ? "" : "s"}`);
      readiness.append(UI.notice({
        tone: "warning",
        title: "Export is blocked",
        body: `${parts.join(" and ")} need attention before the audiobook can be built.`,
        action: hard[0] ? blockerAction(hard[0]) : null,
      }));
      return;
    }
    readiness.append(
      UI.status({ tone: "success", label: "Final preflight is clear", domain: "export", value: "ready" }),
      text("span", "metadata", "The current publication settings can be reviewed and built."),
    );
  }

  function field(name, label, value, options = {}) {
    const wrapper = UI.field({ id: `export-${name}`, label, value, ...options });
    const control = wrapper.querySelector(".field__control");
    controls[name] = control;
    control.addEventListener("input", header);
    control.addEventListener("change", header);
    return wrapper;
  }

  function publicationPanel() {
    const metadata = aggregate.metadata || {};
    const node = panel("export-publication", "Publication");
    const identity = document.createElement("div");
    identity.className = "export-publication__identity";
    const coverUrl = aggregate.cover?.exists && projectId
      ? `/api/projects/${encodeURIComponent(projectId)}/cover`
      : null;
    const cover = UI.sourceCover({
      src: coverUrl,
      alt: coverUrl ? `Cover for ${metadata.title || "audiobook"}` : "",
      label: "Source cover not provided",
    });
    const copy = document.createElement("div");
    copy.append(
      text("h3", "section-title", metadata.title || "Untitled audiobook"),
      text("p", "metadata", metadata.author ? `by ${metadata.author}` : "Author required"),
      text("p", "", metadata.narrator ? `Narrated by ${metadata.narrator}` : "No narrator or cast credits available"),
    );
    identity.append(cover, copy);
    const metadataForm = document.createElement("div");
    metadataForm.className = "export-metadata";
    controls = {};
    metadataForm.append(
      field("title", "Title", metadata.title || "", {
        required: true,
        ...(metadata.title ? {} : { message: "Title is required before build." }),
      }),
      field("author", "Author", metadata.author || "", {
        required: true,
        ...(metadata.author ? {} : { message: "Author is required before build." }),
      }),
      field("narrator", "Narrator", metadata.narrator || ""),
      field("year", "Year", metadata.year || ""),
      field("description", "Description", metadata.description || "", { kind: "textarea" }),
    );
    const current = document.createElement("section");
    current.className = "export-current-take";
    current.append(text("h3", "entity-title", "Final audiobook preview"));
    const player = aggregate.player;
    const output = aggregate.selected_outputs?.find((item) => item.playback_url) || null;
    if (player && output) {
      current.append(
        text("strong", "", "Current Take"),
        text("span", "metadata", `${output.filename} · ${clock(output.duration_ms)}`),
        UI.waveform({
          value: 0,
          maximum: Math.max(1, Math.round((Number(output.duration_ms) || 1000) / 1000)),
          label: `Final audiobook position for ${output.filename}`,
        }),
      );
      shell.player.set({
        state: "active",
        title: metadata.title || output.filename,
        subtitle: `Current Take · ${output.filename}`,
      });
    } else {
      current.append(
        text("strong", "", "No current Take"),
        text("span", "metadata", "Build a verified output to enable final audiobook playback."),
        UI.waveform({ value: 0, maximum: 1, label: "Final audiobook unavailable", disabled: true }),
      );
      shell.player.set({ state: "inactive", title: "No current Export audio" });
    }
    const metrics = document.createElement("div");
    metrics.className = "produce-summary";
    const outputValue = selectedOutput();
    [
      ["Total duration", clock(outputValue?.duration_ms)],
      ["Chapters", aggregate.summary?.chapter_count || aggregate.chapters?.length || 0],
      ["Estimated file size", bytes(outputValue?.size_bytes)],
    ].forEach(([label, value]) => {
      const item = document.createElement("div");
      item.className = "produce-stat";
      item.append(text("strong", "produce-stat__value", value), text("span", "metadata", label));
      metrics.append(item);
    });
    node.append(identity, metadataForm, current, metrics);
    return node;
  }

  function selectChapter(row, chapter) {
    workspace.querySelectorAll("[data-export-chapter]").forEach((item) => {
      if (item === row) item.setAttribute("aria-current", "true");
      else item.removeAttribute("aria-current");
      item.tabIndex = item === row ? 0 : -1;
    });
    if (aggregate.player) {
      shell.player.set({
        state: "active",
        title: chapter.name || `Chapter ${Number(chapter.order) + 1}`,
        subtitle: `Current Take · starts ${clock(chapter.start_ms)}`,
      });
    }
  }

  function chapterRow(chapter, index) {
    const row = document.createElement("li");
    row.className = "export-chapter";
    row.dataset.exportChapter = chapter.chapter_id || String(index);
    row.tabIndex = index === 0 ? 0 : -1;
    const identity = document.createElement("div");
    identity.append(
      text("strong", "", `${Number(chapter.order ?? index) + 1}. ${chapter.name || `Chapter ${index + 1}`}`),
      text("span", "metadata", `Starts ${clock(chapter.start_ms)}`),
    );
    row.append(identity, text("span", "timecode", clock(Number(chapter.end_ms) - Number(chapter.start_ms))));
    row.addEventListener("click", () => selectChapter(row, chapter));
    row.addEventListener("keydown", (event) => {
      if (!["ArrowUp", "ArrowDown", "Home", "End", "Enter", " "].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Enter" || event.key === " ") {
        selectChapter(row, chapter);
        return;
      }
      const rows = [...workspace.querySelectorAll("[data-export-chapter]")];
      const current = rows.indexOf(row);
      const target = event.key === "Home" ? rows[0] : event.key === "End" ? rows.at(-1)
        : rows[(current + (event.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length];
      target?.click();
      target?.focus();
    });
    return row;
  }

  function chapterList(chapters, offset = 0) {
    const list = document.createElement("ol");
    list.className = "export-chapter-list";
    chapters.forEach((chapter, index) => list.append(chapterRow(chapter, offset + index)));
    return list;
  }

  function chaptersPanel() {
    const chapters = aggregate.chapters || [];
    const totalDuration = chapters.reduce((sum, chapter) => (
      sum + Math.max(0, Number(chapter.end_ms) - Number(chapter.start_ms))
    ), 0);
    const node = panel("export-chapters", "Chapters", `${chapters.length} chapters · ${clock(totalDuration)} total`);
    if (!chapters.length) {
      node.append(UI.emptyState({
        title: "No chapters are available to export",
        body: "Review Script chapter structure before building an audiobook.",
        action: UI.button({
          label: "Review Script",
          variant: "secondary",
          onClick: () => shell.navigate(shell.routes.routeForPath("script",
            projectId ? { project: projectId } : {}).hash),
        }),
      }));
      return node;
    }
    const visible = chapters.slice(0, 8);
    node.append(chapterList(visible));
    if (chapters.length > visible.length) {
      node.append(UI.disclosure({
        label: `Show all ${chapters.length} chapters`,
        content: chapterList(chapters.slice(visible.length), visible.length),
      }));
    }
    return node;
  }

  function formatGroup() {
    const group = UI.radioGroup({
      label: "Format",
      name: "export-format",
      options: FORMATS.map((format) => ({
        value: format.value,
        label: format.label,
        disabled: format.disabled,
        checked: selectedFormat === format.value,
      })),
    });
    group.addEventListener("change", (event) => {
      if (!event.target.matches('input[type="radio"]')) return;
      selectedFormat = event.target.value;
      const filename = aggregate.plan?.output_filenames?.[selectedFormat]
        || aggregate.outputs?.[selectedFormat]?.filename
        || `audiobook${FORMATS.find((format) => format.value === selectedFormat)?.extension || ""}`;
      if (controls.filename) controls.filename.value = filename;
      header();
    });
    return group;
  }

  function validationMark(ok, label) {
    const mark = document.createElement("span");
    mark.className = "export-validation__mark";
    mark.dataset.tone = ok ? "success" : "error";
    mark.append(UI.icon(ok ? "check" : "blocked"), text("span", "visually-hidden", `${label}: ${ok ? "ready" : "needs attention"}`));
    return mark;
  }

  function validationSection() {
    const node = document.createElement("section");
    node.className = "export-validation";
    node.append(text("h3", "entity-title", "Final validation"));
    const blockerDestinations = new Set((aggregate.blockers || []).map((blocker) => blocker.native_destination));
    const metadata = formMetadata();
    const checks = [
      ["Script integrity", !blockerDestinations.has("script")],
      ["Voice assignments", !blockerDestinations.has("cast")],
      ["Audio generation", !blockerDestinations.has("produce")],
      ["Chapter structure", Boolean(aggregate.chapters?.length)],
      ["Metadata & credits", Boolean(metadata.title && metadata.author)],
      ["Duration consistency", !aggregate.selected_outputs?.some((output) => output.state === "invalid")],
    ];
    checks.forEach(([label, ok]) => {
      const row = document.createElement("div");
      row.className = "export-validation__row";
      row.append(text("span", "", label), validationMark(ok, label));
      node.append(row);
    });
    return node;
  }

  function technicalDetails() {
    const body = document.createElement("div");
    body.className = "export-output-list";
    Object.values(aggregate.outputs || {}).forEach((output) => {
      const row = document.createElement("div");
      row.className = "export-output-row";
      row.append(
        text("span", "", `${words(output.format)} · ${output.filename}`),
        text("span", "metadata", words(output.state || "missing")),
      );
      body.append(row);
    });
    body.append(text("p", "metadata", "Outputs are written to the current project output folder and verified before becoming current."));
    return UI.disclosure({ label: "Technical Details", content: body });
  }

  function outputPanel() {
    const node = panel("export-output", "Output");
    const fields = document.createElement("div");
    fields.className = "export-output-fields";
    const filename = aggregate.plan?.output_filenames?.[selectedFormat]
      || aggregate.outputs?.[selectedFormat]?.filename
      || `audiobook${FORMATS.find((format) => format.value === selectedFormat)?.extension || ""}`;
    fields.append(field("filename", "Output filename", filename, {
      readOnly: true,
      message: "The current exporter uses this canonical verified filename.",
    }));
    const formats = document.createElement("div");
    formats.className = "export-formats";
    formats.append(formatGroup());
    const chapterMode = UI.field({
      id: "export-chapter-mode",
      label: "Chapter grouping",
      kind: "select",
      value: aggregate.chapter_mode || "smart",
      options: [
        { value: "smart", label: "Smart chapters" },
        { value: "per_chunk", label: "One chapter per chunk" },
        { value: "none", label: "No embedded chapters" },
      ],
    });
    controls.chapterMode = chapterMode.querySelector("select");
    const folder = UI.field({
      label: "Output folder",
      value: "Current project output folder",
      readOnly: true,
      message: "The active backend writes verified outputs inside the current project.",
    });
    fields.append(formats, chapterMode, folder);
    node.append(fields, validationSection(), technicalDetails());
    return node;
  }

  function renderWorkspace() {
    workspace.replaceChildren();
    controls = {};
    const publication = publicationPanel();
    const chapters = chaptersPanel();
    const output = outputPanel();
    workspace.append(publication, chapters, output);
    if (!aggregate.chapters?.length) owner.dataset.pageState = "empty";
    else if (aggregate.process?.running) owner.dataset.pageState = "running";
    else if (aggregate.summary?.complete) owner.dataset.pageState = "complete";
    else if (aggregate.state === "blocked") owner.dataset.pageState = "blocked";
    else if (aggregate.chapters.length > 20) owner.dataset.pageState = "dense";
    else owner.dataset.pageState = "ready";
  }

  function render() {
    selectedFormat = aggregate.formats?.[0] || selectedFormat;
    renderWorkspace();
    renderReadiness();
    header();
    if (aggregate.process?.running) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(loadExport, 1500);
    }
  }

  async function buildExport() {
    if (actionBusy || !canBuild() || disposed || signal.aborted) return;
    actionBusy = true;
    actionMessage = null;
    header();
    const request = {
      metadata: formMetadata(),
      formats: [selectedFormat],
      chapter_mode: controls.chapterMode?.value || aggregate.chapter_mode || "smart",
    };
    const planResponse = await api.post("/api/export/plan", request, { signal });
    if (disposed || signal.aborted) return;
    if (!planResponse.ok) {
      actionBusy = false;
      actionMessage = { tone: "error", title: "Final preflight failed", body: planResponse.error };
      renderReadiness();
      header();
      return;
    }
    const plan = planResponse.data || {};
    if (!plan.safe_to_execute) {
      actionBusy = false;
      actionMessage = {
        tone: "warning",
        title: "Export is blocked",
        body: plan.blockers?.[0]?.explanation || "Resolve final preflight blockers before building.",
      };
      renderReadiness();
      header();
      return;
    }
    const buildResponse = await api.post("/api/export/build", {
      ...request,
      plan_fingerprint: plan.plan_fingerprint,
      dependency_fingerprint: plan.dependency_fingerprint,
    }, { signal });
    if (disposed || signal.aborted) return;
    actionBusy = false;
    if (!buildResponse.ok) {
      actionMessage = {
        tone: "error",
        title: "Audiobook could not be built",
        body: `${buildResponse.error}. Existing generated audio and settings are unchanged.`,
      };
      renderReadiness();
      header();
      return;
    }
    actionMessage = { tone: "success", title: "Build started", body: "Alexandria accepted the reviewed Export plan." };
    await loadExport(false);
  }

  async function cancelBuild() {
    if (actionBusy || disposed || signal.aborted) return;
    actionBusy = true;
    header();
    const response = await api.post("/api/export/cancel", {}, { signal });
    if (disposed || signal.aborted) return;
    actionBusy = false;
    actionMessage = response.ok
      ? { tone: "information", title: "Cancellation requested", body: "The Export build will stop at the next safe boundary." }
      : { tone: "error", title: "Could not cancel Export", body: response.error };
    await loadExport(false);
  }

  async function loadExport(showLoading = true) {
    const epoch = ++loadEpoch;
    clearTimeout(pollTimer);
    if (showLoading) renderLoading();
    const response = await api.get("/api/export", { signal });
    if (disposed || signal.aborted || epoch !== loadEpoch) return;
    if (!response.ok) {
      if (response.kind !== "canceled") renderError(response.error);
      return;
    }
    aggregate = response.data || {};
    render();
  }

  const cleanup = () => {
    if (disposed) return;
    disposed = true;
    loadEpoch += 1;
    clearTimeout(pollTimer);
    shell.inspector.close();
    if (style.owned) style.node.remove();
    signal.removeEventListener("abort", cleanup);
  };
  signal.addEventListener("abort", cleanup, { once: true });

  shell.player.set({ state: "inactive", title: "No current Export audio" });
  renderLoading();
  header();
  await waitForStyle(style.node, signal);
  await loadExport(false);
  return cleanup;
}
