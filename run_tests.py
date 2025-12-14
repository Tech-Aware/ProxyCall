# run_tests.py
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent
    cmd = [sys.executable, "-m", "pytest", "tests", *sys.argv[1:]]
    subprocess.run(cmd, check=True, cwd=project_root)
