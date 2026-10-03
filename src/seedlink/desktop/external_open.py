"""Open exported artifacts without attaching their applications to SeedLink."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def open_local_path_detached(path: Path) -> bool:
    """Ask the desktop to open *path* without inheriting our terminal streams."""

    resolved = str(path.resolve())
    try:
        if sys.platform == "win32":
            os.startfile(resolved)  # type: ignore[attr-defined]
            return True

        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen(
            (opener, resolved),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError:
        return False
    return True
