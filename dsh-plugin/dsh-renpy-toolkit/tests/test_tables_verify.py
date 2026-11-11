"""表格往返、校验器、脚手架、体检的测试。"""

from __future__ import annotations

import os
import unittest

from _util import SAMPLE_COMMON, read_text, temp_dir, temp_tl, write

from renpy_toolkit import audit, tables, verify
from renpy_toolkit import tl as tlmod
from renpy_toolkit import scaffold


class TestTables(unittest.TestCase):
    def test_collect_rows(self) -> None:
        with temp_tl({"common.rpy": SAMPLE_COMMON}) as base:
            rows = tables.collect_rows(base)
        self.assertEqual(len(rows), 4)  # 2 translate say lines + 2 strings
        ids = [r.id for r in rows]
        self.assertIn("start_636ae3f5", ids)
        # 行按文件顺序出现：strings 块在 SAMPLE_COMMON 开头，所以前两行是
        # 界面字符串，后两行才是对话。
        self.assertEqual([r.kind for r in rows],
                         ["strings", "strings", "translate", "translate"])
        self.assertEqual(rows[0].source, "Start Game")
        self.assertEqual(rows[2].source, 'e "Hello, world!"')

    def test_json_roundtrip(self) -> None:
        with temp_tl({"common.rpy": SAMPLE_COMMON}) as base, temp_dir() as out:
            rows = tables.collect_rows(base)
            path = os.path.join(out, "t.json")
            tables.export_table(rows, path)
            back = tables.read_table(path)
        self.assertEqual(len(back), len(rows))
        self.assertEqual(back[0].source, rows[0].source)

    def test_csv_roundtrip_and_apply(self) -> None:
        with temp_tl({"common.rpy": SAMPLE_COMMON}) as base, temp_dir() as out:
            rows = tables.collect_rows(base)
            for row in rows:
                row.target = "译文:" + row.source[-5:]
            path = os.path.join(out, "t.csv")
            tables.export_table(rows, path)

            back = tables.read_table(path)
            self.assertEqual(len(back), len(rows))

            result = tables.apply_rows(back, base, dry_run=False)
            self.assertEqual(result.problems, [], msg=f"problems: {result.problems}")
            text = read_text(os.path.join(base, "common.rpy"))
            self.assertIn("译文:", text)

    def test_csv_defuses_formulas(self) -> None:
        """Game text is attacker-controlled; a cell must not become a formula."""
        rows = [
            tables.Row(id="start_aaaa", kind="translate", file="a.rpy", line=1,
                       context="e", source="=cmd|'/c calc'!A1", target="@SUM(1)"),
            tables.Row(id="start_bbbb", kind="translate", file="a.rpy", line=2,
                       context="e", source="-2+3", target="+HYPERLINK(1)"),
            tables.Row(id="start_cccc", kind="translate", file="a.rpy", line=3,
                       context="e", source="plain", target="正常译文"),
        ]
        with temp_dir() as out:
            path = os.path.join(out, "t.csv")
            tables.export_table(rows, path, format="csv")
            text = read_text(path)
            for cell in ("=cmd", "@SUM", "-2+3", "+HYPERLINK"):
                self.assertNotIn("," + cell, text, msg=f"{cell} was not neutralised")
                self.assertIn("'" + cell, text)

            back = tables.read_table(path, format="csv")
        self.assertEqual([r.source for r in back], [r.source for r in rows])
        self.assertEqual([r.target for r in back], [r.target for r in rows])

    def test_xlsx_defuses_formulas(self) -> None:
        try:
            from openpyxl import load_workbook
        except ImportError:  # pragma: no cover
            self.skipTest("openpyxl not installed")
        rows = [
            tables.Row(id="start_aaaa", kind="translate", file="a.rpy", line=1,
                       context="e", source="=1+1", target="普通"),
        ]
        with temp_dir() as out:
            path = os.path.join(out, "t.xlsx")
            tables.export_table(rows, path, format="xlsx")
            book = load_workbook(path)
            sheet = book[book.sheetnames[0]]
            cell = sheet.cell(row=2, column=6).value
            back = tables.read_table(path, format="xlsx")
        self.assertEqual(cell, "'=1+1")
        self.assertEqual(back[0].source, "=1+1")

    def test_po_roundtrip(self) -> None:
        with temp_tl({"common.rpy": SAMPLE_COMMON}) as base, temp_dir() as out:
            rows = tables.collect_rows(base)
            rows[2].target = "你好，世界！"   # 第一条对话
            rows[0].target = "开始游戏"       # 第一条界面字符串
            path = os.path.join(out, "t.po")
            tables.export_table(rows, path, format="po")

            raw = read_text(path)
            self.assertIn("msgctxt", raw)
            self.assertIn("msgid", raw)
            self.assertIn("你好，世界！", raw)

            back = tables.read_table(path)
        self.assertEqual(len(back), len(rows))
        self.assertEqual(back[2].target, "你好，世界！")
        self.assertEqual(back[2].source, rows[2].source)
        self.assertEqual(back[0].kind, "strings")
        self.assertEqual(back[0].target, "开始游戏")

    def test_po_escapes(self) -> None:
        row = tables.Row(
            id="start_1", kind="translate", file="a.rpy", line=1, context="e",
            source='e "a \\"b\\" c"', target='他"说"',
        )
        data = tables.export_po([row])
        text = data.decode("utf-8")
        self.assertIn('\\"', text)
        back = tables.import_po(data)
        self.assertEqual(back[0].target, '他"说"')
        self.assertEqual(back[0].source, row.source)

    def test_apply_skips_empty_target(self) -> None:
        with temp_tl({"common.rpy": SAMPLE_COMMON}) as base:
            rows = tables.collect_rows(base)
            # 全部 target 为空 -> 什么都不该写
            result = tables.apply_rows(rows, base, dry_run=False)
            self.assertEqual(result.written, [])
            self.assertEqual(result.applied, 0)

    def test_apply_detects_source_mismatch(self) -> None:
        with temp_tl({"common.rpy": SAMPLE_COMMON}) as base:
            rows = tables.collect_rows(base)
            target = rows[2]  # 一条 translate 行，才有 identifier 可查
            target.target = "x"
            target.source = 'e "something else entirely"'
            result = tables.apply_rows(rows, base, dry_run=True)
            self.assertTrue(result.problems)
            self.assertIn("source mismatch", result.problems[0])

    def test_strings_matched_by_context(self) -> None:
        source = (
            'translate chinese strings:\n'
            '\n'
            '    # game/a.rpy:1\n'
            '    old "{#menu}Start"\n'
            '    new ""\n'
            '\n'
            '    # game/a.rpy:2\n'
            '    old "Start"\n'
            '    new ""\n'
        )
        with temp_tl({"s.rpy": source}) as base:
            rows = tables.collect_rows(base)
            rows[0].target = "开始菜单"
            rows[1].target = "开始"
            result = tables.apply_rows(rows, base, dry_run=False)
            self.assertEqual(result.problems, [], msg=str(result.problems))
            text = read_text(os.path.join(base, "s.rpy"))
        self.assertIn("开始菜单", text)
        self.assertIn('new "开始"', text)


