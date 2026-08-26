# Forward-Evaluation Protocol

These six harness-neutral case specifications exercise observable GDO behavior that deterministic unit tests cannot prove. Validation confirms only that the catalog and input files are complete; it never claims that a model behaved correctly or that a game is fun.

## Validate and prepare

Validate the checked-in catalog and every fixture:

```text
python evals/validate_cases.py
```

Prepare one case at a disposable path whose parent already exists but whose destination itself does not. The validator creates and owns the destination directory; it rejects missing parents and even pre-existing empty destinations:

```text
python evals/validate_cases.py prepare --case review-before-rewrite --destination <temporary-project>
```

The preparation command copies only the declared input files and emits a JSON packet containing the invocation, user prompt, and baseline file hashes. It deliberately omits `mechanical_checks` and `human_review`. Fixture and artifact paths use one NFC-normalized, canonical forward-slash spelling and reject control characters, NTFS alternate-data-stream colons, trailing dots/spaces, and Windows device names on every platform.

On Windows, preparation compares each held directory handle's final path with the requested path after standard DOS/UNC normalization. Mapped-drive or `subst` aliases whose handle paths resolve to another spelling can therefore be rejected; use a canonical local or UNC path rather than relying on alias equivalence.

On POSIX, mutations use held directory descriptors plus best-effort identity checks immediately before each mutation. POSIX still permits a same-UID process to rename a held directory inode after a check; descriptor-relative writes remain anchored to that inode, but the returned path could stop naming it. Treat the evaluator and its destination parent as a trusted local control plane. The validator does not claim to defeat every active same-UID filesystem race.

Apply the packet's `invocation` through the runner's native skill mechanism, or as a separate system/developer instruction when no native mechanism exists. Deliver `prompt` as the user request. Do not concatenate the invocation into the prompt and do not show the subject the hidden checks before the run. The evaluator checkout, catalog, and review criteria are control-plane inputs: keep them outside the subject's filesystem sandbox, tool-visible roots, retrieval index, and prompt context. Give the subject only the installed skill, prepared project, declared capabilities, invocation, and prompt.

## Run a case

1. Prepare from a clean evaluator checkout that is not visible to the subject, then start a fresh agent task whose filesystem scope contains only the installed skill and prepared isolated project directory.
2. Declare tool capabilities before invocation, including filesystem read/write, external research, and external mutation.
3. Capture the final response, tool transcript, resulting file tree, and changed-file diff.
4. Record the full repository commit ID; exact repository-relative catalog path and byte SHA-256; runner and model versions; checkout-matching skill version, contract ID, and `SKILL.md` SHA-256; UTC timestamps; operating system; tool capabilities; and tool evidence.
5. Apply mechanical checks first. Then have a fresh human or independent reviewer apply the human-review checks to the raw result.
6. Record failures and ambiguous checks. Do not average a serious fabrication, unauthorized write, or broken implementation contract into a passing score.

Run every file-writing case in a disposable directory. Compare the final tree against the baseline hashes emitted by `prepare`. A discussion-only case must leave that baseline byte-for-byte unchanged. A one-handoff case receives no credit for producing a process packet instead.

## Run records and source verification

The version-3 record fields and canonical capability names live in `cases.json`. `catalog.path` and `catalog.sha256` bind the record to the exact tracked catalog bytes used for validation. Each of the four artifact entries is an object containing a safe record-relative `path`, `bytes`, and `sha256`; all four files must exist beside the run record and have matching metadata. A harness can validate a completed JSON record without evaluating its prose:

```text
python evals/validate_cases.py validate-run --record <run-record.json>
```

`validate-run` requires non-overlapping evaluator and skill scopes and compares their actual regular-file sets and bytes directly with the recorded HEAD tree and blobs. Staged index changes, skip-worktree edits, ignored files, and ordinary untracked files therefore invalidate a run. Only `.pyc`/`.pyo` files under `__pycache__` and the explicit `.pytest_cache`, `.mypy_cache`, and `.ruff_cache` tool-cache directories are excluded. The reported skill lineage is derived from HEAD blobs, not from potentially modified working-tree files.

`claims.source_verification_claimed` is an auditable declaration, not a reviewer impression. Set it to `true` only when `tool_evidence` records an allowed successful open, fetch, or read operation for the cited HTTP(S) target during the UTC run interval. Runner adapters must normalize native tool events to those canonical operations. A reviewer who did not receive that evidence and its retained transcript must say the source was not independently verified.

Run-record validation proves only that declared lineage, exact catalog identity, and artifact metadata are internally consistent with the evaluator checkout and retained files. It does not prove that the transcript is truthful, that a cited page supported the prose, or that any behavioral check passed.

## What to report

- unauthorized writes or extra artifacts;
- missing or materially indistinguishable alternatives;
- unsupported external claims or fabricated player evidence;
- observation/interpretation boundary violations;
- contradictory rules, unreachable states, or incorrect calculations;
- whether the requested handoff can be implemented or tested without guessing core behavior;
- elapsed time and model/tool usage as cost context, not design quality.

Publish raw failures alongside successful runs. A model-generated critique is useful input, not independent proof; retain human review for consequential quality judgments.
