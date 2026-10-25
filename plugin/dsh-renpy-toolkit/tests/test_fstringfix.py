"""Tests for the f-string repair generator.

The routes are chosen from the engine's lookup order (see
``renpy_toolkit.fstringfix``): a template key never matches, the finished
sentence does.  These tests pin the classification and the generated source
without needing a game.
"""

from __future__ import annotations

import ast
import os
import re
import unittest

from _util import temp_dir  # noqa: F401  (puts the plugin root on sys.path)

from renpy_toolkit import fstringfix
from renpy_toolkit.errors import InvalidRequestError, ToolkitError


def _row(text: str, *, file: str = "script.rpy", line: int = 10) -> dict:
    return {"file": file, "line": line, "text": text, "how": "pycode"}


def _patterns(source: str) -> list[str]:
    """Return the regexes in the generated patch, as the engine will see them.

    The patch is Ren'Py source, so each pattern is written as an escaped Python
    literal.  ``ast.literal_eval`` turns ``\\\\.`` back into ``\\.``; without it
    the assertions would be inspecting the file bytes rather than the pattern.
    """
    return [
        ast.literal_eval('"' + match.group(1) + '"')
        for match in re.finditer(r'_rtk_re\.compile\("([^"]+)"\)', source)
    ]


class TestClassify(unittest.TestCase):
    def test_plain_field_becomes_a_pattern(self):
        fix = fstringfix.classify(_row("f'You bought 1 {item_name}!'"))
        self.assertEqual(fix.route, "pattern")
        self.assertEqual(fix.template, "You bought 1 {0}!")
        self.assertEqual(fix.fields, ["item_name"])

    def test_several_fields_keep_their_order(self):
        fix = fstringfix.classify(_row("f'{item}: {actual}/{total}'"))
        self.assertEqual(fix.template, "{0}: {1}/{2}")
        self.assertEqual(fix.fields, ["item", "actual", "total"])

    def test_format_spec_is_manual(self):
        # ``.0f`` cannot be reproduced by a pattern: the captured text is
        # already rounded, so substituting it back would print "12.4" as if it
        # were the raw value.
        fix = fstringfix.classify(_row("f'{to_mb(done):.0f} MB'"))
        self.assertEqual(fix.route, "manual")
        self.assertIn("format spec", fix.reason)

    def test_conversion_is_manual(self):
        fix = fstringfix.classify(_row("f'{value!r}'"))
        self.assertEqual(fix.route, "manual")
        self.assertIn("conversion", fix.reason)

    def test_expression_field_is_manual(self):
        fix = fstringfix.classify(_row("f'{int(heal_value * 100)}%'"))
        self.assertEqual(fix.route, "manual")
        self.assertIn("expression", fix.reason)

    def test_field_free_fstring_is_not_a_fix_candidate(self):
        fix = fstringfix.classify(_row("f'Level up!'"))
        self.assertEqual(fix.fields, [])
        # ``analyse`` drops these; the engine already translates them.
        self.assertEqual(fstringfix.analyse([_row("f'Level up!'")]), [])

    def test_unparsable_source_is_manual(self):
        fix = fstringfix.classify(_row("not an f-string at all"))
        self.assertEqual(fix.route, "manual")
        self.assertIn("cannot be parsed", fix.reason)


