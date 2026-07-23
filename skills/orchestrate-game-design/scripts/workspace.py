#!/usr/bin/env python3
"""Small, shared workspace primitives for Game Design Orchestrator.

The public commands deliberately keep design policy out of this module. It only
owns safe paths, atomic files, a cross-process lock, IDs, and state validation.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator


VERSION = "0.1.1-beta"
CONTRACT_ID = "human-led-deliverable-first-v1"
MIGRATABLE_TOOL_VERSION = "0.1.0-beta"
STATE_SCHEMA_VERSION = 1
DEFAULT_DIRECTORY = "game-design"
KINDS = ("decision", "reference", "test", "system", "review")
PREFIXES = {
    "decision": "DEC",
    "reference": "REF",
    "test": "TST",
    "system": "SYS",
    "review": "REV",
}
PREFIX_TO_KIND = {prefix: kind for kind, prefix in PREFIXES.items()}
ID_PATTERN = re.compile(r"\b(DEC|REF|TST|SYS|REV)-(\d{3,})\b")
MARKER_PATTERN = re.compile(
    r"<!--\s*gdo:artifact\s+type=(decision|reference|test|system|review)\s+"
    r"id=((?:DEC|REF|TST|SYS|REV)-\d{3,})\s*-->"
)
LEGACY_STATE_KEYS = frozenset({"hypothesis", "prototype", "evidence"})


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def legacy_workspace_indicators(target: Path) -> list[str]:
    """Return structural fingerprints of the retired governance workspace."""

    indicators: list[str] = []
    legacy_state = _read_json_dict(target / "project-state.json")
    if legacy_state is not None and legacy_state.get("schema_version") == 2:
        next_ids = legacy_state.get("next_ids")
        if (
            isinstance(next_ids, dict)
            and LEGACY_STATE_KEYS.issubset(next_ids)
        ) or {"stage", "review_mode"}.issubset(legacy_state):
            indicators.append("project-state.json")

    legacy_config = _read_json_dict(target / "design-config.json")
    if legacy_config is not None:
        workflow = legacy_config.get("workflow")
        has_gates = isinstance(workflow, dict) and isinstance(
            workflow.get("enabled_gates"), list
        )
        if (
            has_gates
            and isinstance(legacy_config.get("evidence_kinds"), list)
            and isinstance(legacy_config.get("test_types"), list)
        ):
            indicators.append("design-config.json")
    return indicators


def reject_legacy_workspace(target: Path) -> None:
    indicators = legacy_workspace_indicators(target)
    if not indicators:
        return
    joined = ", ".join(indicators)
    raise WorkspaceError(
        "Incompatible legacy governance workspace detected "
        f"({joined}). Do not mix it with this release or continue its audit/gate "
        "hierarchy. Treat its creative prose as source material and create a clean "
        "reader-facing design workspace."
    )



class WorkspaceError(ValueError):
    """Raised when a workspace path or state cannot be trusted."""


def resolve_root(root: Path) -> Path:
    candidate = root.expanduser()
    if not candidate.exists():
        raise WorkspaceError(
            f"Project root does not exist: {candidate}. Create it first, then rerun."
        )
    if not candidate.is_dir():
        raise WorkspaceError(f"Project root is not a directory: {candidate}.")
    return candidate.resolve()


def resolve_target(root: Path, directory: str | Path = DEFAULT_DIRECTORY) -> Path:
    """Resolve a design directory and reject paths outside the project root."""

    resolved_root = resolve_root(root)
    target = (resolved_root / directory).resolve(strict=False)
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise WorkspaceError("--directory must resolve inside --root.") from exc
    return target


def require_contained(path: Path, target: Path) -> Path:
    resolved_target = target.resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_target)
    except ValueError as exc:
        raise WorkspaceError(f"Refusing path outside design workspace: {path}.") from exc
    return resolved_path


def require_regular_if_present(path: Path, target: Path) -> None:
    require_contained(path, target)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise WorkspaceError(f"Expected a regular file, not a link or device: {path}.")


def render_template(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    unresolved = re.search(r"{{[^{}\n]+}}", text)
    if unresolved:
        raise WorkspaceError(f"Unresolved template token: {unresolved.group(0)}")
    return text


def template_root(script_file: str | Path) -> Path:
    return Path(script_file).resolve().parents[1] / "assets" / "templates"


def read_template(script_file: str | Path, name: str) -> str:
    path = template_root(script_file) / name
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise WorkspaceError(f"Missing bundled template: {path}.") from exc


def write_new(path: Path, text: str, target: Path) -> bool:
    """Create one file without replacing user work; return whether it was created."""

    require_contained(path, target)
    require_contained(path.parent, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    require_contained(path.parent, target)
    require_regular_if_present(path, target)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return True
    except FileExistsError:
        return False


def atomic_write(path: Path, text: str, target: Path) -> None:
    """Atomically replace a regular workspace file with UTF-8 text."""

    require_contained(path, target)
    require_contained(path.parent, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    require_contained(path.parent, target)
    require_regular_if_present(path, target)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        require_contained(path, target)
        require_regular_if_present(path, target)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def atomic_json(path: Path, payload: dict[str, Any], target: Path) -> None:
    atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        target,
    )


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkspaceError(f"Duplicate JSON field: {key}.")
        result[key] = value
    return result


def load_json_object(path: Path, target: Path) -> dict[str, Any]:
    require_contained(path, target)
    require_regular_if_present(path, target)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise WorkspaceError(f"Missing workspace state: {path}.") from exc
    try:
        payload = json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise WorkspaceError(f"Invalid JSON in {path}: {exc}.") from exc
    if not isinstance(payload, dict):
        raise WorkspaceError(f"Expected one JSON object in {path}.")
    return payload


def initial_state(project_name: str) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "tool_version": VERSION,
        "contract_id": CONTRACT_ID,
        "project_name": project_name,
        "next_ids": {kind: 1 for kind in KINDS},
        "updated_at": date.today().isoformat(),
    }


def validate_state(payload: dict[str, Any], source: Path | str) -> dict[str, Any]:
    if payload.get("schema_version") != STATE_SCHEMA_VERSION:
        raise WorkspaceError(
            f"{source}: schema_version must be {STATE_SCHEMA_VERSION}."
        )
    project_name = payload.get("project_name")
    if not isinstance(project_name, str) or not project_name.strip():
        raise WorkspaceError(f"{source}: project_name must be nonempty text.")
    tool_version = payload.get("tool_version")
    if tool_version != VERSION:
        migration_hint = ""
        if (
            tool_version == MIGRATABLE_TOOL_VERSION
            and payload.get("contract_id") is None
        ):
            migration_hint = (
                " Run init_design_project.py with --migrate before modifying "
                "this workspace."
            )
        raise WorkspaceError(
            f"{source}: tool_version is {tool_version!r}, expected {VERSION!r}. "
            "Run this release's workspace checker before modifying the project; "
            "automatic version rewriting is disabled."
            + migration_hint
        )
    contract_id = payload.get("contract_id")
    if contract_id != CONTRACT_ID:
        raise WorkspaceError(
            f"{source}: contract_id is {contract_id!r}, expected {CONTRACT_ID!r}."
        )
    next_ids = payload.get("next_ids")
    if not isinstance(next_ids, dict):
        raise WorkspaceError(f"{source}: next_ids must be an object.")
    if set(next_ids) != set(KINDS):
        raise WorkspaceError(
            f"{source}: next_ids must contain exactly {', '.join(KINDS)}."
        )
    for kind in KINDS:
        value = next_ids[kind]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise WorkspaceError(f"{source}: next_ids.{kind} must be positive.")
    updated_at = payload.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at:
        raise WorkspaceError(f"{source}: updated_at must be nonempty text.")
    return payload


def load_state(target: Path) -> dict[str, Any]:
    path = target / ".gdo" / "state.json"
    return validate_state(load_json_object(path, target), path)


def migrate_official_state(target: Path) -> bool:
    """Explicitly migrate an untouched 0.1.0-beta state; return whether changed."""

    path = target / ".gdo" / "state.json"
    payload = load_json_object(path, target)
    try:
        validate_state(payload, path)
    except WorkspaceError:
        pass
    else:
        return False

    official_keys = {
        "schema_version",
        "tool_version",
        "project_name",
        "next_ids",
        "updated_at",
    }
    if (
        set(payload) != official_keys
        or payload.get("schema_version") != STATE_SCHEMA_VERSION
        or payload.get("tool_version") != MIGRATABLE_TOOL_VERSION
    ):
        raise WorkspaceError(
            f"{path}: cannot migrate automatically. --migrate accepts only an "
            f"unmodified official {MIGRATABLE_TOOL_VERSION} state."
        )

    migrated = dict(payload)
    migrated["tool_version"] = VERSION
    migrated["contract_id"] = CONTRACT_ID
    migrated["migration"] = {
        "from_tool_version": MIGRATABLE_TOOL_VERSION,
        "migrated_at": date.today().isoformat(),
    }
    validate_state(migrated, path)
    atomic_json(path, migrated, target)
    return True


def slugify(value: str, fallback: str = "artifact") -> str:
    """Make a filename slug while preserving Unicode letters and digits."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    mapped = "".join(
        character if character.isalnum() else "-" for character in normalized
    )
    slug = re.sub(r"-+", "-", mapped).strip("-")
    if not slug:
        slug = fallback
    return (slug[:80].rstrip("-") or fallback).casefold()


