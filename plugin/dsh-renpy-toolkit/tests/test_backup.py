"""Tests for :mod:`renpy_toolkit.backup` (the mode switch every write goes through).

The interesting cases are the ones where a mode quietly does the wrong thing:
``auto`` resolving differently mid-run, ``keep`` being asked for a file that no
longer exists, and a 2 GB archive being pulled into memory by a ``.bak`` copy.
"""

from __future__ import annotations

import os
import unittest

import _util
from renpy_toolkit import backup, gitbackup


class _ModeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _util.tempdir_for(self)
        self.previous_env = os.environ.pop("RTK_BACKUP", None)
        backup.reset_mode()
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        backup.reset_mode()
        os.environ.pop("RTK_BACKUP", None)
        if self.previous_env is not None:
            os.environ["RTK_BACKUP"] = self.previous_env

    @staticmethod
    def _write(path: str, text: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(text.encode("utf-8"))

    @staticmethod
    def _read(path: str) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()


class TestModeSelection(_ModeCase):
    def test_default_is_bak(self) -> None:
        """The default must not depend on whether git happens to be installed."""
        self.assertEqual(backup.get_mode(), backup.MODE_BAK)

    def test_set_mode_pins_the_choice(self) -> None:
        self.assertEqual(backup.set_mode(backup.MODE_NONE), backup.MODE_NONE)
        self.assertEqual(backup.get_mode(), backup.MODE_NONE)

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(Exception) as caught:
            backup.set_mode("bogus")
        self.assertIn("bak", str(caught.exception))

    def test_environment_variable_is_honoured(self) -> None:
        os.environ["RTK_BACKUP"] = "none"
        self.assertEqual(backup.get_mode(), backup.MODE_NONE)

    def test_explicit_mode_beats_the_environment(self) -> None:
        os.environ["RTK_BACKUP"] = "none"
        backup.set_mode(backup.MODE_BAK)
        self.assertEqual(backup.get_mode(), backup.MODE_BAK)

    def test_bogus_environment_value_falls_back_to_the_default(self) -> None:
        os.environ["RTK_BACKUP"] = "nonsense"
        self.assertEqual(backup.get_mode(), backup.MODE_BAK)

    def test_auto_resolves_once_and_stays_put(self) -> None:
        """``auto`` must not re-decide per call, or a run could half-back-up."""
        resolved = backup.set_mode(backup.MODE_AUTO)
        expected = backup.MODE_GIT if gitbackup.available() else backup.MODE_BAK
        self.assertEqual(resolved, expected)
        self.assertEqual(backup.get_mode(), expected)

    def test_describe_mentions_the_active_mode(self) -> None:
        backup.set_mode(backup.MODE_NONE)
        self.assertIn("none", backup.describe())

    @unittest.skipIf(gitbackup.available(), "git is installed here")
    def test_git_mode_without_git_is_refused_up_front(self) -> None:
        """Asking for git when git is missing must fail now, not at first write.

        Deferring the check lets a read-only command (``tl verify``) finish with
        exit code 0 while the user believes backups are being taken.
        """
        with self.assertRaises(Exception) as caught:
            backup.set_mode(backup.MODE_GIT)
        self.assertIn("--backup-mode bak", str(caught.exception))

    @unittest.skipIf(gitbackup.available(), "git is installed here")
    def test_environment_cannot_smuggle_in_an_unusable_git_mode(self) -> None:
        os.environ["RTK_BACKUP"] = "git"
        backup.reset_mode()
        with self.assertRaises(Exception):
            backup.get_mode()

    def test_auto_never_lands_on_git_without_git(self) -> None:
        """``auto`` must degrade to ``bak`` rather than pick an unusable mode."""
        self.assertEqual(backup.set_mode(backup.MODE_AUTO),
                         backup.MODE_GIT if gitbackup.available() else backup.MODE_BAK)


class TestKeep(_ModeCase):
    def test_none_mode_writes_nothing(self) -> None:
        target = os.path.join(self.root, "x.rpy")
        self._write(target, "old\n")
        backup.set_mode(backup.MODE_NONE)
        self.assertIsNone(backup.keep(target, b"old\n"))
        self.assertFalse(os.path.exists(target + ".bak"))

    def test_bak_mode_copies_the_previous_bytes(self) -> None:
        target = os.path.join(self.root, "x.rpy")
        self._write(target, "old\n")
        backup.set_mode(backup.MODE_BAK)
        kept = backup.keep(target, b"old\n")
        self.assertEqual(kept, target + ".bak")
        self.assertEqual(self._read(target + ".bak"), b"old\n")

    def test_bak_mode_reads_the_file_when_no_bytes_are_passed(self) -> None:
        target = os.path.join(self.root, "big.rpa")
        self._write(target, "content\n")
        backup.set_mode(backup.MODE_BAK)
        backup.keep(target)
        self.assertEqual(self._read(target + ".bak"), b"content\n")

    def test_missing_target_is_not_an_error(self) -> None:
        backup.set_mode(backup.MODE_BAK)
        self.assertIsNone(backup.keep(os.path.join(self.root, "gone.rpy"), b"x"))


@unittest.skipUnless(gitbackup.available(), "git is not installed")
class TestKeepWithGit(_ModeCase):
    def test_git_mode_creates_a_shadow_repository(self) -> None:
        tl = os.path.join(self.root, "tl", "chinese")
        target = os.path.join(tl, "script.rpy")
        self._write(target, "old\n")
        backup.set_mode(backup.MODE_GIT)
        rev = backup.keep(target, b"old\n")
        self.assertIsNotNone(rev)
        # One shadow repo per ``tl/`` tree, not per language: a snapshot taken
        # while editing chinese should still be able to restore spanish.
        tl_root = os.path.join(self.root, "tl")
        self.assertTrue(os.path.isdir(os.path.join(tl_root, ".renpy-toolkit.git")))
        self.assertFalse(os.path.isdir(os.path.join(tl, ".renpy-toolkit.git")))
        store = gitbackup.GitBackups(tl_root)
        self.assertEqual(store.show(rev, "chinese/script.rpy"), b"old\n")

    def test_repeated_identical_content_records_nothing_new(self) -> None:
        tl = os.path.join(self.root, "tl", "chinese")
        target = os.path.join(tl, "script.rpy")
        self._write(target, "old\n")
        backup.set_mode(backup.MODE_GIT)
        self.assertIsNotNone(backup.keep(target, b"old\n"))
        self.assertIsNone(backup.keep(target, b"old\n"))
        store = gitbackup.GitBackups(os.path.join(self.root, "tl"))
        self.assertEqual(len(store.list()), 1)


class TestBackupRoot(unittest.TestCase):
    def test_picks_the_nearest_tl_ancestor(self) -> None:
        path = os.path.join(os.sep, "games", "x", "game", "tl", "chinese", "a.rpy")
        self.assertEqual(
            backup.backup_root(path),
            os.path.abspath(os.path.join(os.sep, "games", "x", "game", "tl")),
        )

    def test_falls_back_to_the_own_directory(self) -> None:
        path = os.path.join(os.sep, "games", "x", "scripts", "a.rpy")
        self.assertEqual(
            backup.backup_root(path),
            os.path.abspath(os.path.join(os.sep, "games", "x", "scripts")),
        )

    def test_directory_name_match_is_case_insensitive(self) -> None:
        path = os.path.join(os.sep, "games", "x", "TL", "chinese", "a.rpy")
        self.assertEqual(
            backup.backup_root(path),
            os.path.abspath(os.path.join(os.sep, "games", "x", "TL")),
        )
