# Review Lenses

A review exists to improve a consequential choice. Select one to three lenses that expose different failure modes; do not run every lens by default.

## Player experience

Ask whether the actual actions and feedback produce the intended emotion or fantasy. Look for unclear goals, weak agency, dead time, repetitive choices, excessive cognitive load, and reliance on explanation rather than play.

## Rules and system coherence

Trace an ordinary example and a difficult edge case from input to feedback and next choice. Look for contradictory rules, missing states, unclear ownership, degenerate strategies, runaway loops, and dependencies that disagree.

## Scope and implementation

Challenge whether the idea fits the platform, team, schedule, tools, and content capacity. Find the smallest version that preserves the intended experience, name expensive unknowns, and identify what can safely be cut.

## Progression, economy, and motivation

Examine what behaviors rewards encourage, how pacing changes over time, and who is disadvantaged. Look for grind, dead rewards, exploit incentives, pay pressure, value inflation, and conflicts between short-term engagement and long-term trust.

## Accessibility, safety, and inclusion

Check whether controls, timing, feedback, language, sensory load, social features, data use, or monetization exclude or harm likely players. Distinguish a necessary design constraint from an avoidable barrier.

## Content and live sustainability

Review how much content the promise consumes, how repetition is disguised or embraced, what becomes stale, and how updates interact with saved progress. Look for authoring bottlenecks, brittle events, moderation burdens, and irreversible rollout choices.

## Review method

1. Name the human decision the review should inform.
2. Read the design, direct dependencies, and relevant evidence.
3. Use only the lenses that could change that decision.
4. Tie each finding to a source location, direct observation, or labeled inference.
5. Explain the consequence and propose an option, remedy, or focused test.
6. Surface two to four overlooked alternatives when the current choice space is too narrow.
7. End with a recommendation, uncertainty, and the choice that remains with the human.

Use plain severity language only when it helps prioritization:

- `CRITICAL` - likely invalidates the intended experience or makes implementation unsafe or infeasible.
- `IMPORTANT` - materially harms the experience, scope, or maintainability.
- `WATCH` - plausible concern worth observing or revisiting.

Severity is not a score. One well-supported concern can matter more than many cosmetic findings.
