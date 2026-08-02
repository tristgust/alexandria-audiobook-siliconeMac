from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import TypeAlias


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "b19_t07_keyboard_ax_browser.js"
JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class KeyboardAxBrowserTests(unittest.TestCase):
    def run_node(self, program: str) -> dict[str, JsonValue]:
        result = subprocess.run(["node", "-e", program], cwd=ROOT, text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def test_cdp_capture_uses_accessibility_and_physical_keys(self) -> None:
        program = "\n".join((
            "const h = require('./tests/b19_t07_keyboard_ax_browser.js');",
            "const sent = [];",
            "const focusedNode = {backendDOMNodeId: 7, ignored: false, role: {value: 'button'}, name: {value: 'Save'}, description: {value: 'Save preferences'}, value: {value: ''}, properties: [{name: 'focusable', value: {value: true}}, {name: 'labelledby', value: {relatedNodes: [{backendDOMNodeId: 8}]}}, {name: 'describedby', value: {relatedNodes: [{backendDOMNodeId: 9}]}}]};",
            "const client = { events: [{method: 'Runtime.consoleAPICalled', params: {type: 'error'}}], send: async (method, params = {}) => { sent.push({method, params}); if (method === 'Accessibility.getPartialAXTree') return {nodes: [focusedNode]}; if (method === 'Accessibility.getFullAXTree') return {nodes: [focusedNode, {backendDOMNodeId: 10, ignored: false, role: {value: 'status'}, name: {value: 'Saved'}, properties: [{name: 'live', value: {value: 'polite'}}, {name: 'atomic', value: {value: true}}, {name: 'relevant', value: {value: 'additions text'}}]}]}; if (method === 'Runtime.evaluate') return {result: {objectId: 'active'}}; if (method === 'DOM.requestNode') return {nodeId: 0}; if (method === 'DOM.describeNode' && params.objectId === 'active') return {node: {backendNodeId: 7}}; return {}; }};",
            "let step = 0;",
            "const session = {client, evaluate: async () => ({activeBackendNodeId: 7, activeId: 'save', activeTag: 'BUTTON', visibleFocus: true, finalUrl: '#/settings', bodyDestination: 'settings', bodyRoutePath: 'settings', routeOwner: 'navigation_routes.js', step: step++})};",
            "(async () => { const capture = await h.captureKeyboardAx(session, {physical_keys: ['Tab', 'Escape']}, ['Tab', 'Escape']); console.log(JSON.stringify({methods: sent.map((entry) => entry.method), dispatched: sent.filter((entry) => entry.method === 'Input.dispatchKeyEvent').map((entry) => entry.params), focus: capture.focusTrace, semantics: capture.axSemantics, liveRegions: capture.liveRegions, runtime: capture.runtime, violations: h.assertAxFocusAgreement(capture.axTree.nodes, capture.focusTrace)})); })();",
        ))
        result = self.run_node(program)
        accessibility_index = result["methods"].index("Accessibility.enable")
        self.assertEqual(
            result["methods"][accessibility_index:accessibility_index + 2],
            ["Accessibility.enable", "Accessibility.getFullAXTree"],
        )
        self.assertGreaterEqual(accessibility_index, 4)
        self.assertNotIn("DOM.requestNode", result["methods"])
        self.assertEqual(result["methods"].count("Input.dispatchKeyEvent"), 4)
        self.assertEqual([(entry["type"], entry["code"]) for entry in result["dispatched"]], [("keyDown", "Tab"), ("keyUp", "Tab"), ("keyDown", "Escape"), ("keyUp", "Escape")])
        self.assertEqual([item["key"] for item in result["focus"]], ["Tab", "Escape"])
        self.assertEqual([item["sequence"] for item in result["focus"]], [1, 2])
        self.assertTrue(all(item["visibleFocus"] for item in result["focus"]))
        self.assertEqual(result["semantics"], [{"backendNodeId": 7, "ignored": False, "role": "button", "name": "Save", "value": "", "description": "Save preferences", "state": {"focusable": True}, "relationships": {"labelledby": [8], "describedby": [9]}}])
        self.assertEqual(result["liveRegions"], [{"backendNodeId": 10, "role": "status", "name": "Saved", "value": "", "live": "polite", "atomic": True, "relevant": "additions text"}])
        self.assertEqual(result["violations"], [])
        self.assertEqual(len(result["runtime"]["consoleErrors"]), 1)

    def test_hidden_unlabeled_and_untabbable_mutations_fail_exact_assertions(self) -> None:
        program = "\n".join((
            "const h = require('./tests/b19_t07_keyboard_ax_browser.js');",
            "const trace = [{activeBackendNodeId: 1, visibleFocus: false}, {activeBackendNodeId: 2, visibleFocus: true}, {activeBackendNodeId: 3, visibleFocus: true}];",
            "const nodes = [{backendDOMNodeId: 1, ignored: true, role: {value: 'button'}, name: {value: 'Hidden'}, properties: [{name:'focusable', value:{value:true}}]}, {backendDOMNodeId: 2, ignored: false, role: {value: 'button'}, name: {value: ''}, properties: [{name:'focusable', value:{value:true}}]}, {backendDOMNodeId: 3, ignored: false, role: {value: 'button'}, name: {value: 'No tab'}, properties: [{name:'focusable', value:{value:false}}]}];",
            "console.log(JSON.stringify(h.assertAxFocusAgreement(nodes, trace)));",
        ))
        result = self.run_node(program)
        self.assertEqual(result, [
            {"id": "hidden-focus:1", "pass": False},
            {"id": "unlabeled-focus:2", "pass": False},
            {"id": "untabbable-focus:3", "pass": False},
        ])

    def test_roleless_focused_control_fails_ax_semantic_agreement(self) -> None:
        result = self.run_node("const h = require('./tests/b19_t07_keyboard_ax_browser.js'); console.log(JSON.stringify(h.assertAxFocusAgreement([{backendDOMNodeId: 7, ignored: false, name: {value: 'Save'}, properties: [{name: 'focusable', value: {value: true}}]}], [{activeBackendNodeId: 7, visibleFocus: true}])));")
        self.assertEqual(result, [{"id": "unroled-focus:7", "pass": False}])

    def test_transient_focus_uses_semantics_captured_before_node_closes(self) -> None:
        result = self.run_node("const h = require('./tests/b19_t07_keyboard_ax_browser.js'); const trace = [{activeBackendNodeId: 27, visibleFocus: true, axSemantic: {backendNodeId: 27, ignored: false, role: 'button', name: 'Close inspector', state: {focusable: true}}}]; console.log(JSON.stringify(h.assertAxFocusAgreement([], trace)));")
        self.assertEqual(result, [])

    def test_page_load_boundary_is_observed_before_dom_work(self) -> None:
        result = self.run_node("const h = require('./tests/b19_t07_keyboard_ax_browser.js'); const calls = []; (async () => { await h.waitForPageLoad({client: {event: async (method, predicate, timeout) => { calls.push({method, accepted: predicate({}), timeout}); }}}); console.log(JSON.stringify(calls)); })();")
        self.assertEqual(result, [{
            "method": "Page.loadEventFired",
            "accepted": True,
            "timeout": 30000,
        }])

    def test_produce_inspector_focus_policy_matches_responsive_presentation(self) -> None:
        result = self.run_node("const h = require('./tests/b19_t07_keyboard_ax_browser.js'); console.log(JSON.stringify({overlay: h.scenarioFocusPolicy('produce-inspector', 'overlay'), inline: h.scenarioFocusPolicy('produce-inspector', 'inline'), dialog: h.scenarioFocusPolicy('new-project-dialog'), player: h.scenarioFocusPolicy('persistent-player')}));")
        self.assertEqual(result, {
            "overlay": "restore",
            "inline": "inline",
            "dialog": "restore",
            "player": "retain",
        })

    def test_browser_startup_has_bounded_retry_and_cleanup_receipts(self) -> None:
        source = (ROOT / "tests" / "b19_t06_bootstrap_red.js").read_text(encoding="utf-8")
        self.assertIn("const CHROME_STARTUP_ATTEMPTS = 2;", source)
        self.assertIn("const CHROME_STARTUP_RETRY_DELAY_MS = 2000;", source)
        self.assertIn("chrome-startup-attempts.json", source)
        self.assertIn("await terminateBrowser(browser);", source)
        self.assertIn("startupRetryCount", source)

    def test_hostile_injection_preserves_composite_widget_structure(self) -> None:
        source = HARNESS.read_text(encoding="utf-8")
        hostile = source.split("async function injectHostileContent", 1)[1].split(
            "async function routeSnapshot", 1
        )[0]
        self.assertNotIn('[data-route-owner] [role="option"]', hostile)
        self.assertNotIn("querySelectorAll('h1,h2,h3,p,strong", hostile)
        self.assertIn("const dataSelector = [", hostile)
        self.assertIn("Array.from(prefix).length", hostile)
        self.assertIn("!node.querySelector(controlSelector)", hostile)
        self.assertIn("structurePreserved", hostile)
        self.assertIn("programmaticEquivalents", hostile)
        self.assertIn("node.setAttribute('aria-label', value)", hostile)
        self.assertNotIn("dispatchEvent(new Event('input'", hostile)

    def test_route_snapshot_uses_native_labels_and_distinguishes_visual_clipping(self) -> None:
        source = HARNESS.read_text(encoding="utf-8")
        snapshot = source.split("async function routeSnapshot", 1)[1].split(
            "async function exerciseScenario", 1
        )[0]
        self.assertIn("...(node.labels || [])", snapshot)
        self.assertIn("referencedText(node, 'aria-labelledby')", snapshot)
        self.assertIn("visualClippedOperationalText", snapshot)
        self.assertIn("inaccessible:", snapshot)
        self.assertIn("activeRect:", snapshot)

    def test_artifacts_have_fresh_identity_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            program = "\n".join((
                "const h = require('./tests/b19_t07_keyboard_ax_browser.js');",
                "const fs = require('fs');",
                f"const session = {{ screenshot: async (name) => fs.writeFileSync({json.dumps(temporary)} + '/' + name, Buffer.from('89504e470d0a1a0a', 'hex')) }};",
                f"(async () => {{ const result = await h.writeFreshArtifacts(session, {json.dumps(temporary)}, {{axTree: {{nodes: []}}, focusTrace: [], liveRegions: [], runtime: {{}}}}); console.log(JSON.stringify(result)); }})();",
            ))
            result = self.run_node(program)
            self.assertNotEqual(result["runId"], "")
            self.assertEqual({item["kind"] for item in result["artifacts"]}, {"screenshot", "ax_tree", "focus_trace", "live_region", "console_network_log", "identity"})
            self.assertEqual((Path(temporary) / "screenshot.png").read_bytes(), bytes.fromhex("89504e470d0a1a0a"))
            self.assertTrue(all(len(item["sha256"]) == 64 for item in result["artifacts"]))

    def test_run_identity_binds_focus_ax_live_region_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            program = "\n".join((
                "const h = require('./tests/b19_t07_keyboard_ax_browser.js');",
                "const fs = require('fs');",
                f"const session = {{ screenshot: async (name) => fs.writeFileSync({json.dumps(temporary)} + '/' + name, Buffer.from('89504e470d0a1a0a', 'hex')) }};",
                f"const artifacts = await h.writeFreshArtifacts(session, {json.dumps(temporary)}, {{axTree: {{nodes: []}}, focusTrace: [{{sequence: 1}}], axSemantics: [{{backendNodeId: 7}}], liveRegions: [{{backendNodeId: 10}}], runtime: {{}}}}, 'run-42');",
                "const capture = h.withRunIdentity({focusTrace: [{sequence: 1}], axSemantics: [{backendNodeId: 7}], liveRegions: [{backendNodeId: 10}]}, 'run-42');",
                "console.log(JSON.stringify({artifacts, capture}));",
            ))
            result = self.run_node(f"(async () => {{ {program} }})().catch((error) => {{ console.error(error); process.exitCode = 1; }});")
            self.assertEqual(result["artifacts"]["runId"], "run-42")
            self.assertTrue(all(item["runId"] == "run-42" for item in result["artifacts"]["artifacts"]))
            self.assertEqual(result["capture"], {"focusTrace": [{"sequence": 1, "runId": "run-42"}], "axSemantics": [{"backendNodeId": 7, "runId": "run-42"}], "liveRegions": [{"backendNodeId": 10, "runId": "run-42"}]})

    def test_existing_characterization_and_harness_syntax_compile(self) -> None:
        self.assertNotIn(".click(", HARNESS.read_text(encoding="utf-8"))
        for script in (ROOT / "tests" / "b19_t06_accessibility.js", HARNESS):
            subprocess.run(["node", "--check", str(script)], cwd=ROOT, check=True)
