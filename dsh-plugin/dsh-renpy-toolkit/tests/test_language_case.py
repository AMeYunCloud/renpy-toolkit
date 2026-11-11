"""Language folder names are case-sensitive, even though Windows is not.

``tl/Chinese`` and ``tl/chinese`` are two different languages to Ren'Py: a block
written as ``translate chinese ...`` inside ``tl/Chinese`` is never read, and the
game falls back to the original text with no error at all.  These tests pin the
helpers that keep every tool from writing into the wrong spelling.
"""

import os
import unittest
import unittest.mock

import _util
from renpy_toolkit import fresh, hardcoded, tl


def make_game(root, languages):
    """A minimal game tree: game/ plus one empty folder per language name."""
    os.makedirs(os.path.join(root, "game"))
    for name in languages:
        os.makedirs(os.path.join(root, "game", "tl", name))


class TestResolveLanguage(unittest.TestCase):
    def setUp(self):
        self.root = _util.tempdir_for(self)
        make_game(self.root, ["Chinese", "Spanish"])
        self.tls = os.path.join(self.root, "game", "tl")

    def test_exact_match_returns_itself(self):
        self.assertEqual(tl.resolve_language(self.tls, "Chinese"), "Chinese")

    def test_case_insensitive_match_returns_the_disk_spelling(self):
        self.assertEqual(tl.resolve_language(self.tls, "chinese"), "Chinese")
        self.assertEqual(tl.resolve_language(self.tls, "SPANISH"), "Spanish")

    def test_unknown_language_passes_through_so_it_can_be_created(self):
        self.assertEqual(tl.resolve_language(self.tls, "korean"), "korean")

    def test_two_folders_differing_only_in_case_are_reported(self):
        # Windows cannot hold both folders at once, so stand in for a
        # case-sensitive filesystem (a Linux build, a zip made elsewhere).
        with unittest.mock.patch.object(
            tl, "iter_tl_languages", return_value=["Chinese", "chinese"]
        ):
            conflicts = tl.case_conflicts(self.tls)
            self.assertEqual(len(conflicts), 1)
            self.assertEqual({name.lower() for name in conflicts[0]}, {"chinese"})
            # An exact spelling still wins; a third one is ambiguous.
            self.assertEqual(tl.resolve_language(self.tls, "chinese"), "chinese")
            with self.assertRaises(tl.TLError):
                tl.resolve_language(self.tls, "CHINESE")

    def test_invalid_language_name_is_rejected(self):
        for bad in ("", "  ", "with space", "../escape", "中文"):
            with self.assertRaises(tl.TLError):
                tl.resolve_language(self.tls, bad)

    def test_missing_tl_folder_is_not_an_error(self):
        missing = os.path.join(self.root, "game", "tl-none")
        self.assertEqual(tl.resolve_language(missing, "chinese"), "chinese")

    def test_language_dirs_lists_the_spellings_on_disk(self):
        self.assertEqual(sorted(tl.language_dirs(self.tls)), ["Chinese", "Spanish"])


class TestFreshUsesExistingSpelling(unittest.TestCase):
    def setUp(self):
        self.root = _util.tempdir_for(self)
        make_game(self.root, ["Chinese"])

    def test_translate_dir_keeps_the_case_of_the_existing_folder(self):
        target = fresh.translate_dir_for(self.root, "chinese")
        self.assertEqual(os.path.basename(target), "Chinese")

    def test_second_run_does_not_create_a_lowercase_twin(self):
        fresh.translate_dir_for(self.root, "chinese")
        fresh.translate_dir_for(self.root, "chinese")
        names = os.listdir(os.path.join(self.root, "game", "tl"))
        self.assertEqual(names, ["Chinese"])


class TestHardcodedLedgerUsesExistingSpelling(unittest.TestCase):
    def setUp(self):
        self.root = _util.tempdir_for(self)
        make_game(self.root, ["Chinese"])

    def test_language_dir_gets_the_ledger_directly(self):
        tls = os.path.join(self.root, "game", "tl")
        path = hardcoded.ledger_path(os.path.join(tls, "Chinese"), "chinese")
        self.assertEqual(os.path.dirname(path), os.path.join(tls, "Chinese"))

    def test_parent_tl_dir_gets_the_existing_spelling(self):
        tls = os.path.join(self.root, "game", "tl")
        path = hardcoded.ledger_path(tls, "chinese")
        self.assertEqual(os.path.basename(os.path.dirname(path)), "Chinese")


if __name__ == "__main__":
    unittest.main()
