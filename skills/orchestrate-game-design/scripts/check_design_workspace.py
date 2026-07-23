#!/usr/bin/env python3
"""Check mechanical workspace problems without judging the game design."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote

try:
    from .workspace import (
        DEFAULT_DIRECTORY,
        ID_PATTERN,
        KINDS,
        MARKER_PATTERN,
        PREFIXES,
        PREFIX_TO_KIND,
        VERSION,
        WorkspaceError,
        id_number,
        load_state,
        legacy_workspace_indicators,
        require_contained,
        resolve_target,
    )
except ImportError:
    from workspace import (
        DEFAULT_DIRECTORY,
        ID_PATTERN,
        KINDS,
        MARKER_PATTERN,
        PREFIXES,
        PREFIX_TO_KIND,
        VERSION,
        WorkspaceError,
        id_number,
        load_state,
        legacy_workspace_indicators,
        require_contained,
        resolve_target,
    )


LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
TOKEN_PATTERN = re.compile(r"{{[^{}\n]+}}")
PLACEHOLDER_PATTERN = re.compile(
    r"\b(?:TBD|TODO|FIXME|PLACEHOLDER)\b|"
    r"\[(?:fill|write|describe|add)[^\]]*\]",
    re.IGNORECASE,
)
FILENAME_PATTERN = re.compile(r"^(DEC|REF|TST|SYS|REV)-(\d{3,})-.+\.md$")


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str


class Doctor:
    def __init__(self, target: Path) -> None:
        self.target = target
        self.errors: list[Issue] = []
        self.warnings: list[Issue] = []
        self.files_checked = 0
        self.state: dict[str, object] | None = None
        self.text_by_path: dict[Path, str] = {}
        self.definitions: dict[str, list[Path]] = {}
        self.legacy = False

    def add_error(self, code: str, path: Path, message: str) -> None:
        self.errors.append(Issue(code, self.display(path), message))

    def add_warning(self, code: str, path: Path, message: str) -> None:
        self.warnings.append(Issue(code, self.display(path), message))

    def display(self, path: Path) -> str:
        try:
            return path.relative_to(self.target).as_posix()
        except ValueError:
            return str(path)

    def check_structure(self) -> None:
        if not self.target.is_dir():
            self.add_error(
                "missing-workspace",
                self.target,
                "Run init_design_project.py before using this command.",
            )
            return
        indicators = legacy_workspace_indicators(self.target)
        if indicators:
            self.legacy = True
            self.add_error(
                "legacy-workspace-format",
                self.target,
                "Incompatible legacy governance workspace detected: "
                + ", ".join(indicators)
                + ". Do not repair it with this checker; import only useful creative "
                "content into a clean reader-facing workspace.",
            )
            return
        design = self.target / "design.md"
        if not design.is_file():
            self.add_error("missing-design", design, "Required design.md is missing.")
        gitignore = self.target / ".gitignore"
        if not gitignore.is_file():
            self.add_warning(
                "missing-gitignore",
                gitignore,
                "Runtime lock and temporary files may be committed accidentally.",
            )
        state_path = self.target / ".gdo" / "state.json"
        try:
            self.state = load_state(self.target)
        except WorkspaceError as exc:
            self.add_error("invalid-state", state_path, str(exc))

    def read_markdown(self) -> None:
        if not self.target.is_dir():
            return
        for path in sorted(self.target.rglob("*.md")):
            if ".gdo" in path.parts:
                continue
            try:
                require_contained(path, self.target)
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError, WorkspaceError) as exc:
                self.add_error("unreadable-markdown", path, str(exc))
                continue
            self.files_checked += 1
            self.text_by_path[path] = text

    def check_tokens(self) -> None:
        for path, text in self.text_by_path.items():
            tokens = sorted(set(TOKEN_PATTERN.findall(text)))
            if tokens:
                self.add_error(
                    "unresolved-template-token",
                    path,
                    "Unresolved token(s): " + ", ".join(tokens),
                )
            if PLACEHOLDER_PATTERN.search(text):
                self.add_warning(
                    "unfinished-placeholder",
                    path,
                    "This file still contains an obvious drafting placeholder.",
                )

    def collect_definitions(self) -> None:
        for path, text in self.text_by_path.items():
            markers = list(MARKER_PATTERN.finditer(text))
            marker_ids: set[str] = set()
            for match in markers:
                kind = match.group(1)
                artifact_id = match.group(2)
                marker_ids.add(artifact_id)
                prefix = artifact_id.split("-", 1)[0]
                if PREFIX_TO_KIND[prefix] != kind:
                    self.add_error(
                        "marker-type-mismatch",
                        path,
                        f"{artifact_id} cannot define artifact type {kind}.",
                    )
                self.definitions.setdefault(artifact_id, []).append(path)

            filename_match = FILENAME_PATTERN.fullmatch(path.name)
            if filename_match:
                artifact_id = (
                    f"{filename_match.group(1)}-{filename_match.group(2)}"
                )
                if artifact_id not in marker_ids:
                    self.definitions.setdefault(artifact_id, []).append(path)
                    self.add_warning(
                        "missing-artifact-marker",
                        path,
                        f"{artifact_id} is inferred from the filename; restore its marker.",
                    )

        for artifact_id, paths in sorted(self.definitions.items()):
            if len(paths) > 1:
                locations = ", ".join(self.display(path) for path in paths)
                self.add_error(
                    "duplicate-artifact-id",
                    paths[0],
                    f"{artifact_id} is defined more than once: {locations}.",
                )

    def check_references(self) -> None:
        defined = set(self.definitions)
        for path, text in self.text_by_path.items():
            for match in ID_PATTERN.finditer(text):
                artifact_id = match.group(0)
                if artifact_id not in defined:
                    self.add_error(
                        "broken-artifact-reference",
                        path,
                        f"{artifact_id} has no artifact definition.",
                    )

            for match in LINK_PATTERN.finditer(text):
                raw = match.group(1).strip("<>")
                if raw.startswith(("#", "http://", "https://", "mailto:")):
                    continue
                link_path = unquote(raw.split("#", 1)[0])
                if not link_path:
                    continue
                if not (
                    link_path.casefold().endswith(".md")
                    or ID_PATTERN.search(link_path)
                ):
                    continue
                relative = Path(link_path.replace("/", os.sep))
                if relative.is_absolute():
                    self.add_error(
                        "unsafe-local-link",
                        path,
                        f"Local artifact link must be relative: {raw}.",
                    )
                    continue
                candidate = path.parent / relative
                try:
                    require_contained(candidate, self.target)
                except WorkspaceError:
                    self.add_error(
                        "unsafe-local-link",
                        path,
                        f"Local artifact link escapes game-design: {raw}.",
                    )
                    continue
                if not candidate.is_file():
                    self.add_error(
                        "broken-local-link",
                        path,
                        f"Linked artifact does not exist: {raw}.",
                    )

    def check_state_counters(self) -> None:
        if self.state is None:
            return
        next_ids = self.state["next_ids"]
        assert isinstance(next_ids, dict)
        maximums = {kind: 0 for kind in KINDS}
        for artifact_id in self.definitions:
            prefix = artifact_id.split("-", 1)[0]
            kind = PREFIX_TO_KIND[prefix]
            maximums[kind] = max(maximums[kind], id_number(artifact_id))
        for kind in KINDS:
            next_number = next_ids[kind]
            assert isinstance(next_number, int)
            if next_number <= maximums[kind]:
                self.add_error(
                    "stale-id-counter",
                    self.target / ".gdo" / "state.json",
                    (
                        f"next_ids.{kind} is {next_number}, but "
                        f"{PREFIXES[kind]}-{maximums[kind]:03d} already exists."
                    ),
                )
        tool_version = self.state.get("tool_version")
        if tool_version != VERSION:
            self.add_warning(
                "older-tool-version",
                self.target / ".gdo" / "state.json",
                f"Workspace records {tool_version!r}; current tool is {VERSION}.",
            )

    def run(self) -> None:
        self.check_structure()
        if self.legacy:
            return
        self.read_markdown()
        self.check_tokens()
        self.collect_definitions()
        self.check_references()
        self.check_state_counters()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check workspace mechanics without rating the design."
    )
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root.")
    parser.add_argument(
        "--directory", default=DEFAULT_DIRECTORY, help="Design workspace directory."
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    try:
        target = resolve_target(args.root, args.directory)
    except WorkspaceError as exc:
        raise SystemExit(str(exc)) from exc
    doctor = Doctor(target)
    doctor.run()
    payload = {
        "target": str(target),
        "version": VERSION,
        "summary": {
            "errors": len(doctor.errors),
            "warnings": len(doctor.warnings),
            "files_checked": doctor.files_checked,
        },
        "errors": [asdict(issue) for issue in doctor.errors],
        "warnings": [asdict(issue) for issue in doctor.warnings],
        "note": "Workspace mechanics only; design quality remains a human judgment.",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        summary = payload["summary"]
        print(
            f"Workspace check: {summary['errors']} error(s), "
            f"{summary['warnings']} warning(s), "
            f"{summary['files_checked']} Markdown file(s)."
        )
        for label, issues in (("ERROR", doctor.errors), ("WARNING", doctor.warnings)):
            for issue in issues:
                print(f"[{label}] {issue.code} {issue.path}: {issue.message}")
        print(payload["note"])
    return 1 if doctor.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
