# Contributing

Thank you for helping improve Game Design Orchestrator. Contributions should make human-led game design clearer, more thoughtful, or less repetitive.

## What belongs here

Good changes usually help designers:

- discover important options they may have missed;
- challenge assumptions with appropriately cited evidence;
- separate facts, inferences, assumptions, and transfer limits;
- retain creative authority and explicit consequential choices;
- reduce repetitive document and consistency work;
- learn from playtest observations without inventing certainty;
- use the local tools more safely and clearly.

Do not add ceremony solely to satisfy a rubric, inflate a score, or make generated documents look rigorous. A new required field or artifact needs a concrete user problem that cannot be handled more simply.

## Before opening a change

- Search existing issues and pull requests.
- Propose significant workflow or artifact changes before implementation. Describe the designer problem, the smallest useful change, and compatibility cost.
- Follow [SECURITY.md](SECURITY.md) for suspected vulnerabilities.
- Keep credentials, recordings, commercial designs, personal data, and raw player data out of fixtures.

## Development setup

Requirements are Python 3.10 or newer and Git. Runtime scripts use only the standard library. Pytest is optional.

```bash
python -m pip install 'pytest>=8,<9'
python -m compileall -q skills/orchestrate-game-design/scripts
python -m unittest discover -s skills/orchestrate-game-design/tests -p 'test_*.py' -v
```

When changing CLI arguments, check every public surface:

```bash
python skills/orchestrate-game-design/scripts/init_design_project.py --help
python skills/orchestrate-game-design/scripts/create_design_artifact.py --help
python skills/orchestrate-game-design/scripts/create_design_artifact.py decision --help
python skills/orchestrate-game-design/scripts/create_design_artifact.py reference --help
python skills/orchestrate-game-design/scripts/create_design_artifact.py test --help
python skills/orchestrate-game-design/scripts/create_design_artifact.py system --help
python skills/orchestrate-game-design/scripts/create_design_artifact.py review --help
python skills/orchestrate-game-design/scripts/check_design_workspace.py --help
```

CI covers Windows and Ubuntu with Python 3.10 through 3.13.

## Lean artifact contract

Initialization creates only `game-design/design.md`, `game-design/.gdo/state.json`, and `game-design/.gitignore`. Decisions and references appear when recorded; tests, systems, and reviews appear when requested. The workspace checker diagnoses file mechanics and consistency. It does not score a design.

When changing this contract, review `SKILL.md`, templates, scripts, focused tests, README examples, and the changelog. Keep templates short and readable.

## Project invariants

1. Humans own creative direction and consequential choices.
2. AI recommendations never silently approve themselves.
3. Checkable external claims cite sources and state transfer limits.
4. AI opinion and simulation do not prove that a game is fun.
5. Player sessions, quotes, telemetry, and research are never fabricated.
6. Existing user files are not overwritten unexpectedly.
7. Writes remain inside the selected workspace and interrupted creation is recoverable.
8. IDs and recorded decisions are stable.
9. Diagnostics report consistency problems without rating design quality.

## Tests expected

- Bug fixes include focused regression tests.
- New fields demonstrate a user need and cover creation plus reading.
- CLI changes cover success and invalid input.
- Filesystem changes cover containment, links where relevant, interrupted writes, and supported systems.
- Workflow changes include a concise realistic example.

Use fictional projects and clearly synthetic observations.

## Pull requests

Explain the designer problem, why the change is the smallest useful solution, affected contracts, compatibility impact, checks run, and remaining uncertainty. Keep unrelated formatting or refactoring out of the pull request.

Before 1.0, breaking changes remain possible but require changelog and migration guidance.

All participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
