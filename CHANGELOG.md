# Changelog

All notable changes to Game Design Orchestrator are documented here. Pre-1.0 beta releases may include breaking changes.

## 0.1.1-beta

Corrective beta release after a stale experimental checkout produced a process-heavy packet instead of a usable game plan.

### Changed

- Route explicit GDD, design brief, and production-plan requests to one reader-facing primary handoff by default.
- Keep alternatives, critique, risks, and test ideas inside that handoff unless a companion document has a distinct operator or implementation use.
- Use a transient independent consistency review for substantive handoffs, calculate high-impact numeric claims when feasible, integrate valid fixes into the primary document, and avoid exporting the review as paperwork.
- Add practical completion checks for a full play sequence, rules and end states, scope, cut order, implementation sequence, cross-section consistency, and realistic jam-time buffers.
- Replay authored sequences with the state inherited from earlier outcomes so isolated default-state checks cannot falsely validate later lessons, triggered difficulty, fallbacks, or cuts.
- Require separate chronological evidence for every fallback or cut branch whose result is used to claim release readiness.
- Prefer an explicit stop-and-redesign gate over inventing a speculative second mode to make a document look complete.
- Define sampling and accumulator semantics for tick-level claims and allow one-tick tolerance unless an exact boundary is the subject.
- Verify every packaged file against a release manifest, along with the resolved path, version, contract lineage, entrypoints, and retired-file remnants before writes.
- Detect retired 0.2.x governance workspaces structurally and fail before initialization or artifact creation can mix formats.
- Require an exact workspace tool version and contract, with an explicit provenance-recording migration for official 0.1.0-beta state instead of silent rewriting.
- Replace installed skill folders rather than merging upgrades over stale files.
- Retry only transient permission denials around atomic file replacement so concurrent CLI writes remain reliable on Windows runners and antivirus-scanned folders.


## 0.1.0-beta

First public beta.

### Included

- Human-led design loop: orient, expand overlooked options, challenge assumptions, recommend, decide, automate, and recheck.
- Reference-backed critique that separates `FACT`, `INFERENCE`, `ASSUMPTION`, and `TRANSFER LIMIT`.
- Minimal initial workspace with lazy decision, reference, test, system, and review artifacts.
- Local Python helpers for safe initialization, monotonic IDs, Unicode filenames, atomic updates, and consistency checks.
- Windows and Linux CI across Python 3.10 through 3.13.

### Beta status

Automated tests and forward-use simulations cover the packaged workflow. Broad independent designer use has not yet been demonstrated.
