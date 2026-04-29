"""pytest hook: put `src/` on sys.path so tests can `import gmpf_sync` directly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
