#!/usr/bin/env python3
"""Validate and prepare harness-neutral forward-evaluation fixtures.

This module validates catalog structure, copies declared inputs into a clean
directory, and validates run metadata. It intentionally does not score model
responses or claim that any behavioral check passed.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator, Mapping, Sequence
from urllib.parse import urlsplit


EVAL_ROOT = Path(__file__).resolve().parent
DEFAULT_CASES_FILE = EVAL_ROOT / "cases.json"
EXPECTED_CASE_COUNT = 6
SKILL_NAME = "orchestrate-game-design"
RUN_RECORD_SCHEMA_VERSION = 3
ARTIFACT_NAMES = (
    "final_response",
    "tool_transcript",
    "resulting_file_tree",
    "changed_file_diff",
)
ARTIFACT_FIELDS = ("path", "bytes", "sha256")
SOURCE_VERIFICATION_OPERATIONS = frozenset({"fetch", "open", "read"})
REQUIRED_RUN_PATHS = {
    "record_schema_version",
    "case_id",
    "case_schema_version",
    "started_at_utc",
    "completed_at_utc",
    "repository_revision",
    "catalog.path",
    "catalog.sha256",
    "skill.version",
    "skill.contract_id",
    "skill.sha256",
    "runner.name",
    "runner.version",
    "model.name",
    "model.configuration",
    "environment.operating_system",
    "tool_capabilities",
    "tool_evidence",
    "claims.source_verification_claimed",
    "claims.verified_source_targets",
} | {
    f"artifacts.{artifact}.{field}"
    for artifact in ARTIFACT_NAMES
    for field in ARTIFACT_FIELDS
}
REQUIRED_CAPABILITIES = {
    "filesystem_read",
    "filesystem_write",
    "external_research",
    "external_mutation",
}
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
COMMIT_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
CASE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
WINDOWS_RESERVED_NAME_PATTERN = re.compile(
    r"^(?:con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])$",
    re.IGNORECASE,
)
WINDOWS_INVALID_COMPONENT_CHARACTERS = frozenset('<>:"|?*')
CHECKOUT_CACHE_DIRECTORY_NAMES = frozenset(
    {".mypy_cache", ".pytest_cache", ".ruff_cache"}
)
CHECKOUT_CACHE_FILE_SUFFIXES = frozenset({".pyc", ".pyo"})
CHECKOUT_INPUT_FORBIDDEN_DIRECTORY_NAMES = (
    CHECKOUT_CACHE_DIRECTORY_NAMES | frozenset({"__pycache__"})
)


class ValidationError(ValueError):
    """Raised when a catalog, fixture, destination, or run record is invalid."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label} must be a JSON object")
    return value


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    return value


def _string_list(value: Any, label: str, *, minimum: int = 0) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise ValidationError(f"{label} must be a list with at least {minimum} items")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_nonempty_string(item, f"{label}[{index}]"))
    return result


def _exact_schema_version(value: Any, expected: int, label: str) -> int:
    if type(value) is not int or value != expected:
        raise ValidationError(f"{label} must be the integer {expected}")
    return value


def _safe_relative_path(value: Any, label: str) -> PurePosixPath:
    raw = _nonempty_string(value, label)
    if "\\" in raw:
        raise ValidationError(f"{label} must use forward slashes")
    if unicodedata.normalize("NFC", raw) != raw:
        raise ValidationError(f"{label} must use NFC-normalized text")
    path = PurePosixPath(raw)
    if not path.parts or str(path) in {"", "."}:
        raise ValidationError(f"{label} must name a file")
    if path.as_posix() != raw:
        raise ValidationError(f"{label} must use one canonical path spelling")
    if path.is_absolute() or ".." in path.parts:
        raise ValidationError(f"{label} must be a safe relative path")
    for component in path.parts:
        if component.endswith((".", " ")):
            raise ValidationError(
                f"{label} components must not end with a dot or space"
            )
        if any(
            character in WINDOWS_INVALID_COMPONENT_CHARACTERS
            or ord(character) < 32
            for character in component
        ):
            raise ValidationError(
                f"{label} contains characters unsafe on portable filesystems"
            )
        device_stem = component.split(".", 1)[0].rstrip(" .")
        if WINDOWS_RESERVED_NAME_PATTERN.fullmatch(device_stem):
            raise ValidationError(f"{label} uses a reserved Windows device name")
    return path


def _portable_path_key(path: PurePosixPath | str) -> str:
    return unicodedata.normalize("NFC", str(path)).casefold()


def _uses_checkout_cache_directory(path: PurePosixPath | str) -> bool:
    return any(
        component.casefold() in CHECKOUT_INPUT_FORBIDDEN_DIRECTORY_NAMES
        for component in PurePosixPath(path).parts
    )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _overlaps(left: Path, right: Path) -> bool:
    return _inside(left, right) or _inside(right, left)


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


@contextmanager
def _verified_regular_stream(
    path: Path, *, allowed_root: Path, label: str
) -> Iterator[BinaryIO]:
    """Open one stable regular file without trusting an earlier path check."""

    try:
        root = allowed_root.resolve(strict=True)
        resolved_before = path.resolve(strict=True)
        before = os.lstat(path)
        if not _inside(resolved_before, root) or not stat.S_ISREG(before.st_mode):
            raise ValidationError(f"{label} is not a regular file inside {root}: {path}")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if os.name == "nt":
            import msvcrt

            windows_handle = _open_stable_windows_regular_file(path)
            try:
                descriptor = msvcrt.open_osfhandle(windows_handle, flags)
            except BaseException:
                _close_windows_handle(windows_handle)
                raise
        else:
            flags |= getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(path, flags)
    except ValidationError:
        raise
    except (OSError, RuntimeError) as error:
        raise ValidationError(f"could not safely open {label}: {path}: {error}") from error

    stream = os.fdopen(descriptor, "rb")
    try:
        opened = os.fstat(stream.fileno())
        current = os.lstat(path)
        resolved_opened = path.resolve(strict=True)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or not _same_identity(before, opened)
            or not _same_identity(current, opened)
            or not _inside(resolved_opened, root)
        ):
            raise ValidationError(f"{label} changed while it was being opened: {path}")

        yield stream

        after = os.fstat(stream.fileno())
        current_after = os.lstat(path)
        resolved_after = path.resolve(strict=True)
        if (
            not _same_identity(opened, after)
            or not _same_identity(current_after, after)
            or opened.st_size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns
            or opened.st_ctime_ns != after.st_ctime_ns
            or not _inside(resolved_after, root)
        ):
            raise ValidationError(f"{label} changed while it was being read: {path}")
    except ValidationError:
        raise
    except (OSError, RuntimeError) as error:
        raise ValidationError(f"could not safely read {label}: {path}: {error}") from error
    finally:
        stream.close()


def _read_verified_bytes(path: Path, *, allowed_root: Path, label: str) -> bytes:
    with _verified_regular_stream(path, allowed_root=allowed_root, label=label) as stream:
        return stream.read()


