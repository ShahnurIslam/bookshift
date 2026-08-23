# BookShift Benchmark Results

Generated: 2026-08-14T12:26:09+00:00

Public release tables use **public-domain titles only**.

This file preserves two kinds of evidence:

- **Current reproducible Tier 1:** a fast COARSE-path check generated from the
  committed EPUB fixture and a synthetic audiobook chapter catalogue. It does
  not transcribe audio and is not a full-book production timing.
- **Historical measured runs:** results recorded by the benchmark harness on
  2026-08-14 using independently obtained public-domain LibriVox audio and a
  live Whisper endpoint. The report and benchmark code are committed, but the
  downloaded audio and generated alignment maps are intentionally excluded.

No p95 landing-error figure was measured. The accuracy tables publish only the
median, mean, p90, maximum, exact-match, and latency percentiles actually
recorded by the harness.

## Live FINE — remote Whisper (2026-08-14)

Measured end-to-end against a remote whisper.cpp server (`BOOKSHIFT_M1_WHISPER_URL`). Fixture: Gutenberg EPUB *The Strange Case of Dr Jekyll and Mr Hyde* plus the public-domain LibriVox (Kristin Hughes) 64 kbps MP3s. Transcription was **not** skipped.

```bash
python3 benchmarks/run_benchmarks.py --books dr_jekyll --no-skip-transcription
```

| Title | Audio Duration | Ingest → COARSE Ready | First Usable Sync | FINE Ready | Atomic Promotion | Mean COARSE Error | Session Disrupted |
|---|---|---|---|---|---|---|---|
| The Strange Case of Dr Jekyll and Mr Hyde | 02:49:52 | 0.02s | 0.02s | 28m 35s | 32ms | ± 7.7s | Yes |

### Live FINE notes

- Audio wall duration: **10192.8 s** (02:49:52)
- Whisper wall clock (transcribe + EPUB locator compile): **1715.2 s** (28m 35s)
- Real-time factor: **~5.9×**
- Whisper segments: **2572**; FINE locators compiled: **925**
- Per-file Whisper times (s): 128, 163, 57, 101, 148, 125, 53, 278, 159, 470
- Atomic CAS promotion: **32 ms**
- Mean \|COARSE − FINE\| error at sampled timestamps: **± 7.7 s**
- Session disrupted = Yes because the concurrent 100 req/s promotion flood recorded p95 latency ≥ 5 ms (CAS itself still completed). Zero HTTP failures were required for a clean pass; this row is reported as measured.

## Bidirectional 100-point spot-check (2026-08-14)

Same live FINE table as above (925 locators). Uniform samples over FINE-covered audio `[2045 s, 10182 s]`, seed 42.

```bash
python3 benchmarks/run_benchmarks.py --spot-check 100 --books dr_jekyll
```

| Sync Direction | Mode | Precision / Error | Round-Trip Retention | Resolution Latency (p95) |
|---|---|---|---|---|
| **Audio ➔ Ebook** | **FINE** | **100% exact sentence match** (mean/max error 0 s) | 100% same sentence | **0.003 ms** |
| **Audio ➔ Ebook** | **COARSE** | Median ±577 s; mean ±779 s; p90 ±1884 s | Chapter context | **0.004 ms** |
| **Ebook ➔ Audio** | **FINE** | **100% exact sentence start** (mean/max error 0 s) | **100% exact XPointer** | **0.003 ms** |
| **Ebook ➔ Audio** | **COARSE** | Median ±1725 s; mean ±1884 s; p90 ±3549 s | Chapter context | **0.004 ms** |

Pooled FINE (both directions, N=200): exact match **100%**, mean error **0 s**. Pooled COARSE: median **1026 s**, mean **1331 s**, p90 **2901 s**, max **4092 s**. Lookup latency: FINE mean **0.002 ms** / p95 **0.003 ms**; COARSE mean **0.003 ms** / p95 **0.004 ms**. `SPOT_CHECK_ACCEPT=PASS`.

### What the FINE round-trip drift means

Audio ➔ ebook ➔ audio does **not** return the original timestamp. The resolver maps `t` to a sentence XPointer, then that XPointer back to the **sentence start**. Playback therefore resumes at the beginning of the spoken sentence rather than mid-syllable — expected quantization, not lookup error.

