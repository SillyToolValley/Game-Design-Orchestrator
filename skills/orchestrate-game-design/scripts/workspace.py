#!/usr/bin/env python3
"""Small, shared workspace primitives for Game Design Orchestrator.

The public commands deliberately keep design policy out of this module. It only
owns safe paths, atomic files, a cross-process lock, IDs, and state validation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from typing import Any, Iterator


VERSION = "0.1.2-beta"
CONTRACT_ID = "human-led-deliverable-first-v1"
MIGRATABLE_TOOL_VERSIONS = ("0.1.0-beta", "0.1.1-beta")
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
_POSIX_ROOT_LEASE: ContextVar[tuple[str, int, int] | None] = ContextVar(
    "gdo_posix_root_lease", default=None
)
_WINDOWS_MUTEX_PREFIX = "Global\\GameDesignOrchestrator-"


def _read_json_dict(
    path: Path, target: Path, coordination_root: Path
) -> dict[str, Any] | None:
    try:
        payload = json.loads(
            read_workspace_text(path, target, coordination_root),
            parse_constant=reject_nonfinite_json_constant,
        )
    except (FileNotFoundError, OSError, UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def legacy_workspace_indicators(
    target: Path, coordination_root: Path
) -> list[str]:
    """Return structural fingerprints of the retired governance workspace."""

    indicators: list[str] = []
    legacy_state = _read_json_dict(
        target / "project-state.json", target, coordination_root
    )
    if legacy_state is not None and legacy_state.get("schema_version") == 2:
        next_ids = legacy_state.get("next_ids")
        if (
            isinstance(next_ids, dict)
            and LEGACY_STATE_KEYS.issubset(next_ids)
        ) or {"stage", "review_mode"}.issubset(legacy_state):
            indicators.append("project-state.json")

    legacy_config = _read_json_dict(
        target / "design-config.json", target, coordination_root
    )
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


def reject_legacy_workspace(target: Path, coordination_root: Path) -> None:
    indicators = legacy_workspace_indicators(target, coordination_root)
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


def reject_nonfinite_json_constant(value: str) -> None:
    raise WorkspaceError(f"Non-finite JSON number is not allowed: {value}.")


def resolve_root(root: Path) -> Path:
    candidate = root.expanduser()
    if not candidate.exists():
        raise WorkspaceError(
            f"Project root does not exist: {candidate}. Create it first, then rerun."
        )
    if not candidate.is_dir():
        raise WorkspaceError(f"Project root is not a directory: {candidate}.")
    return candidate.resolve()


def _normalized_path_text(value: str) -> str:
    normalized = os.path.normcase(os.path.normpath(value))
    if os.name != "nt":
        return normalized

    # Windows exposes the same DOS/UNC path through both ordinary spellings and
    # the extended ``\\?\`` namespace. Normalize only documented drive and UNC
    # forms; other device namespaces must not become ordinary filesystem paths.
    if normalized.startswith("\\\\?\\unc\\"):
        normalized = "\\\\" + normalized[8:]
    elif re.match(r"^\\\\\?\\[a-z]:\\", normalized):
        normalized = normalized[4:]
    return normalized


def _comparison_path(path: Path) -> Path:
    """Return a resolved path spelling for containment and lock identity checks."""

    return Path(_normalized_path_text(os.fspath(path.resolve(strict=False))))


def _windows_workspace_mutex_name(resolved_root: Path) -> str:
    """Return the cross-session Windows mutex name for one project root."""

    digest = hashlib.sha256(
        os.fsencode(str(_comparison_path(resolved_root)))
    ).hexdigest()
    return _WINDOWS_MUTEX_PREFIX + digest


def _lexical_comparison_path(path: Path) -> Path:
    """Normalize an absolute path without following a newly swapped link."""

    return Path(_normalized_path_text(os.path.abspath(os.fspath(path))))


def resolve_target(root: Path, directory: str | Path = DEFAULT_DIRECTORY) -> Path:
    """Resolve a design directory and reject paths outside the project root."""

    resolved_root = resolve_root(root)
    target = (resolved_root / directory).resolve(strict=False)
    try:
        _comparison_path(target).relative_to(_comparison_path(resolved_root))
    except ValueError as exc:
        raise WorkspaceError("--directory must resolve inside --root.") from exc
    return target


def require_contained(path: Path, target: Path) -> Path:
    resolved_target = target.resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    try:
        _comparison_path(resolved_path).relative_to(_comparison_path(resolved_target))
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


def _relative_lexical(path: Path, parent: Path, label: str) -> Path:
    try:
        return _lexical_comparison_path(path).relative_to(
            _lexical_comparison_path(parent)
        )
    except ValueError as exc:
        raise WorkspaceError(f"{label} must remain inside {parent}.") from exc


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _require_posix_path_identity(
    path: Path, opened: os.stat_result, label: str
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise WorkspaceError(f"{label} changed while held: {path}.") from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or not _same_file_identity(opened, current)
    ):
        raise WorkspaceError(f"{label} changed while held: {path}.")


def _require_posix_root_lease(path: Path, opened: os.stat_result) -> None:
    lease = _POSIX_ROOT_LEASE.get()
    if lease is None:
        return
    expected_path, expected_device, expected_inode = lease
    if (
        os.fspath(_lexical_comparison_path(path)) != expected_path
        or opened.st_dev != expected_device
        or opened.st_ino != expected_inode
    ):
        raise WorkspaceError(
            "Project root changed after the project-wide workspace lock was acquired."
        )


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _verify_windows_handle_path(handle: int, path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    get_final_path = ctypes.WinDLL(
        "kernel32", use_last_error=True
    ).GetFinalPathNameByHandleW
    get_final_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_final_path.restype = wintypes.DWORD
    needed = get_final_path(handle, None, 0, 0)
    if needed == 0:
        error = ctypes.get_last_error()
        raise OSError(error, "Could not verify workspace entry.", str(path))
    buffer = ctypes.create_unicode_buffer(needed + 1)
    if get_final_path(handle, buffer, len(buffer), 0) == 0:
        error = ctypes.get_last_error()
        raise OSError(error, "Could not verify workspace entry.", str(path))
    if Path(_normalized_path_text(buffer.value)) != _lexical_comparison_path(path):
        raise WorkspaceError(f"Workspace entry changed while opening: {path}.")


def _open_stable_windows_directory(path: Path) -> int:
    """Open one lexical directory entry and deny replacement while held."""

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000 | 0x00000080,  # GENERIC_READ | FILE_READ_ATTRIBUTES
        0x00000001 | 0x00000002,  # share read/write, deliberately not delete
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        error = ctypes.get_last_error()
        if error in (2, 3):  # ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND
            raise FileNotFoundError(
                error, "Could not secure workspace directory.", str(path)
            )
        raise ctypes.WinError(error)

    try:
        metadata = os.lstat(path)
        attributes = getattr(metadata, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400):
            raise WorkspaceError(f"Refusing linked workspace directory: {path}.")
        if not attributes & getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x00000010):
            raise WorkspaceError(f"Expected a workspace directory: {path}.")

        _verify_windows_handle_path(handle, path)
        return handle
    except BaseException:
        _close_windows_handle(handle)
        raise


def _open_stable_windows_regular_file(path: Path) -> int:
    """Open a regular lexical file entry without following reparse points."""

    import ctypes
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # share read only; deliberately not write or delete
        None,
        3,  # OPEN_EXISTING
        0x00200000,  # OPEN_REPARSE_POINT
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        error = ctypes.get_last_error()
        if error in (2, 3):
            raise FileNotFoundError(
                error, "Could not secure workspace file.", str(path)
            )
        raise ctypes.WinError(error)

    try:
        metadata = os.lstat(path)
        attributes = getattr(metadata, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400):
            raise WorkspaceError(f"Refusing linked workspace file: {path}.")
        if not stat.S_ISREG(metadata.st_mode):
            raise WorkspaceError(f"Expected a regular workspace file: {path}.")
        _verify_windows_handle_path(handle, path)
        return handle
    except BaseException:
        _close_windows_handle(handle)
        raise


@contextmanager
def stable_workspace_directory(
    directory: Path,
    target: Path,
    coordination_root: Path,
    *,
    create: bool,
) -> Iterator[int | None]:
    """Hold a non-reparse directory chain for one filesystem operation.

    A POSIX project-lock lease rejects a replacement root tree. Directory file
    descriptors cannot prevent a same-UID actor from moving an already-open
    descendant inode elsewhere; callers still fail the project-root exit check.
    """

    root_path = Path(os.path.abspath(os.fspath(coordination_root)))
    root = _lexical_comparison_path(root_path)
    expected_target = _lexical_comparison_path(target)
    _relative_lexical(expected_target, root, "Workspace target")
    _relative_lexical(directory, expected_target, "Mutation directory")
    relative = _relative_lexical(directory, root, "Mutation directory")

    if os.name == "nt":
        handles: list[int] = []
        current = root_path
        try:
            handles.append(_open_stable_windows_directory(current))
            for part in relative.parts:
                current = current / part
                try:
                    handle = _open_stable_windows_directory(current)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        current.mkdir()
                    except FileExistsError:
                        pass
                    handle = _open_stable_windows_directory(current)
                handles.append(handle)
            yield None
        finally:
            for handle in reversed(handles):
                _close_windows_handle(handle)
        return

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root_path, flags | no_follow)
    try:
        opened_root = os.fstat(descriptor)
        _require_posix_path_identity(root_path, opened_root, "Project root")
        _require_posix_root_lease(root_path, opened_root)
        for part in relative.parts:
            try:
                child = os.open(part, flags | no_follow, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(part, flags | no_follow, dir_fd=descriptor)
            except OSError as exc:
                raise WorkspaceError(
                    f"Refusing linked or invalid workspace directory: {directory}."
                ) from exc
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                os.close(child)
                raise WorkspaceError(f"Expected a workspace directory: {directory}.")
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _regular_metadata_at(
    path: Path, parent_descriptor: int | None
) -> os.stat_result | None:
    try:
        if parent_descriptor is None:
            metadata = os.lstat(path)
        else:
            metadata = os.stat(
                path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise WorkspaceError(f"Expected a regular file, not a link or device: {path}.")
    return metadata


def read_workspace_text(path: Path, target: Path, coordination_root: Path) -> str:
    with stable_workspace_directory(
        path.parent, target, coordination_root, create=False
    ) as parent_descriptor:
        before = _regular_metadata_at(path, parent_descriptor)
        if before is None:
            raise FileNotFoundError(path)
        if parent_descriptor is None:
            import msvcrt

            windows_handle = _open_stable_windows_regular_file(path)
            try:
                descriptor = msvcrt.open_osfhandle(
                    windows_handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                )
            except BaseException:
                _close_windows_handle(windows_handle)
                raise
            with os.fdopen(descriptor, "r", encoding="utf-8-sig") as handle:
                opened = os.fstat(handle.fileno())
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or not _same_file_identity(before, opened)
                ):
                    raise WorkspaceError(
                        f"Workspace file changed while opening: {path}."
                    )
                text = handle.read()
                opened_after = os.fstat(handle.fileno())
                current_after = os.lstat(path)
                if (
                    not stat.S_ISREG(opened_after.st_mode)
                    or not stat.S_ISREG(current_after.st_mode)
                    or not _same_file_identity(opened, opened_after)
                    or not _same_file_identity(opened, current_after)
                    or opened.st_size != opened_after.st_size
                    or opened.st_mtime_ns != opened_after.st_mtime_ns
                    or opened.st_ctime_ns != opened_after.st_ctime_ns
                ):
                    raise WorkspaceError(
                        f"Workspace file changed while reading: {path}."
                    )
                return text
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_descriptor,
        )
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not _same_file_identity(before, opened)
            ):
                raise WorkspaceError(
                    f"Workspace file changed while opening: {path}."
                )

            with os.fdopen(
                descriptor, "r", encoding="utf-8-sig"
            ) as handle:
                descriptor = -1
                text = handle.read()
                opened_after = os.fstat(handle.fileno())
                current_after = os.stat(
                    path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(opened_after.st_mode)
                    or not stat.S_ISREG(current_after.st_mode)
                    or not _same_file_identity(opened, opened_after)
                    or not _same_file_identity(opened, current_after)
                    or opened.st_size != opened_after.st_size
                    or opened.st_mtime_ns != opened_after.st_mtime_ns
                    or opened.st_ctime_ns != opened_after.st_ctime_ns
                ):
                    raise WorkspaceError(
                        f"Workspace file changed while reading: {path}."
                    )
                return text
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def write_new(
    path: Path, text: str, target: Path, coordination_root: Path
) -> bool:
    """Create one file without replacing user work; return whether it was created."""

    with stable_workspace_directory(
        path.parent, target, coordination_root, create=True
    ) as parent_descriptor:
        if _regular_metadata_at(path, parent_descriptor) is not None:
            return False
        try:
            if parent_descriptor is None:
                handle = path.open("x", encoding="utf-8", newline="\n")
            else:
                descriptor = os.open(
                    path.name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o666,
                    dir_fd=parent_descriptor,
                )
                handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
            with handle:
                handle.write(text)
            return True
        except FileExistsError:
            _regular_metadata_at(path, parent_descriptor)
            return False


def replace_with_retry(source: str | Path, destination: Path) -> None:
    """Retry transient file-sharing denials without weakening atomic replacement."""

    deadline = time.monotonic() + 2.0
    delay = 0.01
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.10)


def atomic_write(
    path: Path, text: str, target: Path, coordination_root: Path
) -> None:
    """Atomically replace a regular workspace file with UTF-8 text."""

    with stable_workspace_directory(
        path.parent, target, coordination_root, create=True
    ) as parent_descriptor:
        _regular_metadata_at(path, parent_descriptor)
        if parent_descriptor is None:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=path.name + ".", suffix=".tmp", dir=path.parent
            )
            try:
                with os.fdopen(
                    descriptor, "w", encoding="utf-8", newline="\n"
                ) as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                _regular_metadata_at(path, parent_descriptor)
                replace_with_retry(temporary_name, path)
            except BaseException:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
                raise
            return

        temporary_name = ""
        descriptor = -1
        for _attempt in range(100):
            temporary_name = f".{path.name}.{secrets.token_hex(8)}.tmp"
            try:
                descriptor = os.open(
                    temporary_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_descriptor,
                )
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise WorkspaceError(f"Could not create a temporary file for {path}.")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            _regular_metadata_at(path, parent_descriptor)
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        except BaseException:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
            raise


def atomic_json(
    path: Path,
    payload: dict[str, Any],
    target: Path,
    coordination_root: Path,
) -> None:
    atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        target,
        coordination_root,
    )


def canonical_iso_date(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None
    ):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkspaceError(f"Duplicate JSON field: {key}.")
        result[key] = value
    return result


def load_json_object(
    path: Path, target: Path, coordination_root: Path
) -> dict[str, Any]:
    try:
        text = read_workspace_text(path, target, coordination_root)
    except FileNotFoundError as exc:
        raise WorkspaceError(f"Missing workspace state: {path}.") from exc
    except UnicodeDecodeError as exc:
        raise WorkspaceError(f"Workspace state is not valid UTF-8: {path}.") from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite_json_constant,
        )
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
    schema_version = payload.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != STATE_SCHEMA_VERSION
    ):
        raise WorkspaceError(
            f"{source}: schema_version must be {STATE_SCHEMA_VERSION}."
        )
    project_name = payload.get("project_name")
    if not isinstance(project_name, str) or not project_name.strip():
        raise WorkspaceError(f"{source}: project_name must be nonempty text.")
    tool_version = payload.get("tool_version")
    if tool_version != VERSION:
        migration_hint = ""
        if tool_version in MIGRATABLE_TOOL_VERSIONS:
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
    if not canonical_iso_date(updated_at):
        raise WorkspaceError(
            f"{source}: updated_at must be a canonical YYYY-MM-DD date."
        )
    return payload


def load_state(target: Path, coordination_root: Path) -> dict[str, Any]:
    path = target / ".gdo" / "state.json"
    return validate_state(
        load_json_object(path, target, coordination_root), path
    )


def _official_migration_source(payload: dict[str, Any], path: Path) -> str:
    """Validate and identify an exact official state accepted by --migrate."""

    source_version = payload.get("tool_version")
    base_keys = {
        "schema_version",
        "tool_version",
        "project_name",
        "next_ids",
        "updated_at",
    }
    if source_version == "0.1.0-beta":
        expected_keys = base_keys
    elif source_version == "0.1.1-beta":
        expected_keys = base_keys | {"contract_id"}
        if "migration" in payload:
            expected_keys.add("migration")
    else:
        raise WorkspaceError(
            f"{path}: --migrate accepts only official "
            f"{', '.join(MIGRATABLE_TOOL_VERSIONS)} state."
        )

    if set(payload) != expected_keys:
        raise WorkspaceError(
            f"{path}: cannot migrate this state. --migrate accepts only an "
            "unmodified official 0.1.0-beta or 0.1.1-beta state."
        )
    if source_version == "0.1.1-beta" and payload.get("contract_id") != CONTRACT_ID:
        raise WorkspaceError(
            f"{path}: cannot migrate this state. The official 0.1.1-beta "
            f"contract_id must be {CONTRACT_ID!r}."
        )

    migration = payload.get("migration")
    if migration is not None:
        migrated_at = (
            migration.get("migrated_at")
            if isinstance(migration, dict)
            else None
        )
        valid_migration_date = canonical_iso_date(migrated_at)
        if (
            not isinstance(migration, dict)
            or set(migration) != {"from_tool_version", "migrated_at"}
            or migration.get("from_tool_version") != "0.1.0-beta"
            or not valid_migration_date
        ):
            raise WorkspaceError(
                f"{path}: cannot migrate this state. Its 0.1.0-beta migration "
                "provenance is not an official 0.1.1-beta record."
            )

    # Reuse current structural validation without weakening its version or
    # contract checks. Extra source fields were rejected above.
    normalized = dict(payload)
    normalized["tool_version"] = VERSION
    normalized["contract_id"] = CONTRACT_ID
    try:
        validate_state(normalized, path)
    except WorkspaceError as exc:
        raise WorkspaceError(
            f"{path}: cannot migrate this state. Its managed fields are invalid: {exc}"
        ) from exc
    return source_version


def migrate_official_state(target: Path, coordination_root: Path) -> bool:
    """Explicitly migrate an official 0.1.0/0.1.1 state; return whether changed."""

    path = target / ".gdo" / "state.json"
    payload = load_json_object(path, target, coordination_root)
    try:
        validate_state(payload, path)
    except WorkspaceError:
        pass
    else:
        return False

    source_version = _official_migration_source(payload, path)

    migrated = dict(payload)
    migrated["tool_version"] = VERSION
    migrated["contract_id"] = CONTRACT_ID
    if "migration" not in migrated:
        migrated["migration"] = {
            "from_tool_version": source_version,
            "migrated_at": date.today().isoformat(),
        }
    validate_state(migrated, path)
    atomic_json(path, migrated, target, coordination_root)
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


def workspace_markdown_paths(
    target: Path, coordination_root: Path
) -> list[Path]:
    """Enumerate regular Markdown files without traversing linked entries."""

    result: list[Path] = []
    pending = [target]
    reparse_attribute = getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400
    )
    while pending:
        directory = pending.pop()
        with stable_workspace_directory(
            directory, target, coordination_root, create=False
        ) as directory_descriptor:
            scan_target: str | bytes | int | os.PathLike[str]
            scan_target = (
                directory
                if directory_descriptor is None
                else directory_descriptor
            )
            entries = list(os.scandir(scan_target))
            for entry in entries:
                path = directory / entry.name
                metadata = entry.stat(follow_symlinks=False)
                attributes = getattr(metadata, "st_file_attributes", 0)
                if stat.S_ISLNK(metadata.st_mode) or attributes & reparse_attribute:
                    raise WorkspaceError(
                        f"Refusing linked workspace entry during enumeration: {path}."
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    if entry.name != ".gdo":
                        pending.append(path)
                elif stat.S_ISREG(metadata.st_mode) and path.suffix.casefold() == ".md":
                    result.append(path)
    return sorted(result)


def collect_defined_ids(target: Path, coordination_root: Path) -> set[str]:
    result: set[str] = set()
    if not target.is_dir():
        return result
    for path in workspace_markdown_paths(target, coordination_root):
        try:
            text = read_workspace_text(path, target, coordination_root)
        except (OSError, UnicodeDecodeError):
            continue
        result.update(match.group(2) for match in MARKER_PATTERN.finditer(text))
        filename_match = re.match(r"^(DEC|REF|TST|SYS|REV)-(\d{3,})-", path.name)
        if filename_match:
            result.add(f"{filename_match.group(1)}-{filename_match.group(2)}")
    return result


def allocate_id(
    state: dict[str, Any],
    kind: str,
    target: Path,
    coordination_root: Path,
) -> tuple[str, int]:
    if kind not in PREFIXES:
        raise WorkspaceError(f"Unknown artifact kind: {kind}.")
    next_ids = state["next_ids"]
    defined = collect_defined_ids(target, coordination_root)
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


@contextmanager
def workspace_lock(
    target: Path, coordination_root: Path, timeout: float = 15.0
) -> Iterator[None]:
    """Serialize every workspace under one project root without a lock file."""

    if timeout < 0:
        raise WorkspaceError("Workspace lock timeout must not be negative.")
    resolved_root = resolve_root(coordination_root)
    try:
        _comparison_path(target).relative_to(_comparison_path(resolved_root))
    except ValueError as exc:
        raise WorkspaceError(
            "Workspace lock target must be inside the project root."
        ) from exc

    if os.name == "nt":
        # A named mutex avoids changing the workspace merely to coordinate a
        # read. Lock identity is project-root-wide, matching POSIX directory
        # flock scope; ordinary and \\?\ aliases share the same mutex.
        import ctypes
        from ctypes import wintypes

        mutex_name = _windows_workspace_mutex_name(resolved_root)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        create_mutex.restype = wintypes.HANDLE
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        wait_for_single_object.restype = wintypes.DWORD
        release_mutex = kernel32.ReleaseMutex
        release_mutex.argtypes = (wintypes.HANDLE,)
        release_mutex.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        handle = create_mutex(None, False, mutex_name)
        if not handle:
            error = ctypes.get_last_error()
            raise OSError(
                error,
                "Could not create the required cross-session Global workspace "
                "mutex. GDO will not fall back to a session-local lock.",
            )
        acquired = False
        try:
            wait_result = wait_for_single_object(handle, round(timeout * 1000))
            if wait_result in (0x00000000, 0x00000080):
                acquired = True
            elif wait_result == 0x00000102:
                raise WorkspaceError(
                    "Timed out waiting for another workspace update to finish."
                )
            else:
                error = ctypes.get_last_error()
                raise OSError(error, "Could not wait for the workspace mutex.")
            yield
        finally:
            if acquired:
                release_mutex(handle)
            close_handle(handle)
        return

    import errno
    import fcntl

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(resolved_root, flags)
    deadline = time.monotonic() + timeout
    acquired = False
    lease_token = None
    try:
        while not acquired:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise WorkspaceError(
                        "Timed out waiting for another workspace update to finish."
                    ) from exc
                time.sleep(0.02)
        locked_root = os.fstat(descriptor)
        _require_posix_path_identity(
            resolved_root, locked_root, "Project-wide workspace lock root"
        )
        lease_token = _POSIX_ROOT_LEASE.set(
            (
                os.fspath(_lexical_comparison_path(resolved_root)),
                locked_root.st_dev,
                locked_root.st_ino,
            )
        )
        try:
            yield
        finally:
            _require_posix_path_identity(
                resolved_root, locked_root, "Project-wide workspace lock root"
            )
    finally:
        if lease_token is not None:
            _POSIX_ROOT_LEASE.reset(lease_token)
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
