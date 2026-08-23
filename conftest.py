"""Put the repo root on sys.path so tests import engine/, sim/, report/ from anywhere."""

import sys
from pathlib import Path

ROOT = str(Path(__file__).parent.resolve())
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
