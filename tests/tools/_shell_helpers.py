"""Host paths and executables embedded in bash-based integration tests."""

import shlex
import sys
from pathlib import Path

from tools.environments.local import _bash_safe_path


def shell_path(path: str | Path) -> str:
    return shlex.quote(_bash_safe_path(str(path)))


PYTHON = shell_path(sys.executable)
