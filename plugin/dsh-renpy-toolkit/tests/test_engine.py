"""engine.detect() 的判定规则。"""

import os
import unittest

from renpy_toolkit import engine
from renpy_toolkit.errors import NotARenPyGameError
import _util


def _write(path, data):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": "\n"}
    with open(path, mode, **kwargs) as fh:
        fh.write(data)


class TestDetect(unittest.TestCase):
    def setUp(self):
        self.tmp = _util.tempdir_for(self)

    def test_empty_dir_is_not_a_game(self):
        found = engine.detect(self.tmp)
        self.assertFalse(found.is_renpy)
        self.assertIn("没有 game/ 目录", found.problems[0])

    def test_game_dir_without_renpy_evidence_is_rejected(self):
        os.makedirs(os.path.join(self.tmp, "game"))
        _write(os.path.join(self.tmp, "game", "photo.png"), b"\x89PNG")
        found = engine.detect(self.tmp)
        self.assertFalse(found.is_renpy)

    def test_rpc2_magic_is_evidence(self):
        _write(os.path.join(self.tmp, "game", "script.rpyc"), b"RENPY RPC2" + b"\x00" * 32)
        found = engine.detect(self.tmp)
        self.assertTrue(found.is_renpy)
        self.assertEqual(found.kind, "compiled")
        self.assertIn("script.rpyc", found.summary())

    def test_rpa_magic_is_evidence(self):
        _write(os.path.join(self.tmp, "game", "archive.rpa"), b"RPA-3.0 0000000000000000 00000000\n")
        found = engine.detect(self.tmp)
        self.assertTrue(found.is_renpy)
        self.assertEqual(found.kind, "archive")

    def test_runtime_folder_is_evidence(self):
        _write(os.path.join(self.tmp, "renpy", "display", "core.py"), "# engine\n")
        os.makedirs(os.path.join(self.tmp, "game"))
        found = engine.detect(self.tmp)
        self.assertTrue(found.is_renpy)

    def test_launcher_mentioning_renpy_is_evidence(self):
        _write(os.path.join(self.tmp, "MyGame.py"), "import renpy\nrenpy.bootstrap()\n")
        os.makedirs(os.path.join(self.tmp, "game"))
        self.assertTrue(engine.detect(self.tmp).is_renpy)

    def test_rpy_source_structure_is_evidence(self):
        os.makedirs(os.path.join(self.tmp, "game"))
        _write(os.path.join(self.tmp, "game", "script.rpy"), "label start:\n    return\n")
        found = engine.detect(self.tmp)
        self.assertTrue(found.is_renpy)
        self.assertEqual(found.kind, "source")

    def test_game_dir_passed_directly(self):
        _write(os.path.join(self.tmp, "game", "script.rpyc"), b"RENPY RPC2" + b"\x00" * 8)
        self.assertTrue(engine.detect(os.path.join(self.tmp, "game")).is_renpy)

    def test_garbage_rpyc_is_not_enough(self):
        _write(os.path.join(self.tmp, "game", "script.rpyc"), b"NOT RENPY AT ALL")
        self.assertFalse(engine.detect(self.tmp).is_renpy)


class TestRequire(unittest.TestCase):
    def setUp(self):
        self.tmp = _util.tempdir_for(self)

    def test_require_raises_with_actionable_message(self):
        with self.assertRaises(NotARenPyGameError) as ctx:
            engine.require(self.tmp, what="这个文件夹")
        message = str(ctx.exception)
        self.assertIn("这个文件夹", message)
        self.assertIn("不是 Ren'Py 游戏", message)
        self.assertIn("game/", message)

    def test_require_passes_for_real_game(self):
        _write(os.path.join(self.tmp, "game", "script.rpyc"), b"RENPY RPC2" + b"\x00" * 8)
        found = engine.require(self.tmp)
        self.assertTrue(found.is_renpy)

    def test_is_renpy_game_helper(self):
        os.makedirs(os.path.join(self.tmp, "game"))
        self.assertFalse(engine.is_renpy_game(self.tmp))


if __name__ == "__main__":
    unittest.main()
