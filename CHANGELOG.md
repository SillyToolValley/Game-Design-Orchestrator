# Changelog

All notable changes to Game Design Orchestrator are documented here. Pre-1.0 beta releases may include breaking changes.

## Unreleased

### Added

- Add a cross-platform repository installer that independently verifies manifests, rejects overlapping paths, serializes concurrent installs, validates staged and final bundles, preserves the prior skill, and rolls back on failure.
- Add a read-only workspace snapshot command for resuming work or handing current `design.md`, mechanical issues, and the DEC/REF/TST/SYS/REV inventory to another tool in Markdown or JSON.
- Add six public, harness-neutral forward-evaluation specifications with concrete fixtures and deterministic input validation; actual model behavior still requires an isolated run and independent review.
- Add installer and snapshot regression coverage, including concurrent replacement, path containment, strict checker responses, rollback, metadata boundaries, artifact indexing, dependency references, and invalid-workspace reporting.
- Include the MIT license in the installed skill bundle.

### Changed

- Clarify that automated coverage proves CLI and filesystem behavior, not cross-model design quality or broad independent designer adoption.
- Keep the snapshot explicitly subordinate to the human-readable Markdown source of truth so context portability does not create a second design database.
- Serialize managed-workspace readers and writers, and reject linked or replaced directory entries before workspace mutations.
- Use one Windows `Global\` mutex per canonical project root so same-user commands coordinate across login and Remote Desktop sessions, and fail closed when that namespace is unavailable.
- Require reproducible clean-checkout lineage and retained artifact hashes for forward-evaluation run records; metadata validation remains separate from behavioral scoring.
- Reject Git replacement-object lineage, hidden cache inputs, portable path collisions, non-finite JSON numbers, boolean schema versions, and concurrent mixed-generation reads in evaluation and installation boundaries.
- Keep interrupted-install cleanup safe on Python 3.10 when a stale staging entry is a Windows junction or another filesystem link.
- Accept filesystem-equivalent macOS ancestor aliases and Windows 8.3 names during stable-handle checks without relaxing reparse-point, device, or inode replacement detection.
- Extend the Python 3.10-3.13 CI matrix to macOS in addition to Windows and Ubuntu, with third-party actions pinned to verified commit IDs and checked monthly for updates.
- Identify this development bundle as 0.1.2-beta and provide explicit migration from official 0.1.0-beta and 0.1.1-beta workspace state.

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

Automated tests cover the packaged CLI and filesystem workflow. Forward-use simulations probe agent behavior but are not a cross-model regression benchmark. Broad independent designer use has not yet been demonstrated.