class TestVerify(unittest.TestCase):
    def test_clean_translation_passes(self) -> None:
        from _util import sample_translated

        with temp_tl({"common.rpy": sample_translated()}) as base:
            report = verify.verify_tl_dir(base)
        errors = [f for f in report.errors if f.code != "UNTRANSLATED"]
        self.assertEqual(errors, [], msg=report.text())
        self.assertEqual(report.total, 4)
        self.assertEqual(report.translated, 4)

    def test_empty_detected(self) -> None:
        with temp_tl({"common.rpy": SAMPLE_COMMON}) as base:
            report = verify.verify_tl_dir(base)
        self.assertTrue(any(f.code == "EMPTY" for f in report.errors))
        self.assertFalse(report.ok)

    def test_expected_block_count_passes(self) -> None:
        from _util import sample_translated

        with temp_tl({"common.rpy": sample_translated()}) as base:
            report = verify.verify_tl_dir(base, expect_dialogue=2)
        self.assertEqual(
            [f for f in report.errors if f.code == "BLOCK_COUNT"], [], msg=report.text()
        )

    def test_expected_block_count_mismatch_is_an_error(self) -> None:
        # The failure mode this guards: a generator that silently writes fewer
        # blocks than the script tree holds. Every written block is valid, so no
        # per-block check notices -- only the total does.
        from _util import sample_translated

        with temp_tl({"common.rpy": sample_translated()}) as base:
            report = verify.verify_tl_dir(base, expect_dialogue=9)
        codes = [f.code for f in report.errors]
        self.assertIn("BLOCK_COUNT", codes, report.text())
        self.assertFalse(report.ok)
        finding = next(f for f in report.errors if f.code == "BLOCK_COUNT")
        self.assertIn("expected 9", finding.message)

    def test_block_count_error_survives_max_items(self) -> None:
        # A skeleton run produces thousands of warnings; the one error must not
        # be pushed past the display limit.
        from _util import sample_translated

        with temp_tl({"common.rpy": sample_translated()}) as base:
            report = verify.verify_tl_dir(base, expect_dialogue=9)
        text = report.text(max_items=1)
        self.assertIn("BLOCK_COUNT", text)

    def test_tag_mismatch_detected(self) -> None:
        bad = (
            "# game/x.rpy:1\n"
            "translate chinese start_1:\n"
            "\n"
            '    # e "You have [points] left"\n'
            '    e "你还剩 [point] 点"\n'
        )
        with temp_tl({"x.rpy": bad}) as base:
            report = verify.verify_tl_dir(base)
        self.assertTrue(any(f.code == "TAG_MISMATCH" for f in report.errors), report.text())

    def test_literal_newline_detected(self) -> None:
        # 真换行会把一条 say 劈成两条语句：前半句留在块里（且引号没闭合），
        # 后半句掉到块外。解析器把掉出去的行记进 other_statements，
        # 校验器必须据此报 STRAY_STATEMENT——否则覆盖率显示 0/0 假装全绿。
        bad = (
            "# game/x.rpy:1\n"
            "translate chinese start_2:\n"
            "\n"
            '    # e "a b"\n'
            '    e "第一行\n第二行"\n'
        )
        with temp_tl({"x.rpy": bad}) as base:
            report = verify.verify_tl_dir(base)
        codes = {f.code for f in report.errors}
        self.assertIn("STRAY_STATEMENT", codes, report.text())
        self.assertFalse(report.ok)

    def test_honest_literal_newline_escape_is_clean(self) -> None:
        # 正确写法：字面的 \\n 两个字符。不能误报。
        good = (
            "# game/x.rpy:1\n"
            "translate chinese start_2:\n"
            "\n"
            '    # e "a b"\n'
            '    e "第一行\\n第二行"\n'
        )
        with temp_tl({"x.rpy": good}) as base:
            report = verify.verify_tl_dir(base)
        self.assertFalse(
            any(f.code in ("STRAY_STATEMENT", "LITERAL_NEWLINE") for f in report.errors),
            report.text(),
        )

    def test_conflicting_string_detected(self) -> None:
        a = ('translate chinese strings:\n\n'
             '    old "OK"\n    new "好的"\n')
        b = ('translate chinese strings:\n\n'
             '    old "OK"\n    new "确定"\n')
        with temp_tl({"a.rpy": a, "b.rpy": b}) as base:
            report = verify.verify_tl_dir(base)
        self.assertTrue(
            any(f.code == "CONFLICTING_STRING" for f in report.findings), report.text()
        )

    def test_stutter_loss_flagged(self) -> None:
        bad = (
            "# game/x.rpy:1\n"
            "translate chinese start_3:\n"
            "\n"
            '    # e "I-I-I don\'t know."\n'
            '    e "我不知道。"\n'
        )
        with temp_tl({"x.rpy": bad}) as base:
            report = verify.verify_tl_dir(base)
        self.assertTrue(any(f.code == "STUTTER_LOST" for f in report.warnings), report.text())

    def test_quotes(self) -> None:
        self.assertTrue(verify.check_quotes('他说\u201c你好\u201d'))
        # A bare ASCII quote inside a decoded value is normal: the writer
        # escapes it again on the way out (``tl.format_value``). Counting them
        # here flagged a working line in the shipped translation
        # (``v "{size=-10}\"所以……？"``), so only the typographic rule remains.
        self.assertEqual(verify.check_quotes('a "b'), [])
        self.assertEqual(verify.check_quotes(r'a \"b"'), [])
        self.assertEqual(verify.check_quotes('a "b', from_source=True), [])

    def test_is_probably_untranslated(self) -> None:
        self.assertTrue(verify.is_probably_untranslated("Yes, of course", "Yes, of course"))
        self.assertFalse(verify.is_probably_untranslated("OK", "OK"))
        self.assertFalse(verify.is_probably_untranslated("Hello", "你好"))


