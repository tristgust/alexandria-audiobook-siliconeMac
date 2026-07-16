from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from character_roster import (
    build_source_snapshot,
)
from roster_discovery import build_discovery_passages
from visual_discovery import (
    VisualDiscoveryMismatchError,
    load_visual_discovery_state,
    run_visual_discovery,
)
from tests.test_visual_discovery import (
    VisualDiscoveryFixture,
)
from tests.visual_discovery_support import (
    DynamicVisualRuntime,
)


class VisualDiscoveryResumeTests(
    unittest.TestCase,
    VisualDiscoveryFixture,
):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_text = "\n\n".join(
            (
                f"Section {index}. The Doctor adjusted his battered "
                "hat. Roz Forrester pushed back her dark hair."
            )
            for index in range(1, 20)
        )
        self.source_path = self.root / "book.txt"
        self.source_path.write_text(
            self.source_text,
            encoding="utf-8",
        )
        self.source, _ = build_source_snapshot(
            self.source_path
        )
        self.roster = self.roster(
            self.source["fingerprint"]
        )
        self.character_ids = [
            entry["id"]
            for entry in self.roster["entries"]
        ]
        self.state_path = (
            self.root / "character_visual_state.json"
        )
        self.refs = self.root / "persona_refs"
        self.refs.mkdir()
        self.passage_size = 260
        self.overlap = 40
        self.passages = build_discovery_passages(
            self.source_text,
            passage_size=self.passage_size,
            overlap=self.overlap,
        )

    def tearDown(self):
        self.temp.cleanup()

    def run_discovery(self, runtime, **overrides):
        arguments = {
            "runtime_client": runtime,
            "source": self.source,
            "source_text": self.source_text,
            "approved_roster": self.roster,
            "character_ids": self.character_ids,
            "state_path": self.state_path,
            "persona_refs_dir": self.refs,
            "passage_size": self.passage_size,
            "overlap_chars": self.overlap,
        }
        arguments.update(overrides)
        return run_visual_discovery(**arguments)

    def test_interrupted_run_resumes_without_repeating_passages(self):
        interrupted = DynamicVisualRuntime(
            fail_on_discovery_call=2
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "simulated visual interruption",
        ):
            self.run_discovery(interrupted)

        state = load_visual_discovery_state(
            self.state_path
        )
        self.assertIsNotNone(state)
        self.assertEqual(
            len(state["completed_passages"]),
            1,
        )
        self.assertFalse(any(self.refs.iterdir()))

        resumed = DynamicVisualRuntime()
        result = self.run_discovery(resumed)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            resumed.discovery_calls,
            len(self.passages) - 1,
        )
        self.assertEqual(resumed.reconciliation_calls, 1)
        self.assertFalse(self.state_path.exists())
        self.assertEqual(
            len(list(self.refs.glob("*.json"))),
            2,
        )

    def test_changed_model_blocks_existing_progress(self):
        interrupted = DynamicVisualRuntime(
            fail_on_discovery_call=2
        )
        with self.assertRaises(RuntimeError):
            self.run_discovery(interrupted)

        changed = DynamicVisualRuntime(
            model_name="different-model"
        )
        with self.assertRaisesRegex(
            VisualDiscoveryMismatchError,
            "visual configuration",
        ):
            self.run_discovery(changed)
        self.assertEqual(changed.discovery_calls, 0)
        self.assertTrue(self.state_path.exists())

    def test_changed_roster_blocks_existing_progress(self):
        interrupted = DynamicVisualRuntime(
            fail_on_discovery_call=2
        )
        with self.assertRaises(RuntimeError):
            self.run_discovery(interrupted)

        changed_roster = {
            **self.roster,
            "roster_fingerprint": "changed-roster",
        }
        runtime = DynamicVisualRuntime()
        with self.assertRaisesRegex(
            VisualDiscoveryMismatchError,
            "approved roster",
        ):
            self.run_discovery(
                runtime,
                approved_roster=changed_roster,
            )
        self.assertEqual(runtime.discovery_calls, 0)

    def test_changed_selected_character_set_blocks_progress(self):
        interrupted = DynamicVisualRuntime(
            fail_on_discovery_call=2
        )
        with self.assertRaises(RuntimeError):
            self.run_discovery(interrupted)

        runtime = DynamicVisualRuntime()
        with self.assertRaisesRegex(
            VisualDiscoveryMismatchError,
            "selected characters",
        ):
            self.run_discovery(
                runtime,
                character_ids=[self.character_ids[0]],
            )
        self.assertEqual(runtime.discovery_calls, 0)

    def test_changed_source_blocks_existing_progress(self):
        interrupted = DynamicVisualRuntime(
            fail_on_discovery_call=2
        )
        with self.assertRaises(RuntimeError):
            self.run_discovery(interrupted)

        changed_text = self.source_text + " New description."
        changed_path = self.root / "changed.txt"
        changed_path.write_text(
            changed_text,
            encoding="utf-8",
        )
        changed_source, _ = build_source_snapshot(
            changed_path
        )
        changed_roster = {
            **self.roster,
            "source": {
                **self.roster["source"],
                "path": str(changed_path),
                "basename": changed_path.name,
                "fingerprint": changed_source["fingerprint"],
                "character_count": len(changed_text),
            },
        }
        runtime = DynamicVisualRuntime()
        with self.assertRaisesRegex(
            VisualDiscoveryMismatchError,
            "source",
        ):
            self.run_discovery(
                runtime,
                source=changed_source,
                source_text=changed_text,
                approved_roster=changed_roster,
            )
        self.assertEqual(runtime.discovery_calls, 0)

    def test_changed_passage_layout_blocks_progress(self):
        interrupted = DynamicVisualRuntime(
            fail_on_discovery_call=2
        )
        with self.assertRaises(RuntimeError):
            self.run_discovery(interrupted)

        runtime = DynamicVisualRuntime()
        with self.assertRaisesRegex(
            VisualDiscoveryMismatchError,
            "visual configuration|passage layout",
        ):
            self.run_discovery(
                runtime,
                passage_size=340,
                overlap_chars=50,
            )
        self.assertEqual(runtime.discovery_calls, 0)


if __name__ == "__main__":
    unittest.main()
