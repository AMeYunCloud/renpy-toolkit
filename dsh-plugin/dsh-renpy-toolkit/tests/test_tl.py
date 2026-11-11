"""tl.py 与 protect.py 的核心行为测试。

这些用例把「踩过的坑」直接写成断言，防止以后改代码时回退：

* BOM 与逐行 EOL 必须原样保留（否则整份 diff 变成重写）
* 标识符重复时用 index 消歧
* 单行块 / 多 say 的块拒绝写入
* ``\\n`` 必须写字面两字符
* 含 ``\\"`` 的值必须转 raw string
* 标签/占位符一个不能多、一个不能少

夹具说明见 ``_util.py``：这里拿到的是**单个 .rpy 文件路径**，不是目录。
"""

from __future__ import annotations

import os
import unittest

from _util import (
    SAMPLE_COMMON,
    SAMPLE_ONE_BLOCK,
    read_bytes,
    read_text,
    scratch_file as _one,
    temp_dir,
)

from renpy_toolkit import protect
from renpy_toolkit import tl as tlmod
from renpy_toolkit.errors import UnsafeWriteError


class TestParse(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = temp_dir()
        self._dir.__enter__()
        self.addCleanup(self._dir.__exit__, None, None, None)

    def test_parse_blocks_and_strings(self) -> None:
        tf = tlmod.parse_tl_file(_one(SAMPLE_COMMON))
        self.assertTrue(tf.has_bom)
        self.assertEqual(len(tf.blocks), 3)
        # SAMPLE_COMMON 里 strings 块写在文件开头，所以顺序是 strings 在前；
        # 解析必须保持**文件内的原始顺序**，不能按种类重排。
        kinds = [b.kind for b in tf.blocks]
        self.assertEqual(kinds, ["strings", "translate", "translate"])
        strings = tf.blocks[0]
        self.assertEqual(len(strings.strings), 2)
        self.assertEqual(strings.strings[0].old, "Start Game")
        self.assertEqual(tf.blocks[1].identifier, "start_636ae3f5")
        self.assertEqual(tf.blocks[1].language, "chinese")

    def test_roundtrip_byte_identical(self) -> None:
        path = _one(SAMPLE_COMMON)
        before = read_bytes(path)
        tf = tlmod.parse_tl_file(path)
        self.assertFalse(tf.is_dirty())
        self.assertEqual(before, tf.to_bytes(),
                         "unmodified parse must re-render byte-identically")

    def test_crlf_preserved(self) -> None:
        path = _one(SAMPLE_ONE_BLOCK.replace("\n", "\r\n"))
        tf = tlmod.parse_tl_file(path)
        tlmod.apply_new_text(tf, "start_636ae3f5", "改了")
        out = tf.to_bytes().decode("utf-8-sig")
        self.assertNotIn("\n", out.replace("\r\n", ""), "CRLF must not mix with LF")
        self.assertIn("改了", out)

    def test_mirrored_source_extracted(self) -> None:
        tf = tlmod.parse_tl_file(_one(SAMPLE_COMMON))
        self.assertEqual(tf.blocks[1].old_comments[0], 'e "Hello, world!"')
        self.assertEqual(
            tf.blocks[2].old_comments[0],
            'e "You have {image=heart.png} [points] points."',
        )


class TestRewrite(unittest.TestCase):
    def test_apply_new_text(self) -> None:
        path = _one(SAMPLE_COMMON)
        tf = tlmod.parse_tl_file(path)
        tlmod.apply_new_text(tf, "start_636ae3f5", "你好，世界！")
        self.assertTrue(tf.is_dirty())
        text = tf.render()
        self.assertIn('e "你好，世界！"', text)
        self.assertIn('# e "Hello, world!"', text, "source comment must survive")

    def test_literal_newline_escaped(self) -> None:
        tf = tlmod.parse_tl_file(_one(SAMPLE_COMMON))
        tlmod.apply_new_text(tf, "start_636ae3f5", "第一行\n第二行")
        text = tf.render()
        self.assertIn('e "第一行\\n第二行"', text,
                      "a real newline would split the statement")

    def test_embedded_quote_escaped(self) -> None:
        tf = tlmod.parse_tl_file(_one(SAMPLE_COMMON))
        tlmod.apply_new_text(tf, "start_636ae3f5", '他说"你好"')
        text = tf.render()
        # 内嵌引号必须写成 \"，绝不能用全角引号或弯引号（实测会 401）。
        self.assertIn('他说\\"你好\\"', text)
        self.assertNotIn("\u201c", text)
        self.assertNotIn("\u201d", text)
        # 并且要能原样读回来
        again = tlmod.parse_tl_text(text, path="x.rpy")
        say = again.blocks[1].say_lines[0]
        self.assertEqual(say.text, '他说"你好"')

    def test_refuse_single_line_block(self) -> None:
        source = (
            "# game/script.rpy:1\n"
            "translate chinese start_00000001:\n"
            "\n"
            '    # e "x"\n'
        )
        tf = tlmod.parse_tl_file(_one(source))
        with self.assertRaises(UnsafeWriteError):
            tlmod.apply_new_text(tf, "start_00000001", "y")

    def test_refuse_multi_say_block(self) -> None:
        source = (
            "# game/script.rpy:1\n"
            "translate chinese start_00000002:\n"
            "\n"
            '    # e "a"\n'
            '    e "a"\n'
            "\n"
            '    # e "b"\n'
            '    e "b"\n'
        )
        tf = tlmod.parse_tl_file(_one(source))
        with self.assertRaises(UnsafeWriteError):
            tlmod.apply_new_text(tf, "start_00000002", "z")

    def test_string_replacement(self) -> None:
        tf = tlmod.parse_tl_file(_one(SAMPLE_COMMON))
        pair = tf.blocks[0].strings[0]
        tlmod._set_string_new(tf, pair, "开始游戏")
        self.assertIn('new "开始游戏"', tf.render())

    def test_set_string_new_is_public(self) -> None:
        """`pair.new = ...` is a parsed value, not a render source.

        The public setter must exist so callers never have to poke the private
        `_set_string_new` or edit raw lines themselves.
        """
        tf = tlmod.parse_tl_file(_one(SAMPLE_COMMON))
        old = tf.blocks[0].strings[0].old

        # Assigning the dataclass field does not reach the output.
        tf.blocks[0].strings[0].new = "无效"
        self.assertNotIn("无效", tf.render())

        pair = tlmod.set_string_new(tf, old, "开始游戏")
        self.assertEqual(pair.new, "开始游戏")
        self.assertIn('new "开始游戏"', tf.render())
        self.assertNotIn('new "无效"', tf.render())

    def test_set_string_new_unknown_old_raises(self) -> None:
        tf = tlmod.parse_tl_file(_one(SAMPLE_COMMON))
        with self.assertRaises(KeyError):
            tlmod.set_string_new(tf, "没有这条", "x")

    def test_set_string_new_index_picks_occurrence(self) -> None:
        source = (
            "translate chinese strings:\n"
            '    old "Yes"\n'
            '    new "是"\n'
            "\n"
            "translate chinese strings:\n"
            '    old "Yes"\n'
            '    new "是的"\n'
        )
        tf = tlmod.parse_tl_file(_one(source))
        tlmod.set_string_new(tf, "Yes", "好", index=1)
        rendered = tf.render()
        self.assertIn('new "是"', rendered)
        self.assertIn('new "好"', rendered)
        with self.assertRaises(IndexError):
            tlmod.set_string_new(tf, "Yes", "x", index=5)

    def test_set_string_new_respects_language(self) -> None:
        source = (
            "translate chinese strings:\n"
            '    old "Yes"\n'
            '    new "是"\n'
            "\n"
            "translate spanish strings:\n"
            '    old "Yes"\n'
            '    new "Sí"\n'
        )
        tf = tlmod.parse_tl_file(_one(source))
        tlmod.set_string_new(tf, "Yes", "好", language="spanish")
        rendered = tf.render()
        self.assertIn('new "是"', rendered)   # chinese untouched
        self.assertIn('new "好"', rendered)   # spanish rewritten
        with self.assertRaises(KeyError):
            tlmod.set_string_new(tf, "Yes", "x", language="japanese")

    def test_write_creates_backup(self) -> None:
        path = _one(SAMPLE_COMMON)
        tf = tlmod.parse_tl_file(path)
        tlmod.apply_new_text(tf, "start_636ae3f5", "x")
        tf.write(backup=True)
        self.assertTrue(os.path.exists(path + ".bak"))
        self.assertIn("x", read_text(path))


class TestDuplicateIdentifier(unittest.TestCase):
    SOURCE = (
        "# a\n"
        "translate chinese start_11111111:\n"
        "\n"
        '    # e "one"\n'
        '    e ""\n'
        "\n"
        "# b\n"
        "translate chinese start_11111111:\n"
        "\n"
        '    # e "two"\n'
        '    e ""\n'
    )

    def test_index_disambiguates(self) -> None:
        tf = tlmod.parse_tl_file(_one(self.SOURCE))
        self.assertEqual(len(tf.find("start_11111111")), 2)
        with self.assertRaises(UnsafeWriteError):
            tf.one("start_11111111")

    def test_apply_second_index(self) -> None:
        tf = tlmod.parse_tl_file(_one(self.SOURCE))
        tlmod.apply_new_text(tf, "start_11111111", "第二个", index=1)
        text = tf.render()
        self.assertIn('e "第二个"', text)
        self.assertIn('    e ""\n', text.split("# b")[0],
                      "first block must stay untouched")


class TestTripleQuotedValues(unittest.TestCase):
    """``old \"\"\"...\"\"\"`` 是跨多行的**单个**字面量，不是空串加第二个串。

    Ren'Py 的 ``parse_string`` 会把整段交给 ``eval``，所以：

    * 开引号后那个换行、闭引号前那个换行都属于值本身（不能 trim）；
    * 块内其余行都在第 0 列，``_indent_width`` 会把它们判成块外 —— 必须靠
      ``_triple_open`` 把块体撑到闭引号那一行；
    * 解析时后续行要被跳过，否则会被当成独立的 ``old``/``new`` 而报
      "unexpected line in strings block"。
    """

    SOURCE = (
        "# game/devlog_text.rpy:7\n"
        "translate chinese strings:\n"
        "\n"
        '    old """{size=-6}Update{/b}\n'
        "Second paragraph.\n"
        '"""\n'
        '    new """{size=-6}更新{/b}\n'
        "第二段。\n"
        '"""\n'
    )

    def setUp(self) -> None:
        self._dir = temp_dir()
        self._dir.__enter__()
        self.addCleanup(self._dir.__exit__, None, None, None)

    def test_value_spans_lines(self) -> None:
        tf = tlmod.parse_tl_file(_one(self.SOURCE))
        self.assertEqual(len(tf.blocks), 1)
        block = tf.blocks[0]
        self.assertEqual(len(block.strings), 1, block.problems)
        pair = block.strings[0]
        self.assertEqual(pair.old, "{size=-6}Update{/b}\nSecond paragraph.\n")
        self.assertEqual(pair.new, "{size=-6}更新{/b}\n第二段。\n")
        self.assertEqual(block.problems, [])

    def test_roundtrip_byte_identical(self) -> None:
        path = _one(self.SOURCE)
        tf = tlmod.parse_tl_file(path)
        self.assertEqual(tf.to_bytes(), read_bytes(path))

    def test_block_is_not_split(self) -> None:
        tf = tlmod.parse_tl_file(_one(self.SOURCE))
        self.assertEqual(tf.other_statements, [])

    def test_trailing_text_after_closing_quote_is_not_a_value(self) -> None:
        # 闭引号后面还有别的东西 —— 不是合法的 old/new 行，必须如实报错，
        # 而不是把整行前面那截当成值。
        src = (
            "translate chinese strings:\n"
            "\n"
            '    old """a\n'
            'b""" trailing\n'
            '    new "x"\n'
        )
        tf = tlmod.parse_tl_file(_one(src))
        self.assertTrue(tf.blocks[0].problems)


class TestCallWrappedValues(unittest.TestCase):
    """``new _p("…")`` —— 官方文档的 ``_p()`` 排布形态。

    ``_p`` 在显示时重排多行串，所以源码里常写成三引号跨行并包一层调用。
    不认这层包装，``new`` 会解析成 None，下一次写盘就把译文换成原文，
    **静默丢失已完成的翻译**。
    """

    SOURCE = (
        "translate chinese strings:\n"
        "\n"
        '    old "These two lines will be combined."\n'
        '    new _p("""\n'
        "        \u4e24\u884c\u5408\u6210\u4e00\u884c\u3002\n"
        '        """)\n'
    )

    def setUp(self) -> None:
        self._dir = temp_dir()
        self._dir.__enter__()
        self.addCleanup(self._dir.__exit__, None, None, None)

    def test_multiline_p_call_is_parsed(self) -> None:
        tf = tlmod.parse_tl_file(_one(self.SOURCE))
        pair = tf.blocks[0].strings[0]
        self.assertIsNotNone(pair.new)
        self.assertIn("\u4e24\u884c\u5408\u6210\u4e00\u884c", pair.new)
        self.assertEqual(tf.blocks[0].problems, [])

    def test_one_line_p_and_underscore_calls(self) -> None:
        src = (
            "translate chinese strings:\n"
            "\n"
            '    old _p("one line")\n'
            '    new _("  \u4e2d\u6587")\n'
        )
        tf = tlmod.parse_tl_file(_one(src))
        pair = tf.blocks[0].strings[0]
        self.assertEqual(pair.old, "one line")
        self.assertEqual(pair.new, "  \u4e2d\u6587")

    def test_rewriting_a_multiline_value_removes_its_continuation(self) -> None:
        # 关键回归：只改第一行会把后面两行留成游离文本，文件再也解析不了。
        path = _one(self.SOURCE)
        tf = tlmod.parse_tl_file(path)
        pair = tf.blocks[0].strings[0]
        self.assertGreater(pair.new_raw.count("\n"), 0)
        tlmod._set_string_new(tf, pair, "\u65b0\u8bd1\u6587")
        text = tf.to_bytes().decode("utf-8")
        self.assertEqual(text.count("\n"), 4, text)
        again = _one(text)
        reparsed = tlmod.parse_tl_file(again)
        self.assertEqual(reparsed.blocks[0].strings[0].new, "\u65b0\u8bd1\u6587")
        self.assertEqual(reparsed.other_statements, [])

    def test_other_call_arguments_are_not_invented(self) -> None:
        # 两个以上参数或非字面量参数不能被当成引号串，必须如实报错。
        src = (
            "translate chinese strings:\n"
            "\n"
            '    old "a"\n'
            "    new _p(some_variable)\n"
        )
        tf = tlmod.parse_tl_file(_one(src))
        self.assertIsNone(tf.blocks[0].strings[0].new)
        self.assertTrue(tf.blocks[0].problems)


class TestProtect(unittest.TestCase):
    def test_tags_are_protected(self) -> None:
        source = "You have {image=heart.png} [points] points.{w}"
        protected, chunks = protect.protect(source)
        self.assertNotIn("{image", protected)
        self.assertNotIn("[points]", protected)
        self.assertEqual(protect.restore(protected, chunks), source)

    def test_escaped_bracket_survives(self) -> None:
        source = r"Not \[a tag\] here"
        protected, chunks = protect.protect(source)
        self.assertEqual(protect.restore(protected, chunks), source)

    def test_lost_atom_detected(self) -> None:
        protected, chunks = protect.protect("{b}Hi{/b}")
        broken = protected.replace(protect.PROTECTED_MARK, "", 1)
        with self.assertRaises(protect.PlaceholderError):
            protect.restore(broken, chunks)

    def test_parity_allows_an_unclosed_wrapper(self) -> None:
        # The CJK technique of wrapping a whole line in a font/size tag and
        # never closing it: the value ends with the string. The shipped
        # translation does this (``quest.rpy`` prefixes
        # ``{font=fonts/JCHeiTi-Regular.ttf}`` to a full line), and the engine
        # only warns about it at lint time, so it must not be an error here.
        self.assertEqual(
            protect.check_chunk_parity("{b}Hi{/b}", "{b}嗨{/b}{size=+4}"), []
        )

    def test_parity_reports_missing_tag(self) -> None:
        problems = protect.check_chunk_parity("{b}Hi{/b}", "{b}嗨")
        self.assertTrue(problems, "a dropped closing tag must be reported")
        self.assertIn("unclosed", problems[0])
        self.assertIn("'b'", problems[0])

    def test_parity_allows_reordering(self) -> None:
        # Chinese word order differs; tags travel with the words they wrap.
        # Reordering never changes what renders, so it must stay quiet.
        source = "a {color=#21c3de}X{/color} b {color=#ddd}Y{/color}"
        target = "{color=#ddd}Y{/color}a {color=#21c3de}X{/color}"
        self.assertEqual(protect.check_chunk_parity(source, target), [])

    def test_parity_allows_player_name(self) -> None:
        # English "Human" becomes the player's name in Chinese.
        self.assertEqual(
            protect.check_chunk_parity('c "This human is a heavy sleeper."'.split(" ", 1)[1].strip('"'),
                                       "这个[mc]睡得真沉。"),
            [],
        )

    def test_parity_allows_repairing_an_unclosed_tag(self) -> None:
        # `{size=-6}Tsk*` would render the rest of the paragraph small; closing
        # it repairs the source rather than breaking it.
        self.assertEqual(
            protect.check_chunk_parity("{size=-6}Tsk*", "{size=-6}啧*{/size}"), []
        )

    def test_parity_still_catches_changed_colour(self) -> None:
        problems = protect.check_chunk_parity(
            "{color=#f00}x{/color}", "{color=#0f0}x{/color}"
        )
        self.assertTrue(problems)
        self.assertIn("{color=#f00}", " ".join(problems))

    def test_parity_catches_dropped_pause(self) -> None:
        # `{w}` splits a line into two beats; losing it merges them on screen.
        self.assertTrue(protect.check_chunk_parity("a{w}b", "ab"))
        self.assertEqual(protect.check_chunk_parity("a{w}b", "甲{w}乙"), [])

    def test_parity_catches_dropped_inline_image(self) -> None:
        self.assertTrue(protect.check_chunk_parity("a{image=x.png}b", "ab"))

    def test_parity_catches_stray_closing_tag(self) -> None:
        # Ren'Py raises "closes a text tag that isn't open" at render time.
        problems = protect.check_chunk_parity("plain", "{/b}plain")
        self.assertTrue(problems)
        self.assertIn("isn't open", " ".join(problems))

    def test_parity_catches_dropped_interpolation(self) -> None:
        self.assertTrue(protect.check_chunk_parity("[points] left", "left"))

    def test_parity_allows_retuning_presentation_tags(self) -> None:
        # Shrinking the size to fit CJK glyphs and wrapping in a CJK font is the
        # translation technique itself, not a defect.
        self.assertEqual(protect.check_chunk_parity("{size=-16}Hi", "{size=-13}嗨"), [])
        self.assertEqual(
            protect.check_chunk_parity("{i}Hi", "{font=fonts/j.ttf}{i}嗨{/font}"), []
        )

    def test_self_closing_tag_is_balanced_on_its_own(self) -> None:
        # ``{image=...}`` takes no close tag -- the engine rejects ``{/image}``
        # outright (``renpy/text/extras.py`` maps image to False). Counting it
        # as an unclosed open flagged this working pair as "left unclosed":
        #   old '"Magic"?{image=bg/icons/item_text.png}'
        #   new '"魔法"?{image=bg/icons/item_text.png}'
        self.assertEqual(
            protect.check_chunk_parity(
                '"Magic"?{image=bg/icons/item_text.png}',
                '"魔法"?{image=bg/icons/item_text.png}',
            ),
            [],
        )
        self.assertEqual(protect.check_chunk_parity("{w}Hi", "嗨{w}"), [])

    def test_doubled_bracket_is_literal(self) -> None:
        # ``[[`` is an escaped bracket, not an interpolation -- the engine's
        # parser consumes the pair (``renpy/substitutions.py: parse()``). The
        # shipped translation documents the syntax literally:
        # ``old "type filenames(ex: variable, '<silence 2.>' or [[variable, variable])"``.
        source = "type filenames(ex: variable, '<silence 2.>' or [[variable, variable])"
        target = "输入文件名（例：变量, '<silence 2.>' 或 [[变量, 变量]）"
        self.assertEqual(protect.check_chunk_parity(source, target), [])

    def test_content_tag_dropped_is_still_reported(self) -> None:
        # The rules above must not blind the check: losing a colour that the
        # source used twice is a real defect and still fails.
        source = (
            "{color=#21c3de}Alice{/color} at the {color=#FCDEB3}pond{/color} "
            "in the {color=#FCDEB3}afternoon.{/color}"
        )
        target = "也许下午我能在{color=#FCDEB3}池塘{/color}边找到{color=#21c3de}Alice{/color}。"
        problems = protect.check_chunk_parity(source, target)
        self.assertTrue(problems, "a dropped colour must be reported")
        self.assertIn("{color=#FCDEB3}", problems[0])

    def test_content_tag_added_is_still_reported(self) -> None:
        problems = protect.check_chunk_parity(
            "I already talked to her.",
            "我已经和她谈过了。{image=bg/icons/item_text.png}",
        )
        self.assertTrue(problems, "an invented inline image must be reported")
        self.assertIn("{image=bg/icons/item_text.png}", problems[0])

    def test_parity_strict_reports_reordering(self) -> None:
        source = "{color=#f00}a{/color}{color=#0f0}b{/color}"
        target = "{color=#0f0}b{/color}{color=#f00}a{/color}"
        self.assertEqual(protect.check_chunk_parity(source, target), [])
        self.assertTrue(protect.check_chunk_parity(source, target, strict=True))

    def test_parity_clean(self) -> None:
        self.assertEqual(protect.check_chunk_parity("{b}Hi{/b} [p]", "{b}嗨{/b} [p]"), [])

    def test_nested_subscript_interpolation(self) -> None:
        # Since 8.2.0 an interpolation is a Python expression
        # (``incompatible.html``), so ``[player[money]]`` is one atom, not two
        # fragments. A flat ``\[[^\[\]]*\]`` regex split it and then reported a
        # bogus "adds/misses interpolation" whenever source and translation
        # spelled the same expression differently.
        self.assertEqual(_scan_texts("[player[money]]"), ["[player[money]]"])
        self.assertEqual(
            protect.check_chunk_parity("[player[money]] gold", "[player[money]] 金币"),
            [],
        )
        # A real bracket inside a string literal must not shift the depth.
        self.assertEqual(_scan_texts('[d["a]b"]]'), ['[d["a]b"]]'])

    def test_unpaired_brace_is_not_a_tag(self) -> None:
        chunks = protect._scan("a { b")
        self.assertTrue(all(c.kind == "text" for c in chunks))


class TestTagTables(unittest.TestCase):
    """The tag tables are copied from the engine, so they must match it."""

    def test_engine_closable_tags_are_known(self) -> None:
        # renpy/text/extras.py: text_tags = dict(...); True means closeable.
        for name in ("a", "alpha", "alt", "art", "axis", "b", "color", "cps",
                     "font", "i", "instance", "k", "noalt", "outlinecolor",
                     "plain", "rb", "rt", "s", "shader", "size", "u"):
            self.assertTrue(protect._is_known_tag(name), msg=name)

    def test_engine_self_closing_tags_are_not_closeable(self) -> None:
        # False in the engine table == {/tag} raises at render time.
        for name in ("done", "image", "p", "w", "fast", "nw", "space", "vspace"):
            self.assertIn(name, protect.SELF_CLOSING_TAGS, msg=name)
            self.assertNotIn(name, protect.KNOWN_TAGS, msg=name)

    def test_outlinecolor_and_plain_are_closeable(self) -> None:
        # These were missing from the first version of the table, which made
        # every {outlinecolor} in a real translation look like a typo.
        self.assertNotIn("outlinecolor", protect.SELF_CLOSING_TAGS)
        self.assertNotIn("plain", protect.SELF_CLOSING_TAGS)

    def test_unknown_tag_is_reported(self) -> None:
        problems = protect.check_unknown_tags("plain source", "{scc=red}text")
        self.assertEqual(len(problems), 1)
        self.assertIn("scc", problems[0])

    def test_tag_present_in_source_is_accepted(self) -> None:
        # {sc} is a project tag: it works in the source, so it works in the
        # translation even though the stock table has never heard of it.
        self.assertEqual(protect.check_unknown_tags("{sc}x", "{sc}y"), [])

    def test_declared_project_tag_is_accepted(self) -> None:
        problems = protect.check_unknown_tags(
            "plain", "{sc=red}x", known={"sc"}
        )
        self.assertEqual(problems, [])

    def test_case_is_significant(self) -> None:
        # The engine keys text_tags by exact spelling; {B} really is rejected.
        problems = protect.check_unknown_tags("plain", "{B}bold{/B}")
        self.assertEqual(len(problems), 1)


class TestStyleReferenceTags(unittest.TestCase):
    """``{=style}...{/=style}`` -- a tag with a name-shaped hole.

    The docs describe these under ``text.html`` as "text tags where the tag name
    is empty. In this case, the argument is taken to be the name of a style".
    They used to be skipped entirely by the parity check, so a translation could
    drop the whole pair and nothing was reported.
    """

    SOURCE = "{=mystyle}Styled text{/=mystyle}"

    def test_faithful_translation_passes(self) -> None:
        self.assertEqual(
            protect.check_chunk_parity(self.SOURCE, "{=mystyle}译文{/=mystyle}"), []
        )

    def test_dropping_the_whole_pair_is_reported(self) -> None:
        problems = protect.check_chunk_parity(self.SOURCE, "纯文本")
        self.assertEqual(len(problems), 2)
        self.assertIn("{=mystyle}", " ".join(problems))
        self.assertIn("{/=mystyle}", " ".join(problems))

    def test_dropping_only_the_close_is_reported(self) -> None:
        problems = protect.check_chunk_parity(self.SOURCE, "{=mystyle}纯文本")
        self.assertEqual(len(problems), 1)
        self.assertIn("{/=mystyle}", problems[0])

    def test_inventing_a_close_is_reported(self) -> None:
        problems = protect.check_chunk_parity("{=mystyle}x{/=mystyle}", "{=mystyle}译{/=mystyle}{/=mystyle}")
        self.assertEqual(len(problems), 1)
        self.assertIn("adds", problems[0])

    def test_context_marker_is_still_not_a_tag(self) -> None:
        # {#ctx} is a translation marker: no close tag, no rendered effect.
        self.assertEqual(protect.check_chunk_parity("Enter{#hw_key}", "输入{#hw_key}"), [])
        self.assertIsNone(protect.tag_name(protect._scan("{#hw_key}")[0]))

    def test_style_name_is_not_reported_as_unknown(self) -> None:
        self.assertEqual(protect.check_unknown_tags(self.SOURCE, "{=mystyle}译文{/=mystyle}"), [])

    def test_official_colon_tags_still_pass(self) -> None:
        # {feature:liga=0} / {axis:width=125} / {shader=...:...} are named tags
        # whose argument contains a colon; they must not be mistaken for styles.
        for text in ("{feature:liga=0}x{/feature}", "{axis:width=125}x{/axis}",
                     "{shader=jitter:u__jitter=1.0, 3.0}x{/shader}"):
            self.assertEqual(protect.check_chunk_parity(text, text), [], msg=text)
            self.assertEqual(protect.check_unknown_tags(text, text), [], msg=text)


class TestCustomTagDiscovery(unittest.TestCase):
    def test_wrapping_tag(self) -> None:
        source = 'init python:\n    config.custom_text_tags["sc"] = sc_tag\n'
        wrapping, closing = protect.custom_tags(source)
        self.assertEqual(wrapping, {"sc"})
        self.assertEqual(closing, set())

    def test_self_closing_tag(self) -> None:
        source = (
            'init python:\n'
            '    config.self_closing_custom_text_tags["bang"] = bang_tag\n'
        )
        wrapping, closing = protect.custom_tags(source)
        self.assertEqual(wrapping, set())
        self.assertEqual(closing, {"bang"})

    def test_register_text_tag(self) -> None:
        source = 'init python:\n    renpy.register_text_tag("glow", glow_tag)\n'
        wrapping, _closing = protect.custom_tags(source)
        self.assertEqual(wrapping, {"glow"})

    def test_single_quotes_and_spacing(self) -> None:
        source = "config . custom_text_tags [ 'x' ] = f"
        wrapping, _closing = protect.custom_tags(source)
        self.assertEqual(wrapping, {"x"})

    def test_no_declarations(self) -> None:
        self.assertEqual(protect.custom_tags("label start:\n    pass\n"),
                         (set(), set()))


def _scan_texts(text):
    """Chunk texts from the atomic scanner, for assertions."""
    return [c.text for c in protect._scan(text)]


if __name__ == "__main__":
    unittest.main()
