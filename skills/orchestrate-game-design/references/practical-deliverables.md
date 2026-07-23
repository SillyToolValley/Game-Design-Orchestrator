# Practical Deliverables

A finished handoff helps a named reader decide, build, tune, or test the game. Match the document to that use instead of exporting the design process.

## Choose the output

| User need | Primary output | Add a companion only when |
|---|---|---|
| Explore or choose a direction | Update `design.md` | Decision history or reusable sources would otherwise be lost |
| Finish a game design or GDD | One reader-facing GDD at the requested path | A separate operator needs a jam plan or immediate playtest sheet |
| Hand off one approved system | One focused `SYS-*` specification | Its rules truly have an independent owner or lifecycle |
| Plan a time-boxed build | GDD with production section, or GDD plus one jam plan | The schedule is actively used apart from the design |
| Answer one uncertain question | A focused test card | A real observation session is planned or has occurred |

Do not create a companion merely because a category exists. Keep research notes, alternatives, and critique inside the primary handoff unless they have a separate ongoing use.
For a one-off handoff in a new folder, write the primary file directly. Do not initialize `design.md`, `.gdo`, or internal artifact folders unless the user explicitly wants an ongoing managed workspace.


## Practical GDD

Use headings that fit the game and omit irrelevant subjects. A useful implementation handoff usually makes the following clear:

- the one-sentence game promise, intended player, platform, input, session shape, and production constraint;
- the intended experience and a few observable design pillars;
- the core loop and the meaningful choice or skill it repeats;
- one ordinary play sequence from entry through action, feedback, consequence, win or failure, and retry;
- controls, rules, states, transitions, priority, win/fail/retry behavior, and concrete examples where ambiguity would block implementation;
- content structure, progression, economy, social behavior, or narrative only where they support the loop;
- onboarding, interface hierarchy, feedback, accessibility, and recovery from mistakes;
- MVP boundary, non-goals, dependencies, tuning levers, implementation order, and cut order;
- assumptions, unverified player-response claims, and only the open decisions that can still change the build.

Write decisions and recommended defaults, not empty prompts. A developer should know what to build next without reconstructing the design from option logs or review files.
Use status honestly. A design with a novel core interaction still awaiting a go/no-go prototype can be a complete graybox handoff, but it is graybox-ready, not an implementation-ready or production-locked build handoff.


## Time-boxed or jam production

Separate elapsed event time from productive human time.

- Reserve sleep, meals, setup, integration, playtest, export, first upload, submission, and contingency time before allocating feature work.
- Evolve the smallest playable into the production build unless a named technical reason requires a rewrite.
- Set milestones for first playable, core complete, content complete, feature freeze, first uploaded build, and final submission.
- Give each milestone an observable result. Avoid hour-by-hour precision that the available information cannot support.
- Put an export and clean-launch check in or before the first-playable milestone; do not defer the first executable build to feature freeze.
- Put risky, experience-defining interactions first. Put polish and optional content behind a clear cut line.
- Include a cut order that preserves the core promise when time slips.

## Playtest companion

Create a separate playtest sheet only when someone will use it. Keep it brief.

- Test a question that can change a design decision.
- Observe the actual success condition, failure cause, comprehension, fairness, enjoyment, and desire to retry when relevant.
- Make the observation match the claim. Reaching an intermediate state is not evidence of completing the full win condition.
- Record behavior separately from participant explanation and designer inference.
- Use results to keep, tune, remove, or retest. Do not present a tiny sample or AI simulation as validation.

## Integration pass

Before delivery, reconcile the design as one playable system:

