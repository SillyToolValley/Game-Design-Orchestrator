#!/usr/bin/env python3
"""Install the bundled GDO skill with validation, backup, and rollback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SKILL_NAME = "orchestrate-game-design"
STAGING_PREFIX = "gdo-install-"
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ENTRYPOINTS = frozenset(
    {
        "check_design_workspace.py",
        "create_design_artifact.py",
        "init_design_project.py",
        "snapshot_design_workspace.py",
    }
)
EMPTY_LIST_FIELDS = (
    "issues",
    "legacy_remnants",
    "missing_files",
    "unexpected_files",
    "changed_files",
)


class InstallError(RuntimeError):
    """Raised when a coherent skill installation cannot be completed."""


def reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_contract(skill_root: Path) -> tuple[str, str, str]:
    """Independently verify the manifest and return its public contract."""

    manifest_path = skill_root / "bundle-manifest.json"
    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8-sig"),
            parse_constant=reject_nonfinite_json_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise InstallError(f"Invalid bundle manifest at {manifest_path}: {exc}") from exc
    schema_version = manifest.get("schema_version") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        raise InstallError(f"Invalid bundle manifest schema at {manifest_path}")
    version = manifest.get("version")
    contract_id = manifest.get("contract_id")
    files = manifest.get("files")
    if not isinstance(version, str) or not version:
        raise InstallError(f"Bundle manifest has no valid version: {manifest_path}")
    if not isinstance(contract_id, str) or not contract_id:
        raise InstallError(f"Bundle manifest has no valid contract_id: {manifest_path}")
    if not (
        isinstance(files, dict)
        and bool(files)
        and all(
            isinstance(relative, str)
            and relative
            and isinstance(digest, str)
            and HASH_PATTERN.fullmatch(digest)
            for relative, digest in files.items()
        )
    ):
        raise InstallError(f"Bundle manifest has an invalid files map: {manifest_path}")

    actual: dict[str, str] = {}
    for path in sorted(skill_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(skill_root).as_posix()
        if (
            relative == "bundle-manifest.json"
            or "__pycache__" in path.parts
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        actual[relative] = file_sha256(path)
    if set(actual) != set(files):
        missing = sorted(set(files) - set(actual))
        unexpected = sorted(set(actual) - set(files))
        raise InstallError(
            f"Bundle manifest file set mismatch at {skill_root}; "
            f"missing={missing}, unexpected={unexpected}"
        )
    changed = sorted(relative for relative, digest in files.items() if actual[relative] != digest)
    if changed:
        raise InstallError(f"Bundle manifest hash mismatch at {skill_root}: {changed}")
    skill_hash = actual.get("SKILL.md")
    if skill_hash is None:
        raise InstallError(f"Bundle manifest does not include SKILL.md: {manifest_path}")
    return version, contract_id, skill_hash


def check_bundle(skill_root: Path) -> dict[str, Any]:
    expected_version, expected_contract, expected_skill_hash = manifest_contract(skill_root)
    checker = skill_root / "scripts" / "check_installation.py"
    if not checker.is_file():
        raise InstallError(f"Missing installation checker: {checker}")
    result = subprocess.run(
        [sys.executable, "-B", str(checker), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    try:
        payload = json.loads(
            result.stdout, parse_constant=reject_nonfinite_json_constant
        )
    except ValueError as exc:
        raise InstallError(
            f"Installation checker returned invalid JSON for {skill_root}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        ) from exc
    if not isinstance(payload, dict):
        raise InstallError(f"Installation checker returned an invalid result: {skill_root}")
    issues = payload.get("issues")
    if result.returncode != 0:
        details = "; ".join(str(issue) for issue in issues) if isinstance(issues, list) else ""
        raise InstallError(
            f"Skill bundle validation failed for {skill_root}: "
            f"{details or result.stderr.strip() or 'unknown checker failure'}"
        )
    schema_errors: list[str] = []
    for field in EMPTY_LIST_FIELDS:
        if payload.get(field) != []:
            schema_errors.append(f"{field} must be an empty list")
    if payload.get("version") != expected_version:
        schema_errors.append("version does not match the manifest")
    if payload.get("contract_id") != expected_contract:
        schema_errors.append("contract_id does not match the manifest")
    if payload.get("manifest_version") != expected_version:
        schema_errors.append("manifest_version does not match the manifest")
    if payload.get("skill_sha256") != expected_skill_hash:
        schema_errors.append("skill_sha256 does not match SKILL.md")
    reported_root = payload.get("skill_root")
    if (
        not isinstance(reported_root, str)
        or Path(reported_root).resolve(strict=False) != skill_root.resolve(strict=False)
    ):
        schema_errors.append("skill_root does not match the checked path")
    reported_manifest = payload.get("manifest_path")
    if (
        not isinstance(reported_manifest, str)
        or Path(reported_manifest).resolve(strict=False)
        != (skill_root / "bundle-manifest.json").resolve(strict=False)
    ):
        schema_errors.append("manifest_path does not match the checked bundle")
    entrypoints = payload.get("entrypoint_versions")
    if not isinstance(entrypoints, dict) or set(entrypoints) != REQUIRED_ENTRYPOINTS:
        schema_errors.append("entrypoint_versions do not contain the exact required entrypoints")
    elif any(entrypoints[name] != expected_version for name in REQUIRED_ENTRYPOINTS):
        schema_errors.append("entrypoint_versions are inconsistent with the manifest")
    if schema_errors:
        raise InstallError(
            f"Installation checker returned an incomplete or inconsistent result for "
            f"{skill_root}: {'; '.join(schema_errors)}"
        )
    return payload


def _without_windows_namespace(path: Path) -> Path:
    """Map Win32 namespace aliases to their ordinary filesystem spelling."""

    if os.name != "nt":
        return path
    raw = os.fspath(path).replace("/", "\\")
    folded = raw.casefold()
    for prefix in ("\\\\?\\unc\\", "\\\\.\\unc\\"):
        if folded.startswith(prefix):
            remainder = raw[len(prefix) :]
            components = remainder.split("\\")
            if (
                len(components) >= 2
                and components[0] not in {"", ".", ".."}
                and components[1] not in {"", ".", ".."}
            ):
                return Path("\\\\" + remainder)
            raise InstallError(
                f"Refusing unsupported Windows device namespace path: {path}"
            )
    for prefix in ("\\\\?\\", "\\\\.\\", "\\??\\"):
        if folded.startswith(prefix):
            remainder = raw[len(prefix) :]
            if re.match(r"^[A-Za-z]:\\", remainder):
                return Path(remainder)
            raise InstallError(
                f"Refusing unsupported Windows device namespace path: {path}"
            )
    return path


def canonical_path(path: Path) -> Path:
    """Resolve a path after collapsing filesystem namespace aliases."""

    try:
        resolved = _without_windows_namespace(path.expanduser()).resolve(strict=False)
    except RuntimeError as exc:
        raise InstallError(f"Could not resolve path safely: {path}: {exc}") from exc
    return _without_windows_namespace(resolved)


def is_same_or_within(path: Path, parent: Path) -> bool:
    try:
        canonical_path(path).relative_to(canonical_path(parent))
    except ValueError:
        return False
    return True


def validate_layout(
    source: Path, skill_root: Path, backup_root: Path, destination: Path
) -> None:
    if is_same_or_within(destination, source) or is_same_or_within(source, destination):
        raise InstallError(
            "Refusing to install because the source and destination overlap."
        )
    for working_root in (skill_root, backup_root):
        if is_same_or_within(working_root, source):
            raise InstallError(
                "Refusing to install because staging or backup paths would be "
                "created inside the source bundle."
            )


@contextmanager
def installation_lock(codex_home: Path, timeout: float = 30.0) -> Iterator[None]:
    """Serialize installers across processes without third-party packages."""

    lock_path = codex_home / ".gdo-install.lock"
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise InstallError(
                        "Timed out waiting for another GDO installation to finish."
                    ) from exc
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def unique_backup(backup_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    candidate = backup_root / f"{SKILL_NAME}-{stamp}"
    counter = 1
    while candidate.exists() or candidate.is_symlink():
        counter += 1
        candidate = backup_root / f"{SKILL_NAME}-{stamp}-{counter}"
    return candidate


def remove_exact(path: Path) -> None:
    status = path.lstat()
    file_attributes = getattr(status, "st_file_attributes", 0)
    is_windows_reparse_point = bool(file_attributes & 0x400)
    if is_windows_reparse_point:
        if file_attributes & 0x10:
            path.rmdir()
        else:
            path.unlink()
    elif stat.S_ISLNK(status.st_mode):
        path.unlink()
    elif path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def cleanup_stale_staging(skill_root: Path) -> None:
    """Remove direct children reserved by prior interrupted installers."""

    for candidate in skill_root.iterdir():
        if not candidate.name.startswith(STAGING_PREFIX) or candidate.name == STAGING_PREFIX:
            continue
        try:
            remove_exact(candidate)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise InstallError(
                f"Could not remove stale installer staging entry {candidate}: {exc}"
            ) from exc


def install(codex_home: Path, *, dry_run: bool) -> dict[str, Any]:
    repository_root = canonical_path(Path(__file__).parent)
    source = canonical_path(repository_root / "skills" / SKILL_NAME)
    source_report = check_bundle(source)

    resolved_home = canonical_path(codex_home)
    skill_root = resolved_home / "skills"
    backup_root = resolved_home / "skill-backups"
    destination = skill_root / SKILL_NAME
    validate_layout(source, skill_root, backup_root, destination)

    base_result: dict[str, Any] = {
        "installed": False,
        "dry_run": dry_run,
        "version": source_report.get("version"),
        "contract_id": source_report.get("contract_id"),
        "source": str(source),
        "destination": str(destination),
        "backup": None,
    }
    if dry_run:
        return base_result

    resolved_home.mkdir(parents=True, exist_ok=True)
    with installation_lock(resolved_home):
        skill_root.mkdir(parents=True, exist_ok=True)
        backup_root.mkdir(parents=True, exist_ok=True)
        cleanup_stale_staging(skill_root)
        staging_container = Path(
            tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=skill_root)
        )
        staged = staging_container / SKILL_NAME
        backup: Path | None = None
        destination_replaced = False
        try:
            shutil.copytree(
                source,
                staged,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
            check_bundle(staged)

            if destination.exists() or destination.is_symlink():
                backup = unique_backup(backup_root)
                shutil.move(str(destination), str(backup))
            shutil.move(str(staged), str(destination))
            destination_replaced = True
            installed_report = check_bundle(destination)
        except BaseException as install_failure:
            recovery_errors: list[str] = []
            if destination_replaced:
                try:
                    remove_exact(destination)
                except OSError as exc:
                    recovery_errors.append(f"could not remove failed installation: {exc}")
            if backup is not None and (backup.exists() or backup.is_symlink()):
                if destination.exists() or destination.is_symlink():
                    recovery_errors.append(
                        f"previous installation remains preserved at {backup}"
                    )
                else:
                    try:
                        shutil.move(str(backup), str(destination))
                    except (OSError, shutil.Error) as exc:
                        recovery_errors.append(
                            f"could not restore backup {backup}: {exc}"
                        )
            if recovery_errors:
                raise InstallError(
                    f"Installation failed ({install_failure}). Automatic rollback was "
                    "incomplete: " + "; ".join(recovery_errors)
                ) from install_failure
            raise
        finally:
            if staging_container.is_dir():
                shutil.rmtree(staging_container, ignore_errors=True)

    return {
        **base_result,
        "installed": True,
        "backup": str(backup) if backup is not None else None,
        "skill_sha256": installed_report.get("skill_sha256"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Install this repository's GDO skill into Codex after validating the "
            "source, and preserve any prior installation as a backup."
        )
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        help="Override CODEX_HOME; defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and show paths only.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()
    codex_home = args.codex_home
    if codex_home is None:
        configured = os.environ.get("CODEX_HOME")
        codex_home = Path(configured) if configured else Path.home() / ".codex"

    try:
        result = install(codex_home, dry_run=args.dry_run)
    except (InstallError, OSError, shutil.Error, subprocess.SubprocessError) as exc:
        if args.json:
            print(
                json.dumps(
                    {"installed": False, "error": str(exc)},
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return 1
        raise SystemExit(str(exc)) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    elif result["dry_run"]:
        print(f"Source bundle is valid: {result['source']}")
        print(f"Would install to: {result['destination']}")
    else:
        print(f"Installed Game Design Orchestrator {result['version']}")
        print(f"Skill path: {result['destination']}")
        if result["backup"]:
            print(f"Previous installation backed up to: {result['backup']}")
        print("Installation check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
