"""gui 的纯逻辑部分测试（不启动窗口）。

界面本身不做自动化测试：tkinter 的断言价值低、跑起来还要显示器。但"点哪个
按钮跑哪条命令"必须被钉住——那是真正会改用户文件的部分。
"""

from __future__ import annotations

import json
import os
import unittest

from renpy_toolkit import gui

ROOT = os.path.join("C:\\", "games", "TheGame")
TL = os.path.join(ROOT, "game", "tl", "chinese")


def _make_game(root: str, *, launcher: str = "Game.py", lib: bool = False, scripts: bool = False) -> str:
    os.makedirs(os.path.join(root, "game"), exist_ok=True)
    if launcher:
        with open(os.path.join(root, launcher), "w", encoding="utf-8") as fh:
            fh.write("import renpy\n")
    if lib:
        d = os.path.join(root, "lib", "py3-windows-x86_64")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "python.exe"), "wb") as fh:
            fh.write(b"MZ")
    if scripts:
        with open(os.path.join(root, "game", "script.rpy"), "w", encoding="utf-8") as fh:
            fh.write("label start:\n    return\n")
    return root


class TestDetection(unittest.TestCase):
    def setUp(self) -> None:
        from tests import _util

        self.tmp = _util.tempdir_for(self)

    def test_launcher_wins(self) -> None:
        root = _make_game(os.path.join(self.tmp, "a"))
        self.assertTrue(gui.looks_like_game(root))
        self.assertEqual(os.path.basename(gui.find_launcher(root)), "Game.py")

    def test_interpreter_only_is_a_game(self) -> None:
        # 有些发行版不带 .py，只剩 lib/py3-*/python.exe。
        root = _make_game(os.path.join(self.tmp, "b"), launcher="", lib=True)
        self.assertTrue(gui.looks_like_game(root))

    def test_game_without_launcher_or_lib_but_with_scripts(self) -> None:
        root = _make_game(os.path.join(self.tmp, "c"), launcher="", scripts=True)
        self.assertTrue(gui.looks_like_game(root))

    def test_plain_directory_is_not_a_game(self) -> None:
        root = os.path.join(self.tmp, "d")
        os.makedirs(os.path.join(root, "game"), exist_ok=True)
        self.assertFalse(gui.looks_like_game(root))

    def test_directory_without_game_subdir_is_not_a_game(self) -> None:
        root = os.path.join(self.tmp, "e")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "Game.py"), "w", encoding="utf-8") as fh:
            fh.write("x")
        self.assertFalse(gui.looks_like_game(root))

    def test_setup_py_is_not_a_launcher(self) -> None:
        root = _make_game(os.path.join(self.tmp, "f"), launcher="setup.py")
        self.assertFalse(gui.looks_like_game(root))


class TestDiscovery(unittest.TestCase):
    def setUp(self) -> None:
        from tests import _util

        self.tmp = _util.tempdir_for(self)

    def test_scan_covers_self_and_one_level(self) -> None:
        _make_game(os.path.join(self.tmp, "one"))
        _make_game(os.path.join(self.tmp, "two"))
        deep = _make_game(os.path.join(self.tmp, "two", "nested"))
        games = gui.discover_games(self.tmp, depth=1)
        roots = {os.path.basename(g.root) for g in games}
        self.assertEqual(roots, {"one", "two"})
        self.assertNotIn(os.path.basename(deep), roots)

    def test_scan_includes_base_itself(self) -> None:
        _make_game(self.tmp)
        games = gui.discover_games(self.tmp, depth=1)
        self.assertEqual(len(games), 1)
        self.assertEqual(os.path.basename(games[0].root), os.path.basename(self.tmp))

    def test_skip_tool_directories(self) -> None:
        _make_game(os.path.join(self.tmp, "工作区域"))
        _make_game(os.path.join(self.tmp, "keep"))
        games = gui.discover_games(self.tmp, depth=1)
        self.assertEqual([os.path.basename(g.root) for g in games], ["keep"])

    def test_empty_dir_returns_nothing(self) -> None:
        self.assertEqual(gui.discover_games(self.tmp, depth=1), [])


