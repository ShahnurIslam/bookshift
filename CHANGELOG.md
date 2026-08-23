# Changelog

All notable changes to BookShift are documented here.

## [Unreleased]

Prepared public beta functionality.

### Modular architecture

- Added the modular `bookshift/` package
- Centralized configuration (`bookshift/config.py`)
- Domain models, locator indexes, SQLite state repository
- Sync API server in `bookshift/server/sync_server.py`
- Initial pytest suite

### Correctness hardening

- EPUB/audio fingerprints and schema v8
- Alignment job leases with heartbeat and reclamation
- CAS FINE promotion with `STALE_REJECTED` stale guard
- Sync-loop echo suppression (`SyncLoopGuard`)
- COARSE confidence scoring

### BookOrbit decoupling

- Pure-Python CREngine XPointer and EPUB CFI locators
- In-process alignment map compiler (no `docker exec`)
- REST-only `BookOrbitAdapter`

### Docker-first deployment

- Unified CLI (`python3 -m bookshift`)
- Production Dockerfile (non-root UID 1000)
- `docker-compose.yml` and `.env.example`
- SQLite WAL concurrency hardening

### Benchmarking

- Automated benchmark harness (`bookshift/benchmarks/`)
- Progressive sync timing: COARSE < 2s, FINE compile, atomic promotion
- Session disruption test during COARSE→FINE promotion
- Public-domain Dr Jekyll fixture; `BENCHMARK_RESULTS.md` generator
- Bidirectional 100-point spot-check harness (`--spot-check`)

### Public beta readiness

- Open-source documentation suite (README, LICENSE, CONTRIBUTING, SECURITY, ROADMAP)
- OpenAPI 3.1 specification (`docs/openapi.json`, `docs/openapi.yaml`)
- GitHub Actions CI (pytest, Tier 1 benchmark, Docker build)
- `pyproject.toml` packaging with `bookshift` console script
- Deterministic public repository export with security audit
