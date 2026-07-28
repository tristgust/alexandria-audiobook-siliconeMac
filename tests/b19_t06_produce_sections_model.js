'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function main() {
  const modulePath = path.resolve(__dirname, '../app/static/pages/produce_sections.js');
  const model = await import(pathToFileURL(modulePath).href);
  const states = ['ready', 'current', 'stale', 'failed', 'needs_listening', 'generating', 'missing_voice'];
  const chunks = Array.from({ length: 40 }, (_, index) => ({
    chunk_id: `chapter-1:${index + 1}`,
    index,
    group_label: 'Chapter 1',
    state: states[index % states.length],
    voice: { valid: index % 11 !== 0 },
  }));
  const groups = model.buildProduceSections(chunks, chunks.length);
  assert.equal(groups.complete, true);
  assert.equal(groups.sections.length, 1);
  assert.equal(groups.sections[0].chunks.length, 40);
  const expected = chunks.filter((chunk) => ['ready', 'stale'].includes(chunk.state)
    && chunk.voice.valid).map((chunk) => chunk.chunk_id);
  assert.deepEqual(groups.sections[0].eligibleIds, expected);
  assert(expected.some((id) => Number(id.split(':')[1]) > 30));
  assert.deepEqual(model.resolveProduceSectionBatch(groups.sections[0], new Set()), expected);
  const chosen = new Set([expected.at(-1), expected[0], 'unsafe-id']);
  assert.deepEqual(model.resolveProduceSectionBatch(groups.sections[0], chosen), [expected[0], expected.at(-1)]);
  assert.deepEqual(
    [...model.pruneProduceSectionSelection(chosen, groups.sections)].sort(),
    [expected[0], expected.at(-1)].sort(),
  );
  assert.equal(model.buildProduceSections(chunks.slice(0, 30), 40).complete, false);
  process.stdout.write(`${JSON.stringify({ status: 'PASS', eligible: expected.length })}\n`);
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
