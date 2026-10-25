"""fontmap 模块测试：字体映射审计与块模板渲染。

覆盖的坑（每一条对真实工程都踩过）：
* 硬编码 ``text_font`` 必须靠 ``config.font_replacement_map`` 才能换成中文字体；
* ``config.font_replacement_map`` 的查找只做一次，``(bold, italics)`` 任一状态
  缺失，文字一加 ``{b}`` / ``{i}`` 就静默掉回原字体；
* 假字体名（盘上不存在）指向英文字体，是"英文名保持原样"的特权，不能被误报。
"""

from __future__ import annotations

import unittest

from _util import temp_dir, write

from renpy_toolkit import fontmap
from renpy_toolkit.errors import InvalidRequestError

#: A minimal "game/" tree: text files plus whichever fonts the test needs.
BASE_OPTIONS = "# options\n"

MAP_BLOCK = """\
init offset = 1

init python:
    _original_font_replacement_map = config.font_replacement_map.copy()

translate Marisa_ch python:
    config.font_replacement_map[("fonts/big_noodle_titling.ttf", False, False)] = ("tl/Marisa_ch/fonts/TBX.ttf", False, False)

translate None python:
    config.font_replacement_map.clear()
    config.font_replacement_map = _original_font_replacement_map.copy()
"""

SCREEN_BLOCK = """\
screen game_menu(title, scroll=None):
    label title:
        text_font "fonts/big_noodle_titling.ttf"
        text_size 60
"""

GUI_FONTS = """\
define gui.text_font = "fonts/DejaVuSans.ttf"
define gui.name_text_font = "fonts/DejaVuSans.ttf"
"""


def _touch(root: str, *relatives: str) -> None:
    """Create empty files (fonts are only checked for existence)."""
    import os

    for rel in relatives:
        target = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        write(target, "")


def _build(root: str, *, fonts: bool = True) -> str:
    """Create a project tree under ``root``; return the ``game`` dir."""
    import os

    game = os.path.join(root, "game")
    write(os.path.join(game, "font_map.rpy"), MAP_BLOCK)
    write(os.path.join(game, "screens.rpy"), SCREEN_BLOCK)
    write(os.path.join(game, "gui.rpy"), GUI_FONTS)
    if fonts:
        _touch(
            game,
            "fonts/big_noodle_titling.ttf",
            "fonts/DejaVuSans.ttf",
            "tl/Marisa_ch/fonts/TBX.ttf",
        )
    return game


class TestParse(unittest.TestCase):
    def test_font_key_tuple(self) -> None:
        key = fontmap.parse_font_key('("fonts/a.ttf", True, False)')
        self.assertEqual(key.path, "fonts/a.ttf")
        self.assertTrue(key.bold)
        self.assertFalse(key.italics)
        self.assertEqual(key.state, "bold")

    def test_font_key_bare_path(self) -> None:
        key = fontmap.parse_font_key("fonts/a.ttf")
        self.assertEqual(key.as_tuple(), ("fonts/a.ttf", False, False))
        self.assertEqual(key.state, "regular")

    def test_font_key_bold_italic(self) -> None:
        self.assertEqual(
            fontmap.parse_font_key('("f.ttf", true, 1)').state, "bold+italic"
        )

    def test_font_key_rejects_garbage(self) -> None:
        for bad in ("", "   ", '("a", True, False, 1, 2)'):
            with self.assertRaises(InvalidRequestError):
                fontmap.parse_font_key(bad)


class TestScan(unittest.TestCase):
    def test_scan_font_map_records_language(self) -> None:
        with temp_dir() as root:
            game = _build(root)
            entries = fontmap.scan_font_map(game)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.key.path, "fonts/big_noodle_titling.ttf")
        self.assertEqual(entry.value.path, "tl/Marisa_ch/fonts/TBX.ttf")
        self.assertEqual(entry.language, "Marisa_ch")
        self.assertEqual(entry.lineno, 7)

    def test_scan_font_uses_finds_hardcoded_paths(self) -> None:
        with temp_dir() as root:
            game = _build(root)
            uses = fontmap.scan_font_uses(game)
        by_origin = {u.origin for u in uses}
        self.assertIn("font_replacement_map", by_origin)
        self.assertIn("property", by_origin)  # text_font in the screen
        self.assertIn("gui", by_origin)

    def test_commented_code_is_ignored(self) -> None:
        with temp_dir() as root:
            game = _build(root)
            write(
                game + "/commented.rpy",
                "# config.font_replacement_map['fonts/ghost.ttf', False, False] = ('x.ttf', False, False)\n",
            )
            entries = fontmap.scan_font_map(game)
        self.assertEqual(len(entries), 1)

    def test_font_tag_and_font_call(self) -> None:
        with temp_dir() as root:
            game = _build(root)
            write(
                game + "/extra.rpy",
                'label x:\n    e "{font=fonts/tag.ttf}hi{/font}"\n'
                '    python:\n        f = Font("fonts/call.ttf")\n',
            )
            uses = fontmap.scan_font_uses(game)
        paths = {u.path for u in uses}
        self.assertIn("fonts/tag.ttf", paths)
        self.assertIn("fonts/call.ttf", paths)


