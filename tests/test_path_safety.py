"""Portable link and Windows reparse-point checks."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import path_safety  # noqa: E402


class FakePath:
    def __init__(self, flags, junction=False):
        self.flags = flags
        self.junction = junction

    def is_symlink(self):
        return False

    def is_junction(self):
        return self.junction

    def lstat(self):
        return SimpleNamespace(st_file_attributes=self.flags)


class PathSafetyTests(unittest.TestCase):
    def test_windows_reparse_fallback_flags_junction_without_python_312(self):
        class OldPythonPath:
            def is_symlink(self):
                return False
            def lstat(self):
                return SimpleNamespace(st_file_attributes=0x400)
        with patch.object(path_safety, "os", SimpleNamespace(name="nt")):
            self.assertTrue(path_safety.is_link_or_reparse(OldPythonPath()))

    def test_windows_normal_folder_allowed(self):
        with patch.object(path_safety, "os", SimpleNamespace(name="nt")):
            self.assertFalse(path_safety.is_link_or_reparse(FakePath(0)))

    def test_junction_api_blocks_without_stat(self):
        self.assertTrue(path_safety.is_link_or_reparse(FakePath(0, junction=True)))

    def test_posix_nonlink_does_not_use_windows_stat(self):
        with patch.object(path_safety, "os", SimpleNamespace(name="posix")):
            self.assertFalse(path_safety.is_link_or_reparse(FakePath(0x400)))


if __name__ == "__main__":
    unittest.main()
