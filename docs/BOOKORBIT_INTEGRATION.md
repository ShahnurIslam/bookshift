# BookOrbit Integration

BookShift interacts with BookOrbit **only through its REST API**. Locator generation (CREngine XPointers and EPUB CFIs) runs **in-process** inside BookShift; no `docker exec`, container IDs, or host volume staging under BookOrbit appdata are required.

## Authentication

| Step | Endpoint | Method | Body |
|------|----------|--------|------|
| Login | `/api/v1/auth/login` | `POST` | `{"username": "...", "password": "..."}` |

Response includes `accessToken` (Bearer). BookShift's `BookOrbitAdapter.authenticate()` caches this token for subsequent calls.

Environment variables (see `bookshift/config.py`):

- `BOOKORBIT_URL` / `BOOKSHIFT_BOOKORBIT_URL` (default `http://localhost:3010`)
- `BOOKORBIT_USERNAME`, `BOOKORBIT_PASSWORD`

## Progress Endpoints

| Operation | Endpoint | Method |
|-----------|----------|--------|
| List book progress | `/api/v1/books/{book_id}/progress` | `GET` |
| Read file progress | `/api/v1/books/files/{file_id}/progress` | `GET` |
| Write file progress | `/api/v1/books/files/{file_id}/progress` | `POST` |

Adapter methods:

- `get_book_progress(book_id, file_id=None)`
- `update_book_progress(book_id, file_id, progress_data)`

## Progress Payload Schema

BookShift writes the following progress payload:

```json
{
  "percentage": 0.8341,
  "cfi": "epubcfi(/6/16!/4/30/2/1:0)",
  "pageNumber": null,
  "positionSeconds": null,
  "koreaderProgress": "/body/DocFragment[8]/body/p[11]/span/text().0",
  "koboLocationSource": null,
  "koboLocationType": null,
  "koboLocationValue": null,
  "koboContentSourceProgressPercent": null
}
```

| Field | Role |
|-------|------|
| `percentage` | Ebook progress 0.0–1.0 (BookOrbit/KOReader primary %) |
| `cfi` | EPUB Canonical Fragment Identifier (optional but stored) |
| `koreaderProgress` | CREngine XPointer string (exact KOReader anchor) |

## In-Process Locator Generation

Module: `bookshift/domain/locators/`

| Component | Purpose |
|-----------|---------|
| `crengine.py` | Parse/build `/body/DocFragment[N]/body/.../text().offset` |
| `cfi.py` | Parse/build `epubcfi(...)` fragments |
| `compiler.py` | EPUB spine walk, collapsed-text search, alignment map enrichment |

The public implementation is in the `bookshift.domain.locators` package. The
reconciliation runner consumes the resulting locator data through the mapping
API; it does not execute code inside BookOrbit or depend on private analysis
scripts.

## COARSE-First Invariant

Progressive sync preserves these invariants:

1. **COARSE** chapter maps become available after chapter pairing.
2. **FINE** locators promote only after alignment compilation and CAS success.
3. BookOrbit REST progress writes continue to use whichever sync mode is active in `pipeline_state.db`.