class TestScaffold(unittest.TestCase):
    def test_new_project(self) -> None:
        with temp_dir() as root:
            result = scaffold.new_project("Demo", dest=root)
            project = os.path.join(root, "demo")
            game = os.path.join(project, "game")
            self.assertTrue(os.path.isdir(game))
            self.assertTrue(os.path.exists(os.path.join(game, "options.rpy")))
            self.assertTrue(
                os.path.exists(os.path.join(game, "tl", "chinese", "common.rpy"))
            )
            self.assertTrue(result.files)
            # Ren'Py 只认 game/ 下的文件，所以所有产物都必须在 game/ 里
            for rel in result.files:
                self.assertTrue(
                    rel.replace("\\", "/").startswith("game/"),
                    f"{rel} is outside game/ and would be ignored by Ren'Py",
                )

    def test_verify_project(self) -> None:
        with temp_dir() as root:
            scaffold.new_project("Demo2", dest=root)
            problems = scaffold.verify_project(os.path.join(root, "demo2"))
        self.assertEqual(problems, [], msg=str(problems))

    def test_verify_project_flags_flat_layout(self) -> None:
        # 反例：文件平铺在项目根目录，Ren'Py 看不到
        with temp_dir() as root:
            write(os.path.join(root, "options.rpy"), "")
            problems = scaffold.verify_project(root)
        self.assertTrue(any("game/" in p for p in problems), problems)

    def test_rejects_a_path_as_the_project_name(self) -> None:
        # `project new E:\work\MyGame` 会把整条路径压成 ./e-work-my-game，
        # 在看不见的地方建目录。这种输入必须明确报错。
        with temp_dir() as root:
            for bad in (
                os.path.join(root, "MyGame"),
                "a" + os.sep + "b",
                "C:\\games\\MyGame",
            ):
                with self.assertRaises(scaffold.ScaffoldError, msg=bad):
                    scaffold.new_project(bad, dest=root)
            # 拒绝之后不能留下任何东西
            self.assertEqual(os.listdir(root), [])

    def test_accepts_a_bare_name(self) -> None:
        with temp_dir() as root:
            scaffold.new_project("My Game 2", dest=root)
            self.assertTrue(os.path.isdir(os.path.join(root, "my-game-2")))


