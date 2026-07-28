from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from benchmarks.multimodel_round1_handoff import (
    ANSWER_KEY_ROOT_NAME,
    CANONICAL_PUBLIC_ROOT_NAME,
    HandoffPolicyError,
    resolve_round1_handoff_paths,
    supersedable_legacy_roots,
)


class MultimodelRound1HandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_defaults_use_stable_sibling_roots_under_evidence(self) -> None:
        # Given an evidence root and no handoff-root overrides.
        # When the canonical Round 1 paths are resolved.
        paths = resolve_round1_handoff_paths(self.evidence)

        # Then both outputs use the stable names and stay directly under evidence.
        self.assertEqual(
            paths.public_root,
            (self.evidence / CANONICAL_PUBLIC_ROOT_NAME).resolve(),
        )
        self.assertEqual(
            paths.answer_key_root,
            (self.evidence / ANSWER_KEY_ROOT_NAME).resolve(),
        )
        self.assertEqual(paths.public_root.parent, self.evidence.resolve())
        self.assertEqual(paths.answer_key_root.parent, self.evidence.resolve())

    def test_explicit_canonical_paths_are_accepted(self) -> None:
        # Given explicitly supplied canonical sibling roots.
        public_root = self.evidence / CANONICAL_PUBLIC_ROOT_NAME
        answer_key_root = self.evidence / ANSWER_KEY_ROOT_NAME

        # When the paths are resolved.
        paths = resolve_round1_handoff_paths(
            self.evidence,
            public_root=public_root,
            answer_key_root=answer_key_root,
        )

        # Then the resolved values are normalized absolute paths.
        self.assertEqual(paths.public_root, public_root.resolve())
        self.assertEqual(paths.answer_key_root, answer_key_root.resolve())

    def test_legacy_public_default_is_rejected_as_stale(self) -> None:
        # Given a public root using a superseded legacy name.
        stale_root = self.evidence / "review-round1-complete"

        # When it is passed as the public root.
        with self.assertRaises(HandoffPolicyError) as raised:
            resolve_round1_handoff_paths(self.evidence, public_root=stale_root)

        # Then the policy fails closed before the stale root can be used.
        self.assertEqual(raised.exception.code, "stale_public_root")
        self.assertEqual(raised.exception.path, stale_root.resolve())

    def test_arbitrary_root_names_are_rejected(self) -> None:
        # Given public and answer-key paths with names outside the allowlist.
        cases = (
            ("public_root", self.evidence / "custom-public"),
            ("answer_key_root", self.evidence / "answer-keys"),
        )

        # When either unallowlisted path is supplied.
        for parameter, path in cases:
            with self.subTest(parameter=parameter):
                with self.assertRaises(HandoffPolicyError) as raised:
                    resolve_round1_handoff_paths(
                        self.evidence,
                        **{parameter: path},
                    )

                # Then the failure identifies the offending root name.
                self.assertEqual(raised.exception.code, "disallowed_root_name")
                self.assertEqual(raised.exception.path, path.resolve())

    def test_public_and_answer_key_roots_must_remain_contained(self) -> None:
        # Given canonical-looking roots outside or nested below evidence.
        outside = self.root / CANONICAL_PUBLIC_ROOT_NAME
        nested_answer = (
            self.evidence
            / CANONICAL_PUBLIC_ROOT_NAME
            / ANSWER_KEY_ROOT_NAME
        )

        # When either path is supplied.
        with self.assertRaises(HandoffPolicyError) as outside_error:
            resolve_round1_handoff_paths(self.evidence, public_root=outside)
        with self.assertRaises(HandoffPolicyError) as nested_error:
            resolve_round1_handoff_paths(
                self.evidence,
                answer_key_root=nested_answer,
            )

        # Then neither path is accepted as a handoff sibling.
        self.assertEqual(outside_error.exception.code, "path_outside_evidence")
        self.assertEqual(nested_error.exception.code, "path_not_evidence_child")

    def test_symlinked_public_root_cannot_escape_evidence(self) -> None:
        # Given a canonical-named symlink whose target is outside evidence.
        outside = self.root / "outside"
        outside.mkdir()
        link = self.evidence / CANONICAL_PUBLIC_ROOT_NAME
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        # When the symlink is resolved as the public root.
        with self.assertRaises(HandoffPolicyError) as raised:
            resolve_round1_handoff_paths(self.evidence, public_root=link)

        # Then the path escape is rejected.
        self.assertEqual(raised.exception.code, "path_outside_evidence")

    def test_legacy_symlink_name_cannot_alias_the_canonical_public_root(self) -> None:
        # Given a stale-named symlink targeting the canonical public directory.
        target = self.evidence / CANONICAL_PUBLIC_ROOT_NAME
        target.mkdir()
        stale_link = self.evidence / "review"
        try:
            os.symlink(target, stale_link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        # When the stale alias is supplied as the public root.
        with self.assertRaises(HandoffPolicyError) as raised:
            resolve_round1_handoff_paths(self.evidence, public_root=stale_link)

        # Then the lexical legacy name still fails closed.
        self.assertEqual(raised.exception.code, "stale_public_root")

    def test_colocated_answer_keys_fail_closed_without_deletion(self) -> None:
        # Given a public root that already contains an answer-keys directory.
        public_root = self.evidence / CANONICAL_PUBLIC_ROOT_NAME
        colocated = public_root / "answer-keys"
        colocated.mkdir(parents=True)
        marker = colocated / "legacy.json"
        marker.write_text("keep", encoding="utf-8")

        # When canonical paths are resolved.
        with self.assertRaises(HandoffPolicyError) as raised:
            resolve_round1_handoff_paths(self.evidence)

        # Then resolution fails and the caller's files are untouched.
        self.assertEqual(raised.exception.code, "colocated_answer_keys")
        self.assertTrue(marker.is_file())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_answer_key_root_must_not_equal_or_descend_from_public(self) -> None:
        # Given an answer-key root that is colocated with the public package.
        public_root = self.evidence / CANONICAL_PUBLIC_ROOT_NAME
        colocated = public_root / ANSWER_KEY_ROOT_NAME

        # When both paths are supplied.
        with self.assertRaises(HandoffPolicyError) as raised:
            resolve_round1_handoff_paths(
                self.evidence,
                public_root=public_root,
                answer_key_root=colocated,
            )

        # Then private keys cannot be placed inside the public tree.
        self.assertEqual(raised.exception.code, "path_not_evidence_child")

    def test_supersedable_legacy_roots_are_exact_and_not_deleted(self) -> None:
        # Given every supersedable legacy root plus an unrelated directory.
        names = (
            "review",
            "review-integrity-reconciled",
            "review-integrity-reconciled-answer-keys",
            "review-round1-complete",
            "review-round1-complete-answer-keys",
        )
        for name in names:
            (self.evidence / name).mkdir()
        (self.evidence / "review-round1-complete-final").mkdir()
        (self.evidence / "unrelated").mkdir()

        # When legacy roots are identified for a non-destructive supersession.
        identified = supersedable_legacy_roots(self.evidence)

        # Then the exact allowlisted legacy set is returned in stable order.
        self.assertEqual(
            identified,
            tuple((self.evidence / name).resolve() for name in names),
        )
        self.assertTrue(all(path.exists() for path in identified))
        self.assertTrue((self.evidence / "review-round1-complete-final").exists())
        self.assertTrue((self.evidence / "unrelated").exists())

    def test_missing_legacy_roots_are_not_reported(self) -> None:
        # Given only one legacy root on disk.
        legacy = self.evidence / "review"
        legacy.mkdir()

        # When supersedable roots are listed.
        identified = supersedable_legacy_roots(self.evidence)

        # Then only the existing legacy path is reported.
        self.assertEqual(identified, (legacy.resolve(),))

    def test_legacy_symlink_escape_fails_closed(self) -> None:
        # Given a legacy root symlink whose target is outside evidence.
        outside = self.root / "outside"
        outside.mkdir()
        link = self.evidence / "review"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        # When supersedable roots are identified.
        with self.assertRaises(HandoffPolicyError) as raised:
            supersedable_legacy_roots(self.evidence)

        # Then the helper does not expose an unsafe deletion candidate.
        self.assertEqual(raised.exception.code, "path_outside_evidence")


if __name__ == "__main__":
    unittest.main()
