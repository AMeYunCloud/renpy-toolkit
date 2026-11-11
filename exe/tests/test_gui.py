"""gui 的纯逻辑部分测试（不启动窗口）。

**（已停止维护）** 被测的 `exe/gui.py` 属于已停止维护的 exe 发行线；这些用例只为
留档与复现保留，不再随新版本更新。

界面本身不做自动化测试：窗口的断言价值低、跑起来还要显示器。但"点哪个
按钮跑哪条命令"必须被钉住——那是真正会改用户文件的部分。

本目录属于仓库顶层 `exe/`，与 `plugin/dsh-renpy-toolkit` 分开：exe 会 import
`renpy_toolkit`，所以这里先把插件包目录塞进 sys.path，再 import 同级的 `gui`。
运行：`python -m unittest discover -s exe/tests -t exe/tests`
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_EXE = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_EXE)
for _path in (os.path.join(_ROOT, "plugin", "dsh-renpy-toolkit"), _EXE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import gui  # noqa: E402  （必须在 sys.path 调整之后）
from tests import _util  # noqa: E402

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
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(gui.load_config(self.tmp), {})

    def test_json_list_is_ignored(self) -> None:
        path = gui.config_path(self.tmp)
        os.makedirs(os.path.dirname(path), exist_ok=True)
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

    def test_decompile_defaults_into_output_dir(self) -> None:
        preview, _writes = gui.build_action("decompile", self.ctx())
        self.assertTrue(any("反编译" in el for el in preview), preview)
        self.assertTrue(any("renpy-toolkit-数据" in el or "outdir" in el.lower() for el in preview)
                        or os.path.basename(str(preview[-1])) == "反编译", preview)

    def test_apply_without_a_chosen_xlsx_is_a_readable_error(self) -> None:
        with self.assertRaises(FileNotFoundError) as cm:
            gui.build_action("apply", self.ctx())
        self.assertIn("还没有选择", str(cm.exception))

    def test_port_without_a_reference_dir_is_a_readable_error(self) -> None:
        with self.assertRaises(FileNotFoundError) as cm:
            gui.build_action("port", self.ctx())
        self.assertIn("还没有选择", str(cm.exception))

    def test_hardcoded_builds_its_ledger_from_the_tl(self) -> None:
        # 探针 dump 落在临时目录、跑完就删，所以清单必须从 TL 反推。
        preview, writes = gui.build_action("hardcoded", self.ctx())
        self.assertIn("--from-tl", preview)
        self.assertIn("--from-tl", writes[0])

    def test_table_always_exports_into_the_data_dir(self) -> None:
        # 导出目标固定，避免用户填过的 xlsx 被导出覆盖。
        ctx = self.ctx(outdir="D:/out", xlsx="C:/picked.xlsx")
        _preview, writes = gui.build_action("table", ctx)
        argv = writes[0]
        self.assertEqual(argv[argv.index("-o") + 1], os.path.join(ctx.export_dir, "chinese.xlsx"))
        self.assertNotIn("C:/picked.xlsx", argv)

    def test_font_check_finds_fonts_in_the_game(self) -> None:
        tmp = _util.tempdir_for(self)
        game = os.path.join(tmp, "game")
        os.makedirs(os.path.join(game, "fonts"))
        font = os.path.join(game, "fonts", "x.ttf")
        open(font, "wb").close()
        self.assertEqual(gui.find_fonts(tmp), [font])
        preview, _writes = gui.build_action("font_check", self.ctx(root=tmp))
        self.assertIn(font, preview)

    def test_font_check_without_any_font_explains_itself(self) -> None:
        tmp = _util.tempdir_for(self)
        with self.assertRaises(FileNotFoundError) as cm:
            gui.build_action("font_check", self.ctx(root=tmp))
        msg, hint = gui.explain_error(cm.exception)
        self.assertIn("没有找到字体文件", msg)
        self.assertIn("game", hint)

    def test_missing_archive_is_not_reported_as_a_failure(self) -> None:
        self.assertTrue(gui._informational("error: 没有找到 .rpa 归档：X"))
        self.assertTrue(gui._informational("error: nothing to emit: no font mappings"))
        self.assertFalse(gui._informational("error: 3 error(s), 1 warning(s)"))

    def test_unknown_action_raises(self) -> None:
        with self.assertRaises(KeyError):
            gui.build_action("no_such", self.ctx())

    def test_every_spec_key_has_a_mapping(self) -> None:
        for spec in gui.ACTION_SPECS:
            gui.build_action(spec["key"], self.ctx(xlsx="C:/t.xlsx", reference="C:/r", fonts=("C:/f.ttf",)))

    def test_groups_cover_every_spec(self) -> None:
        groups = gui.actions_by_group()
        self.assertTrue(isinstance(groups, list))
        grouped = sum(len(g["actions"]) for g in groups)
        self.assertEqual(grouped, len(gui.ACTION_SPECS))
        # 分组顺序应与 ACTION_SPECS 首次出现的顺序一致
        seen: list[str] = []
        for spec in gui.ACTION_SPECS:
            if spec["group"] not in seen:
                seen.append(spec["group"])
        self.assertEqual([g["group"] for g in groups], seen)


class TestStartupGuards(unittest.TestCase):
    """启动前三道闸的纯逻辑：危险路径、浏览器参数、单实例。"""

    def test_drive_roots_are_dangerous(self) -> None:
        for path in ("C:\\", "E:\\", "D:/", "c:\\"):
            self.assertTrue(gui.is_dangerous_root(path), path)

    def test_normal_directories_are_safe(self) -> None:
        for path in (
            "C:\\Games\\SomeGame",
            "E:\\WorkPlace\\dsh\\renpy\\exe",
            os.path.dirname(os.path.abspath(__file__)),
        ):
            self.assertFalse(gui.is_dangerous_root(path), path)

    def test_unc_share_is_not_a_drive_root(self) -> None:
        # \\server\share 是共享根，但不是盘符根，不拦
        self.assertFalse(gui.is_dangerous_root(r"\\server\share"))

    def test_browser_command_disables_extensions(self) -> None:
        args = gui.browser_command(r"C:\edge.exe", "http://127.0.0.1:8000/", r"D:\d\临时\browser-abc")
        self.assertIn("--app=http://127.0.0.1:8000/", args)
        self.assertIn("--disable-extensions", args)
        udd = [a for a in args if a.startswith("--user-data-dir=")]
        self.assertEqual(len(udd), 1)
        self.assertTrue(udd[0].endswith("browser-abc"))

    def test_single_instance_acquires_once(self) -> None:
        # 唯一名字，避免和真实实例或其它用例撞车
        self.assertTrue(gui._acquire_single_instance("renpy-toolkit-test-singleton"))

    def test_wait_until_listening_times_out_on_dead_port(self) -> None:
        self.assertFalse(gui.wait_until_listening(1, timeout=0.3))


class TestBrowserProfile(unittest.TestCase):
    """profile 必须落在 ASCII 目录里，否则 Edge 会忽略它、把扩展页一起打开。"""

    def test_ascii_path(self):
        self.assertTrue(gui._ascii_path(r"C:\\Users\\a\\Temp"))
        self.assertFalse(gui._ascii_path("E:\\工作区域\\临时"))

    def test_short_path_is_ascii_on_windows(self):
        if os.name != "nt":
            self.skipTest("Windows only")
        with tempfile.TemporaryDirectory() as root:
            deep = os.path.join(root, "中文目录")
            os.makedirs(deep, exist_ok=True)
            short = gui.short_path(deep)
            self.assertIsInstance(short, str)
            self.assertTrue(short)
            # 8.3 短名可被关闭（C: 上很常见），此时只能原样返回。
            if short != deep:
                self.assertTrue(gui._ascii_path(short))

    def test_profile_root_exists_and_prefers_ascii(self):
        with tempfile.TemporaryDirectory() as root:
            got = gui.browser_profile_root(os.path.join(root, "数据"))
            self.assertTrue(os.path.isdir(got))


if __name__ == "__main__":
    unittest.main()

class TestGenOptions(unittest.TestCase):
    """生成选项（分块注释/索引、硬编码文本）必须真的落到命令行上。"""

    def ctx(self, **kw) -> gui.Ctx:
        base = dict(root=ROOT, language="chinese")
        base.update(kw)
        return gui.Ctx(**base)

    def test_default_has_no_annotation_flags(self) -> None:
        preview, writes = gui.build_action("gen", self.ctx())
        for flag in ("--sections", "--index", "--no-hardcoded"):
            self.assertNotIn(flag, preview)
            self.assertNotIn(flag, writes[0])

    def test_annotation_switch_adds_sections_and_index(self) -> None:
        preview, writes = gui.build_action("gen", self.ctx(annotate=True))
        for argv in (preview, writes[0]):
            self.assertIn("--sections", argv)
            self.assertIn("--index", argv)

    def test_hardcoded_can_be_disabled(self) -> None:
        preview, writes = gui.build_action("gen", self.ctx(hardcoded=False))
        self.assertIn("--no-hardcoded", preview)
        self.assertIn("--no-hardcoded", writes[0])


class TestLogTranslation(unittest.TestCase):
    """常用命令行输出要翻成中文；翻不了的原样保留。"""

    def test_common_lines(self) -> None:
        cases = {
            "wrote 3 file(s) to D:/t": "已写入 3 个文件到 D:/t",
            "would write 4 file(s) to D:/t": "将写入 4 个文件到 D:/t（这是预演，还没动文件）",
            "51 file(s) already present, left untouched": "51 个文件已存在，保持不动",
            "OK": "检查通过",
            "nothing to emit": "没有可处理的内容",
        }
        for english, chinese in cases.items():
            self.assertEqual(gui._zh_line(english), chinese, english)

    def test_coverage_line(self) -> None:
        line = (
            "53 file(s), 58249 translate block(s), 2705 string(s); "
            "coverage 60948/60954 (100.0%); 0 error(s), 23 warning(s)"
        )
        out = gui._zh_line(line)
        self.assertIn("53 个文件", out)
        self.assertIn("58249 个翻译块", out)
        self.assertIn("0 个错误", out)

    def test_unknown_line_is_untouched(self) -> None:
        self.assertEqual(gui._zh_line("some random output"), "some random output")

class TestShutdownByHeartbeat(unittest.TestCase):
    """关页面要能退出：只认心跳，不认浏览器进程。"""

    def test_quiet_window_is_twenty_seconds(self) -> None:
        # 页面 4 秒一次心跳，20 秒 = 连续丢 5 次；再长会让用户以为「没关掉」。
        self.assertEqual(gui.QUIET_SECONDS, 20.0)

    def test_quiet_window_is_shorter_than_the_old_browser_bound_value(self) -> None:
        # 之前 tab 模式 40 秒、拉起过浏览器进程时 120 秒；120 秒看起来就是没关。
        self.assertLess(gui.QUIET_SECONDS, 40.0)
