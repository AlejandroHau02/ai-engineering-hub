#!/usr/bin/env python3
"""Test helper: leave a real interrupted transaction in a DISPOSABLE home.

    python.exe -I -B desktop/scripts/stage_interruption.py <scripts-dir> <home> [writes]

Runs a real `sync.py --apply` from ``scripts-dir`` (the bundled core) against
``home`` and kills the process abruptly (``os._exit``) after ``writes``
destination writes, exactly like a crash or power loss would. The journal and
workspace are left for `--recover`. Refuses the real user profile. Never
bundled with the app; used by CI and tests only.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> int:
    scripts, home = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    writes = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    profile = Path(os.environ.get("USERPROFILE") or Path.home()).resolve()
    if home == profile or profile.is_relative_to(home):
        print("refusing to touch the real user profile", file=sys.stderr)
        return 2
    sys.path.insert(0, str(scripts))
    import sync  # noqa: PLC0415 - from the bundle given on the command line

    original, count = sync.write_with_backup, [0]

    def write(*args, **kwargs):
        original(*args, **kwargs)
        count[0] += 1
        if count[0] == writes:
            os._exit(3)  # abrupt: no rollback, no cleanup

    sync.write_with_backup = write
    sync.main(["--target", "both", "--apply", "--home", str(home)])
    print("the apply finished before the interruption point", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
