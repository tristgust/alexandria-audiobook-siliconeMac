from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = (
    ROOT / "app/static/specialists/model_cache.js",
)
SOURCE = "\n".join(path.read_text(encoding="utf-8") for path in SOURCE_PATHS)


class ModelResidencyInterfaceTests(unittest.TestCase):
    def test_surface_reports_residents_identity_leases_and_owners(self) -> None:
        for contract in (
            "memory.residents",
            "resident.component_id",
            "resident.revision",
            "resident.build_id",
            "resident.runtime",
            "resident.device",
            "resident.active_lease_count",
            "resident.owners",
            "memory.current_owner",
            "memory.current_transition",
        ):
            self.assertIn(contract, SOURCE)

    def test_surface_reports_eviction_blockers_and_measured_release(self) -> None:
        for contract in (
            "memory.planned_eviction",
            "memory.blockers",
            "memory.last_release",
            "measured_available_bytes_recovered",
            "data-release-model-residents",
        ):
            self.assertIn(contract, SOURCE)

    def test_release_is_disabled_for_leases_operations_or_transitions(self) -> None:
        self.assertIn("activeJobs > 0 || operationActive || Boolean(transition)", SOURCE)
        self.assertIn("disabled: releaseBlocked", SOURCE)

    def test_module_is_valid_javascript(self) -> None:
        for source_path in SOURCE_PATHS:
            subprocess.run(
                ["node", "--check", str(source_path)],
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
