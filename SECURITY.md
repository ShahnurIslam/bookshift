# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.0-beta | Yes |
| < 0.1.0 | No |

## Reporting a vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

Email security reports to the maintainers (configure `security@` on your fork before public release) with:

1. Description of the vulnerability
2. Steps to reproduce
3. Impact assessment
4. Suggested fix (if any)

We aim to acknowledge reports within **72 hours** and provide a fix or mitigation timeline within **14 days**.

## Scope

In scope:

- BookShift sync API (`bookshift/server/`)
- SQLite state handling and CAS promotion
- Docker deployment configuration
- Public repository export sanitization (`scripts/export_public_repo.py`)

Out of scope:

- Third-party services (Audiobookshelf, BookOrbit, Storyteller, KOReader)
- User-provided media files in `./library`

## Safe deployment practices

- Run containers as non-root (`bookshift`, UID 1000).
- Do not expose port 18001 to the public internet without authentication.
- Keep `.env` out of version control; rotate `BOOKSHIFT_ABS_TOKEN` if leaked.
- Run `scripts/export_public_repo.py` before any public release to verify zero secret/path leaks.

## Security audit

The export script performs automated scans for machine paths, private IP addresses, and authentication tokens. All public releases must pass this audit with **zero findings**.
