"""pytest configuration — offscreen Qt, and an isolated state directory."""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Point state at a throwaway directory before anything imports the app.
# Tests build real MainWindows, which save panes, tabs and geometry on
# close; without this they write the repo's state/session.json and destroy
# whatever the developer had open.
_STATE_TMP = tempfile.mkdtemp(prefix="traverse-test-state-")
os.environ["TRAVERSE_STATE_DIR"] = _STATE_TMP

# Ensure project root is on sys.path
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
