"""sorttl 模块测试：块排序、strings 块保护、行号索引。

每一条测试对应一个真实工程踩过的坑：
* ``strings:`` 块被重排后 ``old``/``new`` 配对错乱，整个文件几乎丢内容
  （这正是 devlog_text / updater_screen 当初被塞进文件名白名单的原因）；
* 索引行号必须是新文件里的行号，而不是排序前的；
* 没有镜像注释的块不能凭空丢掉。
"""

from __future__ import annotations

import os
import unittest

from _util import temp_dir, write

from renpy_toolkit import sorttl
from renpy_toolkit import tl as tlmod


HEADER = """\
# Chinese translation

"""

BLOCK_A = """\
# game/script.rpy:30
translate chinese start_bbb:

    # e "second"
    e "第二个"
"""

BLOCK_B = """\
# game/script.rpy:10
translate chinese start_aaa:

    # e "first"
    e "第一个"
"""

STRINGS = """\
translate chinese strings:

    old "Yes"
    new "是"

    old "No"
    new "否"
"""

def _parse(text: str):
    return sorttl.parse_tl_file_from_text(text, "<mem>")


def _body(text: str) -> str:
    return "".join(line.text + line.eol for line in _parse(text).lines)


class TestSort(unittest.TestCase):
    def test_blocks_sorted_by_source_line(self) -> None:
        text = HEADER + BLOCK_A + "\n" + BLOCK_B
        rebuilt, result = sorttl.sort_tl_file(_parse(text))
        self.assertTrue(result.changed)
        body = "".join(line.text + line.eol for line in rebuilt.lines)
        self.assertLess(body.index("start_aaa"), body.index("start_bbb"))

    def test_strings_block_never_moves(self) -> None:
        text = BLOCK_A + "\n" + STRINGS + "\n" + BLOCK_B
        rebuilt, result = sorttl.sort_tl_file(_parse(text))
        self.assertEqual(result.skipped_strings, 1)
        body = "".join(line.text + line.eol for line in rebuilt.lines)
        # Prose blocks are ordered first, the strings block trails them.
        self.assertLess(body.index("start_aaa"), body.index("start_bbb"))
        self.assertGreater(body.index('old "Yes"'), body.index("start_bbb"))
        # old/new pairing intact
        self.assertLess(body.index('old "Yes"'), body.index('new "是"'))

    def test_already_sorted_is_untouched(self) -> None:
        text = HEADER + BLOCK_B + "\n" + BLOCK_A
        rebuilt, result = sorttl.sort_tl_file(_parse(text))
        self.assertFalse(result.changed)
        self.assertIsNone(rebuilt)

    def test_block_without_source_comment_is_kept(self) -> None:
        orphan = 'translate chinese start_zzz:\n\n    # e "x"\n    e "孤"\n'
        text = BLOCK_B + "\n" + orphan + "\n" + BLOCK_A
        rebuilt, result = sorttl.sort_tl_file(_parse(text))
        self.assertEqual(result.unsorted, 1)
        body = "".join(line.text + line.eol for line in rebuilt.lines)
        self.assertIn("start_zzz", body)  # not dropped
        self.assertGreater(body.index("start_zzz"), body.index("start_aaa"))


class TestBarriers(unittest.TestCase):
    """``translate python`` / ``translate style`` 按文件顺序执行，不得跨越。

    引擎切语言时逐块执行这些块（``renpy.translation.change_language``），
    所以把对白块排到它们前面，会改变初始化顺序 —— 那是纯粹的语义破坏，
    不是排版问题。
    """

    PY_A = "# game/a.rpy:200\ntranslate chinese python:\n    late = 1\n"
    PY_B = "# game/a.rpy:300\ntranslate chinese python:\n    later = 2\n"

    def _render(self, text: str) -> tuple[str, object]:
        rebuilt, result = sorttl.sort_tl_file(_parse(text))
        doc = rebuilt if rebuilt is not None else _parse(text)
        return "".join(line.text + line.eol for line in doc.lines), result

    def test_python_block_does_not_move(self) -> None:
        # BLOCK_B 是 start_aaa(:10)，BLOCK_A 是 start_bbb(:30)。
        # 写成 B 在前 A 在后本来就是有序的；关键断言是 python 块留在开头。
        text = self.PY_A + "\n" + BLOCK_A + "\n" + BLOCK_B
        body, result = self._render(text)
        self.assertTrue(result.changed)
        self.assertLess(body.index("late = 1"), body.index("start_aaa"))
        self.assertLess(body.index("start_aaa"), body.index("start_bbb"))

    def test_barrier_blocks_the_only_ordering_that_would_help(self) -> None:
        # start_bbb(:30) 在 python 之前、start_aaa(:10) 在之后：
        # 全序要求 aaa 越过 python，屏障必须拒绝 → 两侧保持原序，文件不动。
        text = BLOCK_A + "\n" + self.PY_A + "\n" + BLOCK_B
        body, result = self._render(text)
        self.assertLess(body.index("start_bbb"), body.index("late = 1"))
        self.assertLess(body.index("late = 1"), body.index("start_aaa"))
        self.assertFalse(result.changed)

    def test_blocks_still_sort_within_one_run(self) -> None:
        # 屏障之间同一段内的块照旧排序，不能因为加了屏障就不排了。
        text = self.PY_A + "\n" + BLOCK_A + "\n" + BLOCK_B + "\n" + self.PY_B
        body, result = self._render(text)
        self.assertTrue(result.changed)
        self.assertLess(body.index("late = 1"), body.index("start_aaa"))
        self.assertLess(body.index("start_aaa"), body.index("start_bbb"))
        self.assertLess(body.index("start_bbb"), body.index("later = 2"))

    def test_note_explains_the_restriction(self) -> None:
        # 需要跨屏障才能排好时才提示；两侧各自有序时不必打扰用户。
        text = self.PY_A + "\n" + BLOCK_A + "\n" + BLOCK_B + "\n" + self.PY_B
        _, result = self._render(text)
        joined = " ".join(result.notes)
        self.assertIn("python/style", joined)


