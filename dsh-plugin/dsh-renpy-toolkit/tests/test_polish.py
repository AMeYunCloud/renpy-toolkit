"""Tests for :mod:`renpy_toolkit.polish`."""

from __future__ import annotations

import os
import textwrap
import unittest

import _util

from renpy_toolkit import polish, tl
from renpy_toolkit.errors import InvalidRequestError

TL_FILE = """\
# game/script.rpy:12
translate chinese start_aaaa:

    # k "Old line."
    k "旧台词。"

# game/script.rpy:20
translate chinese start_bbbb:

    # k "Second line."
    k "第二句。"

translate chinese strings:

    # game/screens.rpy:5
    old "Load"
    new "读取"
"""


def _write(directory: str, name: str, text: str) -> str:
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestReadPolishScript(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = _util.tempdir_for(self)

    def _script(self, body: str) -> str:
        return _write(self.dir, "polish_x.py", textwrap.dedent(body))

    def test_reads_text_verified(self) -> None:
        path = self._script(
            """\
            translations_by_text_verified = {
                'k "旧台词。"': (12, 'k "新台词。"'),
            }
            """
        )
        batch = polish.read_polish_script(path)
        self.assertEqual(batch.by_text_verified['k "旧台词。"'], (12, 'k "新台词。"'))
        self.assertEqual(batch.entries, 1)

    def test_reads_all_documented_formats(self) -> None:
        path = self._script(
            """\
            translations_by_text = {"a": "b"}
            translations_by_line = {5: "c"}
            translations_verified = {6: ("d", "e")}
            strings_translations = {"Load": "读取"}
            """
        )
        batch = polish.read_polish_script(path)
        self.assertEqual(batch.by_text, {"a": "b"})
        self.assertEqual(batch.by_line, {5: "c"})
        self.assertEqual(batch.by_line_verified, {6: ("d", "e")})
        self.assertEqual(batch.strings, {"Load": "读取"})

    def test_ignores_unrelated_assignments(self) -> None:
        path = self._script(
            """\
            TARGET = "some/file.rpy"
            COUNTER = 3
            translations_by_text = {"a": "b"}
            """
        )
        self.assertEqual(polish.read_polish_script(path).entries, 1)

    def test_does_not_execute_code(self) -> None:
        marker = os.path.join(self.dir, "boom.txt")
        body = (
            "open(%r, 'w').write('x')\n" % marker
            + 'translations_by_text = {"a": "b"}\n'
        )
        path = _write(self.dir, "polish_x.py", body)
        polish.read_polish_script(path)
        self.assertFalse(os.path.exists(marker))

    def test_expression_value_is_an_error(self) -> None:
        path = self._script('translations_by_text = {"a": some_func()}\n')
        with self.assertRaises(InvalidRequestError):
            polish.read_polish_script(path)

    def test_no_known_dictionary_is_an_error(self) -> None:
        path = self._script("SOMETHING = {}\n")
        with self.assertRaises(InvalidRequestError):
            polish.read_polish_script(path)

    def test_missing_file_is_an_error(self) -> None:
        with self.assertRaises(InvalidRequestError):
            polish.read_polish_script(os.path.join(self.dir, "nope.py"))


class TestApplyPolish(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = _util.tempdir_for(self)
        self.path = _write(self.dir, "script.rpy", TL_FILE)

    def _batch(self, **kwargs) -> polish.PolishBatch:
        return polish.PolishBatch(**kwargs)

    def test_replaces_by_text(self) -> None:
        batch = self._batch(by_text={'k "旧台词。"': 'k "改过的台词。"'})
        result = polish.apply_polish(self.dir, batch, dry_run=False)
        self.assertEqual(result.replaced, 1)
        self.assertIn("改过的台词", _read(self.path))

    def test_verified_entry_requires_the_declared_source_line(self) -> None:
        batch = self._batch(
            by_text_verified={'k "旧台词。"': (999, 'k "不该写入。"')}
        )
        result = polish.apply_polish(self.dir, batch, dry_run=False)
        self.assertEqual(result.replaced, 0)
        self.assertNotIn("不该写入", _read(self.path))
        self.assertTrue(any("declared for source line 999" in p for p in result.problems))

    def test_verified_entry_applies_when_line_matches(self) -> None:
        batch = self._batch(
            by_text_verified={'k "旧台词。"': (12, 'k "对的台词。"')}
        )
        result = polish.apply_polish(self.dir, batch, dry_run=False)
        self.assertEqual(result.replaced, 1)
        self.assertIn("对的台词", _read(self.path))

    def test_strings_are_matched_by_old(self) -> None:
        batch = self._batch(strings={"Load": "载入"})
        result = polish.apply_polish(self.dir, batch, dry_run=False)
        self.assertEqual(result.replaced, 1)
        doc = tl.parse_tl_file(self.path)
        self.assertEqual(doc.blocks[2].strings[0].new, "载入")

    def test_already_correct_is_counted_separately(self) -> None:
        batch = self._batch(by_text={'k "旧台词。"': 'k "旧台词。"'})
        result = polish.apply_polish(self.dir, batch, dry_run=False)
        self.assertEqual(result.already, 1)
        self.assertEqual(result.replaced, 0)

    def test_not_found_counts_unmatched_keys(self) -> None:
        batch = self._batch(by_text={"nothing like this": "x"})
        result = polish.apply_polish(self.dir, batch, dry_run=True)
        self.assertEqual(result.not_found, 1)

    def test_dry_run_changes_nothing(self) -> None:
        before = _read(self.path)
        batch = self._batch(by_text={'k "旧台词。"': 'k "改过的台词。"'})
        polish.apply_polish(self.dir, batch, dry_run=True)
        self.assertEqual(_read(self.path), before)

    def test_missing_dir_is_an_error(self) -> None:
        batch = self._batch(by_text={"a": "b"})
        with self.assertRaises(InvalidRequestError):
            polish.apply_polish(os.path.join(self.dir, "nope"), batch)

    def test_summary_mentions_counts(self) -> None:
        text = polish.PolishResult(replaced=3, not_found=2).summary()
        self.assertIn("3", text)
        self.assertIn("2", text)


if __name__ == "__main__":
    unittest.main()
