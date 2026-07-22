# Artifact Recipes

Use the fewest files that keep the current decision, test, or implementation work clear. Never create an artifact because a workflow diagram says it should exist.

## Workspace shape

```text
game-design/
  design.md                   # always first; the compact source of truth
  decisions.md                # optional decision history
  references.md               # optional reusable source notes
  tests/                      # created when a real test is planned or recorded
    TST-001-short-name.md
  systems/                    # created when approved design needs implementation detail
    SYS-001-short-name.md
  reviews/                    # created for consequential critique
    REV-001-short-name.md
```

Allocate the next unused three-digit ID within each folder. Use lowercase hyphenated slugs. Do not renumber existing files.

## `design.md`

Create this first and keep it compact. It should answer:

- Who is the player and in what context do they play?
- What should they feel, notice, or decide?
- What action and feedback repeat?
- What direction has the human currently chosen?
- What constraints and non-goals shape the solution?
- Which assumptions or questions could still change the direction?
- What decision or useful action comes next?

Update it when the chosen direction changes. Do not turn it into a complete encyclopedia.

## `decisions.md`

Create when a consequential choice has multiple credible options, a rationale will matter later, or an earlier decision changes. Record the question, options and tradeoffs, human choice, rationale, evidence, consequences, and revisit condition. Append or mark a prior entry revised; preserve useful history.

## `references.md`

Create when research will be cited more than once or when source context matters. Record the source, type, relevant claim, project use, transfer limit, and access date. Do not copy long passages.

## `TST-*`

Create when uncertainty can change a decision and there is a concrete way to learn. Record the question, current assumption, setup, what to observe, strengthening and challenging signals, practical limits, observations, interpretation, and resulting human decision. Planning and results may live in the same card.

## `SYS-*`

Create when an approved design needs implementation precision. Keep one coherent system per file. Include player purpose, scope, dependencies, rules and state changes, parameters, feedback, edge cases, worked examples, acceptance examples, and unresolved decisions. Update the same file as the system evolves.

## `REV-*`

Create only when a costly, cross-system, hard-to-reverse, or especially uncertain choice benefits from structured critique. Record the decision, lenses, reviewed sources, findings, overlooked alternatives, disagreements, recommendation, and human response.

## Update rules

- Show consequential creative changes before writing them; persist them after human approval.
- Routine clerical updates implied by an approved choice may be made automatically.
- Link to existing details instead of duplicating them.
- Label unsupported values and predictions as assumptions.
- When a test or review changes the direction, update `design.md` and the affected `SYS-*` files in the same pass.
- End with a short change summary and the next unresolved choice.
