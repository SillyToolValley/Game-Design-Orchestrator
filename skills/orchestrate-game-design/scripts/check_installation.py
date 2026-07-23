#!/usr/bin/env python3
"""Verify that this skill directory is one coherent GDO installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    from .workspace import CONTRACT_ID, VERSION
except ImportError:
    from workspace import CONTRACT_ID, VERSION


ENTRYPOINTS = (
    "init_design_project.py",
    "create_design_artifact.py",
    "check_design_workspace.py",
)
LEGACY_REMNANTS = (
    "scripts/audit_design_project.py",
    "scripts/design_config.py",
    "references/artifact-contracts.md",
    "references/configuration.md",
    "references/gates.md",
    "references/review-protocols.md",
    "assets/templates/vision.md.tmpl",
    "assets/templates/hypotheses.md.tmpl",
    "examples",
)
MANIFEST_NAME = "bundle-manifest.json"
MANIFEST_SCHEMA_VERSION = 1
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the resolved skill path, version, and bundle lineage."
    )
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--expect-version",
        help="Fail unless the installed version exactly matches this value.",
    )
    args = parser.parse_args()

    skill_root = Path(__file__).resolve().parents[1]
    issues: list[str] = []
    manifest_path = skill_root / MANIFEST_NAME
    manifest_payload: dict[str, object] = {}
    manifest_files: dict[str, str] = {}
    try:
        loaded_manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        issues.append(f"missing bundle manifest: {MANIFEST_NAME}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        issues.append(f"invalid bundle manifest: {exc}")
    else:
        if not isinstance(loaded_manifest, dict):
            issues.append("bundle manifest must contain one JSON object")
        else:
            manifest_payload = loaded_manifest
            if manifest_payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
                issues.append("bundle manifest schema mismatch")
            if manifest_payload.get("version") != VERSION:
                issues.append("bundle manifest version mismatch")
            if manifest_payload.get("contract_id") != CONTRACT_ID:
                issues.append("bundle manifest contract mismatch")
            candidate_files = manifest_payload.get("files")
            if not (
                isinstance(candidate_files, dict)
                and bool(candidate_files)
                and all(
                    isinstance(relative, str)
                    and isinstance(digest, str)
                    and HASH_PATTERN.fullmatch(digest)
                    for relative, digest in candidate_files.items()
                )
            ):
                issues.append("bundle manifest files map is invalid")
            else:
                manifest_files = candidate_files

    actual_files: dict[str, str] = {}
    for path in sorted(skill_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(skill_root).as_posix()
        if (
            relative == MANIFEST_NAME
            or "__pycache__" in path.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        actual_files[relative] = file_sha256(path)

    expected_paths = set(manifest_files)
    actual_paths = set(actual_files)
    missing_files = sorted(expected_paths - actual_paths)
    unexpected_files = sorted(actual_paths - expected_paths)
    changed_files = sorted(
        relative
        for relative in expected_paths & actual_paths
        if manifest_files[relative] != actual_files[relative]
    )
    if manifest_files:
        if missing_files:
            issues.append("bundle files missing: " + ", ".join(missing_files))
        if unexpected_files:
            issues.append("unexpected bundle files: " + ", ".join(unexpected_files))
        if changed_files:
            issues.append("bundle files changed: " + ", ".join(changed_files))

    if args.expect_version and args.expect_version != VERSION:
        issues.append(
            f"expected version {args.expect_version!r}, bundled version is {VERSION!r}"
        )

    entrypoint_versions: dict[str, str] = {}
    for name in ENTRYPOINTS:
        script = skill_root / "scripts" / name
        if not script.is_file():
            issues.append(f"missing entrypoint: scripts/{name}")
            continue
        result = subprocess.run(
            [sys.executable, str(script), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        reported = result.stdout.strip()
        entrypoint_versions[name] = reported
        if result.returncode != 0:
            issues.append(f"scripts/{name} --version failed")
        elif reported != VERSION:
            issues.append(
                f"scripts/{name} reports {reported!r}, expected {VERSION!r}"
            )

    remnants = [
        relative
        for relative in LEGACY_REMNANTS
        if (skill_root / Path(relative)).exists()
    ]
    if remnants:
        issues.append("legacy files remain: " + ", ".join(remnants))

    skill_file = skill_root / "SKILL.md"
    if not skill_file.is_file():
        issues.append("missing SKILL.md")
        skill_hash = None
    else:
        skill_hash = file_sha256(skill_file)

    payload = {
        "name": "Game Design Orchestrator",
        "version": VERSION,
        "contract_id": CONTRACT_ID,
        "skill_root": str(skill_root),
        "skill_sha256": skill_hash,
        "entrypoint_versions": entrypoint_versions,
        "legacy_remnants": remnants,
        "manifest_path": str(manifest_path),
        "manifest_version": manifest_payload.get("version"),
        "missing_files": missing_files,
        "unexpected_files": unexpected_files,
        "changed_files": changed_files,
        "issues": issues,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        print(f"Game Design Orchestrator {VERSION}")
        print(f"Skill path: {skill_root}")
        print(f"Contract: {CONTRACT_ID}")
        print(f"SKILL.md SHA-256: {skill_hash or 'missing'}")
        if issues:
            for issue in issues:
                print(f"[ERROR] {issue}")
        else:
            print("Installation check: OK")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
