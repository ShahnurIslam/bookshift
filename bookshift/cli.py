"""Unified BookShift CLI entry point."""

from __future__ import annotations

import argparse
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bookshift",
        description="BookShift — audiobook ↔ ebook sync (Docker-first deployment)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    server_p = sub.add_parser("server", help="Run the HTTP sync API")
    server_p.add_argument("--host", default=None)
    server_p.add_argument("--port", type=int, default=None)
    server_p.add_argument("--db", type=Path, default=None)
    server_p.add_argument("--mark-ready", action="store_true")
    server_p.add_argument("--reload-only", action="store_true")

    worker_p = sub.add_parser("worker", help="Run the alignment worker daemon")
    worker_p.add_argument("--db", type=Path, default=None)
    worker_p.add_argument("--m1-url", default=None)
    worker_p.add_argument("--interval", type=float, default=30.0)
    worker_p.add_argument("--once", action="store_true")
    worker_p.add_argument("--dry-run", action="store_true")
    worker_p.add_argument(
        "--simulate-m1", choices=("auto", "online", "offline"), default="auto"
    )
    worker_p.add_argument("--simulate-smil-complete", action="store_true")
    worker_p.add_argument("--mark-ready", action="store_true")

    sync_p = sub.add_parser("sync", help="Run ABS ↔ BookOrbit progress sync bridge")
    sync_p.add_argument("--execute", action="store_true")
    sync_p.add_argument("--interval", type=float, default=60.0)
    sync_p.add_argument("--once", action="store_true")
    sync_p.add_argument("--db", type=Path, default=None)

    init_p = sub.add_parser("init-db", help="Initialize or migrate the SQLite database")
    init_p.add_argument("--db", type=Path, default=None)

    bench_p = sub.add_parser("benchmark", help="Run progressive-sync benchmarks")
    bench_p.add_argument("--catalog", type=Path, default=None)
    bench_p.add_argument("--books", default="")
    bench_p.add_argument("--skip-compile", action="store_true")
    bench_p.add_argument("--skip-disruption", action="store_true")
    bench_p.add_argument("--no-skip-transcription", action="store_true")
    bench_p.add_argument(
        "--spot-check",
        type=int,
        nargs="?",
        const=100,
        default=None,
        metavar="N",
        help="Run N-point bidirectional FINE/COARSE spot-check (default N=100)",
    )
    bench_p.add_argument("--seed", type=int, default=None)
    bench_p.add_argument("--output", type=Path, default=None)

    return parser


def _cmd_server(args: argparse.Namespace) -> int:
    from bookshift.config import get_settings
    from bookshift.server import sync_server

    cfg = get_settings()
    argv: list[str] = []
    if args.host is not None:
        argv.extend(["--host", args.host])
    elif cfg.sync_host:
        argv.extend(["--host", cfg.sync_host])
    if args.port is not None:
        argv.extend(["--port", str(args.port)])
    if args.db is not None:
        argv.extend(["--db", str(args.db)])
    if args.mark_ready:
        argv.append("--mark-ready")
    if args.reload_only:
        argv.append("--reload-only")
    return sync_server.main(argv)


def _cmd_worker(args: argparse.Namespace) -> int:
    from bookshift.worker import alignment_worker

    argv: list[str] = []
    if args.db is not None:
        argv.extend(["--db", str(args.db)])
    if args.m1_url is not None:
        argv.extend(["--m1-url", args.m1_url])
    argv.extend(["--interval", str(args.interval)])
    if args.once:
        argv.append("--once")
    if args.dry_run:
        argv.append("--dry-run")
    argv.extend(["--simulate-m1", args.simulate_m1])
    if args.simulate_smil_complete:
        argv.append("--simulate-smil-complete")
    if args.mark_ready:
        argv.append("--mark-ready")
    return alignment_worker.main(argv)


def _cmd_sync(args: argparse.Namespace) -> int:
    from bookshift.reconciliation import main as sync_main

    argv: list[str] = []
    if args.execute:
        argv.append("--execute")
    argv.extend(["--interval", str(args.interval)])
    if args.once:
        argv.append("--once")
    if args.db is not None:
        argv.extend(["--db", str(args.db)])
    return sync_main(argv)


def _cmd_init_db(args: argparse.Namespace) -> int:
    from bookshift.config import get_settings
    from bookshift.storage.state_repo import SQLiteStateRepository
    from bookshift.storage.init_schema import init_db

    cfg = get_settings()
    db_path = Path(args.db) if args.db is not None else cfg.db_path
    repo = SQLiteStateRepository(db_path, cfg)
    conn = repo.connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='logical_books'"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        init_db(db_path)
    repo.migrate_schema()
    print(f"BookShift database ready: {db_path}", flush=True)
    return 0


def _cmd_benchmark(args: argparse.Namespace) -> int:
    from benchmarks.run_benchmarks import main as bench_main

    argv: list[str] = []
    if args.catalog is not None:
        argv.extend(["--catalog", str(args.catalog)])
    if args.books:
        argv.extend(["--books", args.books])
    if args.skip_compile:
        argv.append("--skip-compile")
    if args.skip_disruption:
        argv.append("--skip-disruption")
    if getattr(args, "no_skip_transcription", False):
        argv.append("--no-skip-transcription")
    if getattr(args, "spot_check", None) is not None:
        argv.extend(["--spot-check", str(args.spot_check)])
    if getattr(args, "seed", None) is not None:
        argv.extend(["--seed", str(args.seed)])
    if args.output is not None:
        argv.extend(["--output", str(args.output)])
    return bench_main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "server": _cmd_server,
        "worker": _cmd_worker,
        "sync": _cmd_sync,
        "init-db": _cmd_init_db,
        "benchmark": _cmd_benchmark,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
