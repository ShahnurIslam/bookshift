# BookShift

BookShift keeps an audiobook position in Audiobookshelf and an EPUB position in
BookOrbit/KOReader synchronized in both directions. EPUB is the canonical ebook
format; Audiobookshelf remains the audiobook player.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](Dockerfile)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](pyproject.toml)

BookShift is a beta. The packaged runtime, mapping API, alignment worker,
reconciliation runner, tests, and public benchmark fixture are present. A clean
checkout can start and validate those components, but automatic library
discovery/pairing is not yet exposed as a public CLI workflow; active book rows
and their mapping artifacts must already exist in `pipeline_state.db` before
real titles can reconcile.

## How it works

```text
Audiobookshelf currentTime
          │
          ▼
 host-side `bookshift sync` runner ─── activity provenance / conflict planning
          │                                      │
          ▼                                      ▼
 BookShift mapping API                    SQLite observation state
  timestamp ↔ EPUB locator
          │
          ▼
BookOrbit file progress ───────────────► KOReader XPointer/CFI landing
```

The Docker Compose `bookshift-core` service runs the HTTP mapping API. The
optional `bookshift-worker` service orchestrates external alignment work and
promotes valid precomputed FINE artifacts; it does not bundle Whisper or a
title-specific compiler script. Bidirectional progress reconciliation is a
separate runner; it is not silently running in the API container. Run it
manually or with the supplied user-level systemd timer.

### Progressive mapping

BookShift exposes usable mapping before expensive alignment is complete:

| Mode | Availability | Mapping behavior |
|---|---|---|
| **COARSE** | After chapter pairing | Chapter-level, approximate timestamp/XPointer mapping |
| **FINE** | After suitable alignment and locator compilation | Precise EPUB CFI/CREngine XPointer-style landing where a locator exists |

COARSE remains available while FINE is generated. Promotion uses
compare-and-swap state updates so readers never observe a half-promoted index.
Promotion does not itself imply that a latency-sensitive session benchmark has
passed; measured disruption results are reported separately.

For ABS → BookOrbit writes, the precise locator is authoritative. If a resolver
also supplies an EPUB percentage, BookShift uses it. Otherwise the numeric
BookOrbit progress field falls back to audiobook percentage while the precise
CFI/XPointer remains the landing authority. If no FINE locator is available,
the resolver can return a COARSE chapter locator.

### Reconciliation safety

BookShift records signatures for both ABS and BookOrbit observations plus the
ABS update revision. This lets it distinguish user activity from different
representations of the same position.

- Fresh ABS activity can update BookOrbit, including an intentional backward
  seek.
- Fresh BookOrbit/KOReader activity can update ABS in either direction.
- A BookShift-authored write is recognized on the next cycle and is not echoed
  back.
- Unchanged state is a no-op.
- A COARSE → FINE reinterpretation without user activity is not treated as a
  new position.
- Stale ABS revisions are ignored. If both sources changed between polls, the
  runner reports a conflict instead of guessing.
- The first cycle after installation or schema migration establishes a safe
  baseline.

## Measured results

Detailed methodology, caveats, and historical tables are in
[BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md). The figures below are measurements,
not production guarantees.

### Reproducible clean-checkout Tier 1 run (2026-08-23)

```bash
python3 benchmarks/run_benchmarks.py \
  --books dr_jekyll --skip-disruption --output /tmp/bookshift-tier1.md
```

Using the committed public-domain Dr Jekyll EPUB, the catalog's representative
`02:49:52` duration, and synthetic ABS chapter timing proportional to EPUB
chapter size:

| COARSE ready | First usable HTTP lookup | FINE generation | Disruption test |
|---:|---:|---|---|
| **0.02 s** | **0.05 s** | Not run | Skipped |

This measures EPUB parsing, synthetic chapter-map construction, index loading,
API startup, and the first successful lookup. It does **not** read full audio,
run Whisper, measure real chapter mismatch, or represent full-book production
ingestion time.

### Historical live FINE run (2026-08-14)

A recorded end-to-end run used the same public-domain work with a Gutenberg
EPUB, `02:49:52` of LibriVox audio, and a remote Apple Silicon whisper.cpp
server:

| COARSE ready | First usable sync | FINE ready | CAS promotion | COARSE-vs-FINE mean error | Session disruption threshold |
|---:|---:|---:|---:|---:|---|
| 0.02 s | 0.02 s | 28 min 35 s | 32 ms | 7.7 s | **Triggered** |

The run produced 2,572 Whisper segments and 925 FINE locators. The CAS update
completed, but the concurrent request flood crossed the benchmark's 5 ms p95
latency threshold, so the measured session-disruption result is **Yes**. The
raw generated audio/alignment artifacts are intentionally not distributed;
reproduction requires independently obtained public-domain audio and a
compatible Whisper endpoint.

