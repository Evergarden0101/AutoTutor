#!/usr/bin/env python3
"""Entry point for the packaged executable.

PyInstaller executes its entry script as a top-level module rather than as
part of a package, so ``autotutor/__main__.py`` (which uses relative imports)
cannot be used directly.  This launcher imports the package absolutely
instead.  Running the project from source still works via
``python -m autotutor``.
"""

import multiprocessing
import sys


def main() -> int:
    # Harmless when unused; required if a frozen build ever spawns a process.
    multiprocessing.freeze_support()
    from autotutor.app import main as run

    return run()


if __name__ == "__main__":
    sys.exit(main())