class TestIndex(unittest.TestCase):
    def test_index_numbers_match_new_file(self) -> None:
        text = HEADER + BLOCK_A + "\n" + BLOCK_B
        rebuilt, result = sorttl.sort_tl_file(_parse(text), write_index=True)
        body = "".join(line.text + line.eol for line in rebuilt.lines)
        lines = body.split("\n")
        for entry in result.index_entries:
            target = lines[entry.lineno - 1]
            self.assertTrue(
                target.startswith("translate "),
                f"index says line {entry.lineno} is {target!r}",
            )
            self.assertIn(entry.identifier, target)

    def test_index_followed_by_blank_line(self) -> None:
        text = HEADER + BLOCK_A + "\n" + BLOCK_B
        built, _ = sorttl.sort_tl_file(_parse(text), write_index=True)
        body = "".join(line.text + line.eol for line in built.lines)
        self.assertTrue(body.endswith("\n"))
        # the index is at the front, followed by exactly one blank line
        self.assertTrue(body.startswith(sorttl.INDEX_BEGIN))
        after = body.split(sorttl.INDEX_END, 1)[1]
        self.assertTrue(after.startswith("\n\n"), repr(after[:10]))

    def test_strip_index_is_idempotent(self) -> None:
        text = HEADER + BLOCK_A + "\n" + BLOCK_B
        built, _ = sorttl.sort_tl_file(_parse(text), write_index=True)
        body = "".join(line.text + line.eol for line in built.lines)
        stripped = sorttl.strip_index(body)
        self.assertNotIn(sorttl.INDEX_BEGIN, stripped)
        again, result = sorttl.sort_tl_file(_parse(stripped), write_index=True)
        self.assertEqual(
            "".join(line.text + line.eol for line in again.lines), body
        )

    def test_render_index_roundtrip(self) -> None:
        entries = [
            sorttl.IndexEntry("start_aaa", 5, label="start"),
            sorttl.IndexEntry("start_bbb", 12, label="start2"),
        ]
        rendered = sorttl.render_index(entries)
        self.assertIn("start: L5", rendered)
        self.assertIn("# === End Index ===", rendered)


