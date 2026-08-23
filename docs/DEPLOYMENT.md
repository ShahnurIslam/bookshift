# Deployment

BookShift has separate runtime roles:

1. `bookshift-core` runs the HTTP timestamp/locator mapping API.
2. `bookshift-worker` optionally orchestrates already-enqueued external
   alignment work and promotes precomputed FINE artifacts.
3. `bookshift sync` polls Audiobookshelf and BookOrbit and reconciles progress.

Docker Compose starts the first role and, when requested, the second. It does
not schedule the third. The supplied user-level systemd timer is one supported
way to invoke reconciliation on the host.

## Prerequisites

- Docker Engine and the Docker Compose plugin
- Python 3.11+ for the host runner
- Audiobookshelf and BookOrbit reachable from the host
- A writable directory for `pipeline_state.db`
- Read-only media/locator mounts as needed by your alignment workflow

The public beta does not yet expose automatic library discovery and pairing as
a CLI command. A clean checkout can initialize and validate the API, but real
reconciliation requires active book, ABS item, BookOrbit file, and mapping
records already present in the state database.

## Install the API container

These commands assume the checkout is `$HOME/bookshift`; another location is
fine if Compose and the later systemd template are adjusted consistently.

```bash
cd "$HOME/bookshift"
cp .env.example .env
mkdir -p data data/benchmarks/artifacts library
```

Edit `.env`. Replace all `example.invalid` URLs and set only credentials needed
by your enabled components. Never commit the populated file.

Build and initialize the database:

```bash
docker compose build bookshift-core
docker compose run --rm bookshift-core \
  python3 -m bookshift init-db --db /data/pipeline_state.db
```

Start only the mapping API:

```bash
docker compose up -d bookshift-core
docker compose ps bookshift-core
curl -fsS http://127.0.0.1:18001/api/v1/sync/health \
  | python3 -m json.tool
```

The Compose bind mounts are:

- `./data` → `/data` writable state
- `./library` → `/library` read-only media
- `BOOKSHIFT_ARTIFACTS_DIR` → `/data/benchmarks/artifacts` read-only maps

The container runs as UID/GID 1000. Ensure the host state directory is writable
by that identity and by the user that runs reconciliation.

### Optional alignment worker

The worker only processes eligible jobs already recorded in the database:

```bash
docker compose --profile worker up -d bookshift-worker
docker compose logs --tail=100 bookshift-worker
```

FINE alignment may require a reachable Whisper-compatible endpoint and/or
Storyteller data, depending on the configured workflow. The public worker does
not bundle ASR or title-specific compiler scripts; promotion requires valid
`fine_map_path` and `fine_locator_table_path` artifacts already recorded for
the book. COARSE mappings remain usable while FINE work is pending.

## Install the host reconciliation runner

Create a virtual environment from the same checkout:

```bash
cd "$HOME/bookshift"
python3 -m venv .venv
.venv/bin/pip install -e .
```

Create a host-side environment file:

```bash
mkdir -p "$HOME/.config/bookshift"
chmod 700 "$HOME/.config/bookshift"
```

Save the following as `$HOME/.config/bookshift/reconcile.env`, replacing the
example URLs, credentials, and username in the database path:

```dotenv
BOOKSHIFT_DB_PATH=/home/your-user/bookshift/data/pipeline_state.db
BOOKSHIFT_SYNC_HOST=127.0.0.1
BOOKSHIFT_SYNC_PORT=18001

BOOKSHIFT_ABS_URL=https://audiobookshelf.example.invalid
BOOKSHIFT_ABS_TOKEN=replace-me

BOOKSHIFT_BOOKORBIT_URL=https://bookorbit.example.invalid
BOOKSHIFT_BOOKORBIT_USERNAME=replace-me
BOOKSHIFT_BOOKORBIT_PASSWORD=replace-me
```

Protect it:

```bash
chmod 600 "$HOME/.config/bookshift/reconcile.env"
```

`BOOKSHIFT_DB_PATH` must be the host path to the same SQLite file mounted as
`/data/pipeline_state.db` in Docker. The URLs must be reachable from the host,
not merely from a Compose network.

### Dry-run first

Load the environment and perform one non-writing cycle:

```bash
set -a
. "$HOME/.config/bookshift/reconcile.env"
set +a

"$HOME/bookshift/.venv/bin/bookshift" sync --once
```

Results are emitted as one JSON object per active title. `status=dry_run`
indicates that a write would have occurred. `status=skip` is normal for a first
baseline, unchanged state, a write echo, or a delta below the configured
threshold.

Run one live cycle only after reviewing the dry-run:

```bash
"$HOME/bookshift/.venv/bin/bookshift" sync --once --execute
```

## Install the user-level systemd timer

The examples assume the checkout is `$HOME/bookshift` and the virtual
environment is `$HOME/bookshift/.venv`. Edit `WorkingDirectory` and `ExecStart`
if yours differ.

```bash
mkdir -p "$HOME/.config/systemd/user"
cp deploy/systemd/bookshift-reconcile.service.example \
  "$HOME/.config/systemd/user/bookshift-reconcile.service"
cp deploy/systemd/bookshift-reconcile.timer.example \
  "$HOME/.config/systemd/user/bookshift-reconcile.timer"

systemctl --user daemon-reload
systemctl --user enable --now bookshift-reconcile.timer
```

The timer invokes a one-shot host process every 60 seconds. It does not restart
or enter the API container.

Check scheduling and the latest result:

```bash
systemctl --user list-timers bookshift-reconcile.timer
systemctl --user status bookshift-reconcile.timer
systemctl --user status bookshift-reconcile.service
journalctl --user -u bookshift-reconcile.service -n 100 --no-pager
```

Follow future cycles:

```bash
journalctl --user -u bookshift-reconcile.service -f
```

If the timer must run while the user is logged out, consult your distribution's
documentation for user lingering. Enabling lingering changes host policy and is
not required for interactive testing.

## Safe disable and rollback

Stop future reconciliation without touching progress or persistent data:

```bash
systemctl --user disable --now bookshift-reconcile.timer
systemctl --user stop bookshift-reconcile.service
```

Confirm no future run is scheduled:

```bash
systemctl --user list-timers bookshift-reconcile.timer
```

To remove the user units while retaining the checkout and data:

```bash
mv "$HOME/.config/systemd/user/bookshift-reconcile.service" \
  "$HOME/.config/systemd/user/bookshift-reconcile.service.disabled"
mv "$HOME/.config/systemd/user/bookshift-reconcile.timer" \
  "$HOME/.config/systemd/user/bookshift-reconcile.timer.disabled"
systemctl --user daemon-reload
```

Disabling the runner does not reverse progress already written. Keep the state
database when rolling back code so activity-provenance baselines are preserved.

Stopping the API is separate:

```bash
docker compose stop bookshift-core
```

## Operational behavior

- Network/write failures are retried by a later timer cycle; there is no
  persistent retry queue.
- Successful writes are read back before BookShift records observation state.
- Stale ABS revisions and simultaneous source changes are not applied.
- A valid exact FINE locator is authoritative. A numeric BookOrbit percentage
  can fall back to audiobook percentage when EPUB percentage is unavailable.
- COARSE chapter mapping remains available before FINE promotion and for
  unresolved FINE positions.
- Generated maps, databases, logs, downloaded media, and credentials are local
  runtime data and are excluded from publication.
