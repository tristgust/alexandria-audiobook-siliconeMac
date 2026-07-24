'use strict';

const childProcess = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const root = __dirname;
const reportPath = path.join(root, 'logs', 'pinokio-preflight.json');
const stableUiCommit = '92c89d84d7d7f8ff711b235457e89f51f9c73de2';
const requiredFiles = [
  'app/app.py',
  'app/static/index.html',
  'app/static/app_shell.js',
  'app/static/shell_chrome.js',
  'app/static/styles/tokens.css',
  'app/static/styles/shell.css',
  'app/env/bin/python',
  'pinokio.js',
  'preview.js',
  'stable_ui_server.js',
  'start.js',
];

function run(command, args, options = {}) {
  return childProcess.spawnSync(command, args, {
    cwd: root,
    encoding: 'utf8',
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
    ...options,
  });
}

function fail(message, details = '') {
  const error = new Error(message);
  error.details = details;
  throw error;
}

function allFiles(directory, extension) {
  const output = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) output.push(...allFiles(target, extension));
    else if (entry.name.endsWith(extension)) output.push(target);
  }
  return output.sort();
}

function checkPort(port) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: '127.0.0.1', port });
    socket.setTimeout(450);
    socket.once('connect', () => {
      socket.destroy();
      reject(new Error(`Port ${port} is already in use. Stop the stale Alexandria process before starting this build.`));
    });
    const available = () => {
      socket.destroy();
      resolve();
    };
    socket.once('timeout', available);
    socket.once('error', available);
  });
}

async function main() {
  const checks = [];
  for (const relative of requiredFiles) {
    const target = path.join(root, relative);
    if (!fs.existsSync(target)) fail(`Required runtime file is missing: ${relative}`);
  }
  checks.push({ name: 'runtime-files', status: 'PASS', count: requiredFiles.length });

  const unmerged = run('git', ['ls-files', '-u']);
  if (unmerged.status !== 0 || unmerged.stdout.trim()) {
    fail('The checkout contains unresolved Git conflicts.', unmerged.stdout || unmerged.stderr);
  }
  checks.push({ name: 'git-conflicts', status: 'PASS' });

  const stableCommit = run('git', ['cat-file', '-e', `${stableUiCommit}^{commit}`]);
  const stableIndex = run('git', ['cat-file', '-e', `${stableUiCommit}:app/static/index.html`]);
  if (stableCommit.status !== 0 || stableIndex.status !== 0) {
    fail('The pinned stable interface source is unavailable.', stableCommit.stderr || stableIndex.stderr);
  }
  checks.push({ name: 'stable-ui-source', status: 'PASS', commit: stableUiCommit });

  const whitespace = run('git', ['diff', '--check']);
  if (whitespace.status !== 0) {
    fail('The checkout contains malformed patch whitespace.', whitespace.stdout || whitespace.stderr);
  }
  checks.push({ name: 'git-diff-check', status: 'PASS' });

  const scripts = allFiles(path.join(root, 'app', 'static'), '.js');
  for (const script of scripts) {
    const result = run(process.execPath, ['--check', script]);
    if (result.status !== 0) {
      fail(`JavaScript syntax validation failed: ${path.relative(root, script)}`, result.stderr);
    }
  }
  checks.push({ name: 'javascript-syntax', status: 'PASS', count: scripts.length });

  const python = path.join(root, 'app', 'env', 'bin', 'python');
  const pythonSyntax = run(python, ['-c', [
    'import ast, pathlib',
    "root = pathlib.Path('app')",
    "files = sorted(p for p in root.rglob('*.py') if not any(part in {'env', 'venv', '.venv', 'site-packages', '__pycache__'} or part.endswith('_env') for part in p.parts))",
    '[(ast.parse(p.read_text(encoding="utf-8"), filename=str(p))) for p in files]',
    'print(len(files))',
  ].join('; ')]);
  if (pythonSyntax.status !== 0) {
    fail('Python syntax validation failed.', pythonSyntax.stderr || pythonSyntax.stdout);
  }
  checks.push({ name: 'python-syntax', status: 'PASS', count: Number(pythonSyntax.stdout.trim()) || 0 });

  const port = Number(process.env.ALEXANDRIA_PORT || 4200);
  await checkPort(port);
  checks.push({ name: 'runtime-port', status: 'PASS', port });

  const head = run('git', ['rev-parse', 'HEAD']);
  const status = run('git', ['status', '--short']);
  const report = {
    status: 'PASS',
    checkedAt: new Date().toISOString(),
    head: head.status === 0 ? head.stdout.trim() : 'unknown',
    trackedChanges: status.status === 0 ? status.stdout.trim().split('\n').filter(Boolean) : [],
    checks,
  };
  fs.mkdirSync(path.dirname(reportPath), { recursive: true });
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`Alexandria preflight passed at ${report.head.slice(0, 12)}.\n`);
}

main().catch((error) => {
  const report = {
    status: 'FAIL',
    checkedAt: new Date().toISOString(),
    error: error.message,
    details: error.details || '',
  };
  fs.mkdirSync(path.dirname(reportPath), { recursive: true });
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  process.stderr.write(`Alexandria preflight failed: ${error.message}\n`);
  if (error.details) process.stderr.write(`${error.details}\n`);
  process.exitCode = 1;
});
