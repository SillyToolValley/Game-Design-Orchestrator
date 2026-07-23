#!/usr/bin/env python3
"""Create a focused design artifact or append a lightweight design record."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date
from pathlib import Path

try:
    from .workspace import (
        DEFAULT_DIRECTORY,
        VERSION,
        WorkspaceError,
        allocate_id,
        artifact_marker,
        atomic_json,
        atomic_write,
        load_state,
        reject_legacy_workspace,
        read_template,
        render_template,
        require_contained,
        resolve_target,
        slugify,
        workspace_lock,
        write_new,
    )
except ImportError:
    from workspace import (
        DEFAULT_DIRECTORY,
        VERSION,
        WorkspaceError,
        allocate_id,
        artifact_marker,
        atomic_json,
        atomic_write,
        load_state,
        reject_legacy_workspace,
        read_template,
        render_template,
        require_contained,
        resolve_target,
        slugify,
        workspace_lock,
        write_new,
    )


FILE_ARTIFACTS = {
    "test": ("TST", "tests", "test-card.md.tmpl"),
    "system": ("SYS", "systems", "system-spec.md.tmpl"),
    "review": ("REV", "reviews", "review.md.tmpl"),
}
AGGREGATES = {
    "decision": ("decisions.md", "decisions.md.tmpl"),
    "reference": ("references.md", "references.md.tmpl"),
}


def add_name(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", required=True, help="Short human-readable name.")


def clean_optional(value: str | None) -> str:
    return value.strip() if value and value.strip() else "Not recorded yet."


def clean_table_cell(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def append_record(
    *,
    kind: str,
    artifact_id: str,
    args: argparse.Namespace,
    target: Path,
    project_name: str,
    today: str,
) -> Path:
    filename, template_name = AGGREGATES[kind]
    path = target / filename
    if path.exists():
        base = path.read_text(encoding="utf-8-sig").rstrip()
    else:
        base = render_template(
            read_template(__file__, template_name),
            {"PROJECT_NAME": project_name, "DATE": today},
        ).rstrip()
    base = re.sub(
        r"(?m)^- Updated: .*$",
        f"- Updated: {today}",
        base,
        count=1,
    )

    marker = artifact_marker(kind, artifact_id)
    if kind == "decision":
        options = [clean_table_cell(value) for value in args.option if value.strip()]
        if not options:
            options = ["Not recorded yet."]
        option_rows = "\n".join(
            f"| {option} | Not recorded yet. | Not recorded yet. | Not recorded yet. |"
            for option in options
        )
        status = "DECIDED" if args.choice and args.choice.strip() else "PROPOSED"
        entry = f"""## {artifact_id} - {args.name}

{marker}

- Date: {today}
- Status: {status}
- Question or context: {clean_optional(args.context)}

### Options Considered

| Option | Benefit | Cost | Likely failure mode |
|---|---|---|---|
{option_rows}

### Decision and Rationale

- Human choice: {clean_optional(args.choice)}
- Rationale: {clean_optional(args.reason)}
- Affected design or systems: {clean_optional(args.impact)}
- Revisit when: {clean_optional(args.revisit)}
"""
    else:
        entry = f"""## {artifact_id} - {args.name}

{marker}