class TestAudit(unittest.TestCase):
    def test_missing_image_flagged(self) -> None:
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(
                os.path.join(game, "script.rpy"),
                "label start:\n    show eileen happy\n    return\n",
            )
            report = audit.scan_project(root, include_archives=False)
        self.assertTrue(report.issues, "a show of an undefined image must be reported")

    def test_clean_project_ok(self) -> None:
        with temp_dir() as root:
            game = os.path.join(root, "game")
            with open(write(os.path.join(game, "images", "bg.png"), ""), "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\n")
            write(
                os.path.join(game, "script.rpy"),
                'image bg room = "images/bg.png"\n'
                "label start:\n"
                "    scene bg room\n"
                "    return\n",
            )
            report = audit.scan_project(root, include_archives=False)
        self.assertTrue(report.ok, msg=report.text())

    def test_force_archives_is_an_error(self) -> None:
        """Archives winning over loose files would silently drop a TL patch.

        ``renpy/loader.py`` loads from the filesystem first *only* while
        ``config.force_archives`` is false, so setting it makes the shipped
        ``archive.rpa`` shadow every loose translation file we write.
        """
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(
                os.path.join(game, "options.rpy"),
                'config.archives = ["archive"]\n'
                "config.force_archives = True\n",
            )
            write(os.path.join(game, "script.rpy"), "label start:\n    return\n")
            report = audit.scan_project(root, include_archives=False)
        found = [i for i in report.issues if i.category == "force-archives"]
        self.assertEqual(len(found), 1, msg=report.text())
        self.assertEqual(found[0].severity, "error")
        self.assertIn("ignored", found[0].detail)

    def test_commented_force_archives_is_ignored(self) -> None:
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(
                os.path.join(game, "options.rpy"),
                "# config.force_archives = True\n"
                'config.archives = ["archive"]\n',
            )
            write(os.path.join(game, "script.rpy"), "label start:\n    return\n")
            report = audit.scan_project(root, include_archives=False)
        found = [i for i in report.issues if i.category == "force-archives"]
        self.assertEqual(found, [], msg=report.text())

    def test_duplicate_old_across_files_is_an_error(self) -> None:
        """A duplicate ``old`` aborts the game before the main menu.

        ``StringTranslator.add()`` raises ``A translation for "..." already
        exists``, so this must be an error rather than a warning.
        """
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(os.path.join(game, "script.rpy"), "label start:\n    return\n")
            for name in ("a.rpy", "b.rpy"):
                write(
                    os.path.join(game, "tl", "chinese", name),
                    "translate chinese strings:\n"
                    '    old "Enter"\n'
                    '    new "进入"\n',
                )
            report = audit.scan_project(root, include_archives=False)
        found = [i for i in report.issues if i.category == "duplicate-translation"]
        self.assertEqual(len(found), 1, msg=report.text())
        self.assertEqual(found[0].severity, "error")
        self.assertIn("Enter", found[0].detail)

    def test_duplicate_old_within_one_file_is_an_error(self) -> None:
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(os.path.join(game, "script.rpy"), "label start:\n    return\n")
            write(
                os.path.join(game, "tl", "chinese", "a.rpy"),
                "translate chinese strings:\n"
                '    old "Enter"\n'
                '    new "进入"\n'
                "\n"
                "translate chinese strings:\n"
                '    old "Enter"\n'
                '    new "进入"\n',
            )
            report = audit.scan_project(root, include_archives=False)
        found = [i for i in report.issues if i.category == "duplicate-translation"]
        self.assertEqual(len(found), 1, msg=report.text())

    def test_distinct_old_values_are_clean(self) -> None:
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(os.path.join(game, "script.rpy"), "label start:\n    return\n")
            write(
                os.path.join(game, "tl", "chinese", "a.rpy"),
                "translate chinese strings:\n"
                '    old "Enter"\n'
                '    new "进入"\n'
                '    old "Return"\n'
                '    new "返回"\n',
            )
            report = audit.scan_project(root, include_archives=False)
        found = [i for i in report.issues if i.category == "duplicate-translation"]
        self.assertEqual(found, [], msg=report.text())

    def test_duplicate_strings_are_legal_across_languages(self) -> None:
        """The same ``old`` under two languages is two separate tables."""
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(os.path.join(game, "script.rpy"), "label start:\n    return\n")
            for lang in ("chinese", "spanish"):
                write(
                    os.path.join(game, "tl", lang, "a.rpy"),
                    "translate %s strings:\n"
                    '    old "Enter"\n'
                    '    new "x"\n' % lang,
                )
            report = audit.scan_project(root, include_archives=False)
        found = [i for i in report.issues if i.category == "duplicate-translation"]
        self.assertEqual(found, [], msg=report.text())

    def test_runtime_strings_callback_is_a_warning(self) -> None:
        """Strings registered from code never reach ``tl gen``.

        ``config.translate_additional_strings_callbacks`` is documented as a
        list of callbacks returning ``(filename, linenumber, string)`` tuples.
        The generator reads the compiled statement graph, so it cannot see
        them; the coverage figure is an upper bound and the audit must say so.
        """
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(os.path.join(game, "script.rpy"), "label start:\n    return\n")
            write(
                os.path.join(game, "options.rpy"),
                "init python:\n"
                "    config.translate_additional_strings_callbacks.append(cb)\n",
            )
            report = audit.scan_project(root, include_archives=False)
        found = [i for i in report.issues if i.category == "runtime-strings"]
        self.assertEqual(len(found), 1, msg=report.text())
        self.assertEqual(found[0].severity, "warning")
        self.assertIn("static scan", found[0].detail)

    def test_runtime_strings_assignment_is_detected(self) -> None:
        # Assignment is the other spelling; matching only `.append(` would miss
        # a project that replaces the whole list.
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(os.path.join(game, "script.rpy"), "label start:\n    return\n")
            write(
                os.path.join(game, "options.rpy"),
                "init python:\n"
                "    config.translate_additional_strings_callbacks = [cb]\n",
            )
            report = audit.scan_project(root, include_archives=False)
        found = [i for i in report.issues if i.category == "runtime-strings"]
        self.assertEqual(len(found), 1, msg=report.text())

    def test_commented_runtime_strings_is_ignored(self) -> None:
        with temp_dir() as root:
            game = os.path.join(root, "game")
            write(os.path.join(game, "script.rpy"), "label start:\n    return\n")
            write(
                os.path.join(game, "options.rpy"),
                "# config.translate_additional_strings_callbacks = [cb]\n",
            )
            report = audit.scan_project(root, include_archives=False)
        found = [i for i in report.issues if i.category == "runtime-strings"]
        self.assertEqual(found, [], msg=report.text())


if __name__ == "__main__":
    unittest.main()
