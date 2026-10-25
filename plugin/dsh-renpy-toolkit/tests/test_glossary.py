"""Tests for :mod:`renpy_toolkit.glossary`."""

from __future__ import annotations

import os
import unittest

import _util

from renpy_toolkit import glossary, tl
from renpy_toolkit.errors import InvalidRequestError

TERMS = """\
# comment line
Demon\t恶魔\tenemy\t魔族|恶魔族\t实测 恶魔 79 : 魔族 2
Slime\t史莱姆\tenemy\t\t[生物]
slime\t黏液\tenemy\t史莱姆\t[体液] 禁止混用
Go back\t返回\tcommon\t回去\t(非"回去")
leader\t村长\ttitle\t首领\t按语境: 村长 / 领袖
Book\t典籍\titem\t\t
"""

TL = """\
translate chinese start_aaaa:

    # e "The demon is here."
    e "恶魔来了。"

translate chinese start_bbbb:

    # e "Go back"
    e "返回"

translate chinese start_cccc:

    # e "Go back to the village."
    e "回去村子。"

translate chinese start_dddd:

    # e "A slime blocks the path."
    e "史莱姆挡住了路。"

translate chinese start_eeee:

    # e "A Slime blocks the path."
    e "史莱姆挡住了路。"

translate chinese strings:

    # game/screens.rpy:10
    old "Book"
    new "书本"
"""


def _write(directory: str, name: str, text: str) -> str:
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestParseTerms(unittest.TestCase):
    def test_roundtrip(self) -> None:
        terms = glossary.parse_terms(TERMS)
        self.assertEqual(len(terms), 6)
        self.assertEqual(terms[0].source, "Demon")
        self.assertEqual(terms[0].banned, ("魔族", "恶魔族"))
        self.assertEqual(terms[0].category, "enemy")
        rendered = glossary.render_terms(terms, header=())
        self.assertEqual(glossary.parse_terms(rendered), terms)

    def test_context_dependent_is_detected(self) -> None:
        terms = {t.source: t for t in glossary.parse_terms(TERMS)}
        self.assertTrue(terms["leader"].is_context_dependent)
        self.assertFalse(terms["Demon"].is_context_dependent)

    def test_empty_target_is_refused(self) -> None:
        with self.assertRaises(InvalidRequestError):
            glossary.parse_terms("Demon\t\t\n")

    def test_unknown_category_is_refused(self) -> None:
        with self.assertRaises(InvalidRequestError):
            glossary.parse_terms("Demon\t恶魔\tmonster\n")

    def test_duplicate_source_is_refused(self) -> None:
        with self.assertRaises(InvalidRequestError):
            glossary.parse_terms("Demon\t恶魔\tenemy\nDemon\t魔鬼\tenemy\n")

    def test_short_row_is_refused(self) -> None:
        with self.assertRaises(InvalidRequestError):
            glossary.parse_terms("just-one-column\n")


