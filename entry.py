"""PyInstaller entry point — keeps the package import path intact."""
from gmpf_sync.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
