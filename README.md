# ⚡ BookShift

> **Switch seamlessly between reading and listening without losing your place. Instantly.**

[![CI](https://github.com/ShahnurIslam/storyteller-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/ShahnurIslam/storyteller-sync/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](Dockerfile)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](pyproject.toml)

---

### The Problem

You’re reading an EPUB on your e-reader on the train. You step off, put on your headphones, and want to pick up the audiobook exactly where you left off.

Existing solutions either:

1. **Force you to wait hours** for a heavy Whisper transcription job before you can sync a single page.
2. **Break entirely** if chapter structures don't match 1:1.
3. **Lock you into proprietary cloud ecosystems.**

### The Fix: Progressive Synchronization

BookShift flips the script with **Progressive Sync**:

1. **COARSE in under a second.** Pair chapters, load the locator index, and expose a usable sync API immediately — even when EPUB and audiobook TOCs are not 1:1.
2. **FINE in the background.** Remote Whisper alignment upgrades that map to sentence-exact CREngine XPointers while you keep reading and listening.
3. **Atomic promotion.** COARSE → FINE switches with compare-and-swap. No waiting for the full job, no proprietary cloud.

```
INGEST → COARSE READY (<2s) → BACKGROUND ALIGNMENT → FINE PROMOTION (atomic CAS)
         ▲ usable sync API          ▲ Whisper / SMIL jobs      ▲ keep the session
```

| Mode | Availability | Precision | Use case |
|---|---|---|---|
| **COARSE** | Seconds after ingest | Chapter-level | Immediate KOReader ↔ Audiobookshelf progress |
| **FINE** | After background alignment | Sentence-exact XPointers | Exact snippets, sub-millisecond lookups |

---

## Benchmarks

Public-domain titles only. Full methodology in [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md).

### Live FINE (remote Whisper, 2026-08-14)

End-to-end run of *The Strange Case of Dr Jekyll and Mr Hyde* (Gutenberg EPUB + LibriVox audio) against a remote Apple Silicon whisper.cpp server. Transcription was **not** skipped.

```bash
BOOKSHIFT_M1_WHISPER_URL="http://whisper-host:8000" \
  python3 benchmarks/run_benchmarks.py --books dr_jekyll --no-skip-transcription
```

| Title | Audio | COARSE Ready | First Usable Sync | FINE Ready | Atomic Promotion | Mean COARSE Error |
|---|---|---|---|---|---|---|
| The Strange Case of Dr Jekyll and Mr Hyde | 02:49:52 | **0.02s** | **0.02s** | **28m 35s** | **32ms** | ± 7.7s |

Whisper produced 2,572 segments; 925 compiled to FINE locators. Real-time factor ~6× (10193 s of audio in 28m 35s wall clock). CAS promotion completed in 32 ms.

### 🎯 100-Point Bidirectional Spot-Check Accuracy (*Dr Jekyll and Mr Hyde*)

We sampled 100 randomized positions (seed 42) in both sync directions against that live FINE index:

```bash
python3 benchmarks/run_benchmarks.py --spot-check 100 --books dr_jekyll
```

| Sync Direction | Mode | Precision / Error | Round-Trip Retention | Resolution Latency (p95) |
|---|---|---|---|---|
| **Audio ➔ Ebook** | **FINE** | **100% exact sentence match** | 100% same sentence (mean 8.2s to sentence start*) | **0.003 ms (3 µs)** |
| **Audio ➔ Ebook** | **COARSE** | Chapter-level boundary | Retains chapter context | **0.004 ms (4 µs)** |
| **Ebook ➔ Audio** | **FINE** | **100% exact sentence start** | **100% exact XPointer** | **0.003 ms (3 µs)** |
| **Ebook ➔ Audio** | **COARSE** | Chapter-level timestamp | Retains chapter context | **0.004 ms (4 µs)** |

\*Audio ➔ ebook ➔ audio always resumes at the **start of the resolved sentence**, never mid-syllable. That quantization averages **1.4 s** when the query already sits inside a compiled sentence (mean spoken window 2.8 s). The **8.2 s** all-sample mean (median 5.3 s, max 54 s) is pulled up by alignment gaps: 925 of 2,572 Whisper segments compiled to locators, so 70 of 100 random timestamps land between sentences and snap back to the previous locator. COARSE is chapter-level by design (see [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md)); it is not sentence-exact.

### COARSE / compile-only (no live Whisper)

CI and scale illustrations. FINE Ready on the last row is in-process locator compile from an existing alignment map (`--skip-transcription`), not ASR.

| Title | Audio Duration | Ingest → COARSE Ready | First Usable Sync | FINE Ready | Atomic Promotion | Mean COARSE Error | Session Disrupted |
|---|---|---|---|---|---|---|---|
| The Strange Case of Dr Jekyll and Mr Hyde | 02:35:10 | 0.09s | 0.12s | — | — | ± 0.0s | No |
| The Picture of Dorian Gray (public-domain scale) | 09:04:00 | 0.10s | 0.10s | — | — | ± 0.0s | No |
| Pride and Prejudice (public-domain scale) | 11:35:00 | 0.21s | 0.21s | 53.06s | 24ms | ± 128.1s | No |

COARSE readiness is under 2.0 seconds for every tested title.

---

## Quickstart (Docker Compose)

### 1. Clone and configure

```bash
git clone https://github.com/ShahnurIslam/storyteller-sync.git
cd storyteller-sync
cp .env.example .env
# Edit .env — set ABS, BookOrbit, and Whisper URLs for your network
```

### 2. Prepare data directories

```bash
mkdir -p data library
python3 -m bookshift init-db
```

Mount your **read-only** media library at `./library` and writable state at `./data`.

### 3. Start the sync API

```bash
docker compose -f docker-compose.yml up -d bookshift-core
curl -s http://127.0.0.1:18001/api/v1/sync/health | python3 -m json.tool
```

### 4. Optional: alignment worker

```bash
docker compose -f docker-compose.yml --profile worker up -d
```

---

## CLI

```bash
python3 -m bookshift server          # HTTP sync API (:18001)
python3 -m bookshift worker          # Background alignment worker
python3 -m bookshift init-db         # Initialize / migrate SQLite schema
python3 -m bookshift benchmark       # Progressive-sync benchmarks
python3 -m bookshift benchmark --spot-check 100 --books dr_jekyll
```

Live FINE (requires audio under `data/benchmarks/dr_jekyll/audio/` and a reachable Whisper server):

```bash
python3 -m bookshift benchmark --books dr_jekyll --no-skip-transcription
```

Install as a package:

```bash
pip install -e ".[dev]"
bookshift server --mark-ready
```

---

## KOReader setup

1. Ensure the BookShift sync API is reachable from your e-reader (LAN IP or Tailscale).
2. Configure KOReader's **Progress sync** plugin (or custom hook) to call:

   ```
   GET http://<bookshift-host>:18001/api/v1/sync/position?book_id=1&xpointer=<XPTR>
   ```

3. On page turn, KOReader sends the CREngine XPointer; BookShift returns the matching audiobook timestamp for Audiobookshelf.

Reverse lookup (audiobook → ebook):

```
GET /api/v1/sync/position?book_id=1&timestamp=276.0
```

See [docs/openapi.yaml](docs/openapi.yaml) for the full API schema.

---

## Audiobookshelf setup

1. Run BookShift on the same host or a reachable container (`host.docker.internal` in `.env`).
2. Set `BOOKSHIFT_ABS_URL` and `BOOKSHIFT_ABS_TOKEN` in `.env`.
3. Poll ABS playback, resolve timestamps via the sync API, then push reading progress to your ebook server.

BookShift discovers active titles from `pipeline_state.db` where `active_sync_mode` is `COARSE` or `FINE`.

---

## Architecture

```
┌─────────────┐   chapters/currentTime   ┌──────────────────┐
│ Audiobookshelf│ ─────────────────────► │  bookshift sync  │
└─────────────┘                          │  (optional)      │
                                         └────────┬─────────┘
┌─────────────┐   XPointer / timestamp            │
│  KOReader   │ ◄─────────────────────────────────┤
└─────────────┘                                   │
                                         ┌────────▼─────────┐
                                         │ bookshift server │
                                         │  :18001 COARSE/  │
                                         │       FINE API   │
                                         └────────┬─────────┘
                                                  │
                                         ┌────────▼─────────┐
                                         │ SQLite WAL state │
                                         │ + locator indexes│
                                         └──────────────────┘
```

- **Runtime:** Python 3.11+, stdlib only (no Redis, Celery, or ORM)
- **Container:** non-root user `bookshift` (UID 1000)
- **State:** SQLite with WAL mode (`BOOKSHIFT_DB_PATH`)

### Progress reconciliation rules

- A valid, non-approximate FINE XPointer is authoritative over COARSE or
  percentage-derived fallback mappings. COARSE remains available before FINE is
  ready and when an exact FINE locator cannot be resolved.
- Optional progress bridges should use `evaluate_reconciliation_plan()` and
  persist the verified BookOrbit `percentage` + `koreaderProgress` signature via
  `update_orbit_observation()`. This distinguishes reader movement from
  COARSE→FINE reinterpretation, unchanged state, and BookShift-authored write
  echoes.
- The first observation after installation or schema migration establishes a
  safe baseline. Genuine later reader movement may proceed in either direction,
  including an intentional move backwards.

---

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
python3 benchmarks/run_benchmarks.py --books dr_jekyll
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

MIT — see [LICENSE](LICENSE).