def artifact_marker(kind: str, artifact_id: str) -> str:
    return f"<!-- gdo:artifact type={kind} id={artifact_id} -->"


def id_number(artifact_id: str) -> int:
    match = ID_PATTERN.fullmatch(artifact_id)
    if match is None:
        raise WorkspaceError(f"Invalid artifact ID: {artifact_id}.")
    return int(match.group(2))


def collect_defined_ids(target: Path) -> set[str]:
    result: set[str] = set()
    if not target.is_dir():
        return result
    for path in target.rglob("*.md"):
        if ".gdo" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        result.update(match.group(2) for match in MARKER_PATTERN.finditer(text))
        filename_match = re.match(r"^(DEC|REF|TST|SYS|REV)-(\d{3,})-", path.name)
        if filename_match:
            result.add(f"{filename_match.group(1)}-{filename_match.group(2)}")
    return result


def allocate_id(state: dict[str, Any], kind: str, target: Path) -> tuple[str, int]:
    if kind not in PREFIXES:
        raise WorkspaceError(f"Unknown artifact kind: {kind}.")
    next_ids = state["next_ids"]
    defined = collect_defined_ids(target)
    prefix = PREFIXES[kind]
    existing_numbers = [
        id_number(artifact_id)
        for artifact_id in defined
        if artifact_id.startswith(prefix + "-")
    ]
    number = max(next_ids[kind], max(existing_numbers, default=0) + 1)
    while f"{prefix}-{number:03d}" in defined:
        number += 1
    return f"{prefix}-{number:03d}", number + 1


def open_lock(path: Path, target: Path):
    require_contained(path, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    require_contained(path.parent, target)
    require_regular_if_present(path, target)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    return handle


@contextmanager
def workspace_lock(target: Path, timeout: float = 15.0) -> Iterator[None]:
    """Serialize writers on Windows and POSIX without third-party packages."""

    lock_path = target / ".gdo" / "workspace.lock"
    handle = open_lock(lock_path, target)
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
                    raise WorkspaceError(
                        "Timed out waiting for another workspace update to finish."
                    ) from exc
                time.sleep(0.02)
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
