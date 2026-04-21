"""Configure the package path for pytest without installing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