def _verified_digest(
    path: Path, *, allowed_root: Path, label: str
) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_count = 0
    with _verified_regular_stream(path, allowed_root=allowed_root, label=label) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            byte_count += len(chunk)
            digest.update(chunk)
    return byte_count, digest.hexdigest()


def _run_git_text(directory: Path, arguments: Sequence[str], label: str) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(directory), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValidationError(f"could not {label}: {error}") from error
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "unknown git failure"
        raise ValidationError(f"could not {label}: {details}")
    return result.stdout


def _run_git_bytes(directory: Path, arguments: Sequence[str], label: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(directory), *arguments],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValidationError(f"could not {label}: {error}") from error
    if result.returncode != 0:
        details = (
            result.stderr.decode("utf-8", errors="replace").strip()
            or result.stdout.decode("utf-8", errors="replace").strip()
            or "unknown git failure"
        )
        raise ValidationError(f"could not {label}: {details}")
    return result.stdout


def _run_git_bytes_with_input(
    directory: Path,
    arguments: Sequence[str],
    input_bytes: bytes,
    label: str,
) -> bytes:
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(directory), *arguments],
            input=input_bytes,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValidationError(f"could not {label}: {error}") from error
    if result.returncode != 0:
        details = (
            result.stderr.decode("utf-8", errors="replace").strip()
            or result.stdout.decode("utf-8", errors="replace").strip()
            or "unknown git failure"
        )
        raise ValidationError(f"could not {label}: {details}")
    return result.stdout


def _repository_checkout(
    eval_root: Path,
) -> tuple[Path, str, tuple[str, str]]:
    resolved_eval_root = eval_root.expanduser().resolve(strict=True)
    root_text = _run_git_text(
        resolved_eval_root,
        ("rev-parse", "--show-toplevel"),
        "resolve evaluator repository root",
    ).strip()
    try:
        repository_root = Path(root_text).resolve(strict=True)
        eval_relative = resolved_eval_root.relative_to(repository_root).as_posix()
    except (OSError, ValueError) as error:
        raise ValidationError("eval_root must be inside its evaluator Git checkout") from error
    if _uses_checkout_cache_directory(PurePosixPath(eval_relative)):
        raise ValidationError(
            "eval_root must not be inside an excluded checkout cache directory"
        )

    skill_root = repository_root / "skills" / SKILL_NAME
    try:
        skill_status = os.lstat(skill_root)
        resolved_skill_root = skill_root.resolve(strict=True)
    except (OSError, ValueError) as error:
        raise ValidationError(
            f"evaluator checkout is missing skills/{SKILL_NAME}"
        ) from error
    skill_attributes = getattr(skill_status, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(skill_status.st_mode)
        or skill_attributes
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400)
        or not stat.S_ISDIR(skill_status.st_mode)
        or not _inside(resolved_skill_root, repository_root)
    ):
        raise ValidationError(
            f"evaluator checkout skills/{SKILL_NAME} must be a real directory"
        )
    if _overlaps(resolved_eval_root, resolved_skill_root):
        raise ValidationError(
            "evaluator eval_root and skills/orchestrate-game-design scopes must not overlap"
        )
    skill_relative = f"skills/{SKILL_NAME}"

    revision = _run_git_text(
        repository_root,
        ("rev-parse", "--verify", "HEAD"),
        "resolve evaluator checkout HEAD",
    ).strip()
    if not COMMIT_PATTERN.fullmatch(revision):
        raise ValidationError("evaluator checkout HEAD is not a full commit id")
    return repository_root, revision, (eval_relative, skill_relative)


def _head_scoped_blobs(
    repository_root: Path,
    revision: str,
    pathspecs: Sequence[str],
) -> dict[str, bytes]:
    tree_output = _run_git_bytes(
        repository_root,
        (
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            revision,
            "--",
            *pathspecs,
        ),
        "read evaluator file tree from HEAD",
    )
    entries: list[tuple[str, str]] = []
    for raw_entry in tree_output.split(b"\0"):
        if not raw_entry:
            continue
        try:
            header, path_bytes = raw_entry.split(b"\t", 1)
            mode, object_type, object_id = header.split(b" ", 2)
            relative = path_bytes.decode("utf-8")
            object_id_text = object_id.decode("ascii")
        except (UnicodeError, ValueError) as error:
            raise ValidationError("evaluator HEAD tree contains an invalid entry") from error
        if object_type != b"blob" or mode == b"120000":
            raise ValidationError(
                f"evaluator HEAD contains a linked or non-file entry: {relative}"
            )
        entries.append((relative, object_id_text))

    request = b"".join(
        object_id.encode("ascii") + b"\n" for _, object_id in entries
    )
    batch_output = _run_git_bytes_with_input(
        repository_root,
        ("cat-file", "--batch"),
        request,
        "read evaluator blobs from HEAD",
    )
    offset = 0
    blobs: dict[str, bytes] = {}
    for relative, expected_object_id in entries:
        header_end = batch_output.find(b"\n", offset)
        if header_end < 0:
            raise ValidationError("evaluator HEAD blob batch ended unexpectedly")
        header = batch_output[offset:header_end].split(b" ")
        if len(header) != 3:
            raise ValidationError("evaluator HEAD blob batch returned an invalid header")
        try:
            actual_object_id = header[0].decode("ascii")
            object_type = header[1].decode("ascii")
            size = int(header[2])
        except (UnicodeError, ValueError) as error:
            raise ValidationError(
                "evaluator HEAD blob batch returned invalid metadata"
            ) from error
        offset = header_end + 1
        blob_end = offset + size
        if (
            actual_object_id != expected_object_id
            or object_type != "blob"
            or blob_end >= len(batch_output)
            or batch_output[blob_end : blob_end + 1] != b"\n"
        ):
            raise ValidationError("evaluator HEAD blob batch did not match its tree")
        if relative in blobs:
            raise ValidationError(f"duplicate evaluator HEAD path: {relative}")
        blobs[relative] = batch_output[offset:blob_end]
        offset = blob_end + 1
    if offset != len(batch_output):
        raise ValidationError("evaluator HEAD blob batch returned trailing data")
    return blobs


