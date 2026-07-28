(() => {
  "use strict";

  const namespace = window.AlexandriaRound1 = window.AlexandriaRound1 || {};
  const REQUIRED_FIELDS = [
    "identity_1_to_5",
    "delivery_1_to_5",
    "naturalness_1_to_5",
    "artifact_severity_1_to_5",
    "spoken_text_matches_expected",
    "requested_mode_is_clear",
    "approve_for_comparison",
  ];
  const RATING_FIELDS = [
    ["identity_1_to_5", "Identity", "How closely does it match the named voice?"],
    ["delivery_1_to_5", "Delivery", "How clearly does it perform the requested mode?"],
    ["naturalness_1_to_5", "Naturalness", "How believable and human does it sound?"],
    ["artifact_severity_1_to_5", "Artifacts", "1 = clean; 5 = severely broken"],
  ];
  const BINARY_FIELDS = [
    ["spoken_text_matches_expected", "Text matches", "Did it say the intended words?"],
    ["requested_mode_is_clear", "Mode is clear", "Is the requested emotion or delivery unmistakable?"],
    ["approve_for_comparison", "Keep", "Is it useful enough to retain for comparison?"],
  ];
  const IMPORT_FIELDS = [...REQUIRED_FIELDS, "flag_for_follow_up", "notes"];
  const RATING_KEYS = new Set(RATING_FIELDS.map(([key]) => key));
  const BINARY_KEYS = new Set(BINARY_FIELDS.map(([key]) => key));

  function isRecord(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatMetric(value, digits = 3) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
    return Number(value).toFixed(digits);
  }

  function storageKey(data, reviewerProfile, reviewSession) {
    return [
      "alexandria",
      "multimodel-review",
      "round1",
      `schema-${data.schema_version || 1}`,
      encodeURIComponent(data.round_id),
      encodeURIComponent(reviewerProfile),
      encodeURIComponent(reviewSession),
    ].join(":");
  }

  function loadSaved(currentKey, legacyKey, allowLegacy) {
    try {
      const stored = localStorage.getItem(currentKey)
        || (allowLegacy ? localStorage.getItem(legacyKey) : null)
        || "{}";
      const parsed = JSON.parse(stored);
      return isRecord(parsed) ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  function restoreSelection(currentKey, legacyKey, allowLegacy, fallback) {
    return localStorage.getItem(currentKey)
      || (allowLegacy ? localStorage.getItem(legacyKey) : null)
      || fallback;
  }

  function validField(field, value) {
    if (RATING_KEYS.has(field)) return Number.isInteger(value) && value >= 1 && value <= 5;
    if (BINARY_KEYS.has(field) || field === "flag_for_follow_up") return typeof value === "boolean";
    if (field === "notes") return value === null || (typeof value === "string" && value.length <= 10000);
    return false;
  }

  function rowIsComplete(row) {
    return isRecord(row) && REQUIRED_FIELDS.every((field) => validField(field, row[field]));
  }

  function isComplete(saved, sampleId) {
    return rowIsComplete(saved[sampleId]);
  }

  function completion(saved, samples) {
    const ready = samples.filter((sample) => sample.status === "ready" && sample.audio);
    return {
      complete: ready.filter((sample) => isComplete(saved, sample.sample_id)).length,
      ready: ready.length,
    };
  }

  function validTimestamp(value) {
    return typeof value === "string" && value.length > 0 && Number.isFinite(Date.parse(value));
  }

  function validRevision(value) {
    return Number.isInteger(value) && value >= 0;
  }

  function validImportPayload(payload, roundId) {
    return isRecord(payload)
      && payload.schema_version === 1
      && payload.round_id === roundId
      && Array.isArray(payload.rows)
      && (!Object.hasOwn(payload, "exported_at") || validTimestamp(payload.exported_at))
      && (!Object.hasOwn(payload, "revision") || validRevision(payload.revision));
  }

  function cleanImportRow(row, payload) {
    if (!isRecord(row) || typeof row.sample_id !== "string" || !row.sample_id) return null;
    const providedFields = IMPORT_FIELDS.filter((field) => Object.hasOwn(row, field));
    if (!providedFields.length || providedFields.some((field) => !validField(field, row[field]))) return null;
    const updatedAt = Object.hasOwn(row, "updated_at") ? row.updated_at : payload.exported_at;
    const revision = Object.hasOwn(row, "revision") ? row.revision : (payload.revision ?? 0);
    if (!validTimestamp(updatedAt) || !validRevision(revision)) return null;
    const cleaned = { sample_id: row.sample_id };
    providedFields.forEach((field) => { cleaned[field] = row[field]; });
    cleaned.updated_at = updatedAt;
    cleaned.revision = revision;
    return cleaned;
  }

  function mergePriority(row) {
    const timestamp = Date.parse(row?.updated_at || "");
    const revision = validRevision(row?.revision) ? row.revision : 0;
    const signature = JSON.stringify(Object.keys(row || {}).sort().map((key) => [key, row[key]]));
    return [Number.isFinite(timestamp) ? timestamp : 0, revision, signature];
  }

  function isNewerImport(incoming, existing) {
    if (!existing) return true;
    const incomingPriority = mergePriority(incoming);
    const existingPriority = mergePriority(existing);
    for (let index = 0; index < incomingPriority.length; index += 1) {
      if (incomingPriority[index] === existingPriority[index]) continue;
      return incomingPriority[index] > existingPriority[index];
    }
    return false;
  }

  namespace.core = {
    BINARY_FIELDS,
    IMPORT_FIELDS,
    RATING_FIELDS,
    REQUIRED_FIELDS,
    cleanImportRow,
    completion,
    escapeHtml,
    formatMetric,
    isComplete,
    isNewerImport,
    loadSaved,
    restoreSelection,
    rowIsComplete,
    storageKey,
    validImportPayload,
  };
})();
