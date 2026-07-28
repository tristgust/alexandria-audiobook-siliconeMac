(() => {
  "use strict";

  const namespace = window.AlexandriaRound1 = window.AlexandriaRound1 || {};

  function create(context) {
    const { core, data, els, groupKeys, state, stylesByKey } = context;

    function firstGeneratedGroup() {
      return groupKeys.find((key) => context.samplesForGroup(key).some((sample) => sample.status === "ready")) || groupKeys[0];
    }

    function firstStyleForGroup(groupKey) {
      return data.groups[groupKey]?.styles?.find((styleKey) => context.samplesForStyle(styleKey).some((sample) => sample.status === "ready"))
        || data.groups[groupKey]?.styles?.[0]
        || data.styles[0]?.key;
    }

    function filteredStyleSamples(options = {}) {
      let samples = context.samplesForStyle(state.activeStyle)
        .filter((sample) => sample.status === "ready" && sample.audio);
      if (state.identityFilter !== "all") {
        samples = samples.filter((sample) => sample.identity_key === state.identityFilter);
      }
      if (!options.ignoreIncomplete && state.incompleteOnly) {
        samples = samples.filter((sample) => !core.isComplete(state.saved, sample.sample_id));
      }
      if (!options.ignoreSearch && state.searchQuery) {
        const needle = state.searchQuery.toLowerCase();
        samples = samples.filter((sample) => [
          sample.expected_identity,
          sample.target_text,
          sample.sample_id,
          sample.style_label,
        ].some((value) => String(value).toLowerCase().includes(needle)));
      }
      return samples.sort((left, right) => (
        left.review_section_label.localeCompare(right.review_section_label)
        || left.sample_id.localeCompare(right.sample_id)
      ));
    }

    function renderGroupNavigation() {
      els.groupNavigation.innerHTML = "";
      groupKeys.forEach((groupKey) => {
        const group = data.groups[groupKey];
        const progress = core.completion(state.saved, context.samplesForGroup(groupKey));
        const button = document.createElement("button");
        button.type = "button";
        button.className = `nav-button${groupKey === state.activeGroup ? " active" : ""}`;
        button.innerHTML = `
          <span class="label">${core.escapeHtml(group.label)}</span>
          <span class="count">${progress.complete}/${progress.ready}</span>
          <small>${core.escapeHtml(group.description)}</small>`;
        button.addEventListener("click", () => selectGroup(groupKey));
        els.groupNavigation.appendChild(button);
      });
    }

    function selectGroup(groupKey) {
      state.activeGroup = groupKey;
      state.activeStyle = firstStyleForGroup(groupKey);
      context.persistSelection("group", state.activeGroup);
      context.persistSelection("style", state.activeStyle);
      context.render();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function renderStyleNavigation() {
      els.styleNavigation.innerHTML = "";
      data.groups[state.activeGroup].styles.forEach((styleKey) => {
        const style = stylesByKey.get(styleKey);
        const progress = core.completion(state.saved, context.samplesForStyle(styleKey));
        const button = document.createElement("button");
        button.type = "button";
        button.className = `nav-button${styleKey === state.activeStyle ? " active" : ""}`;
        button.innerHTML = `<span class="label">${core.escapeHtml(style.label)}</span><span class="count">${progress.complete}/${progress.ready}</span>`;
        button.addEventListener("click", () => selectStyle(styleKey));
        els.styleNavigation.appendChild(button);
      });
    }

    function renderIdentityFilter() {
      const identities = uniqueIdentities(context.samplesForGroup(state.activeGroup)
        .filter((sample) => sample.status === "ready"));
      const previous = state.identityFilter;
      els.identityFilter.innerHTML = '<option value="all">All identities</option>';
      identities.forEach(([key, label]) => {
        const option = document.createElement("option");
        option.value = key;
        option.textContent = label;
        els.identityFilter.appendChild(option);
      });
      state.identityFilter = identities.some(([key]) => key === previous) ? previous : "all";
      els.identityFilter.value = state.identityFilter;
    }

    function uniqueIdentities(samples) {
      const identities = new Map();
      samples.forEach((sample) => identities.set(sample.identity_key, sample.expected_identity));
      return [...identities.entries()].sort((left, right) => left[1].localeCompare(right[1]));
    }

    function renderStyleHeader() {
      const style = stylesByKey.get(state.activeStyle);
      const progress = core.completion(state.saved, context.samplesForStyle(state.activeStyle));
      const blocked = data.blocked_coverage.filter((item) => item.style === state.activeStyle).length;
      els.groupLabel.textContent = data.groups[state.activeGroup].label;
      els.styleTitle.textContent = style.label;
      els.styleInstruction.textContent = style.instruction;
      els.styleProgressText.textContent = `${progress.complete} / ${progress.ready} reviewed`;
      els.styleCoverageText.textContent = blocked ? `${blocked} documented unsupported cells` : "All declared cells available";
    }

    function updateProgressOnly() {
      const overall = core.completion(state.saved, context.readySamples);
      const group = core.completion(state.saved, context.samplesForGroup(state.activeGroup));
      const style = core.completion(state.saved, context.samplesForStyle(state.activeStyle));
      const flagged = Object.values(state.saved).filter((row) => row?.flag_for_follow_up === true).length;
      const pending = data.samples.length - overall.ready;
      els.overallProgress.textContent = `${overall.complete} / ${overall.ready} reviewed`;
      els.overallGenerated.textContent = `${overall.ready} ready · ${pending} pending · ${data.samples.length} planned · ${data.blocked_coverage.length} unsupported`;
      els.groupProgressCompact.textContent = `${group.complete}/${group.ready}`;
      els.styleProgressText.textContent = `${style.complete} / ${style.ready} reviewed`;
      els.followupCount.textContent = `${flagged} flagged`;
      renderGroupNavigation();
      renderStyleNavigation();
    }

    function selectStyle(styleKey) {
      state.activeStyle = styleKey;
      state.activeGroup = stylesByKey.get(styleKey).group;
      context.persistSelection("group", state.activeGroup);
      context.persistSelection("style", state.activeStyle);
      context.render();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function updatePreviousNextButtons() {
      const styles = data.groups[state.activeGroup].styles;
      const index = styles.indexOf(state.activeStyle);
      els.previousStyle.disabled = index <= 0;
      els.nextStyle.disabled = index < 0 || index >= styles.length - 1;
    }

    function moveStyle(delta) {
      const styles = data.groups[state.activeGroup].styles;
      const target = styles[styles.indexOf(state.activeStyle) + delta];
      if (target) selectStyle(target);
    }

    function incompleteSamplesForStyle(styleKey) {
      return context.samplesForStyle(styleKey).filter((sample) => (
        sample.status === "ready"
        && sample.audio
        && (state.identityFilter === "all" || sample.identity_key === state.identityFilter)
        && !core.isComplete(state.saved, sample.sample_id)
      ));
    }

    function goToNextIncomplete() {
      const visibleIncomplete = incompleteSamplesForStyle(state.activeStyle)[0];
      if (visibleIncomplete) {
        const target = document.getElementById(`sample-${visibleIncomplete.sample_id}`);
        target?.scrollIntoView({ behavior: "smooth", block: "start" });
        target?.querySelector("audio")?.focus();
        return;
      }
      const orderedStyles = groupKeys.flatMap((groupKey) => data.groups[groupKey].styles);
      const currentIndex = orderedStyles.indexOf(state.activeStyle);
      for (let offset = 1; offset < orderedStyles.length; offset += 1) {
        const candidateStyle = orderedStyles[(currentIndex + offset) % orderedStyles.length];
        if (!incompleteSamplesForStyle(candidateStyle).length) continue;
        selectStyle(candidateStyle);
        requestAnimationFrame(goToNextIncomplete);
        return;
      }
      const selected = els.identityFilter.selectedOptions[0]?.textContent || "this identity";
      const scope = state.identityFilter === "all" ? "" : ` for ${selected}`;
      context.showNotice(`Every generated sample${scope} has been reviewed.`);
    }

    return {
      filteredStyleSamples,
      firstGeneratedGroup,
      firstStyleForGroup,
      goToNextIncomplete,
      moveStyle,
      renderGroupNavigation,
      renderIdentityFilter,
      renderStyleHeader,
      renderStyleNavigation,
      selectStyle,
      updatePreviousNextButtons,
      updateProgressOnly,
    };
  }

  namespace.navigation = { create };
})();
