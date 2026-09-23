"""Run from any working directory with the project's Python environment."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from intent_platform.evaluation_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
