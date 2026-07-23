'use strict';

const fs = require('fs');
const path = require('path');
const { argsFrom, writeJson } = require('./b19_t06_bootstrap_red.js');
const { ROOT, STATIC, assertion } = require('./navigation_shell_fixture.js');
const {
  browserContract,
  earlyDependencyContract,
} = require('./navigation_shell_browser_scenarios.js');

function staticContract() {
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const shell = fs.readFileSync(path.join(STATIC, 'app_shell.js'), 'utf8');
  const chrome = fs.readFileSync(path.join(STATIC, 'shell_chrome.js'), 'utf8');
  const api = fs.readFileSync(path.join(STATIC, 'api_client.js'), 'utf8');
  const prohibited = [
    'data-tab-panel', 'setup-tab', 'characters-tab', 'editor-tab', 'audio-tab',
    'legacy-tab-store', 'activateWorkspaceTab', 'VoiceCardBridge',
    'canonical_interface.js', 'canonical_pages.css',
  ];
  const assertions = [
    assertion('factory-built-shell', ['UI.appShell(', 'UI.navRail(', 'UI.globalHeader(',
      'UI.projectHeader(', 'UI.persistentPlayer(', 'UI.shellInspector(']
      .every((marker) => html.includes(marker)), true, 'factory composition'),
    assertion('no-manual-shell-anatomy', !html.includes('class="app-shell"')
      && !html.includes('<nav') && !html.includes('<header'), true, 'factory output only'),
    assertion('canonical-bootstrap', html.includes("import('/static/app_shell.js')")
      && html.includes('globalThis.AlexandriaBootstrap'), true, 'guarded canonical bootstrap'),
    assertion('dependency-independent-fallback', /<main[^>]+data-bootstrap-error[^>]+hidden/.test(html)
      && html.includes('onerror="AlexandriaBootstrap.fail()"'), true, 'server-delivered fallback'),
    assertion('no-legacy-scaffold', prohibited.every((token) => !html.includes(token)
      && !shell.includes(token) && !chrome.includes(token)), [], prohibited.filter(
      (token) => html.includes(token) || shell.includes(token) || chrome.includes(token))),
    assertion('api-has-no-dom-coupling', !api.includes('document.') && !api.includes('querySelector'),
      true, api.includes('document.') || api.includes('querySelector')),
  ];
  return { status: assertions.every((item) => item.pass) ? 'PASS' : 'RED', assertions };
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const staticReport = staticContract();
  if (!args.browser) {
    process.stdout.write(`${JSON.stringify({ ok: staticReport.status === 'PASS', ...staticReport }, null, 2)}\n`);
    if (staticReport.status !== 'PASS') process.exitCode = 1;
    return;
  }
  const artifacts = path.resolve(String(args.artifacts
    || path.join(ROOT, '.omo', 'evidence', 'b19-t06-shell', 'browser-lifecycle')));
  fs.mkdirSync(artifacts, { recursive: true });
  const earlyDependency = await earlyDependencyContract(artifacts);
  const browser = await browserContract(artifacts);
  const report = {
    status: [staticReport, earlyDependency, browser].every((item) => item.status === 'PASS')
      ? 'PASS' : 'RED',
    static: staticReport,
    earlyDependency,
    browser,
  };
  writeJson(path.join(artifacts, 'combined-report.json'), report);
  process.stdout.write(`B19_T06_SHELL=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.stack || error);
    process.exitCode = 2;
  });
}

module.exports = { staticContract };