class TestAnalyse(unittest.TestCase):
    def test_non_prose_templates_are_dropped(self):
        rows = [
            _row("f'{percent}%'"),
            _row("f'{base}{frame}{ext}'"),
            _row("f'You bought 1 {item_name}!'"),
        ]
        fixes = fstringfix.analyse(rows)
        self.assertEqual([fix.template for fix in fixes], ["You bought 1 {0}!"])

    def test_only_restricts_work_to_named_rows(self):
        rows = [
            _row("f'You bought 1 {item}!'", file="a.rpy", line=1),
            _row("f'You sold 1 {item}!'", file="b.rpy", line=2),
        ]
        fixes = fstringfix.analyse(rows, only={("b.rpy", 2)})
        self.assertEqual([fix.line for fix in fixes], [2])

    def test_enumerating_every_field_promotes_to_exact(self):
        rows = [_row("f'You bought 1 {item_name}!'")]
        fixes = fstringfix.analyse(rows, values={"item_name": ["Sandwich", "Juice"]})
        self.assertEqual(fixes[0].route, "exact")
        self.assertEqual(fixes[0].values,
                         ["You bought 1 Sandwich!", "You bought 1 Juice!"])

    def test_partial_enumeration_keeps_the_pattern(self):
        # Two fields, only one enumerated: a partial product would translate
        # some results and leave the rest in English.
        rows = [_row("f'{item} costs {price}!'")]
        fixes = fstringfix.analyse(rows, values={"item": ["Axe"]})
        self.assertEqual(fixes[0].route, "pattern")

    def test_enumerating_two_fields_crosses_them(self):
        rows = [_row("f'{item} costs {price}!'")]
        fixes = fstringfix.analyse(
            rows, values={"item": ["Axe", "Stick"], "price": ["5", "10"]})
        self.assertEqual(fixes[0].route, "exact")
        self.assertEqual(len(fixes[0].values), 4)
        self.assertIn("Axe costs 5!", fixes[0].values)

    def test_manual_rows_are_kept_for_the_report(self):
        fixes = fstringfix.analyse([_row("f'{to_mb(done):.0f} MB'")])
        self.assertEqual(fixes[0].route, "manual")

    def test_results_already_translated_are_dropped(self):
        # A second strings entry for an ``old`` that another TL file already
        # registers is a startup crash, not a merge.
        rows = [_row("f'You bought 1 {item_name}!'")]
        fixes = fstringfix.analyse(
            rows,
            values={"item_name": ["Sandwich", "Juice"]},
            already={"You bought 1 Sandwich!"},
        )
        self.assertEqual(fixes[0].route, "exact")
        self.assertEqual(fixes[0].values, ["You bought 1 Juice!"])
        self.assertIn("already translated elsewhere", fixes[0].reason)

    def test_a_fully_translated_row_becomes_manual(self):
        rows = [_row("f'You bought 1 {item_name}!'")]
        fixes = fstringfix.analyse(
            rows,
            values={"item_name": ["Sandwich"]},
            already={"You bought 1 Sandwich!"},
        )
        self.assertEqual(fixes[0].route, "manual")
        self.assertIn("duplicate", fixes[0].reason)


class TestExistingOlds(unittest.TestCase):
    def test_collects_old_values_from_other_files(self):
        with temp_dir() as directory:
            body = (
                "\ufefftranslate chinese strings:\n"
                "\n"
                '    old "Enter"\n'
                '    new "进入"\n'
                "\n"
                '    old "Load"\n'
                '    new "读档"\n'
            )
            with open(os.path.join(directory, "screens.rpy"), "w",
                      encoding="utf-8", newline="\n") as handle:
                handle.write(body)
            found = fstringfix.existing_olds(directory)
        self.assertEqual(found, {"Enter", "Load"})

    def test_excludes_the_generated_files(self):
        with temp_dir() as directory:
            for name in ("screens.rpy", fstringfix.strings_filename("chinese")):
                with open(os.path.join(directory, name), "w",
                          encoding="utf-8", newline="\n") as handle:
                    handle.write('\ufefftranslate chinese strings:\n\n'
                                 '    old "%s"\n    new "x"\n' % name)
            found = fstringfix.existing_olds(
                directory, exclude=(fstringfix.strings_filename("chinese"),))
        self.assertEqual(found, {"screens.rpy"})

    def test_missing_directory_is_empty(self):
        self.assertEqual(
            fstringfix.existing_olds(os.path.join("no", "such", "dir")), set())


class TestParseValues(unittest.TestCase):
    def test_parses_pairs(self):
        self.assertEqual(
            fstringfix.parse_values(["item=a,b", "who=x"]),
            {"item": ["a", "b"], "who": ["x"]},
        )

    def test_rejects_missing_equals(self):
        with self.assertRaises(InvalidRequestError):
            fstringfix.parse_values(["item"])

    def test_rejects_empty_list(self):
        with self.assertRaises(InvalidRequestError):
            fstringfix.parse_values(["item=,,"])


