"""Run from any cwd: python <workspace>/scripts/process_data.py <command>."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdlab_app.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