def _actual_scoped_files(
    repository_root: Path,
    scoped_paths: Sequence[str],
) -> dict[str, Path]:
    actual: dict[str, Path] = {}
    for scoped_relative in scoped_paths:
        scope = repository_root / Path(*PurePosixPath(scoped_relative).parts)
        try:
            scope_status = os.lstat(scope)
        except OSError as error:
            raise ValidationError(
                f"evaluator checkout scope is missing: {scoped_relative}"
            ) from error
        scope_attributes = getattr(scope_status, "st_file_attributes", 0)
        if (
            stat.S_ISLNK(scope_status.st_mode)
            or scope_attributes
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400)
            or not stat.S_ISDIR(scope_status.st_mode)
        ):
            raise ValidationError(
                f"evaluator checkout scope must be a real directory: {scoped_relative}"
            )

        pending = [scope]
        while pending:
            directory = pending.pop()
            try:
                with os.scandir(directory) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name)
            except OSError as error:
                raise ValidationError(
                    f"could not enumerate evaluator checkout directory {directory}: {error}"
                ) from error
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(repository_root).as_posix()
                if relative == ".git" or relative.startswith(".git/"):
                    continue
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError as error:
                    raise ValidationError(
                        f"could not inspect evaluator checkout entry {relative}: {error}"
                    ) from error
                attributes = getattr(metadata, "st_file_attributes", 0)
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or attributes
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400)
                ):
                    raise ValidationError(
                        f"evaluator checkout contains a linked entry: {relative}"
                )
                if stat.S_ISDIR(metadata.st_mode):
                    if entry.name.casefold() in CHECKOUT_CACHE_DIRECTORY_NAMES:
                        continue
                    pending.append(path)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValidationError(
                        f"evaluator checkout contains a non-regular file: {relative}"
                    )
                relative_parts = tuple(
                    part.casefold() for part in PurePosixPath(relative).parts
                )
                if (
                    path.suffix.casefold() in CHECKOUT_CACHE_FILE_SUFFIXES
                    and "__pycache__" in relative_parts
                ):
                    continue
                if relative in actual:
                    raise ValidationError(f"duplicate evaluator checkout path: {relative}")
                actual[relative] = path
    return actual


def _assert_reproducible_checkout(
    repository_root: Path,
    revision: str,
    scoped_paths: Sequence[str],
) -> None:
    current_revision = _run_git_text(
        repository_root,
        ("rev-parse", "--verify", "HEAD"),
        "recheck evaluator checkout HEAD",
    ).strip()
    if current_revision != revision:
        raise ValidationError(
            "evaluator checkout HEAD changed during validation; the recorded commit "
            "cannot reproduce this run"
        )

    pathspecs = tuple(f":(literal){path}" for path in scoped_paths)
    staged = _run_git_bytes(
        repository_root,
        (
            "diff",
            "--cached",
            "--name-only",
            "-z",
            revision,
            "--",
            *pathspecs,
        ),
        "compare evaluator index with HEAD",
    )
    if staged:
        raise ValidationError(
            "evaluator checkout is dirty in the index; HEAD commit cannot reproduce this run"
        )

    expected = _head_scoped_blobs(repository_root, revision, pathspecs)
    actual = _actual_scoped_files(repository_root, scoped_paths)
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing[:3]))
        if extra:
            details.append("extra " + ", ".join(extra[:3]))
        raise ValidationError(
            "evaluator checkout is dirty (file set does not match HEAD: "
            + "; ".join(details)
            + "); HEAD commit cannot reproduce this run"
        )

    for relative, expected_bytes in expected.items():
        actual_bytes = _read_verified_bytes(
            actual[relative],
            allowed_root=repository_root,
            label=f"evaluator checkout file {relative}",
        )
        if actual_bytes != expected_bytes:
            raise ValidationError(
                f"evaluator checkout is dirty ({relative} does not match HEAD bytes); "
                "HEAD commit cannot reproduce this run"
            )

    final_revision = _run_git_text(
        repository_root,
        ("rev-parse", "--verify", "HEAD"),
        "finish rechecking evaluator checkout HEAD",
    ).strip()
    if final_revision != revision:
        raise ValidationError(
            "evaluator checkout HEAD changed during validation; the recorded commit "
            "cannot reproduce this run"
        )


def _checkout_identity(
    eval_root: Path,
) -> tuple[str, dict[str, str], Path, tuple[str, str]]:
    repository_root, revision, scoped_paths = _repository_checkout(eval_root)
    _assert_reproducible_checkout(repository_root, revision, scoped_paths)
    workspace_relative = f"skills/{SKILL_NAME}/scripts/workspace.py"
    skill_relative = f"skills/{SKILL_NAME}/SKILL.md"
    workspace_source = _run_git_bytes(
        repository_root,
        ("show", f"{revision}:{workspace_relative}"),
        "read checkout workspace module from HEAD",
    )
    skill_source = _run_git_bytes(
        repository_root,
        ("show", f"{revision}:{skill_relative}"),
        "read checkout SKILL.md from HEAD",
    )
    try:
        tree = ast.parse(workspace_source, filename=workspace_relative)
    except (SyntaxError, ValueError) as error:
        raise ValidationError(f"checkout workspace module is invalid: {error}") from error

    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in {"VERSION", "CONTRACT_ID"}:
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError, SyntaxError):
                    continue
                if isinstance(value, str) and value:
                    constants[target.id] = value
    missing = sorted({"VERSION", "CONTRACT_ID"} - constants.keys())
    if missing:
        raise ValidationError(
            "checkout workspace module is missing literal constants: "
            + ", ".join(missing)
        )
    lineage = {
        "version": constants["VERSION"],
        "contract_id": constants["CONTRACT_ID"],
        "sha256": hashlib.sha256(skill_source).hexdigest(),
    }
    _assert_reproducible_checkout(repository_root, revision, scoped_paths)
    return revision, lineage, repository_root, scoped_paths


def checkout_skill_lineage(eval_root: Path = EVAL_ROOT) -> dict[str, str]:
    return _checkout_identity(eval_root)[1]


def checkout_revision(eval_root: Path = EVAL_ROOT) -> str:
    return _checkout_identity(eval_root)[0]


def _http_target(value: Any, label: str) -> str:
    target = _nonempty_string(value, label)
    if target != target.strip():
        raise ValidationError(f"{label} must not contain surrounding whitespace")
    try:
        parsed = urlsplit(target)
        hostname = parsed.hostname
    except ValueError as error:
        raise ValidationError(f"{label} must be an HTTP(S) URL") from error
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ValidationError(f"{label} must be an HTTP(S) URL")
    return target


def _parse_json_bytes(data: bytes, label: str, path: Path) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        raise ValidationError(f"{label} is not valid UTF-8: {path}") from error
    def reject_nonfinite_constant(value: str) -> None:
        raise ValueError(f"non-finite numeric constant {value}")

    try:
        return json.loads(text, parse_constant=reject_nonfinite_constant)
    except json.JSONDecodeError as error:
        raise ValidationError(
            f"{label} is not valid JSON at line {error.lineno}, column {error.colno}: "
            f"{error.msg}"
        ) from error
    except ValueError as error:
        raise ValidationError(f"{label} is not valid JSON: {error}") from error


def _load_json(path: Path, label: str) -> Any:
    try:
        data = path.read_bytes()
    except FileNotFoundError as error:
        raise ValidationError(f"{label} does not exist: {path}") from error
    except OSError as error:
        raise ValidationError(f"could not read {label} {path}: {error}") from error
    return _parse_json_bytes(data, label, path)


def load_catalog(path: Path = DEFAULT_CASES_FILE) -> dict[str, Any]:
    value = _load_json(path, "case catalog")
    if not isinstance(value, dict):
        raise ValidationError("case catalog must be a JSON object")
    return value


