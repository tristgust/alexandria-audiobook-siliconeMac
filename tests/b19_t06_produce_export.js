'use strict';

const fs = require('fs');
const path = require('path');
const {
  argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');
const { inspectExport } = require('./produce_export_export_scenario.js');
const { fixtureServer } = require('./produce_export_fixture_server.js');
const { inspectProduce } = require('./produce_export_produce_scenario.js');
const { inspectStates } = require('./produce_export_state_scenario.js');

const DEFAULT_VIEWPORTS = '390x844,768x1024,1280x800,1440x960';
const json = (value) => JSON.stringify(value);

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required({
    ...args, artifacts: args['evidence-dir'] || args.artifacts,
  }, 'artifacts'));
  const scenario = args.scenario || 'all';
  const viewports = String(args.viewports || DEFAULT_VIEWPORTS)
    .split(',').map((value) => value.split('x').map(Number));
  const server = await fixtureServer();
  const results = [];
  try {
    for (const [width, height] of viewports) {
      if (scenario === 'states') {
        results.push(await inspectStates(server, artifacts, width, height));
      } else if (scenario === 'all') {
        results.push(await inspectProduce(server, artifacts, width, height));
        results.push(await inspectExport(server, artifacts, width, height));
      } else if (scenario === 'produce') {
        results.push(await inspectProduce(server, artifacts, width, height));
      } else if (scenario === 'export') {
        results.push(await inspectExport(server, artifacts, width, height));
      } else {
        throw new Error(`Unknown scenario: ${scenario}`);
      }
    }
  } finally {
    server.release();
    await server.close();
  }
  const report = {
    status: results.every((item) => item.status === 'PASS') ? 'PASS' : 'FAIL',
    scenario,
    viewports,
    results,
    requests: server.control.requests.filter((item) => item.path.startsWith('/api/')),
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  fs.writeFileSync(
    path.join(artifacts, 'action.log'),
    `${results.map((item) => `${item.viewport} ${item.status} ${json(item.assertions)}`).join('\n')}\n`,
  );
  writeJson(path.join(artifacts, 'cleanup.json'), {
    serverClosed: !server.server.listening,
    pendingResponses: server.control.pending.length,
  });
  process.stdout.write(`B19_T06_PRODUCE_EXPORT=${json(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
