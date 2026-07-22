#!/usr/bin/env python3
"""Create a minimal, non-destructive game-design workspace."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

try:
    from .workspace import (
        DEFAULT_DIRECTORY,
        VERSION,
        WorkspaceError,
        initial_state,
        load_state,
        read_template,
        render_template,
        resolve_root,
        resolve_target,
        write_new,
    )
except ImportError:
    from workspace import (
        DEFAULT_DIRECTORY,
        VERSION,
        WorkspaceError,
        initial_state,
        load_state,
        read_template,
        render_template,
        resolve_root,
        resolve_target,
        write_new,
    )


GITIGNORE = """# Game Design Orchestrator runtime files
.gdo/*.lock
.gdo/*.tmp
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize a small game-design workspace without overwriting files."
    )
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root.")
    parser.add_argument("--name", help="Project name; defaults to the root directory name.")
    parser.add_argument(
        "--directory", default=DEFAULT_DIRECTORY, help="Design workspace directory."
    )
    args = parser.parse_args()

    try:
        root = resolve_root(args.root)
        target = resolve_target(root, args.directory)
        if target.exists() and not target.is_dir():
            raise WorkspaceError(f"Design workspace is not a directory: {target}.")
        project_name = (args.name or root.name).strip()
        if not project_name:
            raise WorkspaceError("Project name must not be empty.")

        target.mkdir(parents=True, exist_ok=True)
        state_path = target / ".gdo" / "state.json"
        existing_state = load_state(target) if state_path.exists() else None
        effective_name = (
            existing_state["project_name"] if existing_state is not None else project_name
        )
        values = {
            "PROJECT_NAME": effective_name,
            "DATE": date.today().isoformat(),
        }
        design_text = render_template(
            read_template(__file__, "design.md.tmpl"), values
        )
        state_text = json.dumps(
            initial_state(effective_name), ensure_ascii=False, indent=2
        ) + "\n"

        created: list[str] = []
        skipped: list[str] = []
        for path, text in (
            (target / "design.md", design_text),
            (state_path, state_text),
            (target / ".gitignore", GITIGNORE),
        ):
            collection = created if write_new(path, text, target) else skipped
            collection.append(str(path))
    except (WorkspaceError, OSError) as exc:
        raise SystemExit(str(exc)) from exc

    print(
        json.dumps(
            {
                "target": str(target),
                "created": created,
                "skipped": skipped,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