class TestAudit(unittest.TestCase):
    def test_complete_map_is_ok(self) -> None:
        with temp_dir() as root:
            game = _build(root)
            # DejaVuSans is used by gui but not mapped -> add a mapping.
            write(
                game + "/gui_map.rpy",
                'translate Marisa_ch python:\n'
                '    config.font_replacement_map[("fonts/DejaVuSans.ttf", False, False)]'
                ' = ("tl/Marisa_ch/fonts/TBX.ttf", False, False)\n',
            )
            report = fontmap.audit_fonts(game)
        self.assertEqual(report.unmapped, [])
        self.assertEqual(report.dangling, [])
        self.assertTrue(report.ok, report.text())

    def test_hardcoded_screen_font_reports_unmapped(self) -> None:
        with temp_dir() as root:
            game = _build(root)
            write(game + "/screens.rpy", SCREEN_BLOCK + "")
            write(game + "/font_map.rpy", "")  # drop every mapping
            report = fontmap.audit_fonts(game)
        unmapped = {u.path for u in report.unmapped}
        self.assertIn("fonts/big_noodle_titling.ttf", unmapped)
        self.assertFalse(report.ok)

    def test_missing_bold_state_is_reported(self) -> None:
        with temp_dir() as root:
            game = _build(root)
            write(
                game + "/extra_map.rpy",
                'translate Marisa_ch python:\n'
                '    config.font_replacement_map["fonts/DejaVuSans.ttf", False, False]'
                ' = ("tl/Marisa_ch/fonts/TBX.ttf", False, False)\n',
            )
            lax = fontmap.audit_fonts(game)
            strict = fontmap.audit_fonts(
                game, require_states=("regular", "bold", "italic", "bold+italic")
            )
        self.assertEqual(lax.missing_states, {})
        self.assertIn("fonts/big_noodle_titling.ttf", strict.missing_states)
        self.assertEqual(
            strict.missing_states["fonts/big_noodle_titling.ttf"], {"regular"}
        )

    def test_dangling_target(self) -> None:
        with temp_dir() as root:
            game = _build(root, fonts=False)  # no fonts on disk at all
            report = fontmap.audit_fonts(game)
        self.assertEqual(len(report.dangling), 1)
        self.assertEqual(report.dangling[0].value.path, "tl/Marisa_ch/fonts/TBX.ttf")

    def test_duplicate_key(self) -> None:
        with temp_dir() as root:
            game = _build(root)
            write(
                game + "/dup.rpy",
                'translate Marisa_ch python:\n'
                '    config.font_replacement_map[("fonts/big_noodle_titling.ttf", False, False)]'
                ' = ("fonts/DejaVuSans.ttf", False, False)\n',
            )
            report = fontmap.audit_fonts(game)
        self.assertEqual(len(report.duplicates), 1)

    def test_fake_font_name_is_the_escape_hatch(self) -> None:
        with temp_dir() as root:
            game = _build(root)
            write(
                game + "/fake.rpy",
                'translate Marisa_ch python:\n'
                '    config.font_replacement_map["MarisaName", False, False]'
                ' = ("fonts/DejaVuSans.ttf", False, False)\n',
            )
        self.assertTrue(fontmap.is_fake_font_name("MarisaName", game))
        self.assertFalse(fontmap.is_fake_font_name("fonts/DejaVuSans.ttf", game))

    def test_builtin_font_is_ignored_not_unmapped(self) -> None:
        with temp_dir() as root:
            game = _build(root)
            write(game + "/b.rpy", 'define gui.interface_text_font = "DejaVuSans.ttf"\n')
            report = fontmap.audit_fonts(game)
        self.assertNotIn("DejaVuSans.ttf", {u.path for u in report.unmapped})
        self.assertTrue(any("built-in" in item for item in report.ignored))

    def test_strict_without_map_raises(self) -> None:
        with temp_dir() as root:
            game = _build(root)
            write(game + "/font_map.rpy", "")
            with self.assertRaises(Exception):
                fontmap.audit_fonts(game, strict=True)

    def test_unknown_state_rejected(self) -> None:
        with temp_dir() as root:
            game = _build(root)
            with self.assertRaises(InvalidRequestError):
                fontmap.audit_fonts(game, require_states=("ultrabold",))

    def test_report_text_mentions_findings(self) -> None:
        with temp_dir() as root:
            game = _build(root, fonts=False)
            text = fontmap.audit_fonts(game).text()
        self.assertIn("DANGLING", text)
        self.assertIn("UNMAPPED", text)


class TestRender(unittest.TestCase):
    def test_render_block_shape(self) -> None:
        src = fontmap.FontKey("fonts/big_noodle_titling.ttf")
        dst = fontmap.FontKey("tl/Marisa_ch/fonts/TBX.ttf")
        block = fontmap.render_font_map_block([(src, dst)], language="Marisa_ch")
        self.assertIn("translate Marisa_ch python:", block)
        self.assertIn("translate None python:", block)
        self.assertIn("_original_font_replacement_map = config.font_replacement_map.copy()", block)
        self.assertIn(
            "config.font_replacement_map['fonts/big_noodle_titling.ttf', False, False]"
            " = ('tl/Marisa_ch/fonts/TBX.ttf', False, False)",
            block,
        )
        # The None block must restore, never leave the Chinese font installed.
        self.assertIn("config.font_replacement_map.clear()", block)

    def test_render_bold_italic_flags(self) -> None:
        src = fontmap.FontKey("fonts/a.ttf", True, True)
        dst = fontmap.FontKey("tl/x/fonts/b.ttf", True, True)
        block = fontmap.render_font_map_block([(src, dst)], language="Chinese")
        self.assertIn("'fonts/a.ttf', True, True", block)
        self.assertIn("('tl/x/fonts/b.ttf', True, True)", block)

    def test_render_rejects_bad_input(self) -> None:
        key = fontmap.FontKey("f.ttf")
        with self.assertRaises(InvalidRequestError):
            fontmap.render_font_map_block([], language="Chinese")
        with self.assertRaises(InvalidRequestError):
            fontmap.render_font_map_block([(key, key)], language="not an identifier")
        with self.assertRaises(InvalidRequestError):
            fontmap.render_font_map_block([("a", "b")], language="Chinese")


if __name__ == "__main__":
    unittest.main()
