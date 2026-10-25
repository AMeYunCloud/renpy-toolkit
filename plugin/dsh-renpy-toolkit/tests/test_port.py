"""Tests for :mod:`renpy_toolkit.port`."""

from __future__ import annotations

import os
import unittest

import _util

from renpy_toolkit import port, tl
from renpy_toolkit.errors import InvalidRequestError

SKELETON = """\
translate chinese start_aaaa:

    # e "Hello there."
    e "Hello there."

translate chinese start_bbbb:

    # e "The gate is open."
    e "The gate is open."

translate chinese start_cccc:

    # e "Brand new line."
    e "Brand new line."

translate chinese strings:

    # game/screens.rpy:10
    old "Load"
    new "Load"

translate chinese strings:

    # game/screens.rpy:11
    old "{#ctx}Enter"
    new "Enter"
"""

REFERENCE = """\
translate Chinese start_aaaa:

    # e "Hello there."
    e "你好。"

translate Chinese start_bbbb:

    # e "Something entirely different now."
    e "完全不同的台词。"

translate chinese strings:

    # game/screens.rpy:10
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


class TestPortTranslations(unittest.TestCase):
    def setUp(self) -> None:
        self.skeleton = _util.tempdir_for(self)
        self.reference = _util.tempdir_for(self)
        self.path = _write(self.skeleton, "script.rpy", SKELETON)
        _write(self.reference, "script.rpy", REFERENCE)

    def test_matching_identifier_is_ported(self) -> None:
        result = port.port_translations(self.skeleton, self.reference, dry_run=False)
        doc = tl.parse_tl_file(self.path)
        self.assertEqual(doc.blocks[0].say_lines[0].text, "你好。")
        self.assertEqual(result.says, 1)

    def test_changed_source_text_is_skipped(self) -> None:
        result = port.port_translations(self.skeleton, self.reference, dry_run=False)
        doc = tl.parse_tl_file(self.path)
        # start_bbbb matched by identifier but the source text differs.
        self.assertEqual(doc.blocks[1].say_lines[0].text, "The gate is open.")
        self.assertTrue(any("source text changed" in s for s in result.skipped))

    def test_untouched_block_stays_identical(self) -> None:
        port.port_translations(self.skeleton, self.reference, dry_run=False)
        doc = tl.parse_tl_file(self.path)
        self.assertEqual(doc.blocks[2].say_lines[0].text, "Brand new line.")

    def test_strings_are_matched_by_old(self) -> None:
        result = port.port_translations(self.skeleton, self.reference, dry_run=False)
        doc = tl.parse_tl_file(self.path)
        self.assertEqual(doc.blocks[3].strings[0].new, "读取")
        self.assertEqual(result.strings, 1)

    def test_context_tagged_string_is_not_confused(self) -> None:
        port.port_translations(self.skeleton, self.reference, dry_run=False)
        doc = tl.parse_tl_file(self.path)
        # ``{#ctx}Enter`` has no entry in the reference; must stay put.
        self.assertEqual(doc.blocks[4].strings[0].new, "Enter")

    def test_dry_run_changes_nothing(self) -> None:
        before = _read(self.path)
        result = port.port_translations(self.skeleton, self.reference)
        self.assertEqual(result.says, 1)
        self.assertEqual(_read(self.path), before)

    def test_bom_is_preserved(self) -> None:
        with open(self.path, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + SKELETON.encode("utf-8"))
        port.port_translations(self.skeleton, self.reference, dry_run=False)
        with open(self.path, "rb") as fh:
            self.assertTrue(fh.read().startswith(b"\xef\xbb\xbf"))

    def test_file_filter(self) -> None:
        result = port.port_translations(
            self.skeleton, self.reference, dry_run=False, files=["other.rpy"]
        )
        self.assertEqual(result.says, 0)
        self.assertEqual(result.files, [])

    def test_missing_reference_dir_is_an_error(self) -> None:
        with self.assertRaises(InvalidRequestError):
            port.port_translations(self.skeleton, os.path.join(self.skeleton, "nope"))

    def test_empty_reference_is_an_error(self) -> None:
        empty = _util.tempdir_for(self)
        # A skeleton that was never filled in: every ``new`` is still blank.
        _write(
            empty,
            "x.rpy",
            'translate chinese strings:\n\n    old "Load"\n    new ""\n',
        )
        with self.assertRaises(InvalidRequestError):
            port.port_translations(self.skeleton, empty)

    def test_summary_mentions_both_counts(self) -> None:
        text = port.PortResult(says=3, strings=2).summary()
        self.assertIn("3", text)
        self.assertIn("2", text)


if __name__ == "__main__":
    unittest.main()
