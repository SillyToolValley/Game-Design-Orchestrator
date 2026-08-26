from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "orchestrate-game-design"
INSTALL_SCRIPT = REPOSITORY_ROOT / "install.py"
EVAL_CASES = REPOSITORY_ROOT / "evals" / "cases.json"
VERSION = "0.1.2-beta"
CONTRACT_ID = "human-led-deliverable-first-v1"


def run_python(
    script: Path,
    *arguments: object,
    check: bool = True,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-B", str(script), *(str(value) for value in arguments)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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


def load_installer(name: str = "gdo_installer_under_test") -> Any:
    specification = importlib.util.spec_from_file_location(name, INSTALL_SCRIPT)
    if specification is None or specification.loader is None:
        raise AssertionError("Could not load install.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class RepositoryIntegrationTests(unittest.TestCase):
    def test_forward_eval_cases_have_unique_observable_contracts(self) -> None:
        payload_value = json.loads(EVAL_CASES.read_text(encoding="utf-8"))
        self.assertEqual(1, payload_value["schema_version"])
        cases = payload_value["cases"]
        self.assertGreaterEqual(len(cases), 5)
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        for case in cases:
            self.assertTrue(case["title"].strip())
            self.assertTrue(case["setup"].strip())
            self.assertTrue(case["prompt"].strip())
            self.assertGreaterEqual(len(case["mechanical_checks"]), 2)
            self.assertGreaterEqual(len(case["human_review"]), 2)
            self.assertTrue(all(check.strip() for check in case["mechanical_checks"]))
            self.assertTrue(all(check.strip() for check in case["human_review"]))

    def test_checker_cannot_pass_with_an_empty_json_object(self) -> None:
        module = load_installer("gdo_installer_empty_checker")
        fake = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        with mock.patch.object(module.subprocess, "run", return_value=fake):
            with self.assertRaisesRegex(module.InstallError, "incomplete or inconsistent"):
                module.check_bundle(SKILL_ROOT)

    def test_layout_rejects_both_overlap_directions_and_nested_work_roots(self) -> None:
        module = load_installer("gdo_installer_layout")
        with tempfile.TemporaryDirectory(prefix="gdo-layout-") as temporary:
            root = Path(temporary).resolve()
            source = root / "source"
            safe_skills = root / "home" / "skills"
            safe_backups = root / "home" / "skill-backups"
            with self.assertRaisesRegex(module.InstallError, "overlap"):
                module.validate_layout(
                    source, safe_skills, safe_backups, source / "destination"
                )
            with self.assertRaisesRegex(module.InstallError, "overlap"):
                module.validate_layout(
                    source / "nested", safe_skills, safe_backups, source
                )
            with self.assertRaisesRegex(module.InstallError, "inside the source"):
                module.validate_layout(
                    source,
                    source / "nested-skills",
                    safe_backups,
                    root / "elsewhere",
                )

        nested_home = SKILL_ROOT / "would-be-codex-home-test"
        result = run_python(
            INSTALL_SCRIPT,
            "--codex-home",
            nested_home,
            "--dry-run",
            "--json",
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(nested_home.exists())

    def test_installer_validates_replaces_and_backs_up(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-installer-") as temporary:
            codex_home = Path(temporary) / "codex-home"
            first = payload(
                run_python(INSTALL_SCRIPT, "--codex-home", codex_home, "--json")
            )
            destination = Path(first["destination"])
            self.assertTrue(first["installed"])
            self.assertIsNone(first["backup"])
            self.assertTrue((destination / "SKILL.md").is_file())
            self.assertEqual(
                (REPOSITORY_ROOT / "LICENSE").read_bytes(),
                (destination / "LICENSE").read_bytes(),
            )
            self.assertFalse(
                any(path.name == "__pycache__" for path in destination.rglob("*"))
            )
            run_python(
                destination / "scripts" / "check_installation.py",
                "--expect-version",
                VERSION,
            )

            stale = destination / "scripts" / "stale.py"
            stale.write_text("# retired\n", encoding="utf-8")
            second = payload(
                run_python(INSTALL_SCRIPT, "--codex-home", codex_home, "--json")
            )
            backup = Path(second["backup"])
            self.assertTrue(backup.is_dir())
            self.assertTrue((backup / "scripts" / "stale.py").is_file())
            self.assertFalse((destination / "scripts" / "stale.py").exists())
            run_python(
                destination / "scripts" / "check_installation.py",
                "--expect-version",
                VERSION,
            )

    def test_installer_dry_run_does_not_create_codex_home(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-installer-dry-") as temporary:
            codex_home = Path(temporary) / "not-created"
            report = payload(
                run_python(
                    INSTALL_SCRIPT,
                    "--codex-home",
                    codex_home,
                    "--dry-run",
                    "--json",
                )
            )
            self.assertTrue(report["dry_run"])
            self.assertFalse(report["installed"])
            self.assertFalse(codex_home.exists())

    def test_concurrent_installers_leave_a_valid_destination(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-installer-concurrent-") as temporary:
            codex_home = Path(temporary) / "codex-home"

            def install_once(_number: int) -> dict[str, Any]:
                return payload(
                    run_python(INSTALL_SCRIPT, "--codex-home", codex_home, "--json")
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                reports = list(executor.map(install_once, range(2)))

            self.assertTrue(all(report["installed"] for report in reports))
            destination = codex_home / "skills" / "orchestrate-game-design"
            self.assertTrue(destination.is_dir())
            run_python(
                destination / "scripts" / "check_installation.py",
                "--expect-version",
                VERSION,
            )
            self.assertEqual(
                1,
                len(list((codex_home / "skill-backups").glob("orchestrate-game-design-*"))),
            )

    def test_installer_restores_previous_skill_after_failed_final_check(self) -> None:
        module = load_installer("gdo_installer_rollback")
        with tempfile.TemporaryDirectory(prefix="gdo-installer-rollback-") as temporary:
            codex_home = Path(temporary) / "codex-home"
            destination = codex_home / "skills" / "orchestrate-game-design"
            destination.mkdir(parents=True)
            sentinel = destination / "previous-installation.txt"
            sentinel.write_text("keep me\n", encoding="utf-8")
            checks = 0

            def fail_final_check(_skill_root: Path) -> dict[str, Any]:
                nonlocal checks
                checks += 1
                if checks == 3:
                    raise module.InstallError("simulated final validation failure")
                return {
                    "version": VERSION,
                    "contract_id": CONTRACT_ID,
                    "skill_sha256": "0" * 64,
                }

            with mock.patch.object(module, "check_bundle", side_effect=fail_final_check):
                with self.assertRaisesRegex(
                    module.InstallError, "simulated final validation failure"
                ):
                    module.install(codex_home, dry_run=False)

            self.assertEqual(3, checks)
            self.assertEqual("keep me\n", sentinel.read_text(encoding="utf-8"))
            self.assertEqual([], list((codex_home / "skills").glob("gdo-install-*")))


if __name__ == "__main__":
    unittest.main()
