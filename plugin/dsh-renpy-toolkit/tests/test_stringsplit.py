"""上游源索引（stringsplit.SourceIndex）的防回归测试。

真实验证过的场景：一个 ``translate Chinese strings:`` 块里同时有
「源码里确实存在的字面量」和「源码里根本不存在的字面量」。

* 存在的那些要映射回**真正**出现的文件:行号，而不是猜一个显得合理的；
* 不存在的那些必须标成 ``NO_SOURCE_COMMENT``，绝不能凭空造一个位置，
  否则后续按行号定位就会指到无关代码。

另外还盯着一个更隐蔽的坑：``SourceIndex.find`` 必须忽略被注释掉的
``# e "..."`` 镜像行，只认活行。
"""

from __future__ import annotations

import os
import unittest

from _util import temp_dir, write

from renpy_toolkit import stringsplit


# 三条 old，其中前两条在 NOTIFY 里能找到，第三条只在别处出现过。
MULTI = """\
translate Chinese strings:

    # —— 物品 / 数值增减 ——
    old "+1 Fox amulet."
    new "+1 狐狸护身符。"
    old "+100 gold, +15 Reputation"
    new "+100 金币，+15 声望"
    old "Line Spacing Scaling"
    new "行距缩放"
"""

STANDARD = """\
# game/screens.rpy:100
translate Chinese strings:
    old "Start Game"
    new "开始游戏"

# game/screens.rpy:101
translate Chinese strings:
    old "Load"
    new "读取"
"""

SCREENS = """\
screen main_menu():
    textbutton "Start Game" action Start()
    textbutton "Load" action ShowMenu("load")
"""

NOTIFY = """\
init python:
    def stack_notify(msg):
        renpy.notify(_("+1 Fox amulet."))
        renpy.notify(_("+100 gold, +15 Reputation"))
"""


def _parse(text: str):
    from renpy_toolkit import tl

    return tl.parse_tl_text(text, "<mem>")


class TestSearchable(unittest.TestCase):
    def test_strips_tags_and_interpolation(self) -> None:
        self.assertEqual(
            stringsplit.searchable_text("You have {image=heart.png} [points] points."),
            "You have   points.",
        )

    def test_plain_text_untouched(self) -> None:
        self.assertEqual(stringsplit.searchable_text("Hello"), "Hello")


class TestLayout(unittest.TestCase):
    def test_multi_pair_is_not_standard(self) -> None:
        self.assertFalse(stringsplit.is_standard_strings_layout(_parse(MULTI)))

    def test_one_pair_per_block_is_standard(self) -> None:
        self.assertTrue(stringsplit.is_standard_strings_layout(_parse(STANDARD)))


class TestSplit(unittest.TestCase):
    def _game(self, root: str) -> str:
        game = os.path.join(root, "game")
        write(os.path.join(game, "screens.rpy"), SCREENS)
        write(os.path.join(game, "notify.rpy"), NOTIFY)
        return game

    def test_each_pair_becomes_its_own_block(self) -> None:
        with temp_dir() as root:
            game = self._game(root)
            index = stringsplit.build_source_index(game)
            block = _parse(MULTI).blocks[0]
            lines, result = stringsplit.normalise_strings_block(
                block, language="Chinese", source_index=index
            )
            text = "\n".join(lines)
        self.assertEqual(result.pairs_in, 3)
        self.assertEqual(result.blocks_out, 3)
        self.assertEqual(text.count("translate Chinese strings:"), 3)
        self.assertEqual(text.count('old "'), 3)

    def test_source_comment_points_at_the_file_that_holds_the_literal(self) -> None:
        with temp_dir() as root:
            game = self._game(root)
            index = stringsplit.build_source_index(game)
            block = _parse(MULTI).blocks[0]
            lines, _ = stringsplit.normalise_strings_block(
                block, language="Chinese", source_index=index
            )
            text = "\n".join(lines)
        # Both literals live in notify.rpy (lines 3 and 4).
        self.assertIn("# game/notify.rpy:3", text)
        self.assertIn("# game/notify.rpy:4", text)
        # screens.rpy holds "Start Game" / "Load", which this block does not
        # translate at all: inventing a comment there would be a false anchor.
        self.assertNotIn("screens.rpy", text)

    def test_missing_literal_is_marked_not_invented(self) -> None:
        with temp_dir() as root:
            game = self._game(root)
            index = stringsplit.build_source_index(game)
            block = _parse(MULTI).blocks[0]
            lines, result = stringsplit.normalise_strings_block(
                block, language="Chinese", source_index=index
            )
            text = "\n".join(lines)
        self.assertEqual(result.unresolvable, 1)
        self.assertIn(stringsplit.NO_SOURCE_COMMENT, text)
        # The unmatched literal must be annotated as such: the marker has to sit
        # directly above the block that carries ``old "Line Spacing Scaling"``,
        # so a reader can tell which pair lost its anchor.
        rows = text.splitlines()
        old_at = next(
            pos for pos, row in enumerate(rows) if "Line Spacing Scaling" in row
        )
        header_at = max(
            pos for pos, row in enumerate(rows[:old_at]) if row.startswith("translate")
        )
        self.assertEqual(rows[header_at - 1], stringsplit.NO_SOURCE_COMMENT)

    def test_mirrored_comment_lines_are_not_anchors(self) -> None:
        """A ``# e "..."`` mirror must not become the reported source location."""
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(
                os.path.join(game, "script.rpy"),
                'label start:\n    # e "Only in a comment"\n    e "Real line"\n',
            )
            index = stringsplit.build_source_index(game)
            hit = index.find("Only in a comment")
        self.assertIsNone(hit)
        self.assertIsNotNone(index.find("Real line"))


if __name__ == "__main__":
    unittest.main()