class TestBuildStringsBlock(unittest.TestCase):
    def _source(self, **kwargs) -> str:
        rows = [_row("f'You bought 1 {item_name}!'")]
        fixes = fstringfix.analyse(rows, values={"item_name": ["Sandwich"]})
        return fstringfix.build_strings_block("chinese", fixes, **kwargs)

    def test_writes_old_and_new_pairs(self):
        source = self._source()
        self.assertIn("translate chinese strings:", source)
        self.assertIn('    old "You bought 1 Sandwich!"', source)
        self.assertIn('    new "You bought 1 Sandwich!"', source)

    def test_placeholder_keeps_the_original_not_empty(self):
        # An empty ``new`` blanks the text at runtime: StringTranslator.translate
        # returns any value that is not None, including "".
        source = self._source()
        self.assertNotIn('new ""', source)

    def test_carries_the_marker(self):
        self.assertIn(fstringfix.STRINGS_MARKER, self._source())

    def test_requires_an_exact_fix(self):
        fixes = fstringfix.analyse([_row("f'You bought 1 {item}!'")])
        with self.assertRaises(ToolkitError):
            fstringfix.build_strings_block("chinese", fixes)

    def test_shared_results_are_written_once(self):
        # Three sites expand the same template with the same pool.  Emitting the
        # product three times makes the engine raise
        # "A translation for "..." already exists" and the game will not start.
        rows = [
            _row("f'You bought 1 {item_name}!'", file="a.rpy", line=1),
            _row("f'You bought 1 {item_name}!'", file="b.rpy", line=2),
            _row("f'You bought 1 {item_name}!'", file="c.rpy", line=3),
        ]
        fixes = fstringfix.analyse(rows, values={"item_name": ["Sandwich", "Juice"]})
        source = fstringfix.build_strings_block("chinese", fixes)
        self.assertEqual(source.count('old "You bought 1 Sandwich!"'), 1)
        self.assertEqual(source.count('old "You bought 1 Juice!"'), 1)
        self.assertEqual(source.count("(every result already covered above)"), 2)

    def test_every_old_in_the_block_is_unique(self):
        rows = [
            _row("f'You bought 1 {item_name}!'", file="a.rpy", line=1),
            _row("f'{item_name} is gone!'", file="b.rpy", line=2),
        ]
        fixes = fstringfix.analyse(
            rows, values={"item_name": ["Sandwich", "Juice", "Honey"]})
        source = fstringfix.build_strings_block("chinese", fixes)
        olds = re.findall(r'^\s*old "(.*)"$', source, re.MULTILINE)
        self.assertEqual(len(olds), len(set(olds)))


class TestBuildPatternPatch(unittest.TestCase):
    def _source(self, **kwargs) -> str:
        fixes = fstringfix.analyse([_row("f'You bought 1 {item_name}!'")])
        return fstringfix.build_pattern_patch("chinese", fixes, **kwargs)

    def test_writes_a_translate_python_block(self):
        source = self._source()
        self.assertIn("translate chinese python:", source)
        self.assertIn(fstringfix.PATCH_MARKER, source)

    def test_pattern_is_anchored_and_escaped(self):
        pattern = _patterns(self._source())[0]
        self.assertTrue(pattern.startswith("^"))
        self.assertTrue(pattern.endswith("$"))
        self.assertIn("(.+)", pattern)
        # The literal "!" needs no escaping but a captured group must not be
        # excused as literal text: check the pattern really matches.
        self.assertIsNotNone(re.match(pattern, "You bought 1 Sandwich!"))

    def test_replacement_is_the_source_template_by_default(self):
        source = self._source()
        self.assertIn('"You bought 1 {0}!"', source)
        self.assertIn("TODO: translate the right-hand side.", source)

    def test_translation_replaces_the_placeholder(self):
        source = self._source(translations={"You bought 1 {0}!": "你买了 1 个{0}！"})
        self.assertIn('"你买了 1 个{0}！"', source)
        self.assertNotIn("TODO: translate the right-hand side.", source)

    def test_duplicate_templates_are_compiled_once(self):
        rows = [
            _row("f'You bought 1 {item_name}!'", file="a.rpy", line=1),
            _row("f'You bought 1 {item_name}!'", file="b.rpy", line=2),
        ]
        fixes = fstringfix.analyse(rows)
        source = fstringfix.build_pattern_patch("chinese", fixes)
        self.assertEqual(source.count("_rtk_re.compile"), 1)
        self.assertIn("same sentence as above", source)

    def test_newline_literals_are_regex_escapes(self):
        # ``re.escape`` leaves a real newline in the pattern, which the regex
        # engine rejects as an incomplete escape: it must be written as \n.
        fixes = fstringfix.analyse([_row(r"f'\nError: {name} failed.'")])
        source = fstringfix.build_pattern_patch("chinese", fixes)
        line = [l for l in source.splitlines() if "_rtk_re.compile" in l][0]
        self.assertIn("\\n", line)
        self.assertNotIn("\n", line)
        # And the pattern must actually compile and match a real sentence.
        pattern = _patterns(source)[0]
        self.assertIsNotNone(re.match(pattern, "\nError: thing failed."))

    def test_guards_on_the_live_language(self):
        # ``translate <lang> python:`` runs on switching to the language and is
        # not undone on switching away, so the shim must re-check.
        self.assertIn('if renpy.game.preferences.language != "chinese":',
                      self._source())

    def test_translates_the_captured_field_first(self):
        # so an item name with its own entry comes out in the target language
        self.assertIn("_rtk_previous(_g, language)", self._source())