class TestFoldedIndex(unittest.TestCase):
    """索引按 label 折叠：每句都占一行会让索引膨胀两个数量级。

    Ren'Py 的标识符是 ``<label>_<md5[:8]>``，同一 label 里发生哈希碰撞时再追加
    ``_<n>``。一个大工程 57,414 个块按末段切会派生出 4,314 个「label」，而手写
    索引只有 651 条 —— 它按真正的 label 去重，一个 label 只记第一次出现的行号。
    """

    def test_duplicate_suffix_is_stripped(self) -> None:
        # 三种形态都要归到同一个 label
        self.assertEqual(sorttl.derive_label("night_1_5d9497a5"), "night_1")
        self.assertEqual(sorttl.derive_label("night_1_5d9497a5_2"), "night_1")
        self.assertEqual(sorttl.derive_label("night_1_326fe28a"), "night_1")
        # 非贪婪：后缀里本身带数字也不能被吃掉
        self.assertEqual(sorttl.derive_label("script_2_abcdef01_10"), "script_2")
        # 没有哈希后缀的标识符原样返回
        self.assertEqual(sorttl.derive_label("start"), "start")
        self.assertEqual(sorttl.derive_label(""), "")

    def test_one_entry_per_label(self) -> None:
        text = HEADER + (
            '# game/script.rpy:10\ntranslate chinese start_aaaaaaaa:\n\n'
            '    # e "one"\n    e "一"\n\n'
            '# game/script.rpy:11\ntranslate chinese other_bbbbbbbb:\n\n'
            '    # e "two"\n    e "二"\n\n'
            '# game/script.rpy:12\ntranslate chinese start_aaaaaaaa_1:\n\n'
            '    # e "three"\n    e "三"\n'
        )
        rebuilt, result = sorttl.sort_tl_file(_parse(text), write_index=True)
        labels = [e.label for e in result.index_entries]
        # start_aaaaaaaa 与 start_aaaaaaaa_1 归入 start；other_bbbbbbbb 是另一个
        self.assertEqual(labels, ["start", "other"])

    def test_two_hashes_of_one_label_collapse(self) -> None:
        """同一 label 里哈希碰撞出的另一个块，也必须并进同一条索引。"""
        text = HEADER + (
            '# game/script.rpy:10\ntranslate chinese start_aaaaaaaa:\n\n'
            '    # e "one"\n    e "一"\n\n'
            '# game/script.rpy:20\ntranslate chinese start_bbbbbbbb:\n\n'
            '    # e "two"\n    e "二"\n'
        )
        rebuilt, result = sorttl.sort_tl_file(_parse(text), write_index=True)
        self.assertEqual([e.label for e in result.index_entries], ["start"])

    def test_folded_numbers_point_at_first_block(self) -> None:
        text = HEADER + (
            '# game/script.rpy:10\ntranslate chinese start_aaaaaaaa:\n\n'
            '    # e "one"\n    e "一"\n\n'
            '# game/script.rpy:20\ntranslate chinese start_aaaaaaaa_1:\n\n'
            '    # e "two"\n    e "二"\n'
        )
        rebuilt, result = sorttl.sort_tl_file(_parse(text), write_index=True)
        body = "".join(line.text + line.eol for line in rebuilt.lines)
        lines = body.split("\n")
        self.assertEqual(len(result.index_entries), 1)
        entry = result.index_entries[0]
        self.assertIn("start_aaaaaaaa", lines[entry.lineno - 1])
        self.assertNotIn("_1", lines[entry.lineno - 1])

    def test_folded_index_is_a_fixed_point(self) -> None:
        text = HEADER + BLOCK_A + "\n" + BLOCK_B
        first, _ = sorttl.sort_tl_file(_parse(text), write_index=True)
        body = "".join(line.text + line.eol for line in first.lines)
        # ``sort_tl_path`` strips a previous index before sorting; doing the same
        # here is what makes the second run a fixed point.  Feeding indexed text
        # straight back in would count the index lines as body.
        stripped = sorttl.strip_index(body)
        again, result = sorttl.sort_tl_file(_parse(stripped), write_index=True)
        self.assertEqual("".join(line.text + line.eol for line in again.lines), body)
        self.assertEqual(len(result.index_entries), 2)


class TestFiles(unittest.TestCase):
    def test_dry_run_does_not_write(self) -> None:
        with temp_dir() as root:
            game = os.path.join(root, "game")
            path = os.path.join(game, "tl", "chinese", "script.rpy")
            text = HEADER + BLOCK_A + "\n" + BLOCK_B
            write(path, text)
            result = sorttl.sort_tl_path(path, dry_run=True)
            self.assertTrue(result.changed)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), text)

    def test_write_sorts_and_keeps_bom(self) -> None:
        with temp_dir() as root:
            game = os.path.join(root, "game")
            path = os.path.join(game, "tl", "chinese", "script.rpy")
            text = HEADER + BLOCK_A + "\n" + BLOCK_B
            write(path, "\ufeff" + text)
            sorttl.sort_tl_path(path, dry_run=False)
            with open(path, "rb") as handle:
                raw = handle.read()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            body = raw.decode("utf-8-sig")
            self.assertLess(body.index("start_aaa"), body.index("start_bbb"))


