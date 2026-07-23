import { createPersonaVisual } from "/static/components/persona_visual.js";

// allow: SIZE_OK — B19 ownership keeps this route's state machine in its one assigned page module.
const UI = globalThis.AlexandriaUI;
const FILTERS = [
  ["all", "All"],
  ["needs_attention", "Needs attention"],
  ["unassigned", "Unassigned"],
  ["speaking_roles", "Speaking roles"],
  ["non_speaking", "Non-speaking"],
  ["ready", "Ready"],
];
const VOICE_METHODS = [
  ["custom", "Assigned voice"],
  ["clone", "Cloned voice"],
  ["controlled_clone", "Controlled clone"],
  ["designed_voice", "Designed voice"],
  ["adapter", "Trained adapter"],
  ["alias", "Shared voice"],
];

function text(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null || value === "" ? "Not yet described" : String(value);
  return node;
}

function initials(name) {
  return String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2)
    .map((part) => part[0]?.toUpperCase()).join("") || "?";
}

function words(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function readableStatus(character) {
  if (!character?.speaking_role || character.speaking_role === "non_speaking") {
    return { label: "Non-speaking", tone: "neutral" };
  }
  const state = character.readiness_state;
  if (state === "ready") return { label: "Voice assigned", tone: "success" };
  if (character.identity?.review_required || state === "identity_review" || state === "needs_identity_review") {
    return { label: "Identity review", tone: "warning" };
  }
  if (character.voice?.preview?.status === "missing" || state === "preview_recommended") {
    return { label: "Preview recommended", tone: "warning" };
  }
  return { label: "Missing voice", tone: "error" };
}

function list(values, emptyCopy = "None recorded") {
  const items = Array.isArray(values) ? values.filter((value) => value != null && value !== "") : [];
  if (!items.length) return text("p", "cast-profile__muted", emptyCopy);
  const node = document.createElement("ul");
  node.className = "cast-profile__facts";
  items.forEach((value) => node.append(text(
    "li",
    "",
    typeof value === "string" ? value : value.summary || value.description || value.label,
  )));
  return node;
}

function section(name, title, content) {
  const node = UI.flatSection({ title, content });
  node.classList.add("cast-profile__section");
  node.dataset.castSection = name;
  return node;
}

function addStyle() {
  const existing = document.querySelector('link[data-page-style="cast"]');
  if (existing) return { node: existing, owned: false };
  const node = document.createElement("link");
  node.rel = "stylesheet";
  node.href = "/static/styles/pages/cast.css";
  node.dataset.pageStyle = "cast";
  document.head.append(node);
  return { node, owned: true };
}

export async function mount({ root, route, shell, api, signal }) {
  if (!UI) throw new Error("Cast requires Alexandria UI primitives.");
  const style = addStyle();
  const page = document.createElement("article");
  page.className = "cast-page";
  page.dataset.routeOwner = "cast";
  page.dataset.page = "cast";
  page.setAttribute("data-cast-page", "");
  const title = UI.pageTitleBlock({
    title: "Cast",
    subtitle: "Assign production voices and review each character before producing audio.",
  });
  title.querySelector("h1").dataset.pageHeading = "";
  const workspace = document.createElement("div");
  workspace.className = "cast-workspace";
  const master = document.createElement("aside");
  master.className = "cast-roster";
  master.setAttribute("data-cast-roster", "");
  master.setAttribute("aria-label", "Characters");
  const profile = document.createElement("section");
  profile.className = "cast-profile";
  profile.setAttribute("data-cast-profile", "");
  profile.setAttribute("data-selected-character-profile", "");
  profile.setAttribute("aria-label", "Selected character profile");
  workspace.append(master, profile);
  page.append(title, workspace);
  root.replaceChildren(page);

  let aggregate = null;
  let selected = null;
  let activeFilter = route.context.filter || "all";
  let search = route.context.search || "";
  let dirty = false;
  let saveState = "saved";
  let searchTimer = null;
  let disposed = false;
  let persona = null;
  let popover = null;
  let requestController = null;
  let pendingSelection = null;

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
    shell.header.set({
      projectTitle: route.context.project || "Project workspace",
      save: {
        state: saveState === "error" ? "recoverable error" : saveState,
        label: saveState === "dirty" ? "Unsaved changes"
          : saveState === "saving" ? "Saving…"
            : saveState === "error" ? "Save failed" : "Saved",
      },
      status: {
        tone: complete ? "success" : blockers ? "warning" : "information",
        label: complete ? "Cast ready" : blockers ? `${blockers} blockers` : "Cast in progress",
      },
      primaryAction: {
        label: "Continue to Produce",
        disabled: !complete,
        onClick: () => shell.navigate(shell.routes.routeForPath("produce", {
          project: route.context.project,
        }).hash),
      },
    });
    shell.tracker.set({ script: "complete", cast: "current", produce: "future", export: "future" });
  }

  function setDirty(value) {
    dirty = Boolean(value);
    saveState = dirty ? "dirty" : "saved";
    page.dataset.dirty = String(dirty);
    header();
    const saveBar = profile.querySelector("[data-cast-save-bar]");
    if (saveBar) saveBar.hidden = !dirty && saveState !== "error";
  }

  function renderLoading() {
    const loadingList = document.createElement("ul");
    loadingList.className = "cast-roster__list";
    loadingList.setAttribute("role", "listbox");
    loadingList.setAttribute("aria-label", "Characters");
    loadingList.setAttribute("aria-busy", "true");
    loadingList.append(
      UI.skeleton({ label: "Loading character list" }),
      UI.skeleton({ label: "Loading character list" }),
    );
    const appearancePlaceholder = document.createElement("div");
    appearancePlaceholder.dataset.appearanceSummary = "";
    appearancePlaceholder.append(
      UI.skeleton({ label: "Loading appearance summary" }),
      text("p", "cast-profile__muted", "Visual evidence not available while this profile is loading."),
    );
    master.replaceChildren(
      text("h2", "cast-roster__title", "Characters"),
      UI.skeleton({ label: "Loading character filters" }),
      loadingList,
    );
    profile.replaceChildren(
      UI.skeleton({ label: "Loading selected character" }),
      UI.skeleton({ label: "Loading voice profile" }),
      appearancePlaceholder,
    );
    page.dataset.castState = "loading";
  }

  function renderError(message = "Alexandria could not load this Cast profile.") {
    const retry = UI.button({
      label: "Retry",
      variant: "secondary",
      attributes: { "data-cast-retry": "" },
      onClick: loadCast,
    });
    master.replaceChildren(text("h2", "cast-roster__title", "Characters"));
    profile.replaceChildren(UI.notice({
      tone: "error",
      title: "Cast unavailable",
      body: message,
      action: retry,
      live: true,
    }));
    page.dataset.castState = "error";
  }

  function renderEmpty() {
    const reviewScript = UI.button({
      label: "Review Script",
      variant: "secondary",
      onClick: () => shell.navigate(shell.routes.routeForPath("script", {
        project: route.context.project,
      }).hash),
    });
    master.replaceChildren(text("h2", "cast-roster__title", "Characters"));
    profile.replaceChildren(UI.emptyState({
      title: "No characters yet",
      body: "Review Script to identify speaking roles before assigning voices.",
      action: reviewScript,
    }));
    page.dataset.castState = "empty";
  }

  function scheduleReload() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadCast, 180);
  }

  function makeFilters() {
    const group = document.createElement("div");
    group.className = "cast-roster__filters";
    group.setAttribute("aria-label", "Filter characters");
    FILTERS.forEach(([value, label]) => {
      const count = aggregate?.filters?.counts?.[value];
      const chip = UI.filterChip({
        label: Number.isFinite(count) ? `${label} ${count}` : label,
        pressed: activeFilter === value,
      });
      chip.dataset.castFilter = value;
      chip.querySelector("button").addEventListener("click", () => {
        activeFilter = value;
        scheduleReload();
      });
      group.append(chip);
    });
    return group;
  }

  function selectRow(row, characterId, focus = false) {
    if (focus) row.focus();
    if (dirty && characterId !== selected?.character_id) {
      requestSelection(characterId, row);
      return;
    }
    master.querySelectorAll('[role="option"]').forEach((item) => {
      const current = item === row;
      item.setAttribute("aria-selected", String(current));
      item.tabIndex = current ? 0 : -1;
    });
    requestSelection(characterId, row);
  }

  function rowFor(character, index) {
    const row = document.createElement("li");
    row.className = "cast-roster__row";
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(character.character_id === aggregate.selected_character_id));
    row.tabIndex = character.character_id === aggregate.selected_character_id || (!aggregate.selected_character_id && index === 0)
      ? 0 : -1;
    row.dataset.characterId = character.character_id;
    const portrait = UI.monogram({
      initials: initials(character.display_name),
      label: `Monogram for ${character.display_name}`,
    });
    const body = document.createElement("span");
    body.className = "cast-roster__row-body";
    body.append(
      text("strong", "cast-roster__name", character.display_name),
      text("span", "metadata", character.voice_summary || character.identity?.script_voice_label
        || (character.speaking_role ? "Speaking role" : "No spoken lines")),
    );
    const status = readableStatus(character);
    row.append(portrait, body, UI.status({ ...status, domain: "cast" }));
    row.addEventListener("click", () => selectRow(row, character.character_id));
    row.addEventListener("keydown", (event) => {
      if (!["ArrowUp", "ArrowDown", "Home", "End", "Enter", " "].includes(event.key)) return;
      event.preventDefault();
      if (["Enter", " "].includes(event.key)) {
        selectRow(row, character.character_id);
        return;
      }
      const rows = [...master.querySelectorAll('[role="option"]')];
      const current = rows.indexOf(row);
      const target = event.key === "Home" ? rows[0] : event.key === "End" ? rows.at(-1)
        : rows[(current + (event.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length];
      selectRow(target, target.dataset.characterId, true);
    });
    return row;
  }

  function renderMaster() {
    const heading = document.createElement("header");
    heading.className = "cast-roster__header";
    heading.append(
      text("h2", "cast-roster__title", "Characters"),
      text("span", "metadata", `${aggregate.characters?.length || 0} shown`),
    );
    const searchField = UI.searchField({ label: "Search characters", placeholder: "Search characters" });
    const searchInput = searchField.querySelector("input");
    searchInput.value = search;
    searchInput.addEventListener("input", () => {
      search = searchInput.value;
      scheduleReload();
    });
    const rows = document.createElement("ul");
    rows.className = "cast-roster__list";
    rows.setAttribute("role", "listbox");
    rows.setAttribute("aria-label", "Characters");
    (aggregate.characters || []).forEach((character, index) => rows.append(rowFor(character, index)));
    master.replaceChildren(heading, searchField, makeFilters());
    if (aggregate.selection_visible === false && selected) {
      master.append(UI.notice({
        tone: "information",
        title: "Selected character is filtered out",
        body: `${selected.display_name} remains open in the profile.`,
      }));
    }
    if (rows.children.length) master.append(rows);
    else master.append(UI.emptyState({
      title: "No matching characters",
      body: "Clear the search or choose another filter.",
    }));
  }

  function moreContext(path) {
    return shell.routes.routeForPath(path, {
      project: route.context.project,
      character: selected.character_id,
      source: `cast:character:${selected.character_id}`,
      return: shell.routes.routeForPath("cast", {
        project: route.context.project,
        character: selected.character_id,
      }).hash,
    });
  }

  function identityHeader() {
    const headerNode = document.createElement("header");
    headerNode.className = "cast-profile__identity";
    headerNode.setAttribute("data-cast-identity", "");
    const portrait = UI.portrait({
      label: `Portrait evidence unavailable for ${selected.display_name}`,
    });
    portrait.classList.add("cast-profile__portrait");
    const identity = document.createElement("div");
    identity.className = "cast-profile__identity-copy";
    const scriptLabel = selected.identity?.script_voice_label
      || selected.script_connection?.resolved_script_voice_label;
    identity.append(
      text("div", "metadata", scriptLabel && scriptLabel !== selected.display_name
        ? `Script voice · ${scriptLabel}` : "Selected character"),
      text("h2", "cast-profile__name", selected.display_name),
      text("p", "cast-profile__muted", selected.character?.summary?.role
        || selected.identity?.role
        || (selected.speaking_role === "speaking" ? "Speaking role" : "Non-speaking character")),
    );
    const opener = UI.button({
      label: "More actions",
      variant: "secondary",
      size: "compact",
      attributes: { "data-cast-more": "" },
    });
    popover = UI.popover({
      opener,
      label: `More actions for ${selected.display_name}`,
      items: [
        {
          label: "Advanced identity operations",
          onSelect: () => shell.navigate(moreContext("more/advanced-character-operations").hash),
        },
        {
          label: "Open Voice designer",
          onSelect: () => shell.navigate(moreContext("more/voice-designer").hash),
        },
        {
          label: "Prepare reference audio",
          onSelect: () => shell.navigate(moreContext("more/audio-preparer").hash),
        },
      ],
    });
    popover.dataset.returnContext = moreContext("cast").context.return || "";
    headerNode.append(portrait, identity, UI.status(readableStatus(selected)), popover);
    return headerNode;
  }

  function fieldControl(options) {
    const wrapper = UI.field(options);
    const control = wrapper.querySelector(".field__control");
    control.addEventListener("input", () => setDirty(true));
    control.addEventListener("change", () => setDirty(true));
    return { wrapper, control };
  }

  function voiceSection() {
    const voice = selected.voice || {};
    const methodValue = voice.selected_production_method || "custom";
    const methods = VOICE_METHODS.some(([value]) => value === methodValue)
      ? VOICE_METHODS : [[methodValue, words(methodValue)], ...VOICE_METHODS];
    const method = fieldControl({
      id: "cast-voice-method",
      label: "Production method",
      kind: "select",
      value: methodValue,
      options: methods.map(([value, label]) => ({ value, label })),
    });
    method.control.dataset.castVoiceMethod = "";
    const assigned = fieldControl({
      id: "cast-assigned-voice",
      label: "Assigned voice",
      value: voice.selected_voice || "",
      placeholder: "Choose or name a production voice",
      message: "The saved voice used when Alexandria produces this character.",
    });
    assigned.control.dataset.castAssignedVoice = "";
    const description = fieldControl({
      id: "cast-voice-description",
      label: "Voice direction",
      kind: "textarea",
      value: voice.persistent_voice_description || "",
      placeholder: "Describe tone, age, rhythm, and delivery",
    });
    description.control.dataset.castVoiceDescription = "";
    const delivery = UI.field({
      label: "Delivery",
      value: voice.selected_backend ? words(voice.selected_backend) : "Uses the selected production method",
      readOnly: true,
    });
    const grid = document.createElement("div");
    grid.className = "cast-profile__field-grid";
    grid.append(method.wrapper, assigned.wrapper, description.wrapper, delivery);
    return section("voice", "Voice", grid);
  }

  function referenceSection() {
    const voice = selected.voice || {};
    const clone = voice.clone || {};
    const ready = clone.reference_audio_state === "ready";
    const transport = document.createElement("div");
    transport.className = "cast-profile__transport";
    const play = UI.compactPlay({
      state: ready ? "ready" : "disabled",
      label: ready ? "Play reference audio" : "Reference audio unavailable",
    });
    play.dataset.castReferencePlay = "";
    if (ready) play.addEventListener("click", () => shell.player.set({
      state: "playing",
      title: `${selected.display_name} · Reference`,
      subtitle: "Cast reference audio",
    }));
    transport.append(
      play,
      UI.waveform({ value: 0, maximum: 30, label: "Reference audio position", disabled: !ready }),
      text("span", "metadata", ready ? "Reference audio ready" : "No reference audio"),
    );
    const transcript = fieldControl({
      id: "cast-reference-transcript",
      label: "Exact reference transcript",
      kind: "textarea",
      value: clone.exact_reference_transcript || "",
      placeholder: "Enter the exact words spoken in the reference recording",
      message: "This must match the reference recording word for word.",
    });
    transcript.control.dataset.castReferenceTranscript = "";
    const content = document.createElement("div");
    content.append(transport, transcript.wrapper);
    if (clone.reference_audio_state === "missing") content.append(UI.notice({
      tone: "warning",
      title: "Reference audio missing",
      body: "Add or prepare a recording before using a cloned production voice.",
    }));
    return section("reference", "Reference audio", content);
  }

  function previewSection() {
    const preview = selected.voice?.preview || {};
    const approved = preview.approved || preview.status === "approved" || preview.status === "ready";
    const content = document.createElement("div");
    content.className = "cast-profile__transport";
    const play = UI.compactPlay({
      state: approved ? "ready" : preview.status === "failed" ? "failed" : "disabled",
      label: approved ? "Preview again" : "Approved preview unavailable",
    });
    play.dataset.castPreviewPlay = "";
    if (approved) play.addEventListener("click", () => shell.player.set({
      state: "playing",
      title: `${selected.display_name} · Voice preview`,
      subtitle: "Approved Cast preview",
    }));
    content.append(
      play,
      UI.waveform({ value: approved ? 8 : 0, maximum: 18, label: "Voice preview position", disabled: !approved }),
      text("span", "metadata", approved ? "Approved preview" : "Preview recommended"),
    );
    return section("preview", "Approved preview", content);
  }

  function characterSection() {
    const summary = selected.character?.summary || {};
    const content = document.createElement("div");
    content.className = "cast-profile__summary";
    const facts = document.createElement("dl");
    facts.className = "cast-profile__definition-list";
    [
      ["Role", summary.role],
      ["Speaking", words(summary.speaking_state
        || (selected.speaking_role === "speaking" ? "speaking role" : "non-speaking"))],
      ["Type", summary.species_or_type],
      ["Confidence", words(summary.source_confidence)],
    ].forEach(([term, value]) => {
      facts.append(text("dt", "", term), text("dd", "", value));
    });
    content.append(facts);
    const expanded = selected.character?.expanded || {};
    const details = document.createElement("div");
    details.append(
      text("h4", "cast-profile__subheading", "Aliases"),
      list(summary.aliases || expanded.nicknames, "No aliases recorded."),
      text("h4", "cast-profile__subheading", "Relationships"),
      list(summary.relationships, "No relationships recorded."),
      text("h4", "cast-profile__subheading", "Representative script lines"),
      list(expanded.representative_script_lines, "No representative lines available."),
    );
    content.append(UI.disclosure({ label: "Character details", content: details }));
    return section("character", "Character", content);
  }

  function appearanceSection() {
    const appearance = selected.appearance || {};
    const content = document.createElement("div");
    content.dataset.appearanceSummary = "";
    content.className = "cast-profile__summary";
    content.append(text(
      "p",
      "cast-profile__muted",
      appearance.summary || "Visual evidence not available. No stable appearance details have been collected.",
    ));
    const personaHost = createPersonaVisual({ api, character: selected, signal });
    persona = personaHost;
    content.append(UI.disclosure({
      label: "More details",
      id: `persona-${selected.character_id}`,
      content: personaHost,
    }));
    return section("appearance", "Appearance", content);
  }

  function advancedSection() {
    const setup = selected.advanced_voice_setup || {};
    const content = document.createElement("dl");
    content.className = "cast-profile__definition-list";
    [
      ["Expressive reference", setup.expressive_reference_state],
      ["Recording preparation", setup.owned_recording_preparation_state],
      ["Dataset", setup.dataset_state],
      ["Adapter training", setup.adapter_training_state],
      ["Compatibility", setup.compatibility_state],
    ].forEach(([term, value]) => {
      content.append(text("dt", "", term), text("dd", "", words(value || "not started")));
    });
    return section("advanced", "Advanced", UI.disclosure({
      label: "Advanced voice preparation",
      content,
    }));
  }

  function saveBar() {
    const bar = document.createElement("div");
    bar.className = "cast-profile__save";
    bar.dataset.castSaveBar = "";
    bar.hidden = !dirty && saveState !== "error";
    const status = UI.inlineSave({
      state: saveState,
      label: saveState === "error" ? "Changes retained. Retry save."
        : saveState === "saving" ? "Saving…" : "Unsaved changes",
    });
    const button = UI.button({
      label: saveState === "error" ? "Retry save" : "Save changes",
      variant: "secondary",
      disabled: saveState === "saving",
      attributes: { "data-cast-save": "" },
      onClick: saveProfile,
    });
    bar.append(status, button);
    return bar;
  }

  function renderProfile() {
    persona?.cleanup?.();
    popover?.popoverCleanup?.();
    persona = null;
    popover = null;
    if (!selected) {
      profile.replaceChildren(UI.emptyState({
        title: "Choose a character",
        body: "Select a character to review their voice and script-supported identity.",
      }));
      return;
    }
    profile.replaceChildren(
      identityHeader(),
      voiceSection(),
      referenceSection(),
      previewSection(),
      characterSection(),
      appearanceSection(),
      advancedSection(),
      saveBar(),
    );
    page.dataset.castState = "ready";
  }

  async function saveProfile() {
    if (!selected || saveState === "saving") return false;
    const method = profile.querySelector("[data-cast-voice-method]")?.value || "custom";
    const assigned = profile.querySelector("[data-cast-assigned-voice]")?.value || "";
    const description = profile.querySelector("[data-cast-voice-description]")?.value || "";
    const transcript = profile.querySelector("[data-cast-reference-transcript]")?.value || "";
    selected = {
      ...selected,
      voice: {
        ...(selected.voice || {}),
        selected_production_method: method,
        selected_voice: assigned,
        persistent_voice_description: description,
        clone: {
          ...(selected.voice?.clone || {}),
          exact_reference_transcript: transcript,
        },
      },
    };
    saveState = "saving";
    header();
    renderProfile();
    const scriptLabel = selected.script_connection?.resolved_script_voice_label
      || selected.identity?.script_voice_label
      || selected.display_name;
    const response = await api.post("/api/save_voice_config", {
      [scriptLabel]: {
        type: method,
        voice: assigned,
        description,
        ref_text: transcript,
      },
    }, { signal: beginRequest() });
    if (disposed || signal.aborted) return false;
    if (!response.ok) {
      saveState = response.kind === "canceled" ? "dirty" : "error";
      dirty = true;
      header();
      renderProfile();
      return false;
    }
    dirty = false;
    saveState = "saved";
    await loadSelection(selected.character_id, false);
    await refreshAggregate();
    header();
    return true;
  }

  function openDirtyDialog(characterId, opener) {
    pendingSelection = characterId;
    const save = UI.button({
      label: "Save",
      variant: "primary",
      onClick: async () => {
        const target = pendingSelection;
        if (await saveProfile()) {
          dialog.forceClose("save");
          loadSelection(target);
        }
      },
    });
    const dialog = UI.dialog({
      title: "Unsaved Cast changes",
      body: "Save this character’s voice changes before opening another profile.",
      content: save,
      confirmLabel: "Discard",
      destructive: true,
      onConfirm: () => {
        dirty = false;
        saveState = "saved";
        loadSelection(characterId);
      },
      onClose: () => {
        pendingSelection = null;
        opener?.focus();
      },
    });
    dialog.open(opener);
  }

  function requestSelection(characterId, opener) {
    if (!characterId || characterId === selected?.character_id) return;
    if (dirty) {
      openDirtyDialog(characterId, opener);
      return;
    }
    loadSelection(characterId);
  }

  async function loadSelection(characterId, showLoading = true) {
    if (!characterId || disposed) return;
    if (showLoading) {
      const appearancePlaceholder = document.createElement("div");
      appearancePlaceholder.dataset.appearanceSummary = "";
      appearancePlaceholder.append(
        UI.skeleton({ label: "Loading appearance summary" }),
        text("p", "cast-profile__muted", "Visual evidence not available while this profile is loading."),
      );
      profile.replaceChildren(
        UI.skeleton({ label: "Loading character profile" }),
        appearancePlaceholder,
      );
    }
    const response = await api.get(
      `/api/cast/characters/${encodeURIComponent(characterId)}`,
      { signal: beginRequest() },
    );
    if (disposed || signal.aborted) return;
    if (!response.ok) {
      if (response.kind !== "canceled") renderError("Alexandria could not load the selected character.");
      return;
    }
    selected = response.data?.character_id
      ? response.data
      : response.data?.selected_character || response.data?.character || response.data;
    aggregate.selected_character_id = selected.character_id;
    dirty = false;
    saveState = "saved";
    renderMaster();
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
    renderMaster();
  }

  async function loadCast() {
    renderLoading();
    const query = new URLSearchParams({ filter: activeFilter });
    if (search.trim()) query.set("search", search.trim());
    if (route.context.character) query.set("selected_character_id", route.context.character);
    const response = await api.get(`/api/cast?${query}`, { signal: beginRequest() });
    if (disposed || signal.aborted) return;
    if (!response.ok) {
      if (response.kind !== "canceled") renderError();
      return;
    }
    aggregate = response.data || {};
    header();
    if (!(aggregate.characters || []).length && !aggregate.selected_character) {
      renderEmpty();
      return;
    }
    selected = aggregate.selected_character || null;
    renderMaster();
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
    persona?.cleanup?.();
    popover?.popoverCleanup?.();
    if (style.owned) style.node.remove();
    signal.removeEventListener("abort", cleanup);
  };
  signal.addEventListener("abort", cleanup, { once: true });

  shell.player.set({ state: "inactive", title: "No active Cast preview" });
  renderLoading();
  header();
  await loadCast();
  return cleanup;
}
