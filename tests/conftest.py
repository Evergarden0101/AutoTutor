"""Test configuration.

Redirects the application data directory into a throw-away folder *before*
:mod:`autotutor.config` is imported, so running the suite never reads or
overwrites the real settings file of whoever is running it.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TEMP_HOME = Path(tempfile.mkdtemp(prefix="autotutor-tests-"))
os.environ["AUTOTUTOR_HOME"] = str(_TEMP_HOME)

# Make the project importable when pytest is run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
