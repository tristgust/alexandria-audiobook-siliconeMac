'use strict';

const fs = require('fs');
const path = require('path');
const { argsFrom, required, writeJson } = require('./b19_t06_bootstrap_red.js');
const { fixtureServer } = require('./cast_profile_fixture_server.js');
const { inspectViewport } = require('./excluded_speaker_recovery_browser_scenario.js');

function viewportsFrom(value) {
  return String(value || '390x844,1024x768,1536x1024').split(',').map((item) => {
    const [width, height] = item.split('x').map(Number);
    if (!Number.isInteger(width) || !Number.isInteger(height)) {
      throw new Error(`Invalid viewport: ${item}`);
    }
    return [width, height];
  });
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const repoRoot = path.resolve(required(args, 'repo-root'));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const viewports = viewportsFrom(args.viewports);
  const server = await fixtureServer(repoRoot);
  const results = [];
  try {
    for (const [width, height] of viewports) {
      results.push(await inspectViewport(server, artifacts, width, height));
    }
  } finally {
    server.release();
    await server.close();
  }
  const report = {
    status: results.every((result) => result.status === 'PASS') ? 'PASS' : 'FAIL',
    scenario: 'Produce blocker → excluded Script evidence → explicit recovery → Cast Voice editor → Undo',
    viewports, results,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  fs.writeFileSync(path.join(artifacts, 'action.log'), `${results.map((result) => (
    `${result.viewport} ${result.status} ${JSON.stringify(result.assertions)}`
  )).join('\n')}\n`);
  writeJson(path.join(artifacts, 'cleanup.json'), {
    serverClosed: !server.server.listening,
    pendingResponses: server.control.pending.length,
  });
  process.stdout.write(`EXCLUDED_SPEAKER_RECOVERY=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.stack || error);
    process.exitCode = 2;
  });
}

module.exports = { viewportsFrom };
