# Security Policy

## Reporting

Please open a private security advisory or contact the maintainers before
publicly disclosing a vulnerability.

Include:

- affected version or commit
- operating system
- reproduction steps
- expected and actual behavior
- whether credentials, browser profiles, or vault contents were exposed

## Local Data

contents-hub is local-first. Vault data, SQLite state, browser profiles, source
notes, digest notes, logs, and channel credentials stay on the operator's
machine unless the operator publishes or syncs them elsewhere.

Do not commit:

- `.contents-hub/` runtime state
- local planning state
- `sources/` or `digests/` user data
- channel tokens or bot credentials
- browser profile data

## Dependencies

The base package avoids mandatory agent or channel SDK dependencies. Optional
runners and adapters must keep provider SDK imports isolated from core imports.