- Apply only the checks relevant to claims the game actually makes. These are diagnostic prompts, not required systems or document sections.
- Does the described loop plausibly fill the stated session length?
- Does each repeated action remain mechanically valid through its full cycle, including any reset, reposition, recovery, or reversal, and do worked examples stay inside stated bounds?
- Do formula names, ownership, and update semantics agree across rules and implementation notes? Recompute temporary modifiers from an unmodified base unless accumulation is intentional.
- If the document calls content authored, does it include the minimum actual records, order, geometry, or values needed to build it rather than only a schema?
- Where schedules, spawners, or directors drive pacing, do their intervals, caps, queue rules, resolution time, and end conditions produce the claimed count and pace when calculated together?
- If a fallback changes controls or a central mechanic, are every conflicting rule, tutorial step, HUD element, parameter, and schedule item propagated or explicitly removed?
- Where concurrent pressure is part of the promise, do arrival intervals and actual service or completion times create the claimed overlap?
- Do content counts, allowed failures, quotas, early termination, and tie priorities make every claimed outcome reachable?
- For any geometry, timing, probability, capacity, or economy claim that determines whether the core scenario works, run a small calculation or simulation instead of trusting visual intuition. Keep scratch work transient and put the corrected rule or concise result in the handoff.
- Run authored scenarios in chronological order with the state they actually inherit. Include prior rewards, difficulty or weather changes, resource consumption, conditional triggers, and overlapping actors; an isolated zero-modifier check is not evidence that a later lesson or encounter works in the real run.
- Re-run every mode, fallback, and cut branch whose result is used to justify readiness. A base-mode witness cannot validate a variant; if the exact branch was not replayed, remove its numeric success claim and place it below the safe ship/cut line.
- Do not add a fallback merely because the core interaction is unverified. When an immediate alternative is genuinely required, it must contain enough rules and content data to build; otherwise use an explicit stop-and-redesign gate and do not call a speculative replacement ready.
- Is the order of timers, movement, collision, resolution, and state changes deterministic on boundary frames?
- For tick-precise claims, does the document define pre- or post-tick sampling, elapsed-age versus inclusive integration steps, and the update location of every consumed accumulator? Use a one-tick tolerance unless exact boundary behavior is the thing being tested.
- Does every fallback or scope cut recalculate its derived effects on pacing, difficulty, content counts, tests, and schedule rather than changing wording alone?
- Does every fallback or scope cut preserve its trigger, ownership, feedback, and promised player cause-and-effect? If it replaces them, specify and validate the new contract instead of silently making a conditional event unconditional.
- Do decision gates budget recruitment, setup, observation, interpretation, and implementation time rather than assigning the same hours twice?
- Do tutorial cues teach the same information available in normal play?
- Does each tutorial beat occur while its target is still active, and does the authored runtime state still require the skill it claims to teach?
- Does feedback preserve the intended decision or skill instead of solving it for the player?
- Are controls, state transitions, win conditions, and measured test outcomes the same across sections?
- Do progression and economy reward the intended behavior without quietly contradicting player autonomy or trust?
- Do schedule blocks fit without overlap, hidden rewrites, or impossible labor assumptions?
- Are implementation-critical geometry, timing, quantities, ownership, dependencies, and edge cases concrete enough for the next build?
- Are unsupported values labeled as assumptions and exposed as tuning levers?
- Do worked examples use the document's own coordinate conventions and ranges, and can test thresholds distinguish success from a trivial baseline?
- Can duplicated acceptance, scope, implementation, and release prose be consolidated without losing an operator-facing rule?
- Does the production plan schedule a first export and clean-launch test early enough to recover from platform or packaging failure?

Fix contradictions in the primary document. Prefer concrete runtime data and corrected rules over repeated explanatory prose; do not create an audit report as the handoff.

## Avoid

- folders dominated by state, audit, readiness, reviewer, or confirmation records;
- arbitrary weighted scores that let the AI choose the creative direction;
- one file per thought, role, hypothesis, or review lens;
- blank templates and unresolved placeholders in a claimed final delivery;
- withholding the GDD until fun has been proven;
- calling a mechanically consistent workspace a validated game;
- duplicating the same rule in several sources of truth.

A concise GDD may still be detailed. Compact means every section earns its place, not that rules needed for implementation are omitted.