def _case_index(catalog: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    cases = catalog.get("cases")
    if not isinstance(cases, list):
        raise ValidationError("cases must be a list")
    return {
        _nonempty_string(_mapping(case, "case").get("id"), "case.id"): _mapping(
            case, "case"
        )
        for case in cases
    }


def validate_catalog(
    catalog: Mapping[str, Any], *, eval_root: Path = EVAL_ROOT
) -> dict[str, int]:
    _exact_schema_version(catalog.get("schema_version"), 1, "schema_version")
    _nonempty_string(catalog.get("purpose"), "purpose")

    invocation_contract = _mapping(
        catalog.get("invocation_contract"), "invocation_contract"
    )
    _nonempty_string(invocation_contract.get("delivery"), "invocation_contract.delivery")
    _nonempty_string(invocation_contract.get("exposure"), "invocation_contract.exposure")

    run_contract = _mapping(catalog.get("run_record_contract"), "run_record_contract")
    _exact_schema_version(
        run_contract.get("schema_version"),
        RUN_RECORD_SCHEMA_VERSION,
        "run_record_contract.schema_version",
    )
    required_paths = set(
        _string_list(run_contract.get("required_paths"), "required_paths", minimum=1)
    )
    missing_paths = sorted(REQUIRED_RUN_PATHS - required_paths)
    if missing_paths:
        raise ValidationError(
            "run_record_contract is missing required paths: " + ", ".join(missing_paths)
        )
    capability_names = set(
        _string_list(
            run_contract.get("required_tool_capabilities"),
            "required_tool_capabilities",
            minimum=1,
        )
    )
    if capability_names != REQUIRED_CAPABILITIES:
        raise ValidationError(
            "required_tool_capabilities must be exactly: "
            + ", ".join(sorted(REQUIRED_CAPABILITIES))
        )
    _nonempty_string(
        run_contract.get("source_verification_rule"),
        "run_record_contract.source_verification_rule",
    )
    source_operations = set(
        _string_list(
            run_contract.get("source_verification_operations"),
            "run_record_contract.source_verification_operations",
            minimum=1,
        )
    )
    if source_operations != SOURCE_VERIFICATION_OPERATIONS:
        raise ValidationError(
            "source_verification_operations must be exactly: "
            + ", ".join(sorted(SOURCE_VERIFICATION_OPERATIONS))
        )
    _nonempty_string(run_contract.get("artifact_rule"), "run_record_contract.artifact_rule")
    _nonempty_string(run_contract.get("lineage_rule"), "run_record_contract.lineage_rule")
    _nonempty_string(run_contract.get("catalog_rule"), "run_record_contract.catalog_rule")

    cases = catalog.get("cases")
    if not isinstance(cases, list) or len(cases) != EXPECTED_CASE_COUNT:
        raise ValidationError(f"cases must contain exactly {EXPECTED_CASE_COUNT} entries")

    seen_ids: set[str] = set()
    declared_sources: set[str] = set()
    fixture_count = 0
    resolved_eval_root = eval_root.resolve()
    fixture_root = (eval_root / "fixtures").resolve()

    for index, value in enumerate(cases):
        case = _mapping(value, f"cases[{index}]")
        case_id = _nonempty_string(case.get("id"), f"cases[{index}].id")
        if not CASE_ID_PATTERN.fullmatch(case_id):
            raise ValidationError(f"case id is not kebab-case: {case_id}")
        if case_id in seen_ids:
            raise ValidationError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        _nonempty_string(case.get("title"), f"{case_id}.title")
        setup = _nonempty_string(case.get("setup"), f"{case_id}.setup")
        if any(
            marker in setup.casefold()
            for marker in ("expected answer", "mechanical_checks", "human_review")
        ):
            raise ValidationError(f"{case_id}.setup exposes hidden evaluation criteria")

        invocation = _mapping(case.get("invocation"), f"{case_id}.invocation")
        skill = _nonempty_string(invocation.get("skill"), f"{case_id}.invocation.skill")
        if invocation.get("required") is not True:
            raise ValidationError(f"{case_id}.invocation.required must be true")
        prompt = _nonempty_string(case.get("prompt"), f"{case_id}.prompt")
        if f"${skill}".casefold() in prompt.casefold():
            raise ValidationError(
                f"{case_id}.prompt embeds the skill invocation instead of keeping it separate"
            )

        _string_list(
            case.get("mechanical_checks"), f"{case_id}.mechanical_checks", minimum=2
        )
        _string_list(case.get("human_review"), f"{case_id}.human_review", minimum=2)

        fixture_files = case.get("fixture_files")
        if not isinstance(fixture_files, list):
            raise ValidationError(f"{case_id}.fixture_files must be a list")
        seen_destinations: set[str] = set()
        for fixture_index, fixture_value in enumerate(fixture_files):
            fixture = _mapping(
                fixture_value, f"{case_id}.fixture_files[{fixture_index}]"
            )
            source_relative = _safe_relative_path(
                fixture.get("source"), f"{case_id}.fixture_files[{fixture_index}].source"
            )
            if _uses_checkout_cache_directory(source_relative):
                raise ValidationError(
                    f"fixture source must not use an excluded checkout cache directory: "
                    f"{source_relative}"
                )
            destination_relative = _safe_relative_path(
                fixture.get("destination"),
                f"{case_id}.fixture_files[{fixture_index}].destination",
            )
            source_key = _portable_path_key(source_relative.as_posix())
            destination_key = _portable_path_key(destination_relative.as_posix())
            if source_key in declared_sources:
                raise ValidationError(
                    f"fixture source is declared more than once: {source_relative}"
                )
            if destination_key in seen_destinations:
                raise ValidationError(
                    f"duplicate fixture destination in {case_id}: {destination_relative}"
                )
            declared_sources.add(source_key)
            seen_destinations.add(destination_key)

            declared_source = eval_root / Path(*source_relative.parts)
            source_path = declared_source.resolve()
            if not _inside(source_path, resolved_eval_root) or not _inside(
                source_path, fixture_root
            ):
                raise ValidationError(f"fixture source escapes evals/fixtures: {source_relative}")
            try:
                source_status = os.lstat(declared_source)
            except OSError as error:
                raise ValidationError(
                    f"fixture source is not a regular file: {source_relative}"
                ) from error
            if not stat.S_ISREG(source_status.st_mode):
                raise ValidationError(f"fixture source is not a regular file: {source_relative}")
            fixture_count += 1

    actual_source_spellings: dict[str, str] = {}
    for path in (eval_root / "fixtures").rglob("*"):
        if not path.is_file():
            continue
        spelling = path.relative_to(eval_root).as_posix()
        path_key = _portable_path_key(spelling)
        previous = actual_source_spellings.get(path_key)
        if previous is not None and previous != spelling:
            raise ValidationError(
                "fixture files collide on portable filesystems: "
                f"{previous}, {spelling}"
            )
        actual_source_spellings[path_key] = spelling
    actual_sources = set(actual_source_spellings)
    undeclared = sorted(actual_sources - declared_sources)
    missing = sorted(declared_sources - actual_sources)
    if undeclared:
        raise ValidationError("undeclared fixture files: " + ", ".join(undeclared))
    if missing:
        raise ValidationError("declared fixture files are missing: " + ", ".join(missing))

    return {"case_count": len(cases), "fixture_count": fixture_count}


def _normalized_windows_path_text(value: str) -> str:
    normalized = os.path.normcase(os.path.normpath(value))
    if os.name != "nt":
        return normalized
    if normalized.startswith("\\\\?\\unc\\"):
        normalized = "\\\\" + normalized[8:]
    elif re.match(r"^\\\\\?\\[a-z]:\\", normalized):
        normalized = normalized[4:]
    return normalized


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _open_stable_windows_regular_file(path: Path) -> int:
    """Open one regular file while denying concurrent writes and replacement."""

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
            raise FileNotFoundError(error, "Could not secure evaluator file.", str(path))
        raise OSError(error, "Could not secure evaluator file.", str(path))

    try:
        metadata = os.lstat(path)
        attributes = getattr(metadata, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400):
            raise ValidationError(f"refusing linked evaluator file: {path}")
        if not stat.S_ISREG(metadata.st_mode):
            raise ValidationError(f"expected a regular evaluator file: {path}")

        get_final_path = kernel32.GetFinalPathNameByHandleW
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
            raise OSError(error, "Could not verify evaluator file.", str(path))
        buffer = ctypes.create_unicode_buffer(needed + 1)
        if get_final_path(handle, buffer, len(buffer), 0) == 0:
            error = ctypes.get_last_error()
            raise OSError(error, "Could not verify evaluator file.", str(path))
        expected = _normalized_windows_path_text(os.path.abspath(os.fspath(path)))
        actual = _normalized_windows_path_text(buffer.value)
        if actual != expected:
            raise ValidationError(f"evaluator file changed while opening: {path}")
        return handle
    except BaseException:
        _close_windows_handle(handle)
        raise


def _verify_windows_directory_handle(handle: int, path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise ValidationError(
            f"prepared destination directory changed while held: {path}: {error}"
        ) from error
    attributes = getattr(metadata, "st_file_attributes", 0)
    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400):
        raise ValidationError(f"refusing linked prepared destination directory: {path}")
    if not attributes & getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x00000010):
        raise ValidationError(f"expected a prepared destination directory: {path}")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
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
        raise ValidationError(
            f"could not verify prepared destination directory {path}: {ctypes.WinError(error)}"
        )
    buffer = ctypes.create_unicode_buffer(needed + 1)
    if get_final_path(handle, buffer, len(buffer), 0) == 0:
        error = ctypes.get_last_error()
        raise ValidationError(
            f"could not verify prepared destination directory {path}: {ctypes.WinError(error)}"
        )
    expected = _normalized_windows_path_text(os.path.abspath(os.fspath(path)))
    actual = _normalized_windows_path_text(buffer.value)
    if actual != expected:
        raise ValidationError(f"prepared destination directory changed while held: {path}")


