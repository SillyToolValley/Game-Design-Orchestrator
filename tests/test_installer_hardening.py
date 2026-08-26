from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "orchestrate-game-design"
INSTALL_SCRIPT = REPOSITORY_ROOT / "install.py"


def load_installer(name: str) -> Any:
    specification = importlib.util.spec_from_file_location(name, INSTALL_SCRIPT)
    if specification is None or specification.loader is None:
        raise AssertionError("Could not load install.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def copy_bundle(module: Any, temporary: str) -> Path:
    copied = Path(temporary) / "orchestrate-game-design"
    shutil.copytree(SKILL_ROOT, copied)
    manifest_path = copied / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    manifest["files"] = {
        path.relative_to(copied).as_posix(): module.file_sha256(path)
        for path in sorted(copied.rglob("*"))
        if path.is_file()
        and path != manifest_path
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return copied


class InstallerHardeningTests(unittest.TestCase):
    def test_manifest_contract_rejects_corrupt_manifest_json(self) -> None:
        module = load_installer("gdo_installer_bad_manifest_json")
        with tempfile.TemporaryDirectory(prefix="gdo-manifest-json-") as temporary:
            copied = copy_bundle(module, temporary)
            (copied / "bundle-manifest.json").write_text("{", encoding="utf-8")
            with mock.patch.object(module.subprocess, "run") as checker:
                with self.assertRaisesRegex(
                    module.InstallError, "Invalid bundle manifest"
                ):
                    module.check_bundle(copied)
                checker.assert_not_called()

    def test_manifest_contract_rejects_boolean_schema_and_nonfinite_json(self) -> None:
        module = load_installer("gdo_installer_strict_manifest_json")
        cases = (
            ("boolean-schema", lambda payload: payload.update(schema_version=True)),
            ("nonfinite-number", lambda payload: payload.update(extra=float("nan"))),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory(
                    prefix="gdo-manifest-strict-"
                ) as temporary:
                    copied = copy_bundle(module, temporary)
                    manifest_path = copied / "bundle-manifest.json"
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8-sig")
                    )
                    mutate(manifest)
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    with mock.patch.object(module.subprocess, "run") as checker:
                        with self.assertRaisesRegex(
                            module.InstallError, "Invalid bundle manifest"
                        ):
                            module.check_bundle(copied)
                        checker.assert_not_called()

    def test_manifest_contract_rejects_changed_listed_file(self) -> None:
        module = load_installer("gdo_installer_changed_bundle_file")
        with tempfile.TemporaryDirectory(prefix="gdo-manifest-changed-") as temporary:
            copied = copy_bundle(module, temporary)
            skill = copied / "SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8") + "\ncorruption\n",
                encoding="utf-8",
            )
            with mock.patch.object(module.subprocess, "run") as checker:
                with self.assertRaisesRegex(module.InstallError, "hash mismatch"):
                    module.check_bundle(copied)
                checker.assert_not_called()

    def test_manifest_contract_rejects_added_file(self) -> None:
        module = load_installer("gdo_installer_added_bundle_file")
        with tempfile.TemporaryDirectory(prefix="gdo-manifest-added-") as temporary:
            copied = copy_bundle(module, temporary)
            (copied / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
            with mock.patch.object(module.subprocess, "run") as checker:
                with self.assertRaisesRegex(module.InstallError, "file set mismatch"):
                    module.check_bundle(copied)
                checker.assert_not_called()

    def test_manifest_contract_rejects_deleted_file(self) -> None:
        module = load_installer("gdo_installer_deleted_bundle_file")
        with tempfile.TemporaryDirectory(prefix="gdo-manifest-deleted-") as temporary:
            copied = copy_bundle(module, temporary)
            manifest = json.loads(
                (copied / "bundle-manifest.json").read_text(encoding="utf-8-sig")
            )
            relative = next(iter(manifest["files"]))
            (copied / Path(relative)).unlink()
            with mock.patch.object(module.subprocess, "run") as checker:
                with self.assertRaisesRegex(module.InstallError, "file set mismatch"):
                    module.check_bundle(copied)
                checker.assert_not_called()

    def test_checker_must_report_exact_entrypoint_set_and_version(self) -> None:
        module = load_installer("gdo_installer_exact_entrypoints")
        with tempfile.TemporaryDirectory(prefix="gdo-entrypoints-") as temporary:
            copied = copy_bundle(module, temporary)
            version, contract_id, skill_hash = module.manifest_contract(copied)
            base_payload = {
                "issues": [],
                "legacy_remnants": [],
                "missing_files": [],
                "unexpected_files": [],
                "changed_files": [],
                "version": version,
                "contract_id": contract_id,
                "manifest_version": version,
                "skill_sha256": skill_hash,
                "skill_root": str(copied.resolve()),
                "manifest_path": str((copied / "bundle-manifest.json").resolve()),
                "entrypoint_versions": {
                    name: version for name in module.REQUIRED_ENTRYPOINTS
                },
            }

            valid = subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(base_payload), stderr=""
            )
            with mock.patch.object(module.subprocess, "run", return_value=valid):
                self.assertEqual(version, module.check_bundle(copied)["version"])

            cases: dict[str, dict[str, str]] = {}
            missing = dict(base_payload["entrypoint_versions"])
            missing.pop("snapshot_design_workspace.py")
            cases["missing"] = missing
            extra = dict(base_payload["entrypoint_versions"])
            extra["retired_entrypoint.py"] = version
            cases["extra"] = extra
            wrong_version = dict(base_payload["entrypoint_versions"])
            wrong_version["snapshot_design_workspace.py"] = "wrong-version"
            cases["wrong-version"] = wrong_version

            for label, entrypoints in cases.items():
                with self.subTest(label=label):
                    checker_payload = {
                        **base_payload,
                        "entrypoint_versions": entrypoints,
                    }
                    result = subprocess.CompletedProcess(
                        [], 0, stdout=json.dumps(checker_payload), stderr=""
                    )
                    with mock.patch.object(
                        module.subprocess, "run", return_value=result
                    ):
                        with self.assertRaisesRegex(
                            module.InstallError, "incomplete or inconsistent"
                        ):
                            module.check_bundle(copied)

    @unittest.skipUnless(os.name == "nt", "Windows namespace alias regression")
    def test_windows_namespace_aliases_overlap_ordinary_paths(self) -> None:
        module = load_installer("gdo_installer_windows_aliases")
        ordinary = SKILL_ROOT.resolve()
        extended = Path("\\\\?\\" + str(ordinary))
        device = Path("\\\\.\\" + str(ordinary))

        for alias in (extended, device):
            with self.subTest(alias=str(alias)):
                self.assertTrue(module.is_same_or_within(alias, ordinary))
                self.assertTrue(module.is_same_or_within(ordinary, alias))
                with self.assertRaisesRegex(module.InstallError, "overlap"):
                    module.validate_layout(
                        ordinary,
                        ordinary.parent / "safe-skills",
                        ordinary.parent / "safe-backups",
                        alias / "installed",
                    )

    @unittest.skipUnless(os.name == "nt", "Windows device path regression")
    def test_unsupported_windows_device_namespaces_are_rejected(self) -> None:
        module = load_installer("gdo_installer_windows_devices")
        paths = (
            Path(r"\\?\GLOBALROOT\Device\HarddiskVolume1\source"),
            Path(r"\\?\Volume{00000000-0000-0000-0000-000000000000}\source"),
            Path(r"\\.\PhysicalDrive0"),
            Path(r"\\.\pipe\gdo-test"),
            Path(r"\??\GLOBALROOT\Device\HarddiskVolume1\source"),
        )
        for path in paths:
            with self.subTest(path=str(path)):
                with self.assertRaisesRegex(
                    module.InstallError, "unsupported Windows device namespace"
                ):
                    module.canonical_path(path)

    def test_json_mode_returns_structured_failure(self) -> None:
        module = load_installer("gdo_installer_json_failure")
        standard_output = io.StringIO()
        standard_error = io.StringIO()
        with (
            mock.patch.object(module.sys, "argv", ["install.py", "--json"]),
            mock.patch.object(
                module,
                "install",
                side_effect=module.InstallError("simulated failure"),
            ),
            redirect_stdout(standard_output),
            redirect_stderr(standard_error),
        ):
            return_code = module.main()

        self.assertEqual(1, return_code)
        self.assertEqual(
            {"installed": False, "error": "simulated failure"},
            json.loads(standard_output.getvalue()),
        )
        self.assertEqual("", standard_error.getvalue())

    def test_symlink_loop_resolution_is_reported_as_an_install_error(self) -> None:
        module = load_installer("gdo_installer_symlink_loop")
        with mock.patch.object(
            module.Path,
            "resolve",
            side_effect=RuntimeError("Symlink loop"),
        ):
            with self.assertRaisesRegex(module.InstallError, "Could not resolve path safely"):
                module.canonical_path(Path("loop"))

    def test_plain_mode_preserves_system_exit_error(self) -> None:
        module = load_installer("gdo_installer_plain_failure")
        with (
            mock.patch.object(module.sys, "argv", ["install.py"]),
            mock.patch.object(
                module,
                "install",
                side_effect=module.InstallError("simulated failure"),
            ),
        ):
            with self.assertRaisesRegex(SystemExit, "simulated failure"):
                module.main()

    def test_dry_run_does_not_clean_stale_staging(self) -> None:
        module = load_installer("gdo_installer_dry_run_stale")
        with tempfile.TemporaryDirectory(prefix="gdo-dry-run-stale-") as temporary:
            codex_home = Path(temporary) / "codex-home"
            stale = codex_home / "skills" / "gdo-install-old"
            stale.mkdir(parents=True)
            (stale / "partial.txt").write_text("partial\n", encoding="utf-8")
            source_report = {
                "version": "test-version",
                "contract_id": "test-contract",
                "skill_sha256": "0" * 64,
            }

            with mock.patch.object(
                module, "check_bundle", return_value=source_report
            ):
                report = module.install(codex_home, dry_run=True)

            self.assertTrue(report["dry_run"])
            self.assertFalse(report["installed"])
            self.assertTrue(stale.is_dir())

    def test_stale_staging_cleanup_removes_links_without_following_them(self) -> None:
        module = load_installer("gdo_installer_stale_cleanup")
        with tempfile.TemporaryDirectory(prefix="gdo-stale-cleanup-") as temporary:
            root = Path(temporary)
            skills = root / "skills"
            skills.mkdir()
            stale_directory = skills / "gdo-install-old"
            stale_directory.mkdir()
            (stale_directory / "partial.txt").write_text("partial\n", encoding="utf-8")
            unrelated = skills / "not-gdo-install-old"
            unrelated.mkdir()

            external = root / "external"
            external.mkdir()
            sentinel = external / "keep.txt"
            sentinel.write_text("keep\n", encoding="utf-8")
            link = skills / "gdo-install-linked"
            if os.name == "nt":
                junction = subprocess.run(
                    ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(external)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if junction.returncode != 0:
                    self.skipTest(f"directory links unavailable: {junction.stderr}")
            else:
                link.symlink_to(external, target_is_directory=True)

            module.cleanup_stale_staging(skills)

            self.assertFalse(stale_directory.exists())
            self.assertFalse(link.exists())
            self.assertFalse(link.is_symlink())
            self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))
            self.assertTrue(unrelated.is_dir())


if __name__ == "__main__":
    unittest.main()
