'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function main() {
  const model = await import(pathToFileURL(path.resolve(
    __dirname, '../app/static/pages/script_review_model.js',
  )).href);
  const header = await import(pathToFileURL(path.resolve(
    __dirname, '../app/static/pages/script_header.js',
  )).href);

  const lifecycle = {
    state: 'stale',
    accepted: false,
    source_available: true,
    artifact: { script_exists: true, metadata_exists: true },
    fingerprints: { script: 'script', metadata: 'metadata', source: 'source' },
    blockers: [{
      code: 'script_acceptance_stale',
      title: 'Script acceptance is stale',
      explanation: 'Review and accept the current Script.',
      blocking: true,
    }],
  };
  const entries = [{ speaker: 'SECURITYBOT', text: 'Identify yourself.', instruct: 'Flat.' }];
  const issues = model.normalizeIssues({ lifecycle, entries });
  assert.deepEqual(issues, []);
  assert.equal(model.isEntryReviewIssue(lifecycle.blockers[0]), false);
  assert.equal(model.approvalState(lifecycle, issues).canApprove, true);

  const state = header.scriptHeaderState({
    model: { lifecycle, entries, importCandidate: null, reviewOverride: null },
    issues,
    goToCast() {},
    applyImportedScript() {},
    confirmReviewedDifferences() {},
    approve() {},
    continueAttribute: 'data-script-continue',
    approveAttribute: 'data-script-approve',
    applyImportAttribute: 'data-script-apply-import',
    approveLabel: 'Approve Script',
    reviewRequiredLabel: 'Review required',
  });
  assert.equal(state.primaryAction.label, 'Approve Script');
  assert.equal(state.primaryAction.disabled, false);
  assert.equal(state.primaryAction.attributes['data-script-approve'], '');
  assert.equal(state.status.label, 'Ready for approval');

  const sourceMismatch = {
    code: 'script_text_fidelity_failed',
    title: 'Script text differs',
    source_text: 'Original.',
    output_text: 'Changed.',
    blocking: true,
  };
  const blockingIssues = model.normalizeIssues({
    lifecycle: { ...lifecycle, blockers: [sourceMismatch] }, entries,
  });
  assert.equal(blockingIssues.length, 1);
  assert.equal(blockingIssues[0].type, 'source_mismatch');
  assert.equal(model.approvalState(lifecycle, blockingIssues).canApprove, false);

  process.stdout.write(`${JSON.stringify({ ok: true, staleApprovalEnabled: true })}\n`);
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