def _open_stable_windows_directory(path: Path) -> int:
    """Open one non-reparse directory and deny its replacement while held."""

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
        if error in (2, 3):
            raise FileNotFoundError(
                error, "Could not secure prepared destination directory.", str(path)
            )
        raise OSError(error, "Could not secure prepared destination directory.", str(path))
    try:
        _verify_windows_directory_handle(handle, path)
        return handle
    except BaseException:
        _close_windows_handle(handle)
        raise


def _open_stable_posix_directory(path: Path, created: os.stat_result) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        current = os.lstat(path)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        if "descriptor" in locals():
            os.close(descriptor)
        raise ValidationError(
            f"could not secure prepared destination directory {path}: {error}"
        ) from error
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or not _same_identity(created, opened)
        or not _same_identity(current, opened)
        or resolved != path
    ):
        os.close(descriptor)
        raise ValidationError(f"prepared destination directory changed while opening: {path}")
    return descriptor


def _recheck_posix_directory_handle(
    path: Path,
    descriptor: int,
    label: str,
) -> None:
    try:
        opened = os.fstat(descriptor)
        current = os.lstat(path)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValidationError(f"could not recheck {label} {path}: {error}") from error
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or not _same_identity(opened, current)
        or resolved != path
    ):
        raise ValidationError(f"{label} changed before filesystem mutation: {path}")


def _open_posix_child_directory(
    parent_descriptor: int,
    name: str,
    created: os.stat_result,
    path: Path,
) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        current = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        if "descriptor" in locals():
            os.close(descriptor)
        raise ValidationError(
            f"could not secure newly created destination {path}: {error}"
        ) from error
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or not _same_identity(created, opened)
        or not _same_identity(current, opened)
        or resolved != path
    ):
        os.close(descriptor)
        raise ValidationError(f"prepared destination changed while opening: {path}")
    return descriptor


def _open_windows_directory_chain(
    directory: Path,
) -> dict[str, tuple[Path, int]]:
    anchor_text = directory.anchor
    if not directory.is_absolute() or not anchor_text:
        raise ValidationError(f"destination parent must be absolute: {directory}")
    anchor = Path(anchor_text)
    try:
        relative = directory.relative_to(anchor)
    except ValueError as error:
        raise ValidationError(
            f"could not derive destination parent chain: {directory}"
        ) from error

    paths = [anchor]
    current = anchor
    for part in relative.parts:
        current = current / part
        paths.append(current)

    handles: dict[str, tuple[Path, int]] = {}
    try:
        for path in paths:
            key = _normalized_windows_path_text(os.fspath(path))
            handles[key] = (path, _open_stable_windows_directory(path))
    except ValidationError:
        for _, handle in reversed(tuple(handles.values())):
            _close_windows_handle(handle)
        raise
    except OSError as error:
        for _, handle in reversed(tuple(handles.values())):
            _close_windows_handle(handle)
        raise ValidationError(
            f"could not secure destination parent chain {directory}: {error}"
        ) from error
    return handles


