"""多归档（archive.rpa + archive2.rpa）读取的回归测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from renpy_toolkit import rpa  # noqa: E402
from renpy_toolkit.errors import ToolkitError  # noqa: E402


def _make(path: str, files: dict[str, bytes]) -> None:
    archive = rpa.RenPyArchive.create(path)
    for name, data in files.items():
        archive.add(name, data)
    archive.save(path)


class TestFindArchives(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.dir = tempfile.mkdtemp(prefix="rpa-multi-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, True))

    def test_single_file(self):
        target = os.path.join(self.dir, "a.rpa")
        _make(target, {"x.txt": b"1"})
        self.assertEqual(rpa.find_archives(target), [target])

    def test_directory_with_one(self):
        _make(os.path.join(self.dir, "a.rpa"), {"x.txt": b"1"})
        self.assertEqual(len(rpa.find_archives(self.dir)), 1)

    def test_directory_with_two(self):
        _make(os.path.join(self.dir, "archive.rpa"), {"a.txt": b"1"})
        _make(os.path.join(self.dir, "archive2.rpa"), {"b.txt": b"2"})
        self.assertEqual(len(rpa.find_archives(self.dir)), 2)

    def test_project_root_descends_into_game(self):
        game = os.path.join(self.dir, "game")
        os.makedirs(game)
        _make(os.path.join(game, "archive.rpa"), {"a.txt": b"1"})
        found = rpa.find_archives(self.dir)
        self.assertEqual([os.path.basename(p) for p in found], ["archive.rpa"])

    def test_empty_directory_gives_nothing(self):
        self.assertEqual(rpa.find_archives(self.dir), [])


class TestMultiArchive(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.dir = tempfile.mkdtemp(prefix="rpa-view-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, True))
        self.one = os.path.join(self.dir, "archive.rpa")
        self.two = os.path.join(self.dir, "archive2.rpa")
        _make(self.one, {"shared.txt": b"from-one", "only-one.txt": b"1"})
        _make(self.two, {"only-two.txt": b"2"})

    def test_names_are_the_union(self):
        with rpa.open_archive(self.dir) as view:
            self.assertEqual(
                view.names(), ["only-one.txt", "only-two.txt", "shared.txt"]
            )

    def test_read_goes_to_the_owning_archive(self):
        with rpa.open_archive(self.dir) as view:
            self.assertEqual(view.read("only-two.txt"), b"2")
            self.assertEqual(view.read("only-one.txt"), b"1")

    def test_duplicate_name_resolves_to_the_first_archive(self):
        # 顺序沿用 Ren'Py 自己的搜索次序：先打开的归档先命中。
        _make(self.one, {"shared.txt": b"from-one", "only-one.txt": b"1"})
        with rpa.open_archive(self.dir) as view:
            self.assertEqual(view.read("shared.txt"), b"from-one")

    def test_single_archive_returns_plain_archive(self):
        with rpa.open_archive(self.one) as archive:
            self.assertNotIsInstance(archive, rpa.MultiArchive)

    def test_empty_directory_error_says_what_was_looked_at(self):
        empty = os.path.join(self.dir, "nothing")
        os.makedirs(empty)
        with self.assertRaises(ToolkitError) as caught:
            rpa.open_archive(empty)
        message = str(caught.exception)
        self.assertIn("没有找到 .rpa 归档", message)
        self.assertIn("game", message)


if __name__ == "__main__":
    unittest.main()
