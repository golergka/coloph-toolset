"""Import stays independent of runtime frameworks and process output."""

import subprocess
import sys
from pathlib import Path


def test_isolated_import_has_no_runtime_dependencies():
    subprocess.run(
        [sys.executable, "-I", str(Path(__file__).with_name("import_probe.py"))],
        check=True,
    )
