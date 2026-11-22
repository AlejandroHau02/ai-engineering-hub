"""Shared read-only guard against symlinks and Windows reparse points.

Path.is_junction() exists only in newer Python versions; the Windows stat
fallback covers directory junctions on Python 3.10/3.11 as well.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat


def is_link_or_reparse(path: Path) -> bool:
    """Conservatively reject symlinks, junctions and Windows reparse points."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None and is_junction():
        return True
    if os.name != "nt":
        return False
    try:
        mode = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return bool(getattr(mode, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