On this fixture:

| Sample set | N | Mean \|t − t′\| | Median | Max |
|---|---|---|---|---|
| All random timestamps | 100 | **8.2 s** | 5.3 s | 54.1 s |
| Query already inside a compiled sentence window | 30 | **1.4 s** | 1.3 s | (≤ sentence length; mean window **2.8 s**) |
| Query in an alignment gap (snap to previous locator) | 70 | 11.1 s | — | 54.1 s |

The 1.4 s in-sentence figure is half a typical spoken sentence — the quantization the product intends. The 8.2 s headline mean is dominated by **coverage gaps** (925 locators from 2,572 Whisper segments; 590 inter-sentence gaps, mean gap 9.4 s). Round-trip **identity** still held for every trial: same sentence XPointer after audio ➔ ebook ➔ audio, and exact XPointer after ebook ➔ audio ➔ ebook.

### What the COARSE errors mean

COARSE locators are chapter-level (`/body/DocFragment[N]/body/text().0`). Spot-check Δt is `|t_query − t_FINE(XPointer_coarse)|`: the FINE timestamp of that chapter locator (first aligned sentence in the DocFragment), or `|t_coarse − t_FINE(sentence)|` in the reverse direction (COARSE reverse snaps to chapter `abs_start`). Errors of hundreds to thousands of seconds are **chapter duration**, not a resolver bug.

This is a different metric from Gate 5 “Mean COARSE Error ± 7.7 s”, which compared the COARSE *returned playback time* (the query `t` itself on a forward lookup) to the FINE sentence start. Both numbers are real; they answer different questions.

## Historical COARSE / compile-only (no live Whisper)

Earlier Gate 5 run (`--skip-transcription`, 2026-08-14T08:40:57Z). FINE Ready on the last row is in-process locator compile from an existing alignment map, not ASR.

These archival rows are retained for transparency. Their generated catalogues
and maps are not distributed, so they are not clean-checkout reproduction
targets. Use Tier 1 below for the supported public-fixture check.

| Title | Audio Duration | Ingest → COARSE Ready | First Usable Sync | FINE Ready | Atomic Promotion | Mean COARSE Error | Session Disrupted |
|---|---|---|---|---|---|---|---|
| The Strange Case of Dr Jekyll and Mr Hyde | 02:35:10 | 0.09s | 0.12s | — | — | ± 0.0s | No |
| The Picture of Dorian Gray (public-domain scale) | 09:04:00 | 0.10s | 0.10s | — | — | ± 0.0s | No |
| Pride and Prejudice (public-domain scale) | 11:35:00 | 0.21s | 0.21s | 53.06s | 24ms | ± 128.1s | No |

## Acceptance Criteria

- COARSE readiness must be **< 2.0s** from ingest start for every title.
- Atomic promotion must complete without session disruption (0 failed requests, p95 latency < 5ms during switch).
- Mean COARSE error is measured against FINE ground truth when available (N=50 samples).
- Spot-check FINE exact-match rate must be **100%** vs the compiled index; ebook ➔ audio ➔ ebook must retain the same XPointer.

The generated report template in this snapshot still labels the promotion
threshold “p99”; `bookshift/benchmarks/disruption.py` actually calculates and
gates on p95. The criterion above follows the implementation and recorded
result.

## CI Tier 1 (public export)

The GitHub Actions pipeline runs the public-domain fixture only (COARSE path, no live Whisper):

```bash
python3 benchmarks/run_benchmarks.py --books dr_jekyll --skip-disruption
```

Latest Tier 1 COARSE run: **PASS** (COARSE ready **0.02s**, first usable sync
**0.05s**, 2026-08-23). The committed Dr Jekyll EPUB is paired with a synthetic
chapter catalogue scaled to **02:49:52**; no audiobook or Whisper inference is
used, and disruption testing is skipped. These are fixture/harness timings,
not full-book ingestion timings.

Latest live FINE run: **completed** (FINE ready 28m 35s, CAS 32ms, 2026-08-14).
Latest 100-point spot-check: **PASS** (FINE exact 100%, ebook round-trip 100%, FINE p95 0.003 ms, 2026-08-14).
