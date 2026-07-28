from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
HARNESS = ROOT / "tests" / "navigation_shell_harness.js"
HARNESS_PARTS = (
    HARNESS,
    ROOT / "tests" / "navigation_shell_fixture.js",
    ROOT / "tests" / "navigation_shell_visual_scenarios.js",
    ROOT / "tests" / "navigation_shell_browser_scenarios.js",
)
SHELL = STATIC / "app_shell.js"
API = STATIC / "api_client.js"


class NavigationShellContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.shell = SHELL.read_text(encoding="utf-8") if SHELL.exists() else ""
        cls.api = API.read_text(encoding="utf-8") if API.exists() else ""

    def test_static_shell_safety_contract(self) -> None:
        completed = subprocess.run(
            ["node", str(HARNESS)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(all(item["pass"] for item in payload["assertions"]))

    def test_real_browser_lifecycle_and_factory_shell_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="alexandria-shell-contract-") as directory:
            completed = subprocess.run(
                ["node", str(HARNESS), "--browser", "--artifacts", directory],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=90,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
            report = json.loads((Path(directory) / "combined-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "PASS")
            self.assertTrue(all(item["pass"] for item in report["browser"]["assertions"]))
            cleanup = json.loads((Path(directory) / "cleanup.json").read_text(encoding="utf-8"))
            self.assertTrue(cleanup["browserExited"])
            self.assertTrue(cleanup["profileRemoved"])
            early_cleanup = json.loads(
                (Path(directory) / "early-dependency" / "cleanup.json").read_text(encoding="utf-8")
            )
            self.assertTrue(early_cleanup["browserExited"])
            self.assertTrue(early_cleanup["profileRemoved"])

    def test_browser_harness_is_split_into_bounded_event_driven_concerns(self) -> None:
        for path in HARNESS_PARTS:
            source = path.read_text(encoding="utf-8")
            completed = subprocess.run(
                ["node", "--check", str(path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            pure_lines = [
                line for line in source.splitlines()
                if line.strip() and not line.lstrip().startswith("//")
            ]
            self.assertLessEqual(len(pure_lines), 250, path)
        self.assertNotIn("setTimeout(resolve", HARNESS.read_text(encoding="utf-8"))
        self.assertNotIn(
            "setTimeout(resolve",
            (ROOT / "tests" / "navigation_shell_browser_scenarios.js").read_text(encoding="utf-8"),
        )

    def test_one_router_and_explicit_module_registry_remain_bounded(self) -> None:
        self.assertEqual(self.shell.count("addEventListener('popstate'"), 1)
        self.assertEqual(self.shell.count("addEventListener('hashchange'"), 1)
        self.assertNotIn("alexandria:routechange", self.shell)
        for module in (
            "pages/projects.js",
            "pages/script.js",
            "pages/cast.js",
            "pages/produce.js",
            "pages/export.js",
            "pages/library.js",
            "pages/voices.js",
            "pages/templates.js",
            "pages/settings.js",
            "pages/more.js",
            "pages/maintenance.js",
            "specialists/advanced_character_operations.js",
            "specialists/voice_designer.js",
            "specialists/audio_preparer.js",
            "specialists/dataset_builder.js",
            "specialists/voice_training.js",
            "specialists/model_cache.js",
            "specialists/help_center.js",
        ):
            self.assertIn(module, self.shell)

    def test_api_client_is_dom_independent_and_has_explicit_result_taxonomy(self) -> None:
        self.assertNotIn("document.", self.api)
        self.assertNotIn("querySelector", self.api)
        for marker in (
            "validation",
            "decode",
            "http",
            "network",
            "canceled",
            "timeout",
        ):
            self.assertIn(marker, self.api)

    def test_api_client_normalizes_paths_payloads_responses_and_abort_ownership(self) -> None:
        script = r"""
const assert = require('node:assert/strict');
const api = require('./app/static/api_client.js');
const response = (status, type, body, statusText = '') => ({
  status, ok: status >= 200 && status < 300, statusText,
  headers: new Headers({ 'content-type': type }),
  text: async () => body,
});
(async () => {
  const calls = [];
  global.fetch = async (path, options) => {
    calls.push({ path, options });
    return response(201, 'application/json; charset=utf-8', '{"saved":true}', 'Created');
  };
  const created = await api.post('/api/example?project=meridian', { name: 'Alexandria' });
  assert.deepEqual(created, { ok: true, status: 201, data: { saved: true } });
  assert.equal(calls[0].path, '/api/example?project=meridian');
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.body, '{"name":"Alexandria"}');
  assert.equal(calls[0].options.headers.get('content-type'), 'application/json');

  const form = new URLSearchParams({ search: 'Ada' });
  await api.post('/api/search', form);
  assert.equal(calls[1].options.body, form);
  assert.equal(calls[1].options.headers.has('content-type'), false);

  let guardedFetches = 0;
  global.fetch = async () => { guardedFetches += 1; throw new Error('guard failed'); };
  let deeplyEncodedTraversal = '../private';
  for (let depth = 0; depth < 8; depth += 1) deeplyEncodedTraversal = encodeURIComponent(deeplyEncodedTraversal);
  for (const unsafe of [
    '/api/%2e%2e/private',
    '/api/allowed/%2e%2e/private',
    '/api/allowed/%252e%252e/private',
    '/api/allowed/%5c..%5cprivate',
    '/api/allowed/' + deeplyEncodedTraversal,
    'https://example.invalid/api/example',
    'https://user:secret@alexandria.invalid/api/example',
    '//example.invalid/api/example',
  ]) {
    const guarded = await api.get(unsafe);
    assert.equal(guarded.kind, 'validation', unsafe);
    assert.equal(guarded.status, 0, unsafe);
  }
  assert.equal(guardedFetches, 0);

  global.fetch = async () => response(409, 'application/problem+json',
    '{"detail":"Project conflict"}', 'Conflict');
  assert.deepEqual(await api.get('/api/problem'), {
    ok: false, status: 409, kind: 'http', error: 'Project conflict',
    data: { detail: 'Project conflict' },
  });

  global.fetch = async () => response(422, 'application/json', JSON.stringify({
    detail: {
      code: 'stage_contract_validation_failed',
      message: 'Voice dossier OWL requires one source quote.',
      details: {},
    },
  }), 'Unprocessable Entity');
  assert.deepEqual(await api.get('/api/structured-error'), {
    ok: false, status: 422, kind: 'http',
    error: 'Voice dossier OWL requires one source quote.',
    data: {
      detail: {
        code: 'stage_contract_validation_failed',
        message: 'Voice dossier OWL requires one source quote.',
        details: {},
      },
    },
  });

  global.fetch = async () => response(400, 'application/json', '{"broken"', 'Bad Request');
  assert.deepEqual(await api.get('/api/malformed'), {
    ok: false, status: 400, kind: 'decode', error: 'Malformed JSON response (400)',
    data: '{"broken"',
  });

  global.fetch = async () => response(503, 'text/plain', 'model service overheated', 'Unavailable');
  assert.deepEqual(await api.get('/api/text-error'), {
    ok: false, status: 503, kind: 'http', error: 'model service overheated',
    data: 'model service overheated',
  });

  global.fetch = async () => response(204, '', '', 'No Content');
  assert.deepEqual(await api.get('/api/empty'), { ok: true, status: 204, data: null });

  global.fetch = async (_path, options) => {
    if (options.signal.aborted) throw new DOMException('aborted', 'AbortError');
    return new Promise((_resolve, reject) => options.signal.addEventListener(
      'abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true },
    ));
  };
  const external = new AbortController();
  external.abort('timeout');
  assert.deepEqual(await api.get('/api/slow', { signal: external.signal }), {
    ok: false, status: 0, kind: 'canceled', error: 'Request canceled', data: null,
  });
  assert.deepEqual(await api.get('/api/slow', { timeout: 5 }), {
    ok: false, status: 0, kind: 'timeout', error: 'Request timed out', data: null,
  });
  process.stdout.write(JSON.stringify({ ok: true, checks: 31 }));
})().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["checks"], 31)


if __name__ == "__main__":
    unittest.main()