The associated seeded 100-point lookup report measured 100% exact FINE
sentence/XPointer identity against that compiled index. Audio → ebook → audio
landed at sentence starts: median drift 5.3 s and mean drift 8.2 s across all
samples, including alignment gaps. FINE lookup p95 was 0.003 ms. The historical
report recorded COARSE median/mean/p90 errors rather than p95 error; no p95 error
is inferred or published.

## Quick start

Prerequisites:

- Docker Engine with the Compose plugin
- Python 3.11+ for host-side runner/systemd use
- Audiobookshelf and BookOrbit endpoints reachable from the host runner
- An initialized BookShift state database

From a clean checkout:

```bash
cp .env.example .env
# Replace example.invalid URLs and set credentials.
mkdir -p data data/benchmarks/artifacts library

docker compose build bookshift-core
docker compose run --rm bookshift-core \
  python3 -m bookshift init-db --db /data/pipeline_state.db
docker compose up -d bookshift-core

curl -fsS http://127.0.0.1:18001/api/v1/sync/health \
  | python3 -m json.tool
```

This starts an empty but working API on a new checkout. It will report no active
books until `pipeline_state.db` contains paired books and COARSE/FINE mapping
paths.

For complete Docker, host-runner, user-systemd, logging, dry-run, live-operation,
and rollback instructions, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## CLI

```bash
bookshift server                         # HTTP mapping API
bookshift worker --once --dry-run        # Inspect one alignment-worker cycle
bookshift sync --once                    # Reconciliation dry-run
bookshift sync --once --execute          # One live reconciliation cycle
bookshift init-db                        # Initialize/migrate state
bookshift benchmark --books dr_jekyll    # Public Tier 1 benchmark
```

The position API supports both directions:

```text
GET /api/v1/sync/position?book_id=1&timestamp=276.0
GET /api/v1/sync/position?book_id=1&xpointer=<URL-encoded-XPointer>
```

See [docs/openapi.yaml](docs/openapi.yaml) for the API schema.

## Configuration

[.env.example](.env.example) documents the available container, runner, and
optional alignment settings. Important variables are:

| Variable | Required for | Purpose |
|---|---|---|
| `BOOKSHIFT_DB_PATH` | API, worker, runner | Shared SQLite state database |
| `BOOKSHIFT_SYNC_HOST`, `BOOKSHIFT_SYNC_PORT` | API and runner resolver | API bind/address; `18001` is the BookShift default |
| `BOOKSHIFT_ABS_URL`, `BOOKSHIFT_ABS_TOKEN` | Runner | Audiobookshelf API access |
| `BOOKSHIFT_BOOKORBIT_URL` | Runner | BookOrbit API base URL |
| `BOOKSHIFT_BOOKORBIT_USERNAME`, `BOOKSHIFT_BOOKORBIT_PASSWORD` | Runner | BookOrbit login |
| `BOOKSHIFT_BOOKS_DIR`, `BOOKSHIFT_AUDIOBOOKS_DIR` | Alignment/runtime | Mounted media locations |
| `BOOKSHIFT_ANALYSIS_DIR` | Mapping API | Locator/map directory |
| `BOOKSHIFT_ARTIFACTS_DIR` | Compose only | Host directory mounted for mapping artifacts |
| `BOOKSHIFT_M1_WHISPER_URL` | Optional FINE alignment | External Whisper-compatible worker |
| `BOOKSHIFT_STORYTELLER_URL` | Optional alignment workflow | Storyteller service URL |

Use host-reachable URLs in the systemd environment file. Do not copy Docker-only
paths such as `/data/pipeline_state.db` into the host runner configuration.

## Limitations

- Automatic clean-checkout library discovery/pairing is not yet a public CLI
  workflow; the runtime needs populated active-book and mapping records.
- Failed network writes retry on a later timer cycle. There is no persistent
  retry queue.
- FINE availability depends on suitable alignment data and successfully
  compiled EPUB locators. Coverage gaps can fall back to an earlier locator or
  COARSE chapter mapping.
- The Compose API container does not run reconciliation. Use the host runner or
  another explicit scheduler.
- Exact locator/CFI/XPointer data is authoritative. Numeric BookOrbit percentage
  can be an audiobook-percentage fallback when EPUB percentage is unavailable.
- The bundled Tier 1 fixture uses synthetic chapter timing and does not measure
  Whisper or production ingestion. Historical FINE results require external
  public-domain audio and are not reproduced in CI.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/ -v
.venv/bin/python benchmarks/run_benchmarks.py \
  --books dr_jekyll --skip-disruption --output /tmp/bookshift-tier1.md
docker build -t bookshift:test .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
