"""Entry point for ``python -m gmpf_sync``; defers to the CLI."""
from gmpf_sync.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
