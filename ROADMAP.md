# BookShift Roadmap

Post-beta vision for BookShift. Items are ordered by priority but not committed to dates.

## v0.2 — Worker streaming & resilience

- [ ] Streaming alignment progress webhooks (SSE on `/api/v1/sync/jobs`)
- [ ] Worker graceful shutdown with lease handoff
- [ ] Automatic COARSE map rebuild on ABS chapter TOC changes
- [ ] Health dashboard for per-title sync mode and confidence

## v0.3 — E-reader plugins

- [ ] Official KOReader plugin package (luaconf + sync hook)
- [ ] Calibre plugin for metadata and progress export
- [ ] Generic REST client library (Python + TypeScript)

## v0.4 — Scale & multi-user

- [ ] Multi-library support (separate pipeline DB per library root)
- [ ] Read-only replica mode for sync API horizontal scaling
- [ ] Batch ingest CLI for entire library directories

## v0.5 — Quality & alignment

- [ ] On-device Whisper offload profiles (Apple Silicon, CUDA)
- [ ] Automatic COARSE→FINE quality scoring in API health response
- [ ] User-facing alignment review UI (chapter pair confirmation)

## Non-goals (unchanged)

- External message brokers (Redis, Celery)
- ORM layers (SQLAlchemy, etc.)
- Cloud-only SaaS lock-in
- Bundled copyrighted media in public releases

## Contributing to the roadmap

Open a GitHub discussion or issue to propose features. Benchmarks and COARSE-first invariants must be preserved for any sync-path changes.
