from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
INIT_SCRIPT = SCRIPTS_ROOT / "init_design_project.py"
CREATE_SCRIPT = SCRIPTS_ROOT / "create_design_artifact.py"
CHECK_SCRIPT = SCRIPTS_ROOT / "check_design_workspace.py"
VERSION = "0.1.0-beta"


def temporary_workspace(prefix: str) -> tempfile.TemporaryDirectory[str]:
    parent = Path(os.environ.get("GDO_TEST_TMP", tempfile.gettempdir()))
    parent.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=parent)


def run_python(
    script: Path,
    *arguments: object,
    check: bool = True,
    timeout: int = 40,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(script), *(str(value) for value in arguments)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"Command failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def payload(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise AssertionError("Expected a JSON object.")
    return value


def initialize(root: Path, name: str = "Test Game") -> dict[str, Any]:
    return payload(run_python(INIT_SCRIPT, "--root", root, "--name", name))


def create(root: Path, kind: str, name: str, *arguments: str) -> dict[str, Any]:
    return payload(
        run_python(
            CREATE_SCRIPT,
            "--root",
            root,
            kind,
            "--name",
            name,
            *arguments,
        )
    )


def check_workspace(
    root: Path, *, check: bool = True
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    result = run_python(CHECK_SCRIPT, "--root", root, "--json", check=check)
    return result, payload(result)


def issue_codes(report: dict[str, Any], key: str) -> set[str]:
    return {issue["code"] for issue in report[key]}


class LeanOrchestratorTests(unittest.TestCase):
    def test_versions_are_consistent(self) -> None:
        for script in (INIT_SCRIPT, CREATE_SCRIPT, CHECK_SCRIPT):
            result = run_python(script, "--version")
            self.assertEqual(VERSION, result.stdout.strip())

        with temporary_workspace("gdo-version-") as temporary:
            root = Path(temporary)
            initialize(root)
            state = json.loads(
                (root / "game-design" / ".gdo" / "state.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(VERSION, state["tool_version"])

    def test_init_creates_only_the_minimal_workspace(self) -> None:
        with temporary_workspace("gdo-init-minimal-") as temporary:
            root = Path(temporary)
            result = initialize(root, "Small Game")
            design = root / "game-design"
            files = {
                path.relative_to(design).as_posix()
                for path in design.rglob("*")
                if path.is_file()
            }
            self.assertEqual(
                {".gitignore", ".gdo/state.json", "design.md"},
                files,
            )
            self.assertEqual(3, len(result["created"]))
            self.assertFalse((design / ".gdo" / "workspace.lock").exists())
            for directory in ("tests", "systems", "reviews"):
                self.assertFalse((design / directory).exists())

    def test_init_is_idempotent_and_does_not_overwrite(self) -> None:
        with temporary_workspace("gdo-init-idempotent-") as temporary:
            root = Path(temporary)
            initialize(root, "Original")
            design_path = root / "game-design" / "design.md"
            design_path.write_text("my hand-written design\n", encoding="utf-8")
            state_path = root / "game-design" / ".gdo" / "state.json"
            before_state = state_path.read_text(encoding="utf-8")

            result = payload(
                run_python(
                    INIT_SCRIPT,
                    "--root",
                    root,
                    "--name",
                    "Replacement",
                )
            )
            self.assertEqual([], result["created"])
            self.assertEqual(3, len(result["skipped"]))
            self.assertEqual(
                "my hand-written design\n",
                design_path.read_text(encoding="utf-8"),
            )
            self.assertEqual(before_state, state_path.read_text(encoding="utf-8"))

    def test_init_rejects_root_escape(self) -> None:
        with temporary_workspace("gdo-containment-") as temporary:
            root = Path(temporary)
            result = run_python(
                INIT_SCRIPT,
                "--root",
                root,
                "--directory",
                "..",
                check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("inside --root", result.stderr)

    def test_decisions_and_references_append_with_stable_ids(self) -> None:
        with temporary_workspace("gdo-append-") as temporary:
            root = Path(temporary)
            initialize(root)
            first = create(
                root,
                "decision",
                "Use one shared hub",
                "--context",
                "Players were getting lost.",
                "--choice",
                "Keep one hub.",
                "--reason",
                "It is easier to learn.",
                "--option",
                "One shared hub",
                "--option",
                "A hub per song family",
                "--impact",
                "World navigation and onboarding.",
                "--revisit",
                "Players cannot find recently visited songs.",
            )
            second = create(root, "decision", "Show song previews")
            reference = create(
                root,
                "reference",
                "Rhythm game onboarding",
                "--source",
                "https://example.com/design-note",
                "--claim",
                "Previewing reduces blind commitment.",
                "--context",
                "Song selection flow.",
                "--transfer-limit",
                "The source studied a different audience.",
                "--note",
                "Preview before commitment.",
            )
            self.assertEqual("DEC-001", first["id"])
            self.assertEqual("DEC-002", second["id"])
            self.assertEqual("REF-001", reference["id"])
            decisions = (
                root / "game-design" / "decisions.md"
            ).read_text(encoding="utf-8")
            self.assertEqual(1, decisions.count("type=decision id=DEC-001"))
            self.assertEqual(1, decisions.count("type=decision id=DEC-002"))
            self.assertIn("Players were getting lost.", decisions)
            self.assertIn("A hub per song family", decisions)
            self.assertIn("World navigation and onboarding.", decisions)
            self.assertIn("Players cannot find recently visited songs.", decisions)
            references = (
                root / "game-design" / "references.md"
            ).read_text(encoding="utf-8")
            self.assertIn("https://example.com/design-note", references)
            self.assertIn("Previewing reduces blind commitment.", references)
            self.assertIn("The source studied a different audience.", references)
            self.assertEqual(1, references.count("type=reference id=REF-001"))

    def test_record_help_exposes_context_not_process_bureaucracy(self) -> None:
        decision_help = run_python(CREATE_SCRIPT, "decision", "--help").stdout
        for flag in ("--option", "--impact", "--revisit"):
            self.assertIn(flag, decision_help)
        reference_help = run_python(CREATE_SCRIPT, "reference", "--help").stdout
        for flag in ("--claim", "--context", "--transfer-limit"):
            self.assertIn(flag, reference_help)

    def test_file_artifacts_are_created_lazily(self) -> None:
        with temporary_workspace("gdo-artifacts-") as temporary:
            root = Path(temporary)
            initialize(root)
            test_result = create(root, "test", "First room comprehension")
            system_result = create(root, "system", "Song unlocks")
            review_result = create(
                root,
                "review",
                "Challenge the unlock choice",
                "--lens",
                "new player",
                "--artifact",
                "systems/" + Path(system_result["path"]).name,
            )
            self.assertEqual("TST-001", test_result["id"])
            self.assertEqual("SYS-001", system_result["id"])
            self.assertEqual("REV-001", review_result["id"])
            self.assertTrue(Path(test_result["path"]).is_file())
            self.assertTrue(Path(system_result["path"]).is_file())
            review_text = Path(review_result["path"]).read_text(encoding="utf-8")
            self.assertIn("new player", review_text)
            self.assertIn("../systems/", review_text)

    def test_unicode_names_are_preserved_in_slugs(self) -> None:
        with temporary_workspace("gdo-unicode-") as temporary:
            root = Path(temporary)
            initialize(root, "\ud55c\uae00 \uac8c\uc784")
            result = create(root, "test", "\ucd94\uc5b5\uc758 \uc120\uc728 \ud14c\uc2a4\ud2b8")
            path = Path(result["path"])
            self.assertIn("\ucd94\uc5b5\uc758-\uc120\uc728-\ud14c\uc2a4\ud2b8", path.name)
            self.assertIn("\ucd94\uc5b5\uc758 \uc120\uc728 \ud14c\uc2a4\ud2b8", path.read_text(encoding="utf-8"))

    def test_system_dependencies_are_rendered_and_checked(self) -> None:
        with temporary_workspace("gdo-dependencies-") as temporary:
            root = Path(temporary)
            initialize(root)
            first = create(root, "system", "Song catalog")
            second = create(
                root,
                "system",
                "Unlock shop",
                "--dependency",
                first["id"],
            )
            self.assertIn(
                "SYS-001",
                Path(second["path"]).read_text(encoding="utf-8"),
            )
            _result, report = check_workspace(root)
            self.assertEqual(0, report["summary"]["errors"])

            invalid = run_python(
                CREATE_SCRIPT,
                "--root",
                root,
                "system",
                "--name",
                "Bad dependency",
                "--dependency",
                "../outside",
                check=False,
            )
            self.assertNotEqual(0, invalid.returncode)
            self.assertIn("expected an ID", invalid.stderr)

    def test_review_requires_existing_contained_artifacts(self) -> None:
        with temporary_workspace("gdo-review-path-") as temporary:
            root = Path(temporary)
            initialize(root)
            for value in ("missing.md", "../outside.md"):
                result = run_python(
                    CREATE_SCRIPT,
                    "--root",
                    root,
                    "review",
                    "--name",
                    "Bad target",
                    "--artifact",
                    value,
                    check=False,
                )
                self.assertNotEqual(0, result.returncode)

    def test_concurrent_creation_uses_unique_monotonic_ids(self) -> None:
        with temporary_workspace("gdo-concurrent-") as temporary:
            root = Path(temporary)
            initialize(root)

            def create_one(number: int) -> dict[str, Any]:
                return create(root, "test", f"Concurrent test {number}")

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(create_one, range(12)))
            ids = sorted(result["id"] for result in results)
            self.assertEqual(
                [f"TST-{number:03d}" for number in range(1, 13)],
                ids,
            )
            state = json.loads(
                (root / "game-design" / ".gdo" / "state.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(13, state["next_ids"]["test"])

    def test_concurrent_decision_appends_do_not_lose_records(self) -> None:
        with temporary_workspace("gdo-concurrent-decisions-") as temporary:
            root = Path(temporary)
            initialize(root)

            def create_one(number: int) -> dict[str, Any]:
                return create(root, "decision", f"Decision {number}")

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                results = list(executor.map(create_one, range(8)))
            ids = {result["id"] for result in results}
            self.assertEqual({f"DEC-{number:03d}" for number in range(1, 9)}, ids)
            text = (root / "game-design" / "decisions.md").read_text(
                encoding="utf-8"
            )
            for artifact_id in ids:
                self.assertEqual(1, text.count(f"type=decision id={artifact_id}"))

    def test_check_is_a_doctor_not_a_rating(self) -> None:
        with temporary_workspace("gdo-audit-clean-") as temporary:
            root = Path(temporary)
            initialize(root)
            create(root, "test", "Navigation question")
            result, report = check_workspace(root)
            self.assertEqual(0, result.returncode)
            self.assertEqual(0, report["summary"]["errors"])
            self.assertNotIn("passed", report)
            self.assertNotIn("gate", report)
            self.assertNotIn("score", report)
            self.assertEqual([], report["errors"])

    def test_check_finds_duplicate_ids(self) -> None:
        with temporary_workspace("gdo-duplicate-") as temporary:
            root = Path(temporary)
            initialize(root)
            created = create(root, "test", "Original")
            design = root / "game-design" / "design.md"
            design.write_text(
                design.read_text(encoding="utf-8")
                + "\n<!-- gdo:artifact type=test id="
                + created["id"]
                + " -->\n",
                encoding="utf-8",
            )
            result, report = check_workspace(root, check=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIn(
                "duplicate-artifact-id",
                issue_codes(report, "errors"),
            )

    def test_check_finds_broken_id_references(self) -> None:
        with temporary_workspace("gdo-broken-id-") as temporary:
            root = Path(temporary)
            initialize(root)
            design = root / "game-design" / "design.md"
            design.write_text(
                design.read_text(encoding="utf-8") + "\nDepends on SYS-404.\n",
                encoding="utf-8",
            )
            _result, report = check_workspace(root, check=False)
            self.assertIn(
                "broken-artifact-reference",
                issue_codes(report, "errors"),
            )

    def test_check_finds_broken_local_links(self) -> None:
        with temporary_workspace("gdo-broken-link-") as temporary:
            root = Path(temporary)
            initialize(root)
            design = root / "game-design" / "design.md"
            design.write_text(
                design.read_text(encoding="utf-8")
                + "\n[Missing system](systems/SYS-999-missing.md)\n",
                encoding="utf-8",
            )
            _result, report = check_workspace(root, check=False)
            codes = issue_codes(report, "errors")
            self.assertIn("broken-local-link", codes)
            self.assertIn("broken-artifact-reference", codes)

    def test_check_reports_tokens_and_placeholders_separately(self) -> None:
        with temporary_workspace("gdo-placeholders-") as temporary:
            root = Path(temporary)
            initialize(root)
            design = root / "game-design" / "design.md"
            design.write_text(
                design.read_text(encoding="utf-8")
                + "\n{{UNRESOLVED}}\nTODO: make this concrete.\n",
                encoding="utf-8",
            )
            _result, report = check_workspace(root, check=False)
            self.assertIn(
                "unresolved-template-token",
                issue_codes(report, "errors"),
            )
            self.assertIn(
                "unfinished-placeholder",
                issue_codes(report, "warnings"),
            )

    def test_check_reports_invalid_state(self) -> None:
        with temporary_workspace("gdo-invalid-state-") as temporary:
            root = Path(temporary)
            initialize(root)
            state = root / "game-design" / ".gdo" / "state.json"
            state.write_text('{"schema_version": 999}\n', encoding="utf-8")
            _result, report = check_workspace(root, check=False)
            self.assertIn("invalid-state", issue_codes(report, "errors"))

    def test_default_generated_files_do_not_contain_legacy_research_terms(self) -> None:
        with temporary_workspace("gdo-no-legacy-") as temporary:
            root = Path(temporary)
            initialize(root)
            artifacts = [
                Path(create(root, "test", "Question card")["path"]),
                Path(create(root, "system", "Movement")["path"]),
                Path(create(root, "review", "Challenge movement")["path"]),
            ]
            files = [
                root / "game-design" / "design.md",
                root / "game-design" / ".gdo" / "state.json",
                *artifacts,
            ]
            banned = (
                "enabled_gates",
                "preregistration",
                "evidence contract",
                "hypothesis register",
                "scoring rubric",
                "proceed / pivot / kill",
            )
            combined = "\n".join(
                path.read_text(encoding="utf-8").casefold() for path in files
            )
            for term in banned:
                self.assertNotIn(term, combined)

    def test_state_counter_cannot_reuse_an_existing_high_id(self) -> None:
        with temporary_workspace("gdo-high-id-") as temporary:
            root = Path(temporary)
            initialize(root)
            tests = root / "game-design" / "tests"
            tests.mkdir()
            (tests / "TST-007-manual.md").write_text(
                "<!-- gdo:artifact type=test id=TST-007 -->\n",
                encoding="utf-8",
            )
            result = create(root, "test", "After manual import")
            self.assertEqual("TST-008", result["id"])


if __name__ == "__main__":
    unittest.main()
