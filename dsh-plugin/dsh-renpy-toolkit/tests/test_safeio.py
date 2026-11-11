"""``safeio.py`` 测试：有界解压必须挡住压缩炸弹与损坏数据。

这些用例全部自造数据，不依赖真实游戏文件。
"""

from __future__ import annotations

import os
import unittest
import zlib

import _util

from renpy_toolkit import safeio


class TestDecompressLimited(unittest.TestCase):
    def test_roundtrip(self) -> None:
        blob = zlib.compress(b"hello ren'py" * 100)
        self.assertEqual(safeio.decompress_limited(blob), b"hello ren'py" * 100)

    def test_empty_payload(self) -> None:
        self.assertEqual(safeio.decompress_limited(zlib.compress(b"")), b"")

    def test_bomb_is_refused(self) -> None:
        """A 10 KiB blob expanding past the limit must not be allocated."""
        blob = zlib.compress(b"\0" * (8 * 1024 * 1024))
        self.assertLess(len(blob), 20 * 1024, "the fixture should really be small")
        with self.assertRaises(safeio.DecompressError) as ctx:
            safeio.decompress_limited(blob, limit=1024 * 1024, what="bomb")
        self.assertIn("exceeds", str(ctx.exception))
        self.assertIn("bomb", str(ctx.exception))

    def test_limit_is_exclusive_of_slack(self) -> None:
        payload = b"x" * 1000
        blob = zlib.compress(payload)
        self.assertEqual(safeio.decompress_limited(blob, limit=1000), payload)
        with self.assertRaises(safeio.DecompressError):
            safeio.decompress_limited(blob, limit=999)

    def test_corrupt_stream(self) -> None:
        with self.assertRaises(safeio.DecompressError) as ctx:
            safeio.decompress_limited(b"not zlib at all", what="junk")
        self.assertIn("corrupt", str(ctx.exception))

    def test_truncated_stream(self) -> None:
        blob = zlib.compress(b"abcdefghij" * 100)
        with self.assertRaises(safeio.DecompressError):
            safeio.decompress_limited(blob[: len(blob) // 2], what="short")

    def test_trailing_bytes_are_rejected(self) -> None:
        blob = zlib.compress(b"payload") + b"extra"
        with self.assertRaises(safeio.DecompressError) as ctx:
            safeio.decompress_limited(blob, what="trailing")
        self.assertIn("trailing", str(ctx.exception))

    def test_zero_limit_is_a_programming_error(self) -> None:
        with self.assertRaises(ValueError):
            safeio.decompress_limited(zlib.compress(b"x"), limit=0)

    def test_error_is_a_toolkit_error(self) -> None:
        from renpy_toolkit.errors import ToolkitError

        self.assertTrue(issubclass(safeio.DecompressError, ToolkitError))


class TestSafeJoin(unittest.TestCase):
    """Archive indexes carry their own paths, so joining must not trust them."""

    def setUp(self) -> None:
        self._dir = _util.tempdir_for(self)

    def test_plain_relative_name(self) -> None:
        got = safeio.safe_join(self._dir, "game/script.rpy")
        self.assertEqual(os.path.realpath(got),
                         os.path.realpath(os.path.join(self._dir, "game/script.rpy")))

    def test_parent_escape_is_refused(self) -> None:
        for name in ("../evil", "game/../../evil", "a/../../b"):
            with self.subTest(name=name):
                with self.assertRaises(safeio.UnsafePathError):
                    safeio.safe_join(self._dir, name)

    def test_absolute_path_is_refused(self) -> None:
        for name in ("/etc/passwd", "C:/Windows/evil.dll", "\\evil"):
            with self.subTest(name=name):
                with self.assertRaises(safeio.UnsafePathError):
                    safeio.safe_join(self._dir, name)

    def test_empty_name_is_refused(self) -> None:
        for name in ("", "  ", "."):
            with self.subTest(name=name):
                with self.assertRaises(safeio.UnsafePathError):
                    safeio.safe_join(self._dir, name)

    def test_error_is_an_unsafe_write_error(self) -> None:
        from renpy_toolkit.errors import UnsafeWriteError

        self.assertTrue(issubclass(safeio.UnsafePathError, UnsafeWriteError))


if __name__ == "__main__":
    unittest.main()