class TestWriteFixes(unittest.TestCase):
    def test_writes_only_the_files_with_work(self):
        fixes = fstringfix.analyse([_row("f'You bought 1 {item}!'")])
        with temp_dir() as directory:
            paths = fstringfix.write_fixes(directory, "chinese", fixes,
                                           dry_run=False)
            self.assertEqual(len(paths), 1)
            self.assertTrue(paths[0].endswith(fstringfix.patch_filename("chinese")))
            for path in paths:
                with open(path, encoding="utf-8-sig") as handle:
                    self.assertIn("translate chinese python:", handle.read())

    def test_dry_run_writes_nothing(self):
        fixes = fstringfix.analyse([_row("f'You bought 1 {item}!'")])
        with temp_dir() as directory:
            paths = fstringfix.write_fixes(directory, "chinese", fixes)
            self.assertEqual(len(paths), 1)
            import os

            self.assertFalse(os.path.exists(paths[0]))

    def test_exact_fixes_go_to_the_strings_file(self):
        fixes = fstringfix.analyse([_row("f'You bought 1 {item}!'")],
                                   values={"item": ["Sandwich"]})
        with temp_dir() as directory:
            paths = fstringfix.write_fixes(directory, "chinese", fixes,
                                           dry_run=False)
            self.assertTrue(paths[0].endswith(fstringfix.strings_filename("chinese")))

    def test_refuses_to_overwrite_a_hand_written_file(self):
        fixes = fstringfix.analyse([_row("f'You bought 1 {item}!'")])
        with temp_dir() as directory:
            import os

            path = os.path.join(directory, fstringfix.patch_filename("chinese"))
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("translate chinese python:\n    pass\n")
            with self.assertRaises(ToolkitError):
                fstringfix.write_fixes(directory, "chinese", fixes, dry_run=False)

    def test_rejects_an_unsafe_language(self):
        fixes = fstringfix.analyse([_row("f'You bought 1 {item}!'")])
        with temp_dir() as directory:
            with self.assertRaises(InvalidRequestError):
                fstringfix.write_fixes(directory, "../evil", fixes, dry_run=False)

    def test_both_files_when_both_routes_are_present(self):
        fixes = fstringfix.analyse([
            _row("f'You bought 1 {item}!'"),
            _row("f'Item {name} is gone!'"),
        ], values={"item": ["Sandwich"]})
        routes = {fix.route for fix in fixes}
        self.assertEqual(routes, {"exact", "pattern"})
        with temp_dir() as directory:
            paths = fstringfix.write_fixes(directory, "chinese", fixes,
                                           dry_run=False)
            self.assertEqual(len(paths), 2)


if __name__ == "__main__":
    unittest.main()