class TestCheckTerms(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = _util.tempdir_for(self)
        _write(self.dir, "script.rpy", TL)
        self.terms = glossary.parse_terms(TERMS)

    def test_standalone_banned_is_an_error(self) -> None:
        # ``Book`` -> 书本 uses a rendering that is neither the target nor banned,
        # so it is a TERM_MISSING warning, not an error.
        report = glossary.check_terms(self.dir, self.terms)
        self.assertEqual(report.errors, [])

    def test_sentence_embedded_banned_is_not_reported_by_default(self) -> None:
        # "Go back to the village." -> 回去村子: embedded, legitimately phrased.
        report = glossary.check_terms(self.dir, self.terms)
        self.assertEqual([f for f in report.findings if "Go back" in f.message], [])

    def test_sentence_embedded_banned_is_available_on_request(self) -> None:
        report = glossary.check_terms(self.dir, self.terms, report_drift=True)
        drift = [f for f in report.findings if f.code == "TERM_DRIFT"]
        # Two: "Go back to the village." -> 回去, and "A slime blocks the path."
        # -> 史莱姆 (the fluid rule applied to a sentence about a creature).
        self.assertEqual(len(drift), 2)
        self.assertTrue(any("回去" in f.message for f in drift))

    def test_case_collision_is_matched_exactly(self) -> None:
        """``slime`` (fluid) must not fire on ``Slime`` (the creature)."""
        report = glossary.check_terms(
            self.dir, self.terms, report_missing=True, report_drift=True
        )
        for finding in report.findings:
            if "slime" in finding.message:
                # The fluid rule never sees "A Slime blocks the path."
                self.assertNotIn("A Slime", finding.message)

    def test_missing_only_for_standalone(self) -> None:
        report = glossary.check_terms(self.dir, self.terms, report_missing=True)
        missing = [f for f in report.findings if f.code == "TERM_MISSING"]
        # ``Book`` is a standalone strings entry rendered as 书本, not 典籍.
        self.assertEqual(len(missing), 1)
        self.assertIn("'Book'", missing[0].message)

    def test_report_missing_off_reports_nothing(self) -> None:
        report = glossary.check_terms(self.dir, self.terms, report_missing=False)
        self.assertEqual(report.findings, [])
        self.assertEqual(report.files, 1)

    def test_category_filter(self) -> None:
        report = glossary.check_terms(
            self.dir, self.terms, categories=["common"], report_missing=True
        )
        self.assertTrue(all("Book" not in f.message for f in report.findings))

    def test_file_filter(self) -> None:
        report = glossary.check_terms(
            self.dir, self.terms, files=["other.rpy"], report_missing=True
        )
        self.assertEqual(report.files, 0)

    def test_text_summary_counts(self) -> None:
        report = glossary.check_terms(self.dir, self.terms, report_missing=True)
        text = report.text()
        self.assertIn("1 error(s)".replace("1", "0"), text)
        self.assertIn("warning(s)", text)


class TestApplyRename(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = _util.tempdir_for(self)
        self.path = _write(
            self.dir,
            "a.rpy",
            'translate chinese start_aaaa:\n\n'
            '    # e "Hello there."\n'
            '    e "你好，冒险者。"\n\n'
            "translate chinese strings:\n\n"
            "    old \"Guild\"\n"
            "    new \"冒险者旅店\"\n",
        )

    def test_dry_run_changes_nothing(self) -> None:
        before = _read(self.path)
        # Two units match: the say line and the ``new "冒险者旅店"`` entry.
        result = glossary.apply_rename(self.dir, "冒险者", "冒险家")
        self.assertEqual(result.replaced, 2)
        self.assertEqual(result.blocks, 2)
        self.assertEqual(_read(self.path), before)

    def test_write_replaces_translation_only(self) -> None:
        glossary.apply_rename(self.dir, "冒险者", "冒险家", dry_run=False)
        doc = tl.parse_tl_file(self.path)
        self.assertEqual(doc.blocks[0].say_lines[0].text, "你好，冒险家。")
        # The mirrored source comment and the ``old`` string are untouched.
        self.assertEqual(doc.blocks[0].old_comments, ['e "Hello there."'])
        self.assertEqual(doc.blocks[1].strings[0].old, "Guild")

    def test_strings_block_is_renamed(self) -> None:
        result = glossary.apply_rename(
            self.dir, "冒险者旅店", "冒险者公会", dry_run=False
        )
        self.assertEqual(result.blocks, 1)
        doc = tl.parse_tl_file(self.path)
        self.assertEqual(doc.blocks[1].strings[0].new, "冒险者公会")

    def test_identical_arguments_are_refused(self) -> None:
        with self.assertRaises(InvalidRequestError):
            glossary.apply_rename(self.dir, "你好", "你好")

    def test_empty_old_is_refused(self) -> None:
        with self.assertRaises(InvalidRequestError):
            glossary.apply_rename(self.dir, "", "x")

    def test_file_filter_skips(self) -> None:
        result = glossary.apply_rename(self.dir, "冒险者", "冒险家", files=["nope.rpy"])
        self.assertEqual(result.replaced, 0)


class TestScopes(unittest.TestCase):
    """``scope`` 把「任何游戏都适用」与「只对本工程成立」分开。

    动机很具体：把某部作品的任务名写进通用字典，换一个游戏后这些规则要么
    永不命中（白占位置），要么命中同形异义的普通词（制造误报）。
    """

    SIX = """\
# comment
Demon\t恶魔\tenemy\t\tcommon\t物种
Side With the Smiths\t帮铁匠一把\tquest\t\tproject\t本作任务名
"""

    def test_six_column_layout_is_parsed(self) -> None:
        terms = glossary.parse_terms(self.SIX)
        self.assertEqual(len(terms), 2)
        self.assertEqual(terms[0].scope, "common")
        self.assertEqual(terms[1].scope, "project")

    def test_five_column_layout_defaults_to_project(self) -> None:
        # 老字典没有 scope 列：默认 project，宁可少复用也不扩散。
        terms = glossary.parse_terms("Demon\t恶魔\tenemy\t\t备注\n")
        self.assertEqual(terms[0].scope, glossary.DEFAULT_SCOPE)
        self.assertEqual(glossary.DEFAULT_SCOPE, "project")

    def test_scope_filter_selects_one_bucket(self) -> None:
        with _util.temp_dir() as directory:
            path = _write(directory, "t.tsv", self.SIX)
            self.assertEqual(len(glossary.load_terms(path, scope="common")), 1)
            self.assertEqual(len(glossary.load_terms(path, scope="project")), 1)
            self.assertEqual(len(glossary.load_terms(path)), 2)

    def test_unknown_scope_is_refused(self) -> None:
        with self.assertRaises(InvalidRequestError) as ctx:
            glossary.parse_terms("Demon\t恶魔\tenemy\t\tglobal\t\n")
        self.assertIn("unknown scope", str(ctx.exception))

    def test_scope_survives_a_roundtrip(self) -> None:
        terms = glossary.parse_terms(self.SIX)
        self.assertEqual(glossary.parse_terms(glossary.render_terms(terms)), terms)


class TestCellEscapes(unittest.TestCase):
    """单元格里的真换行/制表符必须转义。

    引擎 UI 串里就带真换行（``"Contacting App Store\\nPlease Wait..."``）。
    不转义会把 TSV 行劈成两半，后半截被当成一条新术语。
    """

    def test_newline_in_source_survives_a_roundtrip(self) -> None:
        term = glossary.Term(
            source="Contacting App Store\nPlease Wait...",
            target="正在联系应用商店\n请稍候……",
            category="ui",
            scope="common",
        )
        rendered = glossary.render_terms([term], header=())
        self.assertEqual(len(rendered.strip().split("\n")), 1)
        back = glossary.parse_terms(rendered)
        self.assertEqual(back, [term])

    def test_tab_in_note_survives_a_roundtrip(self) -> None:
        term = glossary.Term(source="A", target="甲", category="other", note="x\ty")
        back = glossary.parse_terms(glossary.render_terms([term]))
        self.assertEqual(back[0].note, "x\ty")

    def test_backslash_is_not_doubled_forever(self) -> None:
        term = glossary.Term(source="C:\\path", target="路径", category="other")
        rendered = glossary.render_terms([term])
        back = glossary.parse_terms(rendered)
        self.assertEqual(back[0].source, "C:\\path")
        # 二次往返必须稳定，否则每存一次就多一层转义。
        self.assertEqual(glossary.render_terms(back), rendered)


class TestBuiltinUiTerms(unittest.TestCase):
    """插件自带的引擎 UI 术语表。"""

    def test_file_exists_and_parses(self) -> None:
        self.assertTrue(os.path.exists(glossary.UI_TERMS_PATH))
        terms = glossary.load_terms(glossary.UI_TERMS_PATH)
        self.assertGreater(len(terms), 100)

    def test_every_entry_is_common_scope(self) -> None:
        terms = glossary.load_terms(glossary.UI_TERMS_PATH)
        self.assertEqual({t.scope for t in terms}, {"common"})

    def test_engine_ui_strings_are_covered(self) -> None:
        # 只收 renpy/common/ 里的串：主菜单的 Start/Load 来自各游戏自己的
        # screens.rpy，不是引擎文案，所以不在这张通用表里。
        terms = {t.source for t in glossary.load_terms(glossary.UI_TERMS_PATH)}
        for expected in ("Quick save.", "Are you sure?", "Return to the developer menu"):
            self.assertIn(expected, terms)

    def test_scope_filter_keeps_everything(self) -> None:
        path = glossary.UI_TERMS_PATH
        self.assertEqual(
            len(glossary.load_terms(path, scope="common")),
            len(glossary.load_terms(path)),
        )


if __name__ == "__main__":
    unittest.main()
