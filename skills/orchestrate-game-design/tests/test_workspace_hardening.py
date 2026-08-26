from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
INIT_SCRIPT = SCRIPTS_ROOT / "init_design_project.py"
CREATE_SCRIPT = SCRIPTS_ROOT / "create_design_artifact.py"
CHECK_SCRIPT = SCRIPTS_ROOT / "check_design_workspace.py"
SNAPSHOT_SCRIPT = SCRIPTS_ROOT / "snapshot_design_workspace.py"

sys.path.insert(0, str(SCRIPTS_ROOT))
import workspace  # noqa: E402
import create_design_artifact as creator  # noqa: E402


VERSION = workspace.VERSION
CONTRACT_ID = workspace.CONTRACT_ID


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


def initialize(root: Path, name: str = "Hardening Test") -> dict[str, Any]:
    return payload(run_python(INIT_SCRIPT, "--root", root, "--name", name))


def tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def create_junction(link: Path, destination: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(destination)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)


def restore_swapped_directory(directory: Path, parked: Path) -> None:
    if not parked.exists():
        return
    if directory.exists():
        os.rmdir(directory)
    parked.rename(directory)


class WorkspaceHardeningTests(unittest.TestCase):
    def test_windows_handle_comparison_canonicalizes_8dot3_spelling(self) -> None:
        long_spelling = Path(r"C:\Users\runneradmin\workspace")

        class ShortNameAlias:
            def __fspath__(self) -> str:
                return r"C:\Users\RUNNER~1\workspace"

            def resolve(self, *, strict: bool) -> Path:
                if not strict:
                    raise AssertionError(
                        "an existing handle path must resolve strictly"
                    )
                return long_spelling

        self.assertEqual(
            Path(workspace._normalized_path_text(os.fspath(long_spelling))),
            workspace._canonical_existing_path(ShortNameAlias()),
        )

    @unittest.skipUnless(os.name == "nt", "Windows 8.3 path aliases")
    def test_windows_8dot3_alias_matches_open_directory_handle(self) -> None:
        import ctypes
        from ctypes import wintypes

        with tempfile.TemporaryDirectory(prefix="gdo-long-path-alias-") as temporary:
            directory = Path(temporary) / "workspace-directory-with-a-long-name"
            directory.mkdir()
            get_short_path = ctypes.WinDLL(
                "kernel32", use_last_error=True
            ).GetShortPathNameW
            get_short_path.argtypes = (
                wintypes.LPCWSTR,
                wintypes.LPWSTR,
                wintypes.DWORD,
            )
            get_short_path.restype = wintypes.DWORD
            needed = get_short_path(str(directory), None, 0)
            if needed == 0:
                self.skipTest("8.3 aliases are unavailable on this volume")
            buffer = ctypes.create_unicode_buffer(needed + 1)
            if get_short_path(str(directory), buffer, len(buffer)) == 0:
                self.skipTest("8.3 aliases are unavailable on this volume")
            alias = Path(buffer.value)
            if workspace._lexical_comparison_path(alias) == (
                workspace._lexical_comparison_path(directory)
            ):
                self.skipTest("this directory has no distinct 8.3 alias")

            handle = workspace._open_stable_windows_directory(alias)
            try:
                self.assertEqual(
                    workspace._canonical_existing_path(alias),
                    workspace._comparison_path(directory),
                )
            finally:
                workspace._close_windows_handle(handle)

            (directory / "game-design").mkdir()
            alias_target = alias / "game-design"
            alias_artifact = alias_target / "short-alias.md"
            with workspace.workspace_lock(alias_target, alias):
                self.assertTrue(
                    workspace.write_new(
                        alias_artifact, "short alias\n", alias_target, alias
                    )
                )
            self.assertEqual(
                (directory / "game-design" / alias_artifact.name).read_text(
                    encoding="utf-8"
                ),
                "short alias\n",
            )

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_windows_root_lease_rejects_swapped_ancestor_junction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-root-junction-lease-") as temporary:
            parent = Path(temporary)
            original_parent = parent / "original"
            outside_parent = parent / "outside"
            alias_parent = parent / "alias"
            original_root = original_parent / "project"
            outside_root = outside_parent / "project"
            original_root.mkdir(parents=True)
            (outside_root / "game-design").mkdir(parents=True)
            create_junction(alias_parent, original_parent)

            root = alias_parent / "project"
            target = root / "game-design"
            target.mkdir()
            artifact = target / "escaped.md"

            with workspace.workspace_lock(target, root):
                os.rmdir(alias_parent)
                create_junction(alias_parent, outside_parent)
                try:
                    with self.assertRaisesRegex(
                        workspace.WorkspaceError, "Project root changed"
                    ):
                        workspace.write_new(
                            artifact, "must stay inside\n", target, root
                        )
                finally:
                    os.rmdir(alias_parent)
                    create_junction(alias_parent, original_parent)

            self.assertFalse((outside_root / "game-design" / artifact.name).exists())
            self.assertFalse(
                (original_root / "game-design" / artifact.name).exists()
            )

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_windows_handle_path_closes_post_verification_junction_race(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-root-junction-race-") as temporary:
            parent = Path(temporary)
            original_parent = parent / "original"
            outside_parent = parent / "outside"
            alias_parent = parent / "alias"
            original_root = original_parent / "project"
            outside_root = outside_parent / "project"
            (original_root / "game-design").mkdir(parents=True)
            (outside_root / "game-design").mkdir(parents=True)
            create_junction(alias_parent, original_parent)

            root = alias_parent / "project"
            target = root / "game-design"
            artifact = target / "bound-to-lease.md"
            original_require = workspace._require_windows_root_lease
            swapped = False

            def swap_after_verification(handle: int) -> Path:
                nonlocal swapped
                stable_root = original_require(handle)
                if not swapped:
                    os.rmdir(alias_parent)
                    create_junction(alias_parent, outside_parent)
                    swapped = True
                return stable_root

            try:
                with workspace.workspace_lock(target, root):
                    with mock.patch.object(
                        workspace,
                        "_require_windows_root_lease",
                        side_effect=swap_after_verification,
                    ):
                        self.assertTrue(
                            workspace.write_new(
                                artifact, "locked root\n", target, root
                            )
                        )
            finally:
                if swapped:
                    os.rmdir(alias_parent)
                    create_junction(alias_parent, original_parent)

            self.assertEqual(
                (original_root / "game-design" / artifact.name).read_text(
                    encoding="utf-8"
                ),
                "locked root\n",
            )
            self.assertFalse((outside_root / "game-design" / artifact.name).exists())

    @unittest.skipUnless(os.name == "nt", "Windows namespace behavior")
    def test_extended_windows_paths_have_stable_containment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-extended-path-") as temporary:
            root = Path(temporary).resolve()
            normal_target = root / "game-design"
            extended_root = Path("\\\\?\\" + str(root))
            extended_target = Path("\\\\?\\" + str(normal_target))

            resolved = workspace.resolve_target(root, extended_target)
            workspace.require_contained(resolved / "design.md", normal_target)
            workspace.require_contained(normal_target / "design.md", extended_target)
            self.assertEqual(
                workspace._comparison_path(normal_target),
                workspace._comparison_path(
                    workspace.resolve_target(extended_root, "game-design")
                ),
            )

            outside = Path("\\\\?\\" + str(root.parent / "outside-game-design"))
            with self.assertRaises(workspace.WorkspaceError):
                workspace.resolve_target(root, outside)

    @unittest.skipUnless(os.name == "nt", "Windows kernel-object namespaces")
    def test_windows_workspace_lock_uses_global_namespace_and_fails_closed(self) -> None:
        import ctypes

        with tempfile.TemporaryDirectory(prefix="gdo-global-lock-name-") as temporary:
            root = Path(temporary).resolve()
            expected_digest = hashlib.sha256(
                os.fsencode(str(workspace._comparison_path(root)))
            ).hexdigest()
            expected_name = "Global\\GameDesignOrchestrator-" + expected_digest

            self.assertEqual(
                expected_name,
                workspace._windows_workspace_mutex_name(root),
            )

            # This exercises CreateMutexW with the asserted name on Windows.
            # The project-wide contention test below independently proves that
            # a second process cannot bypass the same lock.
            with workspace.workspace_lock(root / "game-design", root):
                pass

            denied_kernel32 = SimpleNamespace(
                CreateMutexW=mock.Mock(return_value=0),
                WaitForSingleObject=mock.Mock(),
                ReleaseMutex=mock.Mock(),
                CloseHandle=mock.Mock(),
            )
            with (
                mock.patch.object(ctypes, "WinDLL", return_value=denied_kernel32),
                mock.patch.object(ctypes, "get_last_error", return_value=5),
                self.assertRaisesRegex(OSError, "will not fall back"),
            ):
                with workspace.workspace_lock(root / "game-design", root):
                    self.fail("a denied Global mutex fell back to another lock")
            denied_kernel32.CreateMutexW.assert_called_once_with(
                None, False, expected_name
            )

    @unittest.skipIf(os.name == "nt", "POSIX file-descriptor semantics")
    def test_posix_read_rejects_fifo_and_different_inode_swaps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-posix-read-swap-") as temporary:
            root = Path(temporary)
            target = root / "game-design"
            target.mkdir()
            source = target / "source.md"
            source.write_text("original\n", encoding="utf-8")
            parked = target / "source-original.md"
            original_open = workspace.os.open
            saw_nonblocking = False

            def fifo_swap(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
                nonlocal saw_nonblocking
                if path == source.name and kwargs.get("dir_fd") is not None:
                    saw_nonblocking = bool(
                        flags & getattr(os, "O_NONBLOCK", 0)
                    )
                    source.rename(parked)
                    os.mkfifo(source)
                return original_open(path, flags, *args, **kwargs)

            try:
                with mock.patch.object(workspace.os, "open", side_effect=fifo_swap):
                    with self.assertRaisesRegex(
                        workspace.WorkspaceError, "changed while opening"
                    ):
                        workspace.read_workspace_text(source, target, root)
                self.assertTrue(saw_nonblocking)
            finally:
                if source.exists():
                    source.unlink()
                parked.rename(source)

            replacement = target / "replacement.md"
            replacement.write_text("replacement\n", encoding="utf-8")
            swapped = False

            def inode_swap(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
                nonlocal swapped
                if (
                    not swapped
                    and path == source.name
                    and kwargs.get("dir_fd") is not None
                ):
                    source.rename(parked)
                    replacement.rename(source)
                    swapped = True
                return original_open(path, flags, *args, **kwargs)

            try:
                with mock.patch.object(workspace.os, "open", side_effect=inode_swap):
                    with self.assertRaisesRegex(
                        workspace.WorkspaceError, "changed while opening"
                    ):
                        workspace.read_workspace_text(source, target, root)
            finally:
                if swapped:
                    source.rename(replacement)
                    parked.rename(source)

    def test_read_blocks_or_detects_same_length_in_place_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-read-rewrite-") as temporary:
            root = Path(temporary)
            target = root / "game-design"
            target.mkdir()
            source = target / "source.md"
            original_text = "alpha-bravo\n"
            replacement_text = "gamma-delta\n"
            self.assertEqual(len(original_text), len(replacement_text))
            source.write_text(original_text, encoding="utf-8")
            original_metadata = source.stat()
            original_fdopen = workspace.os.fdopen
            mutation = {"blocked": False, "completed": False}

            class TwoPartMutatingReader:
                def __init__(self, descriptor: int, *args: Any, **kwargs: Any) -> None:
                    self.handle = original_fdopen(descriptor, *args, **kwargs)

                def __enter__(self) -> "TwoPartMutatingReader":
                    return self

                def __exit__(self, *args: object) -> None:
                    self.handle.close()

                def fileno(self) -> int:
                    return self.handle.fileno()

                def read(self) -> str:
                    first = self.handle.read(5)
                    if os.name != "nt":
                        # Avoid relying on a filesystem's sub-millisecond ctime
                        # granularity when proving that restoring mtime is not enough.
                        time.sleep(0.02)
                    try:
                        with source.open("r+b") as writer:
                            writer.seek(0)
                            writer.write(replacement_text.encode("utf-8"))
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
                    except OSError as exc:
                        expected_windows_denial = os.name == "nt" and (
                            isinstance(exc, PermissionError)
                            or getattr(exc, "winerror", None) in (5, 32, 33)
                        )
                        if not expected_windows_denial:
                            raise
                        mutation["blocked"] = True
                    return first + self.handle.read()

            with mock.patch.object(
                workspace.os, "fdopen", TwoPartMutatingReader
            ):
                if os.name == "nt":
                    text = workspace.read_workspace_text(source, target, root)
                    self.assertEqual(text, original_text)
                    self.assertTrue(mutation["blocked"])
                    self.assertFalse(mutation["completed"])
                else:
                    with self.assertRaisesRegex(
                        workspace.WorkspaceError, "changed while reading"
                    ):
                        workspace.read_workspace_text(source, target, root)
                    self.assertFalse(mutation["blocked"])
                    self.assertTrue(mutation["completed"])
                    self.assertEqual(
                        source.stat().st_mtime_ns,
                        original_metadata.st_mtime_ns,
                    )

            # The protective read handle is short lived: an ordinary editor can
            # write the regular file normally once read_workspace_text returns.
            with source.open("r+b") as editor:
                editor.seek(0)
                editor.write(original_text.encode("utf-8"))
                editor.truncate()
            self.assertEqual(
                workspace.read_workspace_text(source, target, root), original_text
            )

    @unittest.skipIf(os.name == "nt", "POSIX inode lease semantics")
    def test_posix_lock_lease_accepts_a_symlinked_ancestor_alias(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-posix-root-alias-") as temporary:
            parent = Path(temporary)
            canonical_parent = parent / "canonical"
            alias_parent = parent / "alias"
            canonical_parent.mkdir()
            alias_parent.symlink_to(canonical_parent, target_is_directory=True)

            root = alias_parent / "project"
            root.mkdir()
            target = root / "game-design"
            artifact = target / "alias-safe.md"

            with workspace.workspace_lock(target, root):
                self.assertTrue(
                    workspace.write_new(artifact, "alias-safe\n", target, root)
                )
                self.assertEqual(
                    workspace.read_workspace_text(artifact, target, root),
                    "alias-safe\n",
                )

            self.assertEqual(
                (canonical_parent / "project" / "game-design" / "alias-safe.md")
                .read_text(encoding="utf-8"),
                "alias-safe\n",
            )

    @unittest.skipIf(os.name == "nt", "POSIX inode lease semantics")
    def test_posix_lock_lease_rejects_a_replacement_root_before_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-posix-root-lease-") as temporary:
            parent = Path(temporary)
            root = parent / "project"
            parked = parent / "project-original"
            root.mkdir()
            target = root / "game-design"

            with workspace.workspace_lock(target, root):
                root.rename(parked)
                root.mkdir()
                try:
                    with self.assertRaisesRegex(
                        workspace.WorkspaceError, "Project root changed"
                    ):
                        workspace.write_new(
                            target / "escaped.md", "escaped\n", target, root
                        )
                    self.assertFalse((root / "game-design" / "escaped.md").exists())
                finally:
                    root.rmdir()
                    parked.rename(root)

            try:
                with self.assertRaisesRegex(
                    workspace.WorkspaceError, "lock root changed"
                ):
                    with workspace.workspace_lock(target, root):
                        root.rename(parked)
                        root.mkdir()
            finally:
                if parked.exists():
                    root.rmdir()
                    parked.rename(root)

    def test_lock_scope_is_project_root_wide(self) -> None:
        holder = r"""
import sys
import time
from pathlib import Path

scripts = Path(sys.argv[1])
root = Path(sys.argv[2])
target = Path(sys.argv[3])
ready = Path(sys.argv[4])
release = Path(sys.argv[5])
sys.path.insert(0, str(scripts))
from workspace import workspace_lock

with workspace_lock(target, root):
    ready.write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 10
    while not release.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("release gate timed out")
        time.sleep(0.01)
"""
        with tempfile.TemporaryDirectory(prefix="gdo-project-lock-scope-") as temporary:
            root = Path(temporary)
            first_target = root / "design-alpha"
            second_target = root / "design-beta"
            ready = root / "holder.ready"
            release = root / "holder.release"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    holder,
                    str(SCRIPTS_ROOT),
                    str(root),
                    str(first_target),
                    str(ready),
                    str(release),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            deadline = time.monotonic() + 10
            while not ready.exists():
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    self.fail(stdout + stderr)
                if time.monotonic() >= deadline:
                    process.kill()
                    self.fail("holder did not acquire the project-wide lock")
                time.sleep(0.01)
            try:
                with self.assertRaisesRegex(
                    workspace.WorkspaceError, "Timed out"
                ):
                    with workspace.workspace_lock(
                        second_target, root, timeout=0.05
                    ):
                        self.fail("alternate workspace bypassed the project lock")
            finally:
                release.touch()
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(0, process.returncode, stdout + stderr)

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_junction_ancestors_stop_all_mutations_before_external_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-junction-static-") as temporary:
            parent = Path(temporary)
            root = parent / "root"
            outside = parent / "outside"
            root.mkdir()
            outside.mkdir()
            initialize(root)
            target = root / "game-design"
            outside.joinpath("forged.md").write_text(
                "<!-- gdo:artifact type=decision id=DEC-999 -->\n",
                encoding="utf-8",
            )
            outside.joinpath("state.json").write_bytes(
                (target / ".gdo" / "state.json").read_bytes()
            )
            outside_before = tree_snapshot(outside)

            gdo = target / ".gdo"
            parked_gdo = target / "parked-gdo"
            gdo.rename(parked_gdo)
            create_junction(gdo, outside)
            try:
                with self.assertRaises(workspace.WorkspaceError):
                    workspace.load_state(target, root)
            finally:
                restore_swapped_directory(gdo, parked_gdo)

            junction = target / "unsafe"
            create_junction(junction, outside)
            try:
                with self.assertRaises(workspace.WorkspaceError):
                    workspace.collect_defined_ids(target, root)
                with self.assertRaises(workspace.WorkspaceError):
                    workspace.write_new(
                        junction / "write-new.md", "escaped\n", target, root
                    )
                with self.assertRaises(workspace.WorkspaceError):
                    workspace.atomic_write(
                        junction / "atomic.md", "escaped\n", target, root
                    )
            finally:
                os.rmdir(junction)

            parked_target = root / "parked-game-design"
            target.rename(parked_target)
            create_junction(target, outside)
            decision_args = SimpleNamespace(
                name="Must stay inside",
                option=[],
                choice=None,
                context=None,
                reason=None,
                impact=None,
                revisit=None,
            )
            try:
                with self.assertRaises(workspace.WorkspaceError):
                    creator.append_record(
                        kind="decision",
                        artifact_id="DEC-001",
                        args=decision_args,
                        target=target,
                        coordination_root=root,
                        project_name="Junction Test",
                        today="2026-08-26",
                    )
            finally:
                restore_swapped_directory(target, parked_target)

            self.assertEqual(outside_before, tree_snapshot(outside))

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_open_ancestor_handles_block_active_junction_swaps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-junction-active-") as temporary:
            parent = Path(temporary)
            root = parent / "root"
            outside = parent / "outside"
            root.mkdir()
            outside.mkdir()
            initialize(root)
            target = root / "game-design"

            def attempt_swap(
                directory: Path, parked: Path, attempts: list[OSError | None]
            ) -> None:
                try:
                    directory.rename(parked)
                except OSError as exc:
                    attempts.append(exc)
                    return
                attempts.append(None)
                create_junction(directory, outside)

            write_parent = target / "write-parent"
            write_parent.mkdir()
            write_path = write_parent / "created.md"
            parked_write = target / "parked-write-parent"
            write_attempts: list[OSError | None] = []
            original_path_open = Path.open

            def canonical_candidate(path: Path) -> Path:
                """Canonicalize an existing parent while allowing a new leaf."""

                parent = workspace._canonical_existing_path(path.parent)
                return Path(
                    workspace._normalized_path_text(
                        os.fspath(parent / path.name)
                    )
                )

            def hooked_path_open(path: Path, *args: object, **kwargs: object) -> Any:
                if (
                    canonical_candidate(path) == canonical_candidate(write_path)
                    and not write_attempts
                ):
                    attempt_swap(write_parent, parked_write, write_attempts)
                return original_path_open(path, *args, **kwargs)

            try:
                with mock.patch.object(Path, "open", hooked_path_open):
                    self.assertTrue(
                        workspace.write_new(write_path, "inside\n", target, root)
                    )
            finally:
                restore_swapped_directory(write_parent, parked_write)

            atomic_parent = target / "atomic-parent"
            atomic_parent.mkdir()
            atomic_path = atomic_parent / "state.md"
            parked_atomic = target / "parked-atomic-parent"
            atomic_attempts: list[OSError | None] = []
            original_mkstemp = workspace.tempfile.mkstemp

            def hooked_atomic_mkstemp(*args: object, **kwargs: object) -> Any:
                if not atomic_attempts:
                    attempt_swap(atomic_parent, parked_atomic, atomic_attempts)
                return original_mkstemp(*args, **kwargs)

            try:
                with mock.patch.object(
                    workspace.tempfile, "mkstemp", hooked_atomic_mkstemp
                ):
                    workspace.atomic_write(atomic_path, "inside\n", target, root)
            finally:
                restore_swapped_directory(atomic_parent, parked_atomic)

            parked_target = root / "parked-game-design"
            append_attempts: list[OSError | None] = []
            decision_args = SimpleNamespace(
                name="Held append",
                option=[],
                choice=None,
                context=None,
                reason=None,
                impact=None,
                revisit=None,
            )

            def hooked_append_mkstemp(*args: object, **kwargs: object) -> Any:
                if not append_attempts:
                    attempt_swap(target, parked_target, append_attempts)
                return original_mkstemp(*args, **kwargs)

            try:
                with mock.patch.object(
                    workspace.tempfile, "mkstemp", hooked_append_mkstemp
                ):
                    creator.append_record(
                        kind="decision",
                        artifact_id="DEC-001",
                        args=decision_args,
                        target=target,
                        coordination_root=root,
                        project_name="Junction Test",
                        today="2026-08-26",
                    )
            finally:
                restore_swapped_directory(target, parked_target)

            for attempts in (write_attempts, atomic_attempts, append_attempts):
                self.assertEqual(1, len(attempts))
                self.assertIsInstance(attempts[0], PermissionError)
                self.assertEqual(32, getattr(attempts[0], "winerror", None))
            self.assertEqual("inside\n", write_path.read_text(encoding="utf-8"))
            self.assertEqual("inside\n", atomic_path.read_text(encoding="utf-8"))
            self.assertTrue((target / "decisions.md").is_file())
            self.assertEqual({}, tree_snapshot(outside))

    def test_concurrent_initializers_never_mix_project_names(self) -> None:
        wrapper = """
import runpy
import sys
import time
from pathlib import Path

gate = Path(sys.argv[1])
script = sys.argv[2]
arguments = sys.argv[3:]
sys.path.insert(0, str(Path(script).parent))
deadline = time.monotonic() + 10
while not gate.exists():
    if time.monotonic() >= deadline:
        raise SystemExit("start gate timed out")
    time.sleep(0.001)
sys.argv = [script, *arguments]
runpy.run_path(script, run_name="__main__")
"""
        with tempfile.TemporaryDirectory(prefix="gdo-concurrent-init-") as temporary:
            parent = Path(temporary)
            for index in range(16):
                root = parent / f"case-{index:02d}"
                root.mkdir()
                gate = root / "start.gate"
                processes = [
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-c",
                            wrapper,
                            str(gate),
                            str(INIT_SCRIPT),
                            "--root",
                            str(root),
                            "--name",
                            name,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                    )
                    for name in ("Alpha", "Beta")
                ]
                gate.touch()
                results = [process.communicate(timeout=20) for process in processes]
                for process, (stdout, stderr) in zip(processes, results):
                    self.assertEqual(0, process.returncode, stdout + stderr)

                target = root / "game-design"
                state = json.loads(
                    (target / ".gdo" / "state.json").read_text(encoding="utf-8")
                )
                project_name = state["project_name"]
                self.assertIn(project_name, {"Alpha", "Beta"})
                design = (target / "design.md").read_text(encoding="utf-8")
                self.assertTrue(
                    design.startswith(f"# {project_name} - Game Design\n"),
                    f"state={project_name!r}, design={design.splitlines()[0]!r}",
                )
                self.assertFalse((target / ".gdo" / "workspace.lock").exists())

    def test_snapshot_locks_before_the_first_initializer_mutation(self) -> None:
        init_wrapper = r"""
import sys
import time
from contextlib import contextmanager
from pathlib import Path

scripts = Path(sys.argv[1])
root = Path(sys.argv[2])
ready = Path(sys.argv[3])
release = Path(sys.argv[4])
sys.path.insert(0, str(scripts))
import init_design_project as init

real_lock = init.workspace_lock

@contextmanager
def gated_lock(target, coordination_root, timeout=15.0):
    with real_lock(target, coordination_root, timeout):
        ready.write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 10
        while not release.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("initializer release gate timed out")
            time.sleep(0.01)
        yield

init.workspace_lock = gated_lock
sys.argv = [
    str(scripts / "init_design_project.py"),
    "--root",
    str(root),
    "--name",
    "First Init Race",
]
raise SystemExit(init.main())
"""
        snapshot_wrapper = r"""
import sys
from contextlib import contextmanager
from pathlib import Path

scripts = Path(sys.argv[1])
root = Path(sys.argv[2])
reached = Path(sys.argv[3])
sys.path.insert(0, str(scripts))
import snapshot_design_workspace as snapshot

real_lock = snapshot.workspace_lock

@contextmanager
def observed_lock(target, coordination_root, timeout=15.0):
    reached.write_text("reached", encoding="utf-8")
    with real_lock(target, coordination_root, timeout):
        yield

snapshot.workspace_lock = observed_lock
sys.argv = [
    str(scripts / "snapshot_design_workspace.py"),
    "--root",
    str(root),
    "--json",
]
raise SystemExit(snapshot.main())
"""
        with tempfile.TemporaryDirectory(prefix="gdo-first-init-lock-") as temporary:
            root = Path(temporary)
            target = root / "game-design"
            init_ready = root / "init.ready"
            init_release = root / "init.release"
            snapshot_reached = root / "snapshot.reached"
            initializer = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    init_wrapper,
                    str(SCRIPTS_ROOT),
                    str(root),
                    str(init_ready),
                    str(init_release),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            deadline = time.monotonic() + 10
            while not init_ready.exists():
                if initializer.poll() is not None:
                    stdout, stderr = initializer.communicate()
                    self.fail(stdout + stderr)
                if time.monotonic() >= deadline:
                    initializer.kill()
                    self.fail("initializer did not acquire the pre-mutation lock")
                time.sleep(0.01)
            self.assertFalse(target.exists())

            snapshot = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    snapshot_wrapper,
                    str(SCRIPTS_ROOT),
                    str(root),
                    str(snapshot_reached),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            try:
                while not snapshot_reached.exists() and snapshot.poll() is None:
                    if time.monotonic() >= deadline:
                        self.fail("snapshot did not reach workspace coordination")
                    time.sleep(0.01)
                self.assertTrue(snapshot_reached.exists())
                self.assertIsNone(snapshot.poll())
            finally:
                init_release.touch()

            init_stdout, init_stderr = initializer.communicate(timeout=10)
            snap_stdout, snap_stderr = snapshot.communicate(timeout=10)
            self.assertEqual(0, initializer.returncode, init_stdout + init_stderr)
            self.assertEqual(0, snapshot.returncode, snap_stdout + snap_stderr)
            report = json.loads(snap_stdout)
            self.assertEqual("First Init Race", report["project_name"])
            self.assertTrue(report["design"]["included"])
            self.assertEqual([], report["validation"]["errors"])

    def test_invalid_state_does_not_leave_a_lock_artifact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-invalid-state-") as temporary:
            root = Path(temporary)
            initialize(root)
            target = root / "game-design"
            state_path = target / ".gdo" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["contract_id"] = "not-the-official-contract"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            before = tree_snapshot(root)

            result = run_python(
                CREATE_SCRIPT,
                "--root",
                root,
                "test",
                "--name",
                "Must not exist",
                check=False,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("contract_id", result.stderr)
            self.assertEqual(before, tree_snapshot(root))
            self.assertFalse((target / ".gdo" / "workspace.lock").exists())

    def test_non_utf8_state_fails_cleanly_without_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-invalid-utf8-") as temporary:
            root = Path(temporary)
            initialize(root)
            target = root / "game-design"
            state_path = target / ".gdo" / "state.json"
            state_path.write_bytes(b"\xff\xfe\x00")
            before = tree_snapshot(root)

            result = run_python(
                CREATE_SCRIPT,
                "--root",
                root,
                "test",
                "--name",
                "Must not exist",
                check=False,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("not valid UTF-8", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(before, tree_snapshot(root))

    def test_explicit_011_migration_preserves_contract_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-migrate-011-") as temporary:
            root = Path(temporary)
            initialize(root, "Migration Game")
            target = root / "game-design"
            state_path = target / ".gdo" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["tool_version"] = "0.1.1-beta"
            state["next_ids"]["system"] = 8
            state_path.write_text(json.dumps(state), encoding="utf-8")
            blocked_state = state_path.read_bytes()

            blocked = run_python(INIT_SCRIPT, "--root", root, check=False)
            self.assertNotEqual(0, blocked.returncode)
            self.assertIn("--migrate", blocked.stderr)
            self.assertEqual(blocked_state, state_path.read_bytes())

            result = payload(
                run_python(INIT_SCRIPT, "--root", root, "--migrate")
            )
            self.assertTrue(result["migrated"])
            migrated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(VERSION, migrated["tool_version"])
            self.assertEqual(CONTRACT_ID, migrated["contract_id"])
            self.assertEqual(8, migrated["next_ids"]["system"])
            self.assertEqual(
                "0.1.1-beta", migrated["migration"]["from_tool_version"]
            )

        with tempfile.TemporaryDirectory(prefix="gdo-migrate-chain-") as temporary:
            root = Path(temporary)
            initialize(root)
            state_path = root / "game-design" / ".gdo" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            provenance = {
                "from_tool_version": "0.1.0-beta",
                "migrated_at": "2026-08-25",
            }
            state["tool_version"] = "0.1.1-beta"
            state["migration"] = provenance
            state_path.write_text(json.dumps(state), encoding="utf-8")

            run_python(INIT_SCRIPT, "--root", root, "--migrate")
            migrated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(provenance, migrated["migration"])
            self.assertEqual(CONTRACT_ID, migrated["contract_id"])

    def test_migration_rejects_modified_011_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-migrate-invalid-") as temporary:
            root = Path(temporary)
            initialize(root)
            state_path = root / "game-design" / ".gdo" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["tool_version"] = "0.1.1-beta"
            state["unexpected"] = "not official"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            before = state_path.read_bytes()

            result = run_python(
                INIT_SCRIPT, "--root", root, "--migrate", check=False
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("unmodified official", result.stderr)
            self.assertEqual(before, state_path.read_bytes())

            del state["unexpected"]
            state["migration"] = {
                "from_tool_version": "0.1.0-beta",
                "migrated_at": "not-an-official-date",
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            before = state_path.read_bytes()
            result = run_python(
                INIT_SCRIPT, "--root", root, "--migrate", check=False
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("provenance", result.stderr)
            self.assertEqual(before, state_path.read_bytes())

            state["migration"]["migrated_at"] = "2026-08-25"
            state["updated_at"] = "not-an-official-date"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            before = state_path.read_bytes()
            result = run_python(
                INIT_SCRIPT, "--root", root, "--migrate", check=False
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("updated_at", result.stderr)
            self.assertEqual(before, state_path.read_bytes())

    def test_snapshot_requires_adjacent_markers_outside_fences(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-snapshot-marker-") as temporary:
            root = Path(temporary)
            initialize(root)
            real = payload(
                run_python(
                    CREATE_SCRIPT,
                    "--root",
                    root,
                    "decision",
                    "--name",
                    "Real choice",
                )
            )
            design_path = root / "game-design" / "design.md"
            design_path.write_text(
                design_path.read_text(encoding="utf-8")
                + """

```markdown
## DEC-000 - Forged in code

<!-- gdo:artifact type=decision id=DEC-000 -->

- Status: DECIDED
```

## REF-000 - Detached heading

This paragraph prevents marker adjacency.

<!-- gdo:artifact type=reference id=REF-000 -->
""",
                encoding="utf-8",
            )

            report = payload(
                run_python(SNAPSHOT_SCRIPT, "--root", root, "--json")
            )
            ids = {artifact["id"] for artifact in report["artifacts"]}
            self.assertIn(real["id"], ids)
            self.assertNotIn("DEC-000", ids)
            self.assertNotIn("REF-000", ids)

    def test_snapshot_and_checker_wait_for_writer_and_remain_read_only(self) -> None:
        holder = r"""
import sys
import time
from pathlib import Path

scripts = Path(sys.argv[1])
target = Path(sys.argv[2])
root = Path(sys.argv[3])
ready = Path(sys.argv[4])
release = Path(sys.argv[5])
sys.path.insert(0, str(scripts))
from workspace import atomic_json, load_state, workspace_lock, write_new

with workspace_lock(target, root):
    artifact = target / "tests" / "TST-001-concurrent.md"
    write_new(
        artifact,
        "<!-- gdo:artifact type=test id=TST-001 -->\n\n"
        "# TST-001 - Concurrent write\n\n"
        "- Status: PLANNED\n- Updated: 2026-08-26\n",
        target,
        root,
    )
    ready.write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 10
    while not release.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("writer release gate timed out")
        time.sleep(0.01)
    state = load_state(target, root)
    state["next_ids"]["test"] = 2
    atomic_json(target / ".gdo" / "state.json", state, target, root)
"""
        reader = r"""
import sys
from contextlib import contextmanager
from pathlib import Path

scripts = Path(sys.argv[1])
module_name = sys.argv[2]
reached = Path(sys.argv[3])
arguments = sys.argv[4:]
sys.path.insert(0, str(scripts))
module = __import__(module_name)
real_lock = module.workspace_lock

@contextmanager
def observed_lock(target, coordination_root, timeout=15.0):
    reached.write_text("reached", encoding="utf-8")
    with real_lock(target, coordination_root, timeout):
        yield

module.workspace_lock = observed_lock
sys.argv = [str(Path(module.__file__)), *arguments]
raise SystemExit(module.main())
"""
        with tempfile.TemporaryDirectory(prefix="gdo-snapshot-lock-") as temporary:
            root = Path(temporary)
            initialize(root)
            target = root / "game-design"
            ready = root / "writer.ready"
            release = root / "writer.release"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    holder,
                    str(SCRIPTS_ROOT),
                    str(target),
                    str(root),
                    str(ready),
                    str(release),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            deadline = time.monotonic() + 10
            while not ready.exists():
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    self.fail(stdout + stderr)
                if time.monotonic() >= deadline:
                    process.kill()
                    self.fail("writer did not reach its locked transition")
                time.sleep(0.01)

            snapshot_reached = root / "snapshot.reached"
            checker_reached = root / "checker.reached"
            readers = {
                "snapshot": subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        reader,
                        str(SCRIPTS_ROOT),
                        "snapshot_design_workspace",
                        str(snapshot_reached),
                        "--root",
                        str(root),
                        "--json",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                ),
                "checker": subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        reader,
                        str(SCRIPTS_ROOT),
                        "check_design_workspace",
                        str(checker_reached),
                        "--root",
                        str(root),
                        "--json",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                ),
            }
            deadline = time.monotonic() + 10
            reached = (snapshot_reached, checker_reached)
            try:
                while not all(path.exists() for path in reached):
                    stopped = [
                        name for name, reader_process in readers.items()
                        if reader_process.poll() is not None
                    ]
                    if stopped:
                        self.fail(f"reader(s) bypassed or failed before lock: {stopped}")
                    if time.monotonic() >= deadline:
                        self.fail("readers did not reach workspace coordination")
                    time.sleep(0.01)
                for reader_process in readers.values():
                    self.assertIsNone(reader_process.poll())
            finally:
                release.touch()

            writer_stdout, writer_stderr = process.communicate(timeout=10)
            outputs = {
                name: reader_process.communicate(timeout=10)
                for name, reader_process in readers.items()
            }
            self.assertEqual(0, process.returncode, writer_stdout + writer_stderr)
            for name, reader_process in readers.items():
                stdout, stderr = outputs[name]
                self.assertEqual(0, reader_process.returncode, stdout + stderr)

            report = json.loads(outputs["snapshot"][0])
            self.assertEqual(0, len(report["validation"]["errors"]))
            self.assertEqual(1, report["counts"]["test"])
            checker = json.loads(outputs["checker"][0])
            self.assertEqual(0, checker["summary"]["errors"])
            self.assertFalse((target / ".gdo" / "workspace.lock").exists())

            before = tree_snapshot(root)
            run_python(SNAPSHOT_SCRIPT, "--root", root, "--json")
            self.assertEqual(before, tree_snapshot(root))

    def test_shortened_markdown_labels_the_design_as_shortened(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gdo-snapshot-short-") as temporary:
            root = Path(temporary)
            initialize(root)
            result = run_python(
                SNAPSHOT_SCRIPT,
                "--root",
                root,
                "--max-design-chars",
                8,
            )
            self.assertIn("## Current Design (shortened)", result.stdout)


if __name__ == "__main__":
    unittest.main()
