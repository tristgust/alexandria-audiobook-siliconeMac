(() => {
  "use strict";

  const namespace = window.AlexandriaRound1 = window.AlexandriaRound1 || {};

  function create(context) {
    const { core, data, els, state } = context;

    function exportRows(scope, key) {
      let samples;
      if (scope === "style") samples = context.samplesForStyle(key);
      else if (scope === "group") samples = context.samplesForGroup(key);
      else samples = data.samples;
      const rows = samples
        .filter((sample) => state.saved[sample.sample_id])
        .map((sample) => ({ ...state.saved[sample.sample_id], sample_id: sample.sample_id }));
      const ready = samples.filter((sample) => sample.status === "ready");
      const complete = ready.filter((sample) => core.isComplete(state.saved, sample.sample_id)).length;
      const payload = {
        schema_version: 1,
        round_id: data.round_id,
        export_scope: scope,
        export_key: key || "all",
        exported_at: new Date().toISOString(),
        revision: rows.reduce((maximum, row) => Math.max(maximum, Number(row.revision) || 0), 0),
        summary: {
          ready_sample_count: ready.length,
          complete_sample_count: complete,
          incomplete_sample_count: ready.length - complete,
          follow_up_flag_count: rows.filter((row) => row.flag_for_follow_up === true).length,
        },
        rows,
      };
      downloadJson(`alexandria_round1_${scope}_${key || "all"}.json`, payload);
    }

    function downloadJson(filename, payload) {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
    }

    async function importFiles(files) {
      const counts = { imported: 0, unknown: 0, malformed: 0, ignored: 0, conflicts: 0 };
      for (const file of files) {
        let payload;
        try {
          payload = JSON.parse(await file.text());
        } catch (_) {
          counts.ignored += 1;
          continue;
        }
        if (!core.validImportPayload(payload, data.round_id)) {
          counts.ignored += 1;
          continue;
        }
        payload.rows.forEach((row) => mergeRow(row, payload, counts));
      }
      localStorage.setItem(context.storageKey, JSON.stringify(state.saved));
      context.render();
      els.importSummary.textContent = [
        `${counts.imported} result rows merged.`,
        `${counts.conflicts} older or duplicate conflicts skipped.`,
        `${counts.unknown} unknown sample IDs skipped.`,
        `${counts.malformed} malformed rows skipped.`,
        `${counts.ignored} files ignored.`,
      ].join(" ");
      if (typeof els.importDialog.showModal === "function") els.importDialog.showModal();
      else context.showNotice(els.importSummary.textContent);
      return counts;
    }

    function mergeRow(row, payload, counts) {
      if (!row || typeof row !== "object" || Array.isArray(row) || typeof row.sample_id !== "string") {
        counts.malformed += 1;
        return;
      }
      if (!context.byId.has(row.sample_id)) {
        counts.unknown += 1;
        return;
      }
      const cleaned = core.cleanImportRow(row, payload);
      if (!cleaned) {
        counts.malformed += 1;
        return;
      }
      if (!core.isNewerImport(cleaned, state.saved[row.sample_id])) {
        counts.conflicts += 1;
        return;
      }
      state.saved[row.sample_id] = { ...state.saved[row.sample_id], ...cleaned };
      counts.imported += 1;
    }

    return { exportRows, importFiles };
  }

  namespace.io = { create };
})();
