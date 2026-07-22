# Game Design Orchestrator

Human-led AI co-design for clearer choices, better questions, and less design paperwork.

Current version: `0.1.0-beta`.

Game Design Orchestrator (GDO) is a Codex skill that helps a designer uncover overlooked options, challenge uncertain assumptions with references, and keep approved design documents consistent. The human remains the creative director and makes every consequential choice.

It is not an autonomous game studio, a one-prompt GDD generator, or a scoring system that certifies a design as fun.

> **Beta:** The workflow and CLI have automated coverage, but broad independent designer use has not yet been demonstrated. Keep project files in version control and expect pre-1.0 changes.

## What it does

- Presents two to four materially different options with benefits, costs, failure modes, and the cheapest way to learn more.
- Challenges weak assumptions using project evidence, user evidence, and cited external sources.
- Separates `FACT`, `INFERENCE`, `ASSUMPTION`, and `TRANSFER LIMIT` so a reference is not mistaken for proof that it fits your game.
- Recommends a direction while preserving alternatives and asking the human to decide.
- Automates approved follow-up work: create files, record decisions and sources, propagate changes, and check contradictions.
- Turns playtest notes into observations, competing interpretations, open questions, and the next useful test.

GDO is engine- and genre-neutral. It is most useful during concept development, preproduction, system design, and playtest iteration.

## Install

Requirements are Codex and, for the optional CLI, Python 3.10 or newer. The Python runtime has no third-party dependencies.

Clone or download this repository, then copy the skill into your Codex skills directory.

```bash
# macOS/Linux
mkdir -p ~/.codex/skills
cp -R skills/orchestrate-game-design ~/.codex/skills/
```

```powershell
# Windows PowerShell
$skillRoot = Join-Path $env:USERPROFILE '.codex\skills'
New-Item -ItemType Directory -Force $skillRoot | Out-Null
Copy-Item -Recurse -Force '.\skills\orchestrate-game-design' $skillRoot
```

Start a new Codex task or reload skills. Installation does not modify a game project.

## Use it in Codex

Describe the design problem in ordinary language and invoke `$orchestrate-game-design`. GDO discusses the problem first and only persists files when you ask.

### Start a new concept

```text
Use $orchestrate-game-design.

I am making a two-player cooperative cooking game for mobile. Clarify the
intended experience, show important options I may have missed, and challenge
the riskiest assumptions with references. Recommend one direction but leave
the final choice to me. Save the design after I approve it.
```

### Review an existing GDD

```text
Use $orchestrate-game-design.

Read docs/GDD.md. Find contradictions, hidden assumptions, missing player
choices, and systems whose purpose is unclear. Show alternatives before
rewriting. After I choose, update the affected files and record the decision.
```

### Challenge a design with references

```text
Use $orchestrate-game-design.

Challenge our claim that daily competitive events fit this audience. Prefer
current primary sources. Separate facts, inferences, assumptions, and transfer
limits. Say what the sources do not support and propose the cheapest next test.
```

A citation provides context, not authority over the design. Platform documentation, telemetry, player observation, and comparable games answer different questions.

### Analyze playtest notes

```text
Use $orchestrate-game-design.

Here are notes from five sessions: [notes]. Separate observed behavior and
direct quotes from our interpretation. Group recurring problems, show competing
explanations, and recommend the next change or test. Do not call the design
validated. After I decide, update the design, test record, and decision log.
```

## How a session works

1. **Orient:** read relevant context and ask at most one to three high-value questions.
2. **Expand:** surface two to four overlooked options when a real choice exists.
3. **Challenge:** inspect uncertainty using references or project evidence.
4. **Recommend:** explain one preferred direction and its tradeoffs.
5. **Decide:** the human chooses, combines, rejects, or postpones.
6. **Automate:** update approved documents, dependencies, decisions, and references.
7. **Learn:** propose the cheapest useful next test instead of manufacturing certainty.

The AI may recommend strongly. It may not silently turn its recommendation into project truth or fabricate player evidence.

## Project files

A new workspace starts small:

```text
game-design/
  design.md
  .gitignore
  .gdo/
    state.json
```

Other human-readable files appear only when needed:

```text
game-design/
  decisions.md
  references.md
  tests/TST-001-*.md
  systems/SYS-001-*.md
  reviews/REV-001-*.md
```

`design.md` holds the current direction and open questions. Decisions and sources are append-only records. Tests, systems, and reviews are focused numbered artifacts. `.gdo/state.json` holds IDs and tool state, not design truth.

## Optional lean CLI

Codex can run these commands for you. Direct use is available for scripting.

```bash
python skills/orchestrate-game-design/scripts/init_design_project.py --root . --name 'My Game'

python skills/orchestrate-game-design/scripts/create_design_artifact.py --root . decision --name 'Keep song-first navigation' --context 'Players enter through a song picker' --option 'Choose a region first' --option 'Choose a song first' --choice 'Choose the song first' --reason 'Music is the primary intent' --impact 'Navigation and onboarding' --revisit 'Players cannot predict the destination'
python skills/orchestrate-game-design/scripts/create_design_artifact.py --root . reference --name 'Platform input guidance' --source 'https://example.com/source' --claim 'The platform recommends touch targets of this size' --context 'May inform mobile song selection' --transfer-limit 'Guidance does not prove what this audience prefers'
python skills/orchestrate-game-design/scripts/create_design_artifact.py --root . test --name 'First-session comprehension test'
python skills/orchestrate-game-design/scripts/create_design_artifact.py --root . system --name 'Song unlock economy'
python skills/orchestrate-game-design/scripts/create_design_artifact.py --root . review --name 'Economy challenge' --lens 'new-player comprehension' --artifact 'design.md'

python skills/orchestrate-game-design/scripts/check_design_workspace.py --root .
```

Add `--json` to the check for machine-readable output. It is a consistency doctor for missing files, broken IDs, invalid references, and inconsistent state. A clean result is not a quality score and does not prove that a game is fun.

All commands accept `--directory <name>` for a workspace other than `game-design`. Initialization does not overwrite existing files.

## Principles

- Human taste and intent outrank AI preference.
- Show consequential alternatives before converging.
- Cite checkable claims and state what a source cannot establish.
- Treat every unapproved proposal as an assumption.
- Automate clerical work only after the choice is approved.
- Never invent research, player sessions, quotes, telemetry, or confidence.

## Privacy and development

Design files may contain unreleased information or player data. Review what you send to any agent, keep credentials and personal data out of GDO artifacts, and follow that environment's retention policy. The bundled Python scripts operate locally and do not make network requests.

Commit or back up `game-design/` before adopting a new beta version, and review generated changes before committing.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the [MIT License](LICENSE).
