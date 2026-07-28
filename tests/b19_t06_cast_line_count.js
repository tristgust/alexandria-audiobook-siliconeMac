'use strict';

const path = require('path');
const { pathToFileURL } = require('url');

async function main() {
  const root = path.resolve(process.argv[2] || '.');
  const { castScriptLineCount } = await import(pathToFileURL(
    path.join(root, 'app/static/pages/cast_line_count.js'),
  ));
  const assertions = {
    preferredNumeric: castScriptLineCount({
      script_connection: { script_line_count: 12 }, line_count: 40,
    }) === 12,
    malformedFallsBack: castScriptLineCount({
      script_connection: { script_line_count: 'unknown' }, line_count: 40,
    }) === 40,
    blankFallsBack: castScriptLineCount({
      script_connection: { script_line_count: '' }, line_count: 21,
    }) === 21,
    authoritativeZero: castScriptLineCount({
      script_connection: { script_line_count: 0 }, line_count: 21,
    }) === 0,
  };
  const report = { status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL', assertions };
  process.stdout.write(`B19_T06_CAST_LINE_COUNT=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
