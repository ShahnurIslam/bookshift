# Contributing to BookShift

Thank you for contributing to BookShift. This project prioritizes **correctness**, **COARSE-first availability**, and **minimal dependencies**.

## Developer setup

```bash
git clone https://github.com/bookshift/bookshift.git
cd bookshift
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python3 -m bookshift init-db --db data/pipeline_state.db
python -m pytest tests/ -v
```

### Docker development

```bash
docker compose -f docker-compose.yml up -d bookshift-core
docker run --rm bookshift:latest python3 -m pytest tests/ -v
```

## Running tests

```bash
python -m pytest tests/ -v                    # full suite
python3 benchmarks/run_benchmarks.py --books dr_jekyll  # Tier 1 benchmark
python3 benchmarks/run_benchmarks.py --spot-check 100 --books dr_jekyll
```

## Coding guidelines

1. **COARSE-first preservation** — Never block sync API availability on FINE alignment. COARSE must remain queryable while background jobs run.
2. **Non-root Docker** — Container processes run as UID/GID 1000 (`bookshift`).
3. **Zero unneeded dependencies** — Core runtime is stdlib-only. Add pip dependencies only with strong justification and optional extras.
4. **No algorithm changes without benchmarks** — Sync resolver, CAS promotion, and locator index behavior require benchmark or focused regression evidence.
5. **SQLite WAL** — All DB connections use `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`.

## Project layout

```
bookshift/          Core package (domain, storage, server, worker, benchmarks)
tests/              Pytest suite
benchmarks/         Benchmark runner CLI
docs/               OpenAPI spec and integration notes
data/benchmarks/    Public-domain benchmark fixtures (Dr Jekyll EPUB)
```

## Pull requests

- Keep diffs focused; one logical change per PR.
- Include test coverage for new behavior.
- Ensure CI passes (pytest + Dr Jekyll benchmark + Docker build).
- Do not commit secrets, machine-specific paths, or copyrighted media.

## Reporting issues

See [SECURITY.md](SECURITY.md) for vulnerability reporting. For bugs and features, open a GitHub issue with reproduction steps and environment details.
