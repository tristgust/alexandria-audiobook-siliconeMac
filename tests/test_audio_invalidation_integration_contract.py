from __future__ import annotations

import ast
import inspect
import unittest

import app as app_module
import generate_personas
from external_workflows import _transaction as external_workflow_transaction
from speaker_management import _apply_transaction as speaker_transaction
from speaker_management import undo_speaker_operation


def called_names(function) -> set[str]:
    tree = ast.parse(inspect.getsource(function))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


class AudioInvalidationIntegrationContractTests(unittest.TestCase):
    def test_ordinary_voice_save_uses_canonical_dependency_transaction(self) -> None:
        calls = called_names(app_module.save_voice_config)
        self.assertIn("_apply_voice_config_dependency_change", calls)
        self.assertNotIn("atomic_json_write", calls)

    def test_voice_library_assign_and_clear_use_canonical_dependency_transaction(self) -> None:
        for function in (
            app_module.assign_voice_library_voice,
            app_module.clear_voice_library_assignment,
        ):
            calls = called_names(function)
            self.assertIn("_apply_voice_config_dependency_change", calls)
            self.assertNotIn("atomic_json_write", calls)

    def test_persona_generation_commits_voice_config_through_invalidation(self) -> None:
        calls = called_names(generate_personas.main)
        self.assertIn("_commit_voice_config", calls)

    def test_imported_script_and_speaker_management_retain_canonical_service(self) -> None:
        self.assertIn(
            "apply_audio_invalidation_transaction",
            called_names(external_workflow_transaction),
        )
        self.assertIn(
            "apply_audio_invalidation_transaction",
            called_names(speaker_transaction),
        )
        self.assertIn(
            "undo_audio_invalidation_transaction",
            called_names(undo_speaker_operation),
        )


if __name__ == "__main__":
    unittest.main()
