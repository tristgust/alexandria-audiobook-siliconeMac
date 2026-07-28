from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from produce_aggregate import build_produce_aggregate


class ProduceSpeakerRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        self.config = {"tts": {"language": "English", "parallel_workers": 2}}
        (self.root / "app" / "config.json").write_text(
            json.dumps(self.config), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def aggregate(
        self,
        *,
        cast: dict[str, Any],
    ) -> dict[str, Any]:
        return build_produce_aggregate(
            root_dir=self.root,
            chunks=[{
                "id": 7,
                "speaker": "VICAR",
                "text": "You are welcome in this parish.",
                "instruct": "Formal but kind.",
                "status": "pending",
                "audio_path": None,
            }],
            voice_config={},
            config=self.config,
            cast=cast,
            audio_validity={},
            process={},
            selected_chunk_id="chunk:7",
        )

    def test_identityless_missing_voice_routes_to_full_cast_recovery(self) -> None:
        blocker = self.aggregate(cast={"characters": []})["chunks"][0][
            "blockers"
        ][0]

        self.assertEqual(
            blocker["native_destination"],
            "more/advanced-character-operations",
        )
        self.assertEqual(blocker["target_id"], "VICAR")

    def test_active_missing_voice_still_routes_to_cast_character(self) -> None:
        blocker = self.aggregate(cast={
            "characters": [{
                "character_id": "character_vicar",
                "display_name": "Vicar",
                "script_connection": {
                    "resolved_script_voice_label": "VICAR",
                },
                "voice": {
                    "configuration_key": "VICAR",
                    "selected_production_method": "custom",
                    "valid": False,
                },
            }],
        })["chunks"][0]["blockers"][0]

        self.assertEqual(blocker["native_destination"], "cast")
        self.assertEqual(blocker["target_id"], "cast:character:character_vicar")


if __name__ == "__main__":
    unittest.main()
