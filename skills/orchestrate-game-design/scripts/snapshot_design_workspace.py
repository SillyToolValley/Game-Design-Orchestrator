#!/usr/bin/env python3
"""Emit a read-only, portable context snapshot of one GDO workspace."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

try:
    from .check_design_workspace import Doctor
    from .workspace import (
        DEFAULT_DIRECTORY,
        ID_PATTERN,
        KINDS,
        MARKER_PATTERN,
        PREFIX_TO_KIND,
        VERSION,
        WorkspaceError,
        id_number,
        resolve_root,
        resolve_target,
        workspace_lock,
    )
except ImportError:
    from check_design_workspace import Doctor
    from workspace import (
        DEFAULT_DIRECTORY,
        ID_PATTERN,
        KINDS,
        MARKER_PATTERN,
        PREFIX_TO_KIND,
        VERSION,
        WorkspaceError,
        id_number,
        resolve_root,
        resolve_target,
        workspace_lock,
    )


ARTIFACT_HEADING_PATTERN = re.compile(
    r"^#{1,2}\s+((?:DEC|REF|TST|SYS|REV)-\d{3,})\s+-\s+(.+?)\s*$"
)
METADATA_PATTERN = re.compile(r"^-\s+([^:\n]+):\s*(.*?)\s*$")
FENCE_OPEN_PATTERN = re.compile(r"^(`{3,}|~{3,}).*$")
KIND_ORDER = {kind: index for index, kind in enumerate(KINDS)}


def bounded_text(value: str, limit: int) -> tuple[str, bool]:
    """Keep both the beginning and end when an explicit context limit is used."""

    if limit <= 0 or len(value) <= limit:
        return value, False
    marker = "\n\n[... design snapshot shortened by --max-design-chars ...]\n\n"
    if limit <= len(marker) + 2:
        return value[:limit], True
    remaining = limit - len(marker)
    head = (remaining + 1) // 2
    tail = remaining // 2
    return value[:head].rstrip() + marker + value[-tail:].lstrip(), True


def metadata(section: str) -> dict[str, str]:
    """Read only the leading artifact metadata block, never body lookalikes."""

    lines = section.splitlines()
    if not lines:
        return {}
    index = 1
    while index < len(lines) and (
        not lines[index].strip()
        or MARKER_PATTERN.fullmatch(lines[index].strip())
    ):
        index += 1

    fields: dict[str, str] = {}
    while index < len(lines):
        match = METADATA_PATTERN.fullmatch(lines[index])
        if match is None:
            break
        key = match.group(1).strip().casefold()
        if key not in fields:
            fields[key] = match.group(2).strip()
        index += 1
    return fields


def display_status(kind: str, value: str | None) -> str:
    if value and "|" not in value:
        return value
    if kind == "reference":
        return "RECORDED"
    return "UNSET"


def structural_line(line: str) -> str | None:
    """Return a Markdown block line only when it is not code-indented."""

    if line.startswith("\t"):
        return None
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return None
    return stripped.rstrip()


def visible_markdown_lines(lines: list[str]) -> list[bool]:
    """Mark lines outside fenced code blocks for structural parsing."""

    visible: list[bool] = []
    fence_character: str | None = None
    fence_width = 0
    for line in lines:
        structural = structural_line(line)
        if fence_character is not None:
            visible.append(False)
            if structural is not None and re.fullmatch(
                re.escape(fence_character) + "{" + str(fence_width) + ",}[ \\t]*",
                structural,
            ):
                fence_character = None
                fence_width = 0
            continue

        opening = FENCE_OPEN_PATTERN.fullmatch(structural or "")
        if opening is not None:
            fence = opening.group(1)
            fence_character = fence[0]
            fence_width = len(fence)
            visible.append(False)
        else:
            visible.append(True)
    return visible


def artifact_sections(text: str) -> list[dict[str, str]]:
    """Bind artifact sections to adjacent, non-code marker/heading pairs."""

    lines = text.splitlines()
    visible = visible_markdown_lines(lines)
    headings: dict[int, re.Match[str]] = {}
    markers: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        if not visible[index]:
            continue
        structural = structural_line(line)
        if structural is None:
            continue
        heading = ARTIFACT_HEADING_PATTERN.fullmatch(structural)
        if heading is not None:
            headings[index] = heading
        marker = MARKER_PATTERN.fullmatch(structural)
        if marker is not None:
            markers.append((index, marker))

    pairs: list[tuple[int, int, re.Match[str], re.Match[str]]] = []
    paired_headings: set[int] = set()
    for marker_index, marker in markers:
        neighbors: list[int] = []
        for step in (-1, 1):
            cursor = marker_index + step
            while 0 <= cursor < len(lines) and not lines[cursor].strip():
                cursor += step
            neighbors.append(cursor)
        for heading_index in neighbors:
            heading = headings.get(heading_index)
            if (
                heading is not None
                and heading.group(1) == marker.group(2)
                and heading_index not in paired_headings
            ):
                pairs.append((min(marker_index, heading_index), heading_index, heading, marker))
                paired_headings.add(heading_index)
                break

    pairs.sort(key=lambda pair: pair[0])
    sections: list[dict[str, str]] = []
    for index, (_boundary, heading_index, heading, marker) in enumerate(pairs):
        end = pairs[index + 1][0] if index + 1 < len(pairs) else len(lines)
        raw_section = "\n".join(lines[heading_index:end])
        visible_section = "\n".join(
            line if visible[line_index] else ""
            for line_index, line in enumerate(
                lines[heading_index:end], start=heading_index
            )
        )
        sections.append(
            {
                "id": marker.group(2),
                "marker_kind": marker.group(1),
                "name": heading.group(2).strip(),
                "raw": raw_section,
                "visible": visible_section,
            }
        )
    return sections


def artifact_inventory(doctor: Doctor) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(doctor.text_by_path):
        text = doctor.text_by_path[path]
        for section in artifact_sections(text):
            artifact_id = section["id"]
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            fields = metadata(section["raw"])
            prefix = artifact_id.split("-", 1)[0]
            kind = PREFIX_TO_KIND[prefix]
            status = display_status(kind, fields.get("status"))
            changed = fields.get("updated") or fields.get("date") or "Unknown"
            references = sorted(
                {
                    match.group(0)
                    for match in ID_PATTERN.finditer(section["visible"])
                    if match.group(0) != artifact_id
                }
            )
            artifacts.append(
                {
                    "id": artifact_id,
                    "kind": kind,
                    "name": section["name"],
                    "status": status,
                    "updated": changed,
                    "path": doctor.display(path),
                    "references": references,
                }
            )
    return sorted(
        artifacts,
        key=lambda artifact: (
            KIND_ORDER[artifact["kind"]],
            id_number(artifact["id"]),
        ),
    )


def _build_payload_unlocked(
    target: Path,
    coordination_root: Path,
    *,
    include_design: bool,
    max_design_chars: int,
) -> tuple[dict[str, Any], Doctor]:
    doctor = Doctor(target, coordination_root)
    doctor.run()
    artifacts = artifact_inventory(doctor)
    state = doctor.state or {}
    design_path = target / "design.md"
    design_text = doctor.text_by_path.get(design_path)
    shortened = False
    if design_text is not None and include_design:
        design_text, shortened = bounded_text(design_text, max_design_chars)
    elif not include_design:
        design_text = None

    counts = {kind: 0 for kind in KINDS}
    for artifact in artifacts:
        counts[artifact["kind"]] += 1

    payload = {
        "target": str(target),
        "version": VERSION,
        "project_name": state.get("project_name"),
        "workspace_updated_at": state.get("updated_at"),
        "validation": {
            "errors": [asdict(issue) for issue in doctor.errors],
            "warnings": [asdict(issue) for issue in doctor.warnings],
            "files_checked": doctor.files_checked,
        },
        "design": {
            "path": "design.md",
            "content": design_text,
            "included": include_design and design_text is not None,
            "shortened": shortened,
        },
        "counts": counts,
        "artifacts": artifacts,
        "note": (
            "Read-only orientation data. Markdown files remain the design source of "
            "truth, and mechanical validation is not a design-quality score."
        ),
    }
    return payload, doctor


def build_payload(
    target: Path,
    coordination_root: Path,
    *,
    include_design: bool,
    max_design_chars: int,
) -> tuple[dict[str, Any], Doctor]:
    """Read one consistent workspace generation without changing its files."""

    with workspace_lock(target, coordination_root):
        return _build_payload_unlocked(
            target,
            coordination_root,
            include_design=include_design,
            max_design_chars=max_design_chars,
        )


def render_markdown(payload: dict[str, Any]) -> str:
    validation = payload["validation"]
    lines = [
        "# GDO Workspace Snapshot",
        "",
        f"- Project: {payload['project_name'] or 'Unknown'}",
        f"- Workspace updated: {payload['workspace_updated_at'] or 'Unknown'}",
        f"- GDO version: {payload['version']}",
        (
            f"- Mechanical check: {len(validation['errors'])} error(s), "
            f"{len(validation['warnings'])} warning(s)"
        ),
        "",
        "## Artifact Inventory",
        "",
        "| ID | Type | Status | Updated | Name | Path | References |",
        "|---|---|---|---|---|---|---|",
    ]
    artifacts = payload["artifacts"]
    if artifacts:
        for artifact in artifacts:
            references = ", ".join(artifact["references"]) or "None"
            cells = (
                artifact["id"],
                artifact["kind"],
                artifact["status"],
                artifact["updated"],
                artifact["name"],
                artifact["path"],
                references,
            )
            safe = [str(value).replace("|", "\\|").replace("\n", " ") for value in cells]
            lines.append("| " + " | ".join(safe) + " |")
    else:
        lines.append("| None | - | - | - | - | - | - |")

    issues = [
        ("ERROR", issue) for issue in validation["errors"]
    ] + [("WARNING", issue) for issue in validation["warnings"]]
    if issues:
        lines.extend(["", "## Mechanical Issues", ""])
        for severity, issue in issues:
            lines.append(
                f"- `{severity}` `{issue['code']}` {issue['path']}: {issue['message']}"
            )

    design = payload["design"]
    if design["included"]:
        design_heading = "## Current Design"
        if design["shortened"]:
            design_heading += " (shortened)"
        lines.extend(["", design_heading, "", design["content"].rstrip()])
    lines.extend(["", "---", "", payload["note"], ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Print a read-only design context snapshot for resuming work or handing "
            "context to another tool."
        )
    )
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root.")
    parser.add_argument(
        "--directory", default=DEFAULT_DIRECTORY, help="Design workspace directory."
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--no-design",
        action="store_true",
        help="Emit only validation and the artifact index.",
    )
    parser.add_argument(
        "--max-design-chars",
        type=int,
        default=0,
        help="Explicitly bound embedded design text; 0 keeps it complete.",
    )
    args = parser.parse_args()
    if args.max_design_chars < 0:
        parser.error("--max-design-chars must be zero or positive.")

    try:
        root = resolve_root(args.root)
        target = resolve_target(root, args.directory)
        payload, doctor = build_payload(
            target,
            root,
            include_design=not args.no_design,
            max_design_chars=args.max_design_chars,
        )
    except (WorkspaceError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        print(render_markdown(payload), end="")
    return 1 if doctor.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
