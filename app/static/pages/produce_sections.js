'use strict';

function headingChunk(chunk) {
  const text = String(chunk.text || chunk.text_excerpt || '').trim();
  const direction = String(chunk.delivery_direction || '').toLowerCase();
  return text.length > 0 && text.length <= 90
    && (direction.includes('announce') || direction.includes('heading')
      || /^(chapter|prologue|epilogue|part|book|cover)\b/i.test(text));
}

export function groupProduceChunks(chunks = []) {
  const groups = [];
  let current = null;
  chunks.forEach((chunk) => {
    const explicit = chunk.group_label || chunk.chapter?.name || chunk.chapter_title
      || chunk.scene?.name || chunk.scene_title;
    const label = explicit || (headingChunk(chunk) ? chunk.text || chunk.text_excerpt : null);
    if (!current || (label && current.label !== label)) {
      current = {
        label: label || (groups.length ? `Audio section ${groups.length + 1}` : 'Opening'),
        chunks: [],
      };
      groups.push(current);
    }
    current.chunks.push(chunk);
  });
  return groups;
}

export function isProduceSectionEligible(chunk) {
  return ['ready', 'stale', 'failed'].includes(chunk?.state)
    && chunk?.voice?.valid === true;
}

export function describeProduceBatch(chunks = [], selectedIds = null) {
  const selected = selectedIds == null ? null : new Set(selectedIds);
  const actionable = chunks.filter(isProduceSectionEligible);
  const chosen = selected == null
    ? actionable
    : actionable.filter((chunk) => selected.has(chunk.chunk_id));
  const stateCounts = chosen.reduce((counts, chunk) => {
    counts[chunk.state] = (counts[chunk.state] || 0) + 1;
    return counts;
  }, {});
  const failedCount = stateCounts.failed || 0;
  const generationCount = chosen.length - failedCount;
  return {
    ids: chosen.map((chunk) => chunk.chunk_id),
    count: chosen.length,
    failedCount,
    generationCount,
    stateCounts,
  };
}

export function produceBatchActionLabel(batch, scope = 'selected') {
  if (!batch?.count) return scope === 'selected' ? 'No audio selected' : 'No actionable audio';
  if (batch.failedCount === batch.count) {
    return `Retry ${batch.count.toLocaleString()} ${scope === 'selected' ? 'selected' : 'failed'}`;
  }
  if (!batch.failedCount) {
    return `Generate ${batch.count.toLocaleString()} ${scope}`;
  }
  return `Generate ${batch.generationCount.toLocaleString()} + retry ${batch.failedCount.toLocaleString()}`;
}

export function buildProduceSections(chunks = [], allChunkCount = chunks.length) {
  const complete = chunks.length === Number(allChunkCount);
  const sections = groupProduceChunks(chunks).map((group, ordinal) => ({
    ...group,
    key: `${group.chunks[0]?.chunk_id || 'empty'}:${ordinal}`,
    eligibleIds: group.chunks.filter(isProduceSectionEligible).map((chunk) => chunk.chunk_id),
  }));
  return { complete, sections };
}

export function resolveProduceSectionBatch(section, selectedIds) {
  const selected = new Set(selectedIds || []);
  const chosen = section.eligibleIds.filter((id) => selected.has(id));
  return selected.size ? chosen : [...section.eligibleIds];
}

export function pruneProduceSectionSelection(selectedIds, sections) {
  const eligible = new Set(sections.flatMap((section) => section.eligibleIds));
  return new Set([...selectedIds].filter((id) => eligible.has(id)));
}
