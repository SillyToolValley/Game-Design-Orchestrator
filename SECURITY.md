# Security policy

Game Design Orchestrator creates Markdown and JSON inside a user-selected project directory. Path containment, non-destructive writes, atomic updates, and stable ID allocation are security-sensitive.

## Supported versions

GDO is currently in beta. Security fixes target the latest default-branch revision until stable releases establish supported lines.

| Version | Supported |
|---|---|
| 0.1.x beta / latest default branch | Yes |
| Older snapshots | No guarantee |

## Report privately

Use GitHub private vulnerability reporting from the repository Security tab when available. Do not open a public issue with exploit details, credentials, private project files, player data, or sensitive local paths.

If private reporting is unavailable, open a public issue requesting private maintainer contact without reproduction details.

A useful private report includes the affected revision, operating system, Python version, exact command, disposable test layout, expected and actual filesystem effects, relevant links or interrupted writes, impact, and known mitigation.

This beta handles reports on a best-effort basis and has no fixed response-time SLA.

## Security-relevant issues

Examples include:

- reading or writing outside the selected design workspace;
- bypassing path-containment or link checks;
- unexpectedly overwriting an existing design file;
- corrupting `.gdo/state.json` or duplicating IDs during concurrent or interrupted operations;
- unsafe recovery after an interrupted write;
- executing untrusted artifact or template content;
- exposing private designs, credentials, or personal data through output;
- dependency or CI changes that introduce supply-chain risk.

Poor design advice, a missed contradiction, or disagreement with a recommendation is normally a product-quality issue, not a security vulnerability.

## Safe usage

- Commit or back up the project before adopting a new beta.
- Run commands against a specific game repository, not a home or broad shared directory.
- Review generated changes before committing.
- Keep credentials, identifiable player data, recordings, and unrestricted telemetry out of GDO artifacts.
- Treat third-party templates and forks as untrusted.
- The Python scripts are local, but the agent environment may have separate network and retention behavior.

Please allow maintainers a reasonable opportunity to investigate and publish a fix before public disclosure.