class TestConfig(unittest.TestCase):
    def setUp(self) -> None:
        from tests import _util

        self.tmp = _util.tempdir_for(self)

    def test_roundtrip(self) -> None:
        self.assertIsNone(gui.save_config(self.tmp, {"last_root": "D:/g", "language": "chinese"}))
        self.assertEqual(gui.load_config(self.tmp), {"last_root": "D:/g", "language": "chinese"})

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(gui.load_config(os.path.join(self.tmp, "nope")), {})

    def test_broken_json_is_empty(self) -> None:
        path = gui.config_path(self.tmp)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(gui.load_config(self.tmp), {})

    def test_json_list_is_ignored(self) -> None:
        path = gui.config_path(self.tmp)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([1, 2, 3], fh)
        self.assertEqual(gui.load_config(self.tmp), {})


class TestActionMapping(unittest.TestCase):
    def ctx(self, **kw) -> gui.Ctx:
        base = dict(root=ROOT, language="chinese")
        base.update(kw)
        return gui.Ctx(**base)

    def test_readonly_actions_have_no_write_step(self) -> None:
        for key in ("audit", "verify", "glossary", "backup_list", "rpa_list", "font_map"):
            preview, writes = gui.build_action(key, self.ctx())
            self.assertTrue(preview, key)
            self.assertEqual(writes, [], key)

    def test_gen_previews_then_writes_with_clean_scratch(self) -> None:
        preview, writes = gui.build_action("gen", self.ctx())
        self.assertIn("--write", " ".join(writes[0]))
        self.assertNotIn("--write", preview)
        self.assertIn("--clean-scratch", writes[0])
        # 输出必须落在游戏自己的 tl/<lang> 下，而不是某个临时目录。
        self.assertTrue(any(el.endswith(os.path.join("game", "tl", "chinese")) for el in preview), preview)

    def test_apply_preview_has_no_write_then_writes(self) -> None:
        preview, writes = gui.build_action("apply", self.ctx(xlsx="C:/t.xlsx"))
        self.assertNotIn("--write", preview)
        self.assertEqual(writes, [["tl", "apply", TL, "C:/t.xlsx", "--write"]])

    def test_hardcoded_scans_then_marks(self) -> None:
        _preview, writes = gui.build_action("hardcoded", self.ctx())
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0][:4], ["tl", "hardcoded", "scan", TL])
        self.assertEqual(writes[1][:4], ["tl", "hardcoded", "apply", TL])

    def test_port_uses_skeleton_then_reference_order(self) -> None:
        preview, _writes = gui.build_action("port", self.ctx(reference="C:/old"))
        self.assertEqual(preview[:2], ["tl", "port"])
        self.assertEqual(preview[2], TL)
        self.assertEqual(preview[3], "C:/old")

    def test_decompile_defaults_to_work_area(self) -> None:
        preview, _writes = gui.build_action("decompile", self.ctx())
        self.assertTrue(any("工作区域" in el and "反编译" in el for el in preview), preview)

    def test_unknown_action_raises(self) -> None:
        with self.assertRaises(KeyError):
            gui.build_action("no_such", self.ctx())

    def test_every_spec_key_has_a_mapping(self) -> None:
        for spec in gui.ACTION_SPECS:
            gui.build_action(spec["key"], self.ctx(xlsx="C:/t.xlsx", reference="C:/r", fonts=("C:/f.ttf",)))

    def test_groups_cover_every_spec(self) -> None:
        grouped = sum(len(v) for v in gui.actions_by_group().values())
        self.assertEqual(grouped, len(gui.ACTION_SPECS))


if __name__ == "__main__":
    unittest.main()
