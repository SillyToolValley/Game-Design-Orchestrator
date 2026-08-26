from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = REPOSITORY_ROOT / "evals"
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "orchestrate-game-design"
CASES_FILE = EVAL_ROOT / "cases.json"
VALIDATOR = EVAL_ROOT / "validate_cases.py"


def load_validator() -> Any:
    specification = importlib.util.spec_from_file_location(
        "gdo_eval_validator_under_test", VALIDATOR
    )
    if specification is None or specification.loader is None:
        raise AssertionError("Could not load evals/validate_cases.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def copy_eval_root(temporary: str) -> Path:
    copied = Path(temporary) / "evals"
    shutil.copytree(
        EVAL_ROOT,
        copied,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    return copied


def run_git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def create_clean_checkout(temporary: str) -> tuple[Path, Path]:
    repository = Path(temporary) / "checkout"
    repository.mkdir()
    copied_evals = repository / "evals"
    copied_skill = repository / "skills" / "orchestrate-game-design"
    shutil.copytree(
        EVAL_ROOT,
        copied_evals,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    shutil.copytree(
        SKILL_ROOT,
        copied_skill,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    alternate_catalog = json.loads(
        (copied_evals / "cases.json").read_text(encoding="utf-8")
    )
    alternate_catalog["cases"][0]["prompt"] += " Use the alternate framing."
    (copied_evals / "alternate-cases.json").write_text(
        json.dumps(alternate_catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (repository / ".gitattributes").write_text("* -text\n", encoding="utf-8")
    run_git(repository, "init", "-q")
    run_git(repository, "config", "user.name", "Eval Test")
    run_git(repository, "config", "user.email", "eval-test@example.invalid")
    run_git(
        repository,
        "add",
        "--",
        ".gitattributes",
        "evals",
        "skills/orchestrate-game-design",
    )
    run_git(repository, "commit", "-q", "-m", "clean evaluator fixture")
    return repository, copied_evals


def valid_run_record(
    validator: Any,
    directory: Path,
    eval_root: Path,
    catalog_path: Path,
) -> tuple[dict[str, Any], Path]:
    directory.mkdir(parents=True, exist_ok=True)
    artifacts_directory = directory / "artifacts"
    artifacts_directory.mkdir()
    artifact_contents = {
        "final_response": b"final response\n",
        "tool_transcript": b'{"event":"complete"}\n',
        "resulting_file_tree": b"audience-claim.md\n",
        "changed_file_diff": b"no changes\n",
    }
    artifact_names = {
        "final_response": "final-response.md",
        "tool_transcript": "tool-transcript.jsonl",
        "resulting_file_tree": "file-tree.txt",
        "changed_file_diff": "changed.diff",
    }
    artifacts: dict[str, dict[str, Any]] = {}
    for name, content in artifact_contents.items():
        relative = Path("artifacts") / artifact_names[name]
        (directory / relative).write_bytes(content)
        artifacts[name] = {
            "path": relative.as_posix(),
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    revision, lineage, repository, _ = validator._checkout_identity(eval_root)
    catalog_bytes = catalog_path.read_bytes()
    record = {
        "record_schema_version": validator.RUN_RECORD_SCHEMA_VERSION,
        "case_id": "research-transfer-limit",
        "case_schema_version": 1,
        "started_at_utc": "2026-08-26T10:00:00Z",
        "completed_at_utc": "2026-08-26T10:05:00Z",
        "repository_revision": revision,
        "catalog": {
            "path": catalog_path.resolve().relative_to(repository).as_posix(),
            "sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        },
        "skill": lineage,
        "runner": {"name": "example-harness", "version": "1.4.0"},
        "model": {"name": "example-model", "configuration": {"reasoning": "high"}},
        "environment": {"operating_system": "example-os"},
        "tool_capabilities": [
            {"name": "filesystem_read", "available": True},
            {"name": "filesystem_write", "available": False},
            {"name": "external_research", "available": True},
            {"name": "external_mutation", "available": False},
        ],
        "tool_evidence": [],
        "claims": {
            "source_verification_claimed": False,
            "verified_source_targets": [],
        },
        "artifacts": artifacts,
    }
    record_path = directory / "run-record.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    return record, record_path


class ForwardEvalFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = load_validator()
        cls.catalog = json.loads(CASES_FILE.read_text(encoding="utf-8"))
        cls.clean_checkout_temporary = tempfile.TemporaryDirectory(
            prefix="gdo-eval-clean-checkout-"
        )
        cls.clean_repository, cls.clean_eval_root = create_clean_checkout(
            cls.clean_checkout_temporary.name
        )
        cls.clean_catalog = json.loads(
            (cls.clean_eval_root / "cases.json").read_text(encoding="utf-8")
        )
        cls.clean_catalog_path = cls.clean_eval_root / "cases.json"
        cls.alternate_catalog_path = cls.clean_eval_root / "alternate-cases.json"
        cls.alternate_catalog = json.loads(
            cls.alternate_catalog_path.read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.clean_checkout_temporary.cleanup()

    def test_default_cli_validates_without_claiming_behavior(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", str(VALIDATOR)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("valid-catalog", report["status"])
        self.assertEqual(6, report["case_count"])
        self.assertFalse(report["behavior_scored"])

    def test_invocation_is_separate_and_fixtures_are_declared(self) -> None:
        counts = self.validator.validate_catalog(self.catalog)
        self.assertEqual({"case_count": 6, "fixture_count": 4}, counts)
        for case in self.catalog["cases"]:
            self.assertEqual("orchestrate-game-design", case["invocation"]["skill"])
            self.assertNotIn("$orchestrate-game-design", case["prompt"])
            self.assertNotIn("expected answer", case["setup"].casefold())

    def test_catalog_schema_versions_require_json_integers(self) -> None:
        mutations = (
            (
                "case-schema-bool",
                lambda catalog: catalog.__setitem__("schema_version", True),
                "schema_version must be the integer 1",
            ),
            (
                "run-schema-float",
                lambda catalog: catalog["run_record_contract"].__setitem__(
                    "schema_version", 3.0
                ),
                "run_record_contract.schema_version must be the integer 3",
            ),
        )
        for label, mutate, expected in mutations:
            with self.subTest(label=label):
                catalog = deepcopy(self.catalog)
                mutate(catalog)
                with self.assertRaisesRegex(
                    self.validator.ValidationError, expected
                ):
                    self.validator.validate_catalog(catalog)

    def test_prepare_copies_inputs_and_hides_review_criteria(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-prepare-") as temporary:
            destination = Path(temporary) / "project"
            packet = self.validator.prepare_case(
                self.catalog, "review-before-rewrite", destination
            )
            self.assertTrue((destination / "GDD.md").is_file())
            self.assertEqual(["GDD.md"], [item["path"] for item in packet["baseline_files"]])
            self.assertNotIn("mechanical_checks", packet)
            self.assertNotIn("human_review", packet)
            serialized = json.dumps(packet)
            self.assertNotIn("incompatible round durations", serialized)

        nested_catalog = deepcopy(self.catalog)
        review_case = next(
            case
            for case in nested_catalog["cases"]
            if case["id"] == "review-before-rewrite"
        )
        review_case["fixture_files"][0]["destination"] = "inputs/review/GDD.md"
        with tempfile.TemporaryDirectory(prefix="gdo-eval-nested-prepare-") as temporary:
            destination = Path(temporary) / "project"
            packet = self.validator.prepare_case(
                nested_catalog, "review-before-rewrite", destination
            )
            self.assertTrue((destination / "inputs" / "review" / "GDD.md").is_file())
            self.assertEqual(
                ["inputs/review/GDD.md"],
                [item["path"] for item in packet["baseline_files"]],
            )

    def test_prepare_accepts_a_destination_below_a_resolvable_parent_alias(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-parent-alias-") as temporary:
            root = Path(temporary)
            real_parent = root / "canonical-parent"
            alias_parent = root / "parent-alias"
            real_parent.mkdir()

            if os.name == "nt":
                result = subprocess.run(
                    [
                        "cmd.exe",
                        "/d",
                        "/c",
                        "mklink",
                        "/J",
                        str(alias_parent),
                        str(real_parent),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    self.skipTest(f"directory junctions unavailable: {result.stderr}")
            else:
                alias_parent.symlink_to(real_parent, target_is_directory=True)

            requested_destination = alias_parent / "prepared"
            canonical_destination = real_parent.resolve(strict=True) / "prepared"
            try:
                packet = self.validator.prepare_case(
                    self.catalog,
                    "review-before-rewrite",
                    requested_destination,
                )
                self.assertEqual(
                    canonical_destination,
                    Path(packet["prepared_project"]),
                )
                self.assertTrue((canonical_destination / "GDD.md").is_file())
                self.assertTrue((requested_destination / "GDD.md").is_file())
            finally:
                if alias_parent.exists():
                    if os.name == "nt":
                        alias_parent.rmdir()
                    else:
                        alias_parent.unlink()

    def test_handle_path_comparison_uses_the_resolved_existing_spelling(self) -> None:
        resolved_spelling = Path(r"C:\Users\runneradmin\prepared")

        class ShortNameAlias:
            def __fspath__(self) -> str:
                return r"C:\RUNNER~1\prepared"

            def resolve(self, *, strict: bool) -> Path:
                if not strict:
                    raise AssertionError("existing handle paths must resolve strictly")
                return resolved_spelling

        self.assertEqual(
            self.validator._normalized_windows_path_text(
                os.fspath(resolved_spelling)
            ),
            self.validator._canonical_existing_path_text(ShortNameAlias()),
        )

    def test_prepare_requires_a_destination_that_does_not_exist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-nonempty-") as temporary:
            root = Path(temporary)
            destination = root / "existing-empty-project"
            destination.mkdir()
            with self.assertRaisesRegex(
                self.validator.ValidationError, "must not already exist"
            ):
                self.validator.prepare_case(
                    self.catalog, "playtest-boundaries", destination
                )
            self.assertFalse((destination / "playtest-notes.md").exists())

            missing_parent_destination = root / "missing-parent" / "project"
            with self.assertRaisesRegex(
                self.validator.ValidationError, "parent must already exist"
            ):
                self.validator.prepare_case(
                    self.catalog,
                    "playtest-boundaries",
                    missing_parent_destination,
                )
            self.assertFalse((root / "missing-parent").exists())

        help_result = subprocess.run(
            [sys.executable, "-B", str(VALIDATOR), "prepare", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(0, help_result.returncode, help_result.stderr)
        self.assertIn("non-existing directory whose parent exists", help_result.stdout)
        self.assertIn("parent must already exist", help_result.stdout)

    def test_custom_cases_uses_the_catalog_parent_for_fixtures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-custom-") as temporary:
            copied_eval_root = copy_eval_root(temporary)
            alternate = "alternate fixture from custom catalog\n"
            (copied_eval_root / "fixtures/review-before-rewrite/GDD.md").write_text(
                alternate, encoding="utf-8"
            )
            destination = Path(temporary) / "prepared"
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(VALIDATOR),
                    "--cases",
                    str(copied_eval_root / "cases.json"),
                    "prepare",
                    "--case",
                    "review-before-rewrite",
                    "--destination",
                    str(destination),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                alternate,
                (destination / "GDD.md").read_text(encoding="utf-8"),
            )

    def test_prepare_rejects_both_eval_root_overlap_directions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-overlap-") as temporary:
            copied_eval_root = copy_eval_root(temporary)
            catalog = json.loads(
                (copied_eval_root / "cases.json").read_text(encoding="utf-8")
            )
            destinations = (
                copied_eval_root,
                copied_eval_root / "prepared-output",
                copied_eval_root.parent,
            )
            for destination in destinations:
                with self.subTest(destination=str(destination)):
                    with self.assertRaisesRegex(
                        self.validator.ValidationError, "must not overlap"
                    ):
                        self.validator.prepare_case(
                            catalog,
                            "review-before-rewrite",
                            destination,
                            eval_root=copied_eval_root,
                        )

    def test_prepare_rechecks_fixture_identity_on_the_open_handle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-source-swap-") as temporary:
            root = Path(temporary)
            copied_eval_root = copy_eval_root(temporary)
            catalog = json.loads(
                (copied_eval_root / "cases.json").read_text(encoding="utf-8")
            )
            source_directory = (
                copied_eval_root / "fixtures" / "review-before-rewrite"
            )
            original_directory = source_directory.with_name(
                "review-before-rewrite-original"
            )
            outside = root / "outside"
            outside.mkdir()
            (outside / "GDD.md").write_text(
                "must not be copied\n", encoding="utf-8"
            )
            destination = root / "prepared"
            original_case_index = self.validator._case_index
            swapped = False

            def swap_after_validation(value: Any) -> Any:
                nonlocal swapped
                if not swapped:
                    source_directory.rename(original_directory)
                    if os.name == "nt":
                        result = subprocess.run(
                            [
                                "cmd.exe",
                                "/d",
                                "/c",
                                "mklink",
                                "/J",
                                str(source_directory),
                                str(outside),
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if result.returncode != 0:
                            original_directory.rename(source_directory)
                            self.skipTest(f"directory links unavailable: {result.stderr}")
                    else:
                        source_directory.symlink_to(outside, target_is_directory=True)
                    swapped = True
                return original_case_index(value)

            try:
                with mock.patch.object(
                    self.validator, "_case_index", side_effect=swap_after_validation
                ):
                    with self.assertRaisesRegex(
                        self.validator.ValidationError,
                        "regular file inside|changed while",
                    ):
                        self.validator.prepare_case(
                            catalog,
                            "review-before-rewrite",
                            destination,
                            eval_root=copied_eval_root,
                        )
                self.assertFalse((destination / "GDD.md").exists())
            finally:
                if swapped:
                    if os.name == "nt":
                        source_directory.rmdir()
                    else:
                        source_directory.unlink()
                    original_directory.rename(source_directory)

    @unittest.skipIf(os.name == "nt", "POSIX FIFO semantics only")
    def test_verified_fixture_open_cannot_block_on_fifo_swap(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-fifo-swap-") as temporary:
            root = Path(temporary)
            source = root / "fixture.md"
            source.write_text("regular before swap\n", encoding="utf-8")
            original_open = os.open
            saw_nonblocking_open = False

            def swap_to_fifo(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
                nonlocal saw_nonblocking_open
                if Path(path) == source:
                    saw_nonblocking_open = bool(
                        flags & getattr(os, "O_NONBLOCK", 0)
                    )
                    if not saw_nonblocking_open:
                        raise OSError("fixture open omitted O_NONBLOCK")
                    source.unlink()
                    os.mkfifo(source)
                return original_open(path, flags, *args, **kwargs)

            with mock.patch.object(
                self.validator.os,
                "open",
                side_effect=swap_to_fifo,
            ):
                with self.assertRaisesRegex(
                    self.validator.ValidationError,
                    "changed while|regular file",
                ):
                    self.validator._read_verified_bytes(
                        source,
                        allowed_root=root,
                        label="swapped FIFO fixture",
                    )
            self.assertTrue(saw_nonblocking_open)

    def test_verified_read_blocks_or_detects_same_length_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-read-rewrite-") as temporary:
            root = Path(temporary)
            source = root / "fixture.bin"
            original = b"alpha-bravo\n"
            replacement = b"gamma-delta\n"
            self.assertEqual(len(original), len(replacement))
            source.write_bytes(original)
            original_metadata = source.stat()
            original_fdopen = self.validator.os.fdopen
            mutation = {"blocked": False, "completed": False}

            class TwoPartMutatingReader:
                def __init__(
                    self, descriptor: int, *args: Any, **kwargs: Any
                ) -> None:
                    self.handle = original_fdopen(descriptor, *args, **kwargs)

                def fileno(self) -> int:
                    return self.handle.fileno()

                def close(self) -> None:
                    self.handle.close()

                def read(self) -> bytes:
                    first = self.handle.read(5)
                    if os.name != "nt":
                        time.sleep(0.02)
                    try:
                        with source.open("r+b") as writer:
                            writer.seek(0)
                            writer.write(replacement)
                            writer.flush()
                            os.fsync(writer.fileno())
                        os.utime(
                            source,
                            ns=(
                                original_metadata.st_atime_ns,
                                original_metadata.st_mtime_ns,
                            ),
                        )
                        mutation["completed"] = True
                    except OSError as error:
                        expected_windows_denial = os.name == "nt" and (
                            isinstance(error, PermissionError)
                            or getattr(error, "winerror", None) in (5, 32, 33)
                        )
                        if not expected_windows_denial:
                            raise
                        mutation["blocked"] = True
                    return first + self.handle.read()

            with mock.patch.object(
                self.validator.os,
                "fdopen",
                TwoPartMutatingReader,
            ):
                if os.name == "nt":
                    result = self.validator._read_verified_bytes(
                        source,
                        allowed_root=root,
                        label="concurrently edited fixture",
                    )
                    self.assertEqual(original, result)
                    self.assertTrue(mutation["blocked"])
                    self.assertFalse(mutation["completed"])
                else:
                    with self.assertRaisesRegex(
                        self.validator.ValidationError,
                        "changed while it was being read",
                    ):
                        self.validator._read_verified_bytes(
                            source,
                            allowed_root=root,
                            label="concurrently edited fixture",
                        )
                    self.assertFalse(mutation["blocked"])
                    self.assertTrue(mutation["completed"])
                    self.assertEqual(
                        original_metadata.st_mtime_ns,
                        source.stat().st_mtime_ns,
                    )

            with source.open("r+b") as editor:
                editor.seek(0)
                editor.write(original)
                editor.truncate()
            self.assertEqual(
                original,
                self.validator._read_verified_bytes(
                    source,
                    allowed_root=root,
                    label="ordinary fixture",
                ),
            )

    def test_prepare_rejects_destination_swap_before_opening_any_fixture(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-destination-swap-") as temporary:
            root = Path(temporary)
            destination = root / "prepared"
            parked_destination = root / "prepared-original"
            outside = root / "outside"
            outside.mkdir()
            swapped = False

            if os.name == "nt":
                original_open = self.validator._open_stable_windows_directory

                def swap_before_open(path: Path) -> int:
                    nonlocal swapped
                    if (
                        not swapped
                        and path.resolve(strict=False)
                        == destination.resolve(strict=False)
                    ):
                        destination.rename(parked_destination)
                        result = subprocess.run(
                            [
                                "cmd.exe",
                                "/d",
                                "/c",
                                "mklink",
                                "/J",
                                str(destination),
                                str(outside),
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if result.returncode != 0:
                            parked_destination.rename(destination)
                            self.skipTest(
                                f"directory junctions unavailable: {result.stderr}"
                            )
                        swapped = True
                    return original_open(path)

                patcher = mock.patch.object(
                    self.validator,
                    "_open_stable_windows_directory",
                    side_effect=swap_before_open,
                )
                expected_error = "^refusing linked prepared destination directory:"
            else:
                original_open = self.validator._open_posix_child_directory

                def swap_before_open(
                    parent_descriptor: int,
                    name: str,
                    created: os.stat_result,
                    path: Path,
                ) -> int:
                    nonlocal swapped
                    if (
                        not swapped
                        and path.resolve(strict=False)
                        == destination.resolve(strict=False)
                    ):
                        destination.rename(parked_destination)
                        destination.symlink_to(outside, target_is_directory=True)
                        swapped = True
                    return original_open(parent_descriptor, name, created, path)

                patcher = mock.patch.object(
                    self.validator,
                    "_open_posix_child_directory",
                    side_effect=swap_before_open,
                )
                expected_error = "^could not secure newly created destination "

            try:
                with patcher:
                    with self.assertRaisesRegex(
                        self.validator.ValidationError,
                        expected_error,
                    ) as raised:
                        self.validator.prepare_case(
                            self.catalog,
                            "review-before-rewrite",
                            destination,
                        )
                self.assertTrue(swapped, "the destination race hook did not run")
                self.assertIn(str(destination), str(raised.exception))
                self.assertFalse(
                    (outside / "GDD.md").exists(),
                    "a swapped destination must not receive the fixture",
                )
            finally:
                if swapped:
                    if os.name == "nt":
                        destination.rmdir()
                    else:
                        destination.unlink()
                    parked_destination.rename(destination)

    def test_prepare_secures_parent_before_creating_destination(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-parent-swap-") as temporary:
            root = Path(temporary)
            parent = root / "intended-parent"
            parked_parent = root / "intended-parent-original"
            destination = parent / "prepared"
            outside = root / "outside"
            parent.mkdir()
            outside.mkdir()
            swapped = False

            if os.name == "nt":
                original_open = self.validator._open_stable_windows_directory

                def swap_before_open(path: Path) -> int:
                    nonlocal swapped
                    if (
                        not swapped
                        and path.resolve(strict=False)
                        == parent.resolve(strict=False)
                    ):
                        parent.rename(parked_parent)
                        result = subprocess.run(
                            [
                                "cmd.exe",
                                "/d",
                                "/c",
                                "mklink",
                                "/J",
                                str(parent),
                                str(outside),
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if result.returncode != 0:
                            parked_parent.rename(parent)
                            self.skipTest(
                                f"directory junctions unavailable: {result.stderr}"
                            )
                        swapped = True
                    return original_open(path)

                patcher = mock.patch.object(
                    self.validator,
                    "_open_stable_windows_directory",
                    side_effect=swap_before_open,
                )
                expected_error = "^refusing linked prepared destination directory:"
            else:
                original_open = self.validator._open_stable_posix_directory

                def swap_before_open(path: Path, created: os.stat_result) -> int:
                    nonlocal swapped
                    if (
                        not swapped
                        and path.resolve(strict=False)
                        == parent.resolve(strict=False)
                    ):
                        parent.rename(parked_parent)
                        parent.symlink_to(outside, target_is_directory=True)
                        swapped = True
                    return original_open(path, created)

                patcher = mock.patch.object(
                    self.validator,
                    "_open_stable_posix_directory",
                    side_effect=swap_before_open,
                )
                expected_error = "^could not secure prepared destination directory "

            try:
                with patcher:
                    with self.assertRaisesRegex(
                        self.validator.ValidationError,
                        expected_error,
                    ) as raised:
                        self.validator.prepare_case(
                            self.catalog,
                            "review-before-rewrite",
                            destination,
                        )
                self.assertTrue(swapped, "the parent race hook did not run")
                self.assertIn(str(parent), str(raised.exception))
                self.assertFalse((outside / "prepared").exists())
                self.assertFalse((outside / "GDD.md").exists())
            finally:
                if swapped:
                    if os.name == "nt":
                        parent.rmdir()
                    else:
                        parent.unlink()
                    parked_parent.rename(parent)

    def test_fixture_paths_must_name_safe_relative_files(self) -> None:
        unsafe_paths = (
            ".",
            "../outside.md",
            "C:/outside.md",
            "folder\\file.md",
            "./file.md",
            "folder//file.md",
            "folder/file.md/",
            "folder/file.md:stream",
            "folder/NUL.txt",
            "folder/NUL .txt",
            "folder/COM1.log",
            "folder/name.",
            "folder/name ",
            "folder/a?b.md",
            "folder/control\x00.md",
            "folder/e\u0301.md",
        )
        for unsafe in unsafe_paths:
            with self.subTest(path=unsafe):
                with self.assertRaises(self.validator.ValidationError):
                    self.validator._safe_relative_path(unsafe, "fixture path")
        self.assertEqual(
            "folder/é.md",
            self.validator._safe_relative_path(
                "folder/é.md", "fixture path"
            ).as_posix(),
        )
        self.assertEqual(
            self.validator._portable_path_key("folder/É.md"),
            self.validator._portable_path_key("folder/E\u0301.md"),
        )

    def test_fixture_source_and_destination_reject_ntfs_ads_spelling(self) -> None:
        review_index = next(
            index
            for index, case in enumerate(self.catalog["cases"])
            if case["id"] == "review-before-rewrite"
        )
        for field, value in (
            ("source", "fixtures/review-before-rewrite/GDD.md:payload"),
            ("destination", "inputs/GDD.md:payload"),
        ):
            with self.subTest(field=field):
                catalog = deepcopy(self.catalog)
                catalog["cases"][review_index]["fixture_files"][0][field] = value
                with self.assertRaisesRegex(
                    self.validator.ValidationError,
                    "portable filesystems",
                ):
                    self.validator.validate_catalog(catalog)

    def test_fixture_sources_cannot_use_checkout_cache_exclusions(self) -> None:
        review_index = next(
            index
            for index, case in enumerate(self.catalog["cases"])
            if case["id"] == "review-before-rewrite"
        )
        for directory in sorted(
            self.validator.CHECKOUT_INPUT_FORBIDDEN_DIRECTORY_NAMES
        ):
            with self.subTest(directory=directory):
                catalog = deepcopy(self.catalog)
                catalog["cases"][review_index]["fixture_files"][0]["source"] = (
                    f"fixtures/{directory}/hidden-GDD.md"
                )
                with self.assertRaisesRegex(
                    self.validator.ValidationError,
                    "excluded checkout cache directory",
                ):
                    self.validator.validate_catalog(catalog)

    def test_actual_fixture_tree_rejects_portable_path_collisions(self) -> None:
        actual_files = list((EVAL_ROOT / "fixtures").rglob("*"))
        collision = mock.Mock()
        collision.is_file.return_value = True
        collision.relative_to.return_value = Path(
            "fixtures/review-before-rewrite/gdd.md"
        )

        with mock.patch.object(
            Path,
            "rglob",
            return_value=[*actual_files, collision],
        ):
            with self.assertRaisesRegex(
                self.validator.ValidationError,
                "collide on portable filesystems",
            ):
                self.validator.validate_catalog(self.catalog)

    def test_playtest_fixture_has_exactly_five_sessions_and_two_quotes(self) -> None:
        notes = (
            EVAL_ROOT
            / "fixtures"
            / "playtest-boundaries"
            / "playtest-notes.md"
        ).read_text(encoding="utf-8")
        self.assertEqual(5, len(re.findall(r"^## Session [1-5] — ", notes, re.MULTILINE)))
        self.assertEqual(2, len(re.findall(r"^- Participant quote:", notes, re.MULTILINE)))
        self.assertEqual(5, len(re.findall(r"^- Request after the run:", notes, re.MULTILINE)))

    def test_source_verification_requires_matching_tool_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-run-source-") as temporary:
            record, record_path = valid_run_record(
                self.validator,
                Path(temporary),
                self.clean_eval_root,
                self.clean_catalog_path,
            )
            self.validator.validate_run_record(
                record,
                self.clean_catalog,
                eval_root=self.clean_eval_root,
                record_path=record_path,
                catalog_path=self.clean_catalog_path,
            )

            unsupported = deepcopy(record)
            unsupported["claims"] = {
                "source_verification_claimed": True,
                "verified_source_targets": ["https://example.test/primary-source"],
            }
            with self.assertRaisesRegex(
                self.validator.ValidationError,
                "lacks successful external_research evidence",
            ):
                self.validator.validate_run_record(
                    unsupported,
                    self.clean_catalog,
                    eval_root=self.clean_eval_root,
                    record_path=record_path,
                    catalog_path=self.clean_catalog_path,
                )

            supported = deepcopy(unsupported)
            supported["tool_evidence"] = [
                {
                    "capability": "external_research",
                    "operation": "open",
                    "target": "https://example.test/primary-source",
                    "outcome": "success",
                    "recorded_at_utc": "2026-08-26T10:02:00Z",
                }
            ]
            self.validator.validate_run_record(
                supported,
                self.clean_catalog,
                eval_root=self.clean_eval_root,
                record_path=record_path,
                catalog_path=self.clean_catalog_path,
            )

            invalid_operation = deepcopy(supported)
            invalid_operation["tool_evidence"][0]["operation"] = "search"
            outside_run = deepcopy(supported)
            outside_run["tool_evidence"][0]["recorded_at_utc"] = (
                "1999-01-01T00:00:00Z"
            )
            non_utc = deepcopy(supported)
            non_utc["tool_evidence"][0]["recorded_at_utc"] = (
                "2026-08-26T19:02:00+09:00"
            )
            non_http = deepcopy(supported)
            non_http["tool_evidence"][0]["target"] = "file:///primary-source"
            cases = (
                (invalid_operation, "source-verification operation"),
                (outside_run, "outside the run"),
                (non_utc, "must be a UTC timestamp"),
                (non_http, "must be an HTTP"),
            )
            for invalid, expected in cases:
                with self.subTest(expected=expected):
                    with self.assertRaisesRegex(
                        self.validator.ValidationError, expected
                    ):
                        self.validator.validate_run_record(
                            invalid,
                            self.clean_catalog,
                            eval_root=self.clean_eval_root,
                            record_path=record_path,
                            catalog_path=self.clean_catalog_path,
                        )

    def test_run_record_requires_skill_lineage_and_declared_capabilities(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-run-lineage-") as temporary:
            record, record_path = valid_run_record(
                self.validator,
                Path(temporary),
                self.clean_eval_root,
                self.clean_catalog_path,
            )
            missing_hash = deepcopy(record)
            missing_hash["skill"].pop("sha256")
            with self.assertRaisesRegex(self.validator.ValidationError, "skill.sha256"):
                self.validator.validate_run_record(
                    missing_hash,
                    self.clean_catalog,
                    eval_root=self.clean_eval_root,
                    record_path=record_path,
                    catalog_path=self.clean_catalog_path,
                )

            missing_capability = deepcopy(record)
            missing_capability["tool_capabilities"] = missing_capability[
                "tool_capabilities"
            ][:-1]
            with self.assertRaisesRegex(
                self.validator.ValidationError, "external_mutation"
            ):
                self.validator.validate_run_record(
                    missing_capability,
                    self.clean_catalog,
                    eval_root=self.clean_eval_root,
                    record_path=record_path,
                    catalog_path=self.clean_catalog_path,
                )

            short_revision = deepcopy(record)
            short_revision["repository_revision"] = "0123456789abcdef"
            wrong_revision = deepcopy(record)
            wrong_revision["repository_revision"] = "f" * 40
            wrong_version = deepcopy(record)
            wrong_version["skill"]["version"] = "invented-version"
            wrong_contract = deepcopy(record)
            wrong_contract["skill"]["contract_id"] = "invented-contract"
            wrong_hash = deepcopy(record)
            wrong_hash["skill"]["sha256"] = "0" * 64
            float_record_schema = deepcopy(record)
            float_record_schema["record_schema_version"] = 3.0
            boolean_case_schema = deepcopy(record)
            boolean_case_schema["case_schema_version"] = True
            cases = (
                (short_revision, "full 40- or 64-character commit id"),
                (wrong_revision, "does not match evaluator checkout HEAD"),
                (wrong_version, "skill.version does not match"),
                (wrong_contract, "skill.contract_id does not match"),
                (wrong_hash, "skill.sha256 does not match"),
                (float_record_schema, "record_schema_version does not match"),
                (boolean_case_schema, "case_schema_version does not match"),
            )
            for invalid, expected in cases:
                with self.subTest(expected=expected):
                    with self.assertRaisesRegex(
                        self.validator.ValidationError, expected
                    ):
                        self.validator.validate_run_record(
                            invalid,
                            self.clean_catalog,
                            eval_root=self.clean_eval_root,
                            record_path=record_path,
                            catalog_path=self.clean_catalog_path,
                        )

    def test_run_record_artifacts_are_safe_real_and_hash_matched(self) -> None:
        mutations = (
            (
                "ntfs-ads-path",
                lambda record, _root: record["artifacts"]["final_response"].update(
                    {"path": "artifacts/final-response.md:payload"}
                ),
                "portable filesystems",
            ),
            (
                "unsafe-path",
                lambda record, _root: record["artifacts"]["final_response"].update(
                    {"path": "../outside.md"}
                ),
                "safe relative path",
            ),
            (
                "wrong-bytes",
                lambda record, _root: record["artifacts"]["final_response"].update(
                    {"bytes": 999}
                ),
                "bytes does not match",
            ),
            (
                "wrong-hash",
                lambda record, _root: record["artifacts"]["final_response"].update(
                    {"sha256": "0" * 64}
                ),
                "sha256 does not match",
            ),
            (
                "missing-file",
                lambda record, root: (
                    root / record["artifacts"]["final_response"]["path"]
                ).unlink(),
                "does not name a regular file",
            ),
            (
                "duplicate-path",
                lambda record, _root: record["artifacts"]["final_response"].update(
                    {
                        "path": record["artifacts"]["tool_transcript"]["path"],
                        "bytes": record["artifacts"]["tool_transcript"]["bytes"],
                        "sha256": record["artifacts"]["tool_transcript"]["sha256"],
                    }
                ),
                "artifact paths must be distinct",
            ),
        )
        for label, mutate, expected in mutations:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory(
                    prefix=f"gdo-run-artifact-{label}-"
                ) as temporary:
                    root = Path(temporary)
                    record, record_path = valid_run_record(
                        self.validator,
                        root,
                        self.clean_eval_root,
                        self.clean_catalog_path,
                    )
                    mutate(record, root)
                    with self.assertRaisesRegex(
                        self.validator.ValidationError, expected
                    ):
                        self.validator.validate_run_record(
                            record,
                            self.clean_catalog,
                            eval_root=self.clean_eval_root,
                            record_path=record_path,
                            catalog_path=self.clean_catalog_path,
                        )

    def test_validate_run_rejects_scoped_dirty_checkout_states(self) -> None:
        dirty_states = (
            "unstaged",
            "staged",
            "untracked",
            "skip-worktree",
            "ignored",
        )
        for dirty_state in dirty_states:
            with self.subTest(dirty_state=dirty_state):
                with tempfile.TemporaryDirectory(
                    prefix=f"gdo-eval-dirty-{dirty_state}-"
                ) as temporary:
                    repository, eval_root = create_clean_checkout(temporary)
                    catalog = json.loads(
                        (eval_root / "cases.json").read_text(encoding="utf-8")
                    )
                    record_root = Path(temporary) / "record"
                    record, record_path = valid_run_record(
                        self.validator,
                        record_root,
                        eval_root,
                        eval_root / "cases.json",
                    )

                    if dirty_state == "unstaged":
                        readme = eval_root / "README.md"
                        readme.write_text(
                            readme.read_text(encoding="utf-8") + "\ndirty\n",
                            encoding="utf-8",
                        )
                    elif dirty_state == "staged":
                        skill = repository / "skills" / "orchestrate-game-design" / "SKILL.md"
                        skill.write_text(
                            skill.read_text(encoding="utf-8") + "\nstaged dirty\n",
                            encoding="utf-8",
                        )
                        run_git(repository, "add", "--", "skills/orchestrate-game-design/SKILL.md")
                    elif dirty_state == "untracked":
                        (eval_root / "untracked-evaluator-input.txt").write_text(
                            "untracked dirty\n", encoding="utf-8"
                        )
                    elif dirty_state == "skip-worktree":
                        readme = eval_root / "README.md"
                        run_git(
                            repository,
                            "update-index",
                            "--skip-worktree",
                            "evals/README.md",
                        )
                        readme.write_text(
                            readme.read_text(encoding="utf-8")
                            + "\nskip-worktree dirty\n",
                            encoding="utf-8",
                        )
                        self.assertEqual(
                            "",
                            run_git(
                                repository,
                                "status",
                                "--porcelain=v1",
                                "--",
                                "evals/README.md",
                            ),
                        )
                    else:
                        ignored_name = "ignored-evaluator-input.txt"
                        exclude = repository / ".git" / "info" / "exclude"
                        exclude.write_text(
                            exclude.read_text(encoding="utf-8")
                            + f"/evals/{ignored_name}\n",
                            encoding="utf-8",
                        )
                        (eval_root / ignored_name).write_text(
                            "ignored dirty\n", encoding="utf-8"
                        )
                        self.assertEqual(
                            "",
                            run_git(
                                repository,
                                "status",
                                "--porcelain=v1",
                                "--untracked-files=all",
                                "--",
                                f"evals/{ignored_name}",
                            ),
                        )

                    with self.assertRaisesRegex(
                        self.validator.ValidationError,
                        "dirty.*HEAD commit cannot reproduce",
                    ):
                        self.validator.validate_run_record(
                            record,
                            catalog,
                            eval_root=eval_root,
                            record_path=record_path,
                            catalog_path=eval_root / "cases.json",
                        )

    def test_validate_run_rejects_an_ignored_custom_catalog_tree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-ignored-catalog-") as temporary:
            repository, eval_root = create_clean_checkout(temporary)
            primary_catalog_path = eval_root / "cases.json"
            record, record_path = valid_run_record(
                self.validator,
                Path(temporary) / "record",
                eval_root,
                primary_catalog_path,
            )
            ignored_root = repository / "ignored-evals"
            shutil.copytree(
                eval_root,
                ignored_root,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
            exclude = repository / ".git" / "info" / "exclude"
            exclude.write_text(
                exclude.read_text(encoding="utf-8") + "/ignored-evals/\n",
                encoding="utf-8",
            )
            ignored_catalog_path = ignored_root / "cases.json"
            ignored_catalog = json.loads(
                ignored_catalog_path.read_text(encoding="utf-8")
            )
            record["catalog"] = {
                "path": "ignored-evals/cases.json",
                "sha256": hashlib.sha256(
                    ignored_catalog_path.read_bytes()
                ).hexdigest(),
            }
            self.assertEqual(
                "",
                run_git(
                    repository,
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                    "--",
                    "ignored-evals",
                ),
            )
            with self.assertRaisesRegex(
                self.validator.ValidationError,
                "file set does not match HEAD",
            ):
                self.validator.validate_run_record(
                    record,
                    ignored_catalog,
                    eval_root=ignored_root,
                    record_path=record_path,
                    catalog_path=ignored_catalog_path,
                )

    def test_validate_run_allows_only_explicit_cache_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-cache-exclusions-") as temporary:
            repository, eval_root = create_clean_checkout(temporary)
            catalog_path = eval_root / "cases.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            record, record_path = valid_run_record(
                self.validator,
                Path(temporary) / "record",
                eval_root,
                catalog_path,
            )
            bytecode = eval_root / "__pycache__" / "validator.pyc"
            bytecode.parent.mkdir()
            bytecode.write_bytes(b"cache bytes")
            pytest_cache = (
                repository
                / "skills"
                / "orchestrate-game-design"
                / ".pytest_cache"
                / "state"
            )
            pytest_cache.parent.mkdir()
            pytest_cache.write_text("cache state\n", encoding="utf-8")
            self.validator.validate_run_record(
                record,
                catalog,
                eval_root=eval_root,
                record_path=record_path,
                catalog_path=catalog_path,
            )

            unexpected = eval_root / "unscoped-bytecode.pyc"
            unexpected.write_bytes(b"not an allowed cache location")
            with self.assertRaisesRegex(
                self.validator.ValidationError,
                "file set does not match HEAD",
            ):
                self.validator.validate_run_record(
                    record,
                    catalog,
                    eval_root=eval_root,
                    record_path=record_path,
                    catalog_path=catalog_path,
                )

    def test_validate_run_binds_record_to_one_exact_tracked_catalog(self) -> None:
        self.assertNotEqual(
            self.clean_catalog["cases"][0]["prompt"],
            self.alternate_catalog["cases"][0]["prompt"],
        )
        with tempfile.TemporaryDirectory(prefix="gdo-eval-catalog-bind-") as temporary:
            record, record_path = valid_run_record(
                self.validator,
                Path(temporary),
                self.clean_eval_root,
                self.clean_catalog_path,
            )
            with self.assertRaisesRegex(
                self.validator.ValidationError,
                "catalog.path does not match",
            ):
                self.validator.validate_run_record(
                    record,
                    self.alternate_catalog,
                    eval_root=self.clean_eval_root,
                    record_path=record_path,
                    catalog_path=self.alternate_catalog_path,
                )

            wrong_hash = deepcopy(record)
            wrong_hash["catalog"]["path"] = "evals/alternate-cases.json"
            with self.assertRaisesRegex(
                self.validator.ValidationError,
                "catalog.sha256 does not match",
            ):
                self.validator.validate_run_record(
                    wrong_hash,
                    self.alternate_catalog,
                    eval_root=self.clean_eval_root,
                    record_path=record_path,
                    catalog_path=self.alternate_catalog_path,
                )

            with self.assertRaisesRegex(
                self.validator.ValidationError,
                "supplied catalog does not match catalog_path bytes",
            ):
                self.validator.validate_run_record(
                    record,
                    self.alternate_catalog,
                    eval_root=self.clean_eval_root,
                    record_path=record_path,
                    catalog_path=self.clean_catalog_path,
                )

    def test_checkout_rejects_overlapping_eval_and_skill_scopes(self) -> None:
        with self.assertRaisesRegex(
            self.validator.ValidationError,
            "scopes must not overlap",
        ):
            self.validator._checkout_identity(self.clean_repository)

    def test_checkout_lineage_ignores_git_replace_refs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-replace-ref-") as temporary:
            repository, eval_root = create_clean_checkout(temporary)
            original_revision = run_git(repository, "rev-parse", "HEAD")
            skill_path = (
                repository / "skills" / "orchestrate-game-design" / "SKILL.md"
            )
            skill_path.write_text(
                skill_path.read_text(encoding="utf-8")
                + "\nreplacement-only skill contents\n",
                encoding="utf-8",
            )
            run_git(
                repository,
                "add",
                "--",
                "skills/orchestrate-game-design/SKILL.md",
            )
            run_git(repository, "commit", "-q", "-m", "replacement contents")
            replacement_revision = run_git(repository, "rev-parse", "HEAD")
            run_git(repository, "reset", "-q", "--hard", original_revision)
            run_git(repository, "replace", original_revision, replacement_revision)
            run_git(repository, "reset", "-q", "--hard", "HEAD")
            self.assertEqual("", run_git(repository, "status", "--porcelain=v1"))

            with self.assertRaisesRegex(
                self.validator.ValidationError,
                "HEAD commit cannot reproduce",
            ):
                self.validator._checkout_identity(eval_root)

    def test_cli_wraps_invalid_utf8_and_read_errors_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-eval-json-errors-") as temporary:
            root = Path(temporary)
            invalid_utf8_catalog = root / "invalid-utf8-cases.json"
            invalid_utf8_catalog.write_bytes(b"\xff\xfe")
            commands = (
                [
                    sys.executable,
                    "-B",
                    str(VALIDATOR),
                    "--cases",
                    str(invalid_utf8_catalog),
                    "validate",
                ],
                [
                    sys.executable,
                    "-B",
                    str(VALIDATOR),
                    "--cases",
                    str(root),
                    "validate",
                ],
            )
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(
                        command,
                        cwd=REPOSITORY_ROOT,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        check=False,
                    )
                    self.assertEqual(2, result.returncode)
                    self.assertIn("error:", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def test_json_parser_rejects_nonfinite_numeric_constants(self) -> None:
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                with self.assertRaisesRegex(
                    self.validator.ValidationError,
                    rf"non-finite numeric constant {re.escape(constant)}",
                ):
                    self.validator._parse_json_bytes(
                        f'{{"extra": {constant}}}'.encode("utf-8"),
                        "case catalog",
                        CASES_FILE,
                    )

    def test_validate_run_cli_confirms_metadata_without_scoring_behavior(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-run-cli-") as temporary:
            record, record_path = valid_run_record(
                self.validator,
                Path(temporary),
                self.clean_eval_root,
                self.clean_catalog_path,
            )
            record_path.write_text(json.dumps(record), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(self.clean_eval_root / "validate_cases.py"),
                    "--cases",
                    str(self.clean_eval_root / "cases.json"),
                    "validate-run",
                    "--record",
                    str(record_path),
                ],
                cwd=self.clean_repository,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual("valid-metadata", report["status"])
            self.assertFalse(report["behavior_scored"])

    def test_blind_protocol_keeps_evaluator_inputs_outside_subject_visibility(self) -> None:
        protocol = (EVAL_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("outside the subject's filesystem sandbox", protocol)
        self.assertIn("not visible to the subject", protocol)
        self.assertIn("parent already exists", protocol)
        self.assertIn("destination itself does not", protocol)
        self.assertIn("does not prove", protocol)


if __name__ == "__main__":
    unittest.main()
