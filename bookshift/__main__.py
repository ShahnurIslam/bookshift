"""Allow ``python -m bookshift`` as the unified CLI entry point."""

from bookshift.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