- Date: {today}
- Source: {clean_optional(args.source)}
- Claim or observation: {clean_optional(args.claim)}
- Project context or use: {clean_optional(args.context)}
- Transfer limit: {clean_optional(args.transfer_limit)}
- Notes: {clean_optional(args.note)}
"""
    atomic_write(path, base + "\n\n" + entry.rstrip() + "\n", target)
    return path


def validate_dependencies(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = value.strip().upper()
        if re.fullmatch(r"SYS-\d{3,}", normalized) is None:
            raise WorkspaceError(
                f"Invalid dependency {value!r}; expected an ID such as SYS-001."
            )
        if normalized not in result:
            result.append(normalized)
    return result


def review_links(values: list[str], target: Path, artifact_dir: Path) -> str:
    if not values:
        return "None specified."
    links: list[str] = []
    for raw in values:
        relative = Path(raw)
        if relative.is_absolute():
            raise WorkspaceError("--artifact paths must be relative to game-design.")
        candidate = target / relative
        require_contained(candidate, target)
        if not candidate.is_file():
            raise WorkspaceError(f"Reviewed artifact does not exist: {raw}.")
        link = os.path.relpath(candidate, artifact_dir).replace(os.sep, "/")
        links.append(f"[{relative.as_posix()}]({link})")
    return ", ".join(links)


def create_file_artifact(
    *,
    kind: str,
    artifact_id: str,
    args: argparse.Namespace,
    target: Path,
    today: str,
) -> Path:
    _prefix, directory_name, template_name = FILE_ARTIFACTS[kind]
    artifact_dir = target / directory_name
    artifact_dir.mkdir(parents=True, exist_ok=True)
    require_contained(artifact_dir, target)
    path = artifact_dir / f"{artifact_id}-{slugify(args.name, kind)}.md"

    values = {
        "ID": artifact_id,
        "NAME": args.name,
        "DATE": today,
        "DEPENDENCIES": "None",
        "LENSES": "None specified.",
        "REVIEWED_ARTIFACTS": "None specified.",
    }
    if kind == "system":
        dependencies = validate_dependencies(args.dependency)
        values["DEPENDENCIES"] = (
            ", ".join(dependencies) if dependencies else "None"
        )
    elif kind == "review":
        values["LENSES"] = (
            "; ".join(lens.strip() for lens in args.lens if lens.strip())
            or "None specified."
        )
        values["REVIEWED_ARTIFACTS"] = review_links(
            args.artifact, target, artifact_dir
        )

    content = render_template(read_template(__file__, template_name), values)
    marker = artifact_marker(kind, artifact_id)
    if marker not in content:
        content = marker + "\n\n" + content.lstrip()
    if not content.endswith("\n"):
        content += "\n"
    if not write_new(path, content, target):
        raise WorkspaceError(f"Artifact already exists: {path}.")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create one useful game-design record or working document."
    )
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root.")
    parser.add_argument(
        "--directory", default=DEFAULT_DIRECTORY, help="Design workspace directory."
    )
    subparsers = parser.add_subparsers(dest="kind", required=True)

    decision = subparsers.add_parser("decision", help="Append a design decision.")
    add_name(decision)
    decision.add_argument("--context", help="What prompted the decision.")
    decision.add_argument("--choice", help="What was chosen.")
    decision.add_argument("--reason", help="Why this option was chosen.")
    decision.add_argument(
        "--option", action="append", default=[], help="Option considered; repeat as needed."
    )
    decision.add_argument("--impact", help="Design or systems affected by the choice.")
    decision.add_argument("--revisit", help="Condition that should reopen the choice.")

    reference = subparsers.add_parser("reference", help="Append a reference note.")
    add_name(reference)
    reference.add_argument("--source", help="URL, title, file, game, or other source.")
    reference.add_argument("--claim", help="Relevant fact, claim, or observation.")
    reference.add_argument("--context", help="How this source may inform the project.")
    reference.add_argument(
        "--transfer-limit", help="Why the source may not transfer directly."
    )
    reference.add_argument("--note", help="Relevant observation or caution.")

    test = subparsers.add_parser("test", help="Create a lightweight test card.")
    add_name(test)

    system = subparsers.add_parser("system", help="Create a system working document.")
    add_name(system)
    system.add_argument(
        "--dependency",
        action="append",
        default=[],
        help="Related SYS-NNN ID; repeat as needed.",
    )

    review = subparsers.add_parser("review", help="Create a focused design review.")
    add_name(review)
    review.add_argument(
        "--lens", action="append", default=[], help="Review perspective; repeat as needed."
    )
    review.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Existing path relative to game-design; repeat as needed.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        target = resolve_target(args.root, args.directory)
        if not target.is_dir():
            raise WorkspaceError(
                f"Missing design workspace {target}. Run init_design_project.py first."
            )
        reject_legacy_workspace(target)
        today = date.today().isoformat()
        state_path = target / ".gdo" / "state.json"
        # Fail on version or contract mismatch before the lock file is opened.
        load_state(target)
        with workspace_lock(target):
            state = load_state(target)
            artifact_id, next_number = allocate_id(state, args.kind, target)
            if args.kind in AGGREGATES:
                path = append_record(
                    kind=args.kind,
                    artifact_id=artifact_id,
                    args=args,
                    target=target,
                    project_name=state["project_name"],
                    today=today,
                )
            else:
                path = create_file_artifact(
                    kind=args.kind,
                    artifact_id=artifact_id,
                    args=args,
                    target=target,
                    today=today,
                )
            state["next_ids"][args.kind] = next_number
            state["updated_at"] = today
            state["tool_version"] = VERSION
            atomic_json(state_path, state, target)
    except (WorkspaceError, OSError) as exc:
        raise SystemExit(str(exc)) from exc

    print(
        json.dumps(
            {
                "type": args.kind,
                "id": artifact_id,
                "path": str(path),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
