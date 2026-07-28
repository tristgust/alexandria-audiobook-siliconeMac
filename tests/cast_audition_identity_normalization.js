'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { argsFrom, required, writeJson } = require('./b19_t06_bootstrap_red.js');

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const repoRoot = path.resolve(required(args, 'repo-root'));

  // Given: the production helper and a canonical identity repeated with sentence punctuation.
  const source = fs.readFileSync(path.join(repoRoot, 'app/static/pages/cast_model.js'), 'utf8');
  globalThis.AlexandriaUI = {};
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
  const { castAuditionText } = await import(moduleUrl);
  const canonicalName = 'The Woman Standing Beside The Window';
  const preferredSentence = 'At dawn, the telegram arrived.';

  // When: audition content is selected from the decorated identity and a real sentence.
  const selected = castAuditionText({
    display_name: canonicalName,
    character: { expanded: { representative_script_lines: [
      `${canonicalName}.)`,
      preferredSentence,
    ] } },
  });

  // Then: the identity is rejected and the spoken sentence wins.
  const report = {
    status: selected === preferredSentence ? 'PASS' : 'FAIL',
    assertion: 'decorated_identity_is_not_an_audition_sentence',
    decoratedIdentity: `${canonicalName}.)`,
    expected: preferredSentence,
    selected,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`CAST_AUDITION_IDENTITY=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.stack || error);
    process.exitCode = 2;
  });
}

module.exports = { main };
