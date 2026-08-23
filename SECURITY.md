# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.0-beta | Yes |
| < 0.1.0 | No |

## Reporting a vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

Use GitHub's private vulnerability-reporting form in the repository Security
tab. Include:

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
- Publication configuration and documented secret-handling practices

Out of scope:

- Third-party services (Audiobookshelf, BookOrbit, Storyteller, KOReader)
- User-provided media files in `./library`

## Safe deployment practices

- Run containers as non-root (`bookshift`, UID 1000).
- Do not expose port 18001 to the public internet without authentication.
- Keep `.env` out of version control; rotate `BOOKSHIFT_ABS_TOKEN` if leaked.
- Before releasing, scan tracked files for credentials, private keys,
  machine-specific paths, runtime databases, logs, generated maps, and media.

## Security audit

The export script performs automated scans for machine paths, private IP addresses, and authentication tokens. All public releases must pass this audit with **zero findings**.
