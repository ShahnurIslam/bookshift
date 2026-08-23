# Changelog

All notable changes to BookShift are documented here.

## [0.1.0-beta] — 2026-08-14

First public beta release. Implements Gates 1–6 of the BookShift programme.

### Gate 1 — Modular architecture

- Extracted `bookshift/` package from analysis scripts
- Centralized configuration (`bookshift/config.py`)
- Domain models, locator indexes, SQLite state repository
- Gate 17 sync API server in `bookshift/server/sync_server.py`
- Initial pytest suite

### Gate 2 — Correctness hardening

- EPUB/audio fingerprints and schema v8
- Alignment job leases with heartbeat and reclamation
- CAS FINE promotion with `STALE_REJECTED` stale guard
- Sync-loop echo suppression (`SyncLoopGuard`)
- COARSE confidence scoring

### Gate 3 — BookOrbit decoupling

- Pure-Python CREngine XPointer and EPUB CFI locators
- In-process alignment map compiler (no `docker exec`)
- REST-only `BookOrbitAdapter`

### Gate 4 — Docker-first deployment

- Unified CLI (`python3 -m bookshift`)
- Production Dockerfile (non-root UID 1000)
- `docker-compose.yml` and `.env.example`
- SQLite WAL concurrency hardening

### Gate 5 — Benchmarking

- Automated benchmark harness (`bookshift/benchmarks/`)
- Progressive sync timing: COARSE < 2s, FINE compile, atomic promotion
- Session disruption test during COARSE→FINE promotion
- Public-domain Dr Jekyll fixture; `BENCHMARK_RESULTS.md` generator
- Bidirectional 100-point spot-check harness (`--spot-check`)

### Gate 6 — Public beta readiness

- Open-source documentation suite (README, LICENSE, CONTRIBUTING, SECURITY, ROADMAP)
- OpenAPI 3.1 specification (`docs/openapi.json`, `docs/openapi.yaml`)
- GitHub Actions CI (pytest, Tier 1 benchmark, Docker build)
- `pyproject.toml` packaging with `bookshift` console script
- Deterministic public repository export with security audit

[0.1.0-beta]: https://github.com/bookshift/bookshift/releases/tag/v0.1.0-beta