class TestSections(unittest.TestCase):
    """``--sections`` writes ``# --- <label> ---`` markers and stays idempotent.

    Three separate defects were found on the real project while adding this:
    a banner between two blocks was deleted outright, a second run duplicated
    every marker, and ``--index`` reported a change on every run because the
    index was stripped and re-added with identical content.
    """

    def test_marker_inserted_before_first_block_of_a_label(self) -> None:
        tl = tlmod.parse_tl_text(HEADER + BLOCK_A + "\n" + BLOCK_B, "x.rpy")
        rebuilt, _ = sorttl.sort_tl_file(tl, sections=True)
        body = "".join(line.text + "\n" for line in rebuilt.lines)
        self.assertEqual(body.count("# --- "), 2)
        self.assertLess(body.index("# --- start_aaa ---"), body.index("start_aaa"))
        self.assertLess(body.index("# --- start_bbb ---"), body.index("start_bbb"))

    def test_second_run_is_a_fixed_point(self) -> None:
        tl = tlmod.parse_tl_text(HEADER + BLOCK_A + "\n" + BLOCK_B, "x.rpy")
        first, _ = sorttl.sort_tl_file(tl, sections=True)
        text = "".join(line.text + line.eol for line in first.lines)
        again, _ = sorttl.sort_tl_file(
            tlmod.parse_tl_text(text, "x.rpy"), sections=True
        )
        # None means "nothing to write"; a second marker would mean the
        # regenerated marker was appended next to the old one instead of
        # replacing it.
        self.assertIsNone(again)

    def test_hand_written_banner_survives(self) -> None:
        banner = "# === 补译：运行期文案 ===\n"
        source = HEADER + BLOCK_A + "\n" + banner + "\n" + BLOCK_B
        tl = tlmod.parse_tl_text(source, "x.rpy")
        rebuilt, _ = sorttl.sort_tl_file(tl, sections=True)
        body = "".join(line.text + "\n" for line in rebuilt.lines)
        self.assertIn("# === 补译：运行期文案 ===", body)

    def test_section_regex_ignores_other_banners(self) -> None:
        # A banner is not a section: only '# --- name ---' is regenerated.
        for line in ("# === 补译：运行期文案 ===", "# --- a b c ---",
                     "# Chinese translation", "# ---x---"):
            self.assertIsNone(sorttl._SECTION_RE.match(line), line)
        self.assertIsNotNone(sorttl._SECTION_RE.match("# --- name_change ---"))

    def test_index_run_twice_reports_no_change(self) -> None:
        with temp_dir() as root:
            path = os.path.join(root, "game", "tl", "chinese", "script.rpy")
            write(path, HEADER + BLOCK_A + "\n" + BLOCK_B)
            first = sorttl.sort_tl_path(path, write_index=True, dry_run=False,
                                        backup=False)
            self.assertTrue(first.changed)
            second = sorttl.sort_tl_path(path, write_index=True, dry_run=False,
                                         backup=False)
            self.assertFalse(second.changed)


class TestTripleQuotedBody(unittest.TestCase):
    """Indentation inside ``new \"\"\"...\"\"\"`` is data, not formatting.

    The real project keeps patch notes in triple-quoted strings whose
    continuation lines are indented on purpose (``    新增内容说明``).  Both
    the dedent in the parser and the reindent in the writer used to shift those
    lines by four columns, which silently rewrote the translation.
    """

    SOURCE = (
        "# game/devlog.rpy:1\n"
        "translate chinese strings:\n"
        "    old \"\"\"first line\n"
        "    line two\n"
        "\"\"\"\n"
        "    new \"\"\"第一行\n"
        "    第二行\n"
        "        {b}第三行{/b}\n"
        "\"\"\"\n"
    )

    def test_value_round_trips_unchanged(self) -> None:
        tl = _parse(self.SOURCE)
        self.assertEqual(len(tl.blocks[0].strings), 1)
        rebuilt, _ = sorttl.sort_tl_file(tl)
        # A single block in source order has nothing to sort.
        self.assertIsNone(rebuilt, "an already-sorted file must not be rebuilt")

    def test_continuation_indent_is_preserved(self) -> None:
        tl = _parse(self.SOURCE)
        pair = tl.blocks[0].strings[0]
        # The value keeps the continuation indentation exactly as written.
        self.assertEqual(pair.old, "first line\n    line two\n")
        self.assertIn("    第二行", pair.new)
        self.assertIn("        {b}第三行{/b}", pair.new)

    def test_two_blocks_keep_their_continuations(self) -> None:
        # ``strings:`` blocks are never reordered (that would desynchronise
        # ``old``/``new`` pairs), so the writer is exercised directly: this is
        # the exact call ``emit`` makes for every block body.
        body = (
            'old """first line\n'
            "    line two\n"
            '"""\n'
            'new """第一行\n'
            "        {b}第三行{/b}\n"
            '"""'
        )
        lines = sorttl._reindent(body, "")
        joined = "\n".join(lines)
        self.assertIn("    old \"\"\"first line", joined)
        self.assertIn("\n    line two\n", joined, "continuation indent must survive")
        self.assertIn("\n        {b}第三行{/b}\n", joined)

    def test_reindent_leaves_ordinary_lines_alone(self) -> None:
        lines = sorttl._reindent('# e "hi"\ne "你好"', "")
        self.assertEqual(lines, ['    # e "hi"', '    e "你好"'])


if __name__ == "__main__":
    unittest.main()