@contextmanager
def _owned_destination(
    destination: Path,
) -> Iterator[tuple[int | None, dict[str, tuple[Path, int]]]]:
    try:
        parent_status = os.lstat(destination.parent)
    except FileNotFoundError as error:
        raise ValidationError(
            f"destination parent must already exist: {destination.parent}"
        ) from error
    except OSError as error:
        raise ValidationError(
            f"could not inspect destination parent {destination.parent}: {error}"
        ) from error
    if stat.S_ISLNK(parent_status.st_mode) or not stat.S_ISDIR(parent_status.st_mode):
        raise ValidationError(
            f"destination parent must be a real directory: {destination.parent}"
        )

    if os.name == "nt":
        handles = _open_windows_directory_chain(destination.parent)
        try:
            for path, held_handle in handles.values():
                _verify_windows_directory_handle(held_handle, path)
            try:
                destination.mkdir(exist_ok=False)
            except FileExistsError as error:
                raise ValidationError(
                    f"destination must not already exist: {destination}"
                ) from error
            except OSError as error:
                raise ValidationError(
                    f"could not create destination {destination}: {error}"
                ) from error
            try:
                created = os.lstat(destination)
            except OSError as error:
                raise ValidationError(
                    f"could not inspect newly created destination {destination}: {error}"
                ) from error
            attributes = getattr(created, "st_file_attributes", 0)
            if (
                attributes
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400)
                or not attributes
                & getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x00000010)
            ):
                raise ValidationError(
                    f"newly created destination is not a real directory: {destination}"
                )
            handle = _open_stable_windows_directory(destination)
            key = _normalized_windows_path_text(os.fspath(destination))
            handles[key] = (destination, handle)
            yield None, handles
            for path, held_handle in handles.values():
                _verify_windows_directory_handle(held_handle, path)
        finally:
            for _, held_handle in reversed(tuple(handles.values())):
                _close_windows_handle(held_handle)
        return

    parent_descriptor = _open_stable_posix_directory(
        destination.parent,
        parent_status,
    )
    descriptor: int | None = None
    try:
        _recheck_posix_directory_handle(
            destination.parent,
            parent_descriptor,
            "destination parent",
        )
        try:
            os.mkdir(destination.name, dir_fd=parent_descriptor)
        except FileExistsError as error:
            raise ValidationError(
                f"destination must not already exist: {destination}"
            ) from error
        except OSError as error:
            raise ValidationError(
                f"could not create destination {destination}: {error}"
            ) from error
        try:
            created = os.stat(
                destination.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            raise ValidationError(
                f"could not inspect newly created destination {destination}: {error}"
            ) from error
        if stat.S_ISLNK(created.st_mode) or not stat.S_ISDIR(created.st_mode):
            raise ValidationError(
                f"newly created destination is not a real directory: {destination}"
            )
        descriptor = _open_posix_child_directory(
            parent_descriptor,
            destination.name,
            created,
            destination,
        )
        yield descriptor, {}
        opened = os.fstat(descriptor)
        current = os.stat(
            destination.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not _same_identity(opened, current)
            or destination.resolve(strict=True) != destination
        ):
            raise ValidationError(
                f"prepared destination directory changed while held: {destination}"
            )
    except (OSError, RuntimeError) as error:
        raise ValidationError(
            f"could not recheck prepared destination directory {destination}: {error}"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)


def _write_posix_fixture(
    root_descriptor: int,
    destination: Path,
    destination_relative: PurePosixPath,
    source_bytes: bytes,
) -> tuple[int, str]:
    descriptor = os.dup(root_descriptor)
    current_path = destination
    try:
        for part in destination_relative.parent.parts:
            _recheck_posix_directory_handle(
                current_path,
                descriptor,
                "fixture destination parent",
            )
            try:
                os.mkdir(part, dir_fd=descriptor)
            except FileExistsError:
                pass
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            child = os.open(part, flags, dir_fd=descriptor)
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                os.close(child)
                raise ValidationError(
                    f"fixture destination parent is not a directory: {destination_relative}"
                )
            os.close(descriptor)
            descriptor = child
            current_path = current_path / part

        _recheck_posix_directory_handle(
            current_path,
            descriptor,
            "fixture destination parent",
        )
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(
            destination_relative.name,
            flags,
            0o666,
            dir_fd=descriptor,
        )
        with os.fdopen(file_descriptor, "w+b") as stream:
            stream.write(source_bytes)
            stream.flush()
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValidationError(
                    f"fixture destination is not a regular file: {destination_relative}"
                )
            stream.seek(0)
            written = stream.read()
    except ValidationError:
        raise
    except OSError as error:
        raise ValidationError(
            f"could not create fixture destination {destination_relative}: {error}"
        ) from error
    finally:
        os.close(descriptor)
    return len(written), hashlib.sha256(written).hexdigest()


def _write_windows_fixture(
    destination: Path,
    destination_relative: PurePosixPath,
    source_bytes: bytes,
    handles: dict[str, tuple[Path, int]],
) -> tuple[int, str]:
    current = destination
    for part in destination_relative.parent.parts:
        current = current / part
        key = _normalized_windows_path_text(os.fspath(current))
        if key in handles:
            continue
        for held_path, held_handle in handles.values():
            _verify_windows_directory_handle(held_handle, held_path)
        try:
            current.mkdir()
        except FileExistsError:
            pass
        except OSError as error:
            raise ValidationError(
                f"could not create fixture destination parent {current}: {error}"
            ) from error
        try:
            handles[key] = (current, _open_stable_windows_directory(current))
        except ValidationError:
            raise
        except OSError as error:
            raise ValidationError(
                f"could not secure fixture destination parent {current}: {error}"
            ) from error

    for held_path, held_handle in handles.values():
        _verify_windows_directory_handle(held_handle, held_path)
    destination_path = destination / Path(*destination_relative.parts)
    try:
        with destination_path.open("x+b") as stream:
            stream.write(source_bytes)
            stream.flush()
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValidationError(
                    f"fixture destination is not a regular file: {destination_relative}"
                )
            stream.seek(0)
            written = stream.read()
    except ValidationError:
        raise
    except OSError as error:
        raise ValidationError(
            f"could not create fixture destination {destination_relative}: {error}"
        ) from error
    return len(written), hashlib.sha256(written).hexdigest()


def prepare_case(
    catalog: Mapping[str, Any], case_id: str, destination: Path, *, eval_root: Path = EVAL_ROOT
) -> dict[str, Any]:
    eval_root = eval_root.resolve()
    validate_catalog(catalog, eval_root=eval_root)
    cases = _case_index(catalog)
    if case_id not in cases:
        raise ValidationError(f"unknown case id: {case_id}")
    case = cases[case_id]

    requested_destination = Path(
        os.path.abspath(os.fspath(destination.expanduser()))
    )
    destination_present = False
    try:
        os.lstat(requested_destination)
    except FileNotFoundError:
        pass
    except OSError as error:
        raise ValidationError(
            f"could not inspect destination {requested_destination}: {error}"
        ) from error
    else:
        destination_present = True
    try:
        comparison_destination = requested_destination.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise ValidationError(
            f"could not resolve destination {requested_destination}: {error}"
        ) from error
    if _overlaps(comparison_destination, eval_root):
        raise ValidationError(
            "destination must not overlap the evaluator catalog and fixture root: "
            f"{eval_root}"
        )
    if destination_present:
        raise ValidationError(
            f"destination must not already exist: {requested_destination}"
        )
    destination = requested_destination

    baseline: list[dict[str, Any]] = []
    with _owned_destination(destination) as (root_descriptor, windows_handles):
        for fixture_value in case["fixture_files"]:
            fixture = _mapping(fixture_value, "fixture")
            source_relative = _safe_relative_path(fixture["source"], "fixture.source")
            destination_relative = _safe_relative_path(
                fixture["destination"], "fixture.destination"
            )
            source_path = eval_root / Path(*source_relative.parts)
            source_bytes = _read_verified_bytes(
                source_path,
                allowed_root=eval_root / "fixtures",
                label=f"fixture source {source_relative.as_posix()}",
            )
            if os.name == "nt":
                byte_count, digest = _write_windows_fixture(
                    destination,
                    destination_relative,
                    source_bytes,
                    windows_handles,
                )
            else:
                if root_descriptor is None:
                    raise ValidationError("prepared destination descriptor is unavailable")
                byte_count, digest = _write_posix_fixture(
                    root_descriptor,
                    destination,
                    destination_relative,
                    source_bytes,
                )
            baseline.append(
                {
                    "path": destination_relative.as_posix(),
                    "bytes": byte_count,
                    "sha256": digest,
                }
            )

    return {
        "case_schema_version": catalog["schema_version"],
        "case_id": case_id,
        "title": case["title"],
        "setup": case["setup"],
        "invocation": case["invocation"],
        "prompt": case["prompt"],
        "prepared_project": str(destination),
        "baseline_files": baseline,
        "run_record_contract": catalog["run_record_contract"],
    }


def _nested_value(record: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = record
    for segment in dotted_path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            raise ValidationError(f"run record is missing {dotted_path}")
        current = current[segment]
    return current


def _timestamp(value: Any, label: str) -> datetime:
    raw = _nonempty_string(value, label)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValidationError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValidationError(f"{label} must include a UTC offset")
    if parsed.utcoffset() != timedelta(0):
        raise ValidationError(f"{label} must be a UTC timestamp")
    return parsed


def _validate_artifacts(record: Mapping[str, Any], record_path: Path) -> None:
    record_path = record_path.expanduser().resolve(strict=False)
    record_root = record_path.parent.resolve(strict=True)
    artifacts = _mapping(record.get("artifacts"), "artifacts")
    seen_paths: set[str] = set()
    seen_resolved_paths: set[Path] = set()
    for name in ARTIFACT_NAMES:
        label = f"artifacts.{name}"
        metadata = _mapping(artifacts.get(name), label)
        relative = _safe_relative_path(metadata.get("path"), f"{label}.path")
        path_key = _portable_path_key(relative.as_posix())
        if path_key in seen_paths:
            raise ValidationError("artifact paths must be distinct")
        seen_paths.add(path_key)

        expected_bytes = metadata.get("bytes")
        if (
            not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or expected_bytes < 0
        ):
            raise ValidationError(f"{label}.bytes must be a non-negative integer")
        expected_sha256 = metadata.get("sha256")
        if not isinstance(expected_sha256, str) or not SHA256_PATTERN.fullmatch(
            expected_sha256
        ):
            raise ValidationError(
                f"{label}.sha256 must contain exactly 64 hexadecimal characters"
            )

        artifact_path = record_root / Path(*relative.parts)
        try:
            resolved_artifact = artifact_path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValidationError(f"{label}.path does not name a regular file") from error
        if not _inside(resolved_artifact, record_root) or resolved_artifact == record_path:
            raise ValidationError(f"{label}.path must stay beside the run record")
        if resolved_artifact in seen_resolved_paths:
            raise ValidationError("artifact paths must resolve to distinct files")
        seen_resolved_paths.add(resolved_artifact)
        actual_bytes, actual_sha256 = _verified_digest(
            artifact_path,
            allowed_root=record_root,
            label=label,
        )
        if expected_bytes != actual_bytes:
            raise ValidationError(f"{label}.bytes does not match the artifact")
        if expected_sha256.casefold() != actual_sha256:
            raise ValidationError(f"{label}.sha256 does not match the artifact")


def _verified_catalog_input(
    catalog: Mapping[str, Any],
    catalog_path: Path,
    eval_root: Path,
) -> tuple[Path, bytes]:
    requested_path = Path(
        os.path.abspath(os.fspath(catalog_path.expanduser()))
    )
    try:
        resolved_root = eval_root.expanduser().resolve(strict=True)
        resolved_path = requested_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValidationError(
            f"could not resolve case catalog {requested_path}: {error}"
        ) from error
    if resolved_path.parent != resolved_root:
        raise ValidationError("catalog_path must name a catalog directly inside eval_root")
    catalog_bytes = _read_verified_bytes(
        requested_path,
        allowed_root=resolved_root,
        label="case catalog",
    )
    parsed = _mapping(
        _parse_json_bytes(catalog_bytes, "case catalog", requested_path),
        "case catalog",
    )
    try:
        parsed_canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        supplied_canonical = json.dumps(
            catalog,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValidationError(f"case catalog contains non-JSON values: {error}") from error
    if parsed_canonical != supplied_canonical:
        raise ValidationError("supplied catalog does not match catalog_path bytes")
    return resolved_path, catalog_bytes


def validate_run_record(
    record: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    eval_root: Path = EVAL_ROOT,
    record_path: Path,
    catalog_path: Path,
) -> None:
    resolved_catalog_path, catalog_bytes = _verified_catalog_input(
        catalog,
        catalog_path,
        eval_root,
    )
    validate_catalog(catalog, eval_root=eval_root)
    contract = _mapping(catalog["run_record_contract"], "run_record_contract")
    for dotted_path in contract["required_paths"]:
        _nested_value(record, dotted_path)

    record_schema_version = record.get("record_schema_version")
    if (
        type(record_schema_version) is not int
        or record_schema_version != contract["schema_version"]
    ):
        raise ValidationError("record_schema_version does not match the run contract")
    case_schema_version = record.get("case_schema_version")
    if (
        type(case_schema_version) is not int
        or case_schema_version != catalog["schema_version"]
    ):
        raise ValidationError("case_schema_version does not match the case catalog")
    case_id = _nonempty_string(record.get("case_id"), "case_id")
    if case_id not in _case_index(catalog):
        raise ValidationError(f"unknown case id in run record: {case_id}")

    started = _timestamp(record.get("started_at_utc"), "started_at_utc")
    completed = _timestamp(record.get("completed_at_utc"), "completed_at_utc")
    if completed < started:
        raise ValidationError("completed_at_utc precedes started_at_utc")
    repository_revision = _nonempty_string(
        record.get("repository_revision"), "repository_revision"
    )
    if not COMMIT_PATTERN.fullmatch(repository_revision):
        raise ValidationError(
            "repository_revision must be a full 40- or 64-character commit id"
        )
    (
        expected_revision,
        expected_lineage,
        repository_root,
        scoped_paths,
    ) = _checkout_identity(eval_root)
    if repository_revision != expected_revision:
        raise ValidationError("repository_revision does not match evaluator checkout HEAD")

    catalog_metadata = _mapping(record.get("catalog"), "catalog")
    reported_catalog_path = _safe_relative_path(
        catalog_metadata.get("path"),
        "catalog.path",
    ).as_posix()
    try:
        expected_catalog_path = resolved_catalog_path.relative_to(
            repository_root
        ).as_posix()
    except ValueError as error:
        raise ValidationError("catalog_path must be inside the evaluator checkout") from error
    if reported_catalog_path != expected_catalog_path:
        raise ValidationError("catalog.path does not match the catalog used for validation")
    reported_catalog_sha256 = catalog_metadata.get("sha256")
    expected_catalog_sha256 = hashlib.sha256(catalog_bytes).hexdigest()
    if (
        not isinstance(reported_catalog_sha256, str)
        or not SHA256_PATTERN.fullmatch(reported_catalog_sha256)
        or reported_catalog_sha256.casefold() != expected_catalog_sha256
    ):
        raise ValidationError("catalog.sha256 does not match the catalog used for validation")

    for dotted_path in (
        "runner.name",
        "runner.version",
        "model.name",
        "environment.operating_system",
    ):
        _nonempty_string(_nested_value(record, dotted_path), dotted_path)
    for field in ("version", "contract_id"):
        reported = _nonempty_string(_nested_value(record, f"skill.{field}"), f"skill.{field}")
        if reported != expected_lineage[field]:
            raise ValidationError(f"skill.{field} does not match this checkout")
    skill_sha256 = _nested_value(record, "skill.sha256")
    if (
        not isinstance(skill_sha256, str)
        or not SHA256_PATTERN.fullmatch(skill_sha256)
        or skill_sha256.casefold() != expected_lineage["sha256"]
    ):
        raise ValidationError("skill.sha256 does not match this checkout")
    configuration = _nested_value(record, "model.configuration")
    if not isinstance(configuration, (str, Mapping)) or not configuration:
        raise ValidationError("model.configuration must be a non-empty string or object")
    _validate_artifacts(record, record_path)

    capabilities = record.get("tool_capabilities")
    if not isinstance(capabilities, list):
        raise ValidationError("tool_capabilities must be a list")
    capability_map: dict[str, bool] = {}
    for index, value in enumerate(capabilities):
        capability = _mapping(value, f"tool_capabilities[{index}]")
        name = _nonempty_string(capability.get("name"), f"tool_capabilities[{index}].name")
        available = capability.get("available")
        if not isinstance(available, bool):
            raise ValidationError(
                f"tool_capabilities[{index}].available must be true or false"
            )
        if name in capability_map:
            raise ValidationError(f"duplicate tool capability: {name}")
        capability_map[name] = available
    missing_capabilities = sorted(REQUIRED_CAPABILITIES - capability_map.keys())
    if missing_capabilities:
        raise ValidationError(
            "tool_capabilities is missing: " + ", ".join(missing_capabilities)
        )

    evidence_items = record.get("tool_evidence")
    if not isinstance(evidence_items, list):
        raise ValidationError("tool_evidence must be a list")
    successful_research_targets: set[str] = set()
    for index, value in enumerate(evidence_items):
        evidence = _mapping(value, f"tool_evidence[{index}]")
        capability = _nonempty_string(
            evidence.get("capability"), f"tool_evidence[{index}].capability"
        )
        if capability not in capability_map:
            raise ValidationError(
                f"tool_evidence[{index}] names an undeclared capability: {capability}"
            )
        operation = _nonempty_string(
            evidence.get("operation"), f"tool_evidence[{index}].operation"
        )
        target = _nonempty_string(
            evidence.get("target"), f"tool_evidence[{index}].target"
        )
        outcome = _nonempty_string(
            evidence.get("outcome"), f"tool_evidence[{index}].outcome"
        )
        recorded = _timestamp(
            evidence.get("recorded_at_utc"),
            f"tool_evidence[{index}].recorded_at_utc",
        )
        if not capability_map[capability]:
            raise ValidationError(
                f"tool_evidence[{index}] uses unavailable capability: {capability}"
            )
        if capability == "external_research" and outcome.casefold() == "success":
            normalized_operation = operation.strip().casefold()
            if normalized_operation not in SOURCE_VERIFICATION_OPERATIONS:
                raise ValidationError(
                    f"tool_evidence[{index}].operation is not a source-verification "
                    "operation"
                )
            target = _http_target(target, f"tool_evidence[{index}].target")
            if not started <= recorded <= completed:
                raise ValidationError(
                    f"tool_evidence[{index}].recorded_at_utc is outside the run"
                )
            successful_research_targets.add(target)

    claims = _mapping(record.get("claims"), "claims")
    verification_claimed = claims.get("source_verification_claimed")
    if not isinstance(verification_claimed, bool):
        raise ValidationError("claims.source_verification_claimed must be true or false")
    verified_targets = [
        _http_target(value, f"claims.verified_source_targets[{index}]")
        for index, value in enumerate(
            _string_list(
                claims.get("verified_source_targets"),
                "claims.verified_source_targets",
            )
        )
    ]
    if len(verified_targets) != len(set(verified_targets)):
        raise ValidationError("claims.verified_source_targets contains duplicates")
    if verification_claimed:
        if not verified_targets:
            raise ValidationError(
                "source verification is claimed without any verified source targets"
            )
        targets_without_evidence = sorted(
            set(verified_targets) - successful_research_targets
        )
        if targets_without_evidence:
            raise ValidationError(
                "source verification lacks successful external_research evidence for: "
                + ", ".join(targets_without_evidence)
            )
    elif verified_targets:
        raise ValidationError(
            "verified_source_targets must be empty when source verification is not claimed"
        )
    _assert_reproducible_checkout(repository_root, expected_revision, scoped_paths)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or prepare harness-neutral GDO forward-evaluation cases."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_FILE,
        help="case catalog (default: evals/cases.json)",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("validate", help="validate the catalog and fixture files")
    prepare_contract = (
        "copy one case's fixtures into a non-existing directory whose parent exists"
    )
    prepare = subparsers.add_parser(
        "prepare",
        help=prepare_contract,
        description=prepare_contract,
    )
    prepare.add_argument("--case", required=True, dest="case_id")
    prepare.add_argument(
        "--destination",
        required=True,
        type=Path,
        help="new directory path; its parent must already exist",
    )
    validate_run = subparsers.add_parser(
        "validate-run", help="validate metadata for a completed run without scoring it"
    )
    validate_run.add_argument("--record", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        cases_path = arguments.cases.expanduser().resolve()
        eval_root = cases_path.parent
        catalog = load_catalog(cases_path)
        command = arguments.command or "validate"
        if command == "prepare":
            report = prepare_case(
                catalog,
                arguments.case_id,
                arguments.destination,
                eval_root=eval_root,
            )
        elif command == "validate-run":
            record = _mapping(_load_json(arguments.record, "run record"), "run record")
            validate_run_record(
                record,
                catalog,
                eval_root=eval_root,
                record_path=arguments.record,
                catalog_path=cases_path,
            )
            report = {
                "status": "valid-metadata",
                "case_id": record["case_id"],
                "behavior_scored": False,
            }
        else:
            counts = validate_catalog(catalog, eval_root=eval_root)
            report = {
                "status": "valid-catalog",
                **counts,
                "behavior_scored": False,
            }
    except ValidationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
