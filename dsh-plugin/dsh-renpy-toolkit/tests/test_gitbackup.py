"""Tests for :mod:`renpy_toolkit.gitbackup` (Git-backed backups).

Every case here runs against a real ``git`` in a throwaway directory. There is
no mock: the whole point of the module is that it drives git correctly, and the
bugs it has actually had (line endings rewritten by ``core.autocrlf``, ``prune``
silently doing nothing) only show up against the real executable.

When git is missing the whole class is skipped rather than failing, because the
package itself has no git dependency.
"""

from __future__ import annotations

import os
import unittest

import _util
from renpy_toolkit import gitbackup


CRLF_FILE = "\ufefftranslate chinese start_1:\r\n    e \"one\"\r\n"
LF_FILE = "translate chinese strings:\n    old \"x\"\n    new \"y\"\n"


@unittest.skipUnless(gitbackup.available(), "git is not installed")
class TestGitBackups(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _util.tempdir_for(self)
        self.tl = os.path.join(self.root, "tl", "chinese")
        os.makedirs(self.tl)
        self.a = os.path.join(self.tl, "a.rpy")
        self.b = os.path.join(self.tl, "b.rpy")
        self._write(self.a, CRLF_FILE)
        self._write(self.b, LF_FILE)
        self.backups = gitbackup.GitBackups(self.tl)

    # ----------------------------------------------------------- helpers
    @staticmethod
    def _write(path: str, text: str) -> None:
        with open(path, "wb") as handle:
            handle.write(text.encode("utf-8"))

    @staticmethod
    def _read(path: str) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()

    # -------------------------------------------------------------- cases
    def test_repository_is_created_lazily(self) -> None:
        self.assertFalse(self.backups.exists())
        self.assertIsNone(self.backups.head())
        snapshot = self.backups.save([self.a], message="first")
        self.assertIsNotNone(snapshot)
        self.assertTrue(self.backups.exists())
        self.assertEqual(self.backups.head(), snapshot.rev)

    def test_identical_content_creates_no_commit(self) -> None:
        first = self.backups.save([self.a], message="first")
        self.assertIsNotNone(first)
        self.assertIsNone(self.backups.save([self.a], message="again"))
        self.assertEqual(self.backups.head(), first.rev)

    def test_snapshot_records_a_full_tree(self) -> None:
        """A later snapshot must still carry files it did not touch.

        This is what makes ``restore`` able to hand back a whole consistent
        state instead of only the files a particular command happened to edit.
        ``Snapshot.files`` reports the files of *this* call, so the proof is in
        restoring the second revision and getting both files back.
        """
        self.backups.save([self.a], message="only a")
        second = self.backups.save([self.b], message="only b")
        self.assertEqual(second.files, ["b.rpy"])

        self._write(self.a, "touched\n")
        self.backups.restore(second.rev)
        self.assertEqual(self._read(self.a), CRLF_FILE.encode("utf-8"))

    def test_crlf_and_bom_survive_the_round_trip(self) -> None:
        """The bug this guards: ``core.autocrlf=true`` rewrites CRLF to LF.

        Git for Windows ships ``core.autocrlf=true`` system-wide, so a naive
        ``hash-object`` stores a normalised blob and restoring a CRLF file hands
        back an LF one. Both the blob write and the blob read have to bypass the
        filters for a byte-exact backup.
        """
        first = self.backups.save([self.a], message="first")
        self._write(self.a, "translate chinese start_1:\n    e \"uno\"\n")
        self.backups.save([self.a], message="second")

        self.assertEqual(self.backups.show(first.rev, "a.rpy"), CRLF_FILE.encode("utf-8"))
        self.backups.restore(first.rev)
        self.assertEqual(self._read(self.a), CRLF_FILE.encode("utf-8"))

    def test_restore_writes_every_file_in_the_snapshot(self) -> None:
        first = self.backups.snapshot_all(message="both")
        self.assertEqual(sorted(first.files), ["a.rpy", "b.rpy"])
        self._write(self.a, "changed\n")
        self._write(self.b, "changed too\n")
        written = self.backups.restore(first.rev)
        self.assertEqual(len(written), 2)
        self.assertEqual(self._read(self.a), CRLF_FILE.encode("utf-8"))
        self.assertEqual(self._read(self.b), LF_FILE.encode("utf-8"))

    def test_restore_is_idempotent_and_leaves_equal_files_alone(self) -> None:
        first = self.backups.snapshot_all(message="both")
        self.backups.restore(first.rev)
        before = os.stat(self.a).st_mtime_ns
        self.assertEqual(self.backups.restore(first.rev), [])
        self.assertEqual(os.stat(self.a).st_mtime_ns, before)

    def test_dry_run_writes_nothing(self) -> None:
        first = self.backups.snapshot_all(message="both")
        self._write(self.a, "changed\n")
        planned = self.backups.restore(first.rev, dry_run=True)
        self.assertEqual(len(planned), 2)
        self.assertEqual(self._read(self.a), b"changed\n")

    def test_history_is_newest_first(self) -> None:
        first = self.backups.save([self.a], message="first")
        self._write(self.a, "translate x:\n")
        second = self.backups.save([self.a], message="second")
        history = self.backups.list()
        self.assertEqual([s.rev for s in history], [second.rev, first.rev])
        self.assertEqual(history[0].message, "second")

    def test_paths_outside_the_backup_root_are_refused(self) -> None:
        outside = os.path.join(self.root, "outside.rpy")
        self._write(outside, "x\n")
        with self.assertRaises(Exception) as caught:
            self.backups.save([outside])
        self.assertIn("不在备份目录", str(caught.exception))

    def test_missing_files_are_skipped_not_fatal(self) -> None:
        """A vanished file must not abort the whole backup."""
        missing = os.path.join(self.tl, "nope.rpy")
        self.assertIsNone(self.backups.save([missing]))
        self.assertIsNone(self.backups.head())

    def test_nothing_is_written_next_to_the_translations(self) -> None:
        """Only the shadow repo may appear; no ``.git`` in the TL directory."""
        self.backups.save([self.a], message="first")
        entries = sorted(os.listdir(self.tl))
        self.assertEqual(entries, [".renpy-toolkit.git", "a.rpy", "b.rpy"])

    def test_prune_keeps_the_newest_snapshots_and_shrinks_history(self) -> None:
        """``prune`` used to return 0 without doing anything (a real bug).

        Each snapshot writes a full tree, so re-rooting the kept commits is
        enough to keep them restorable. Re-rooting rewrites the oldest kept
        commit, and because a commit id covers its parent, every kept commit
        gets a new id -- so this asserts on *content*, not on revisions.
        """
        for index in range(5):
            self._write(self.a, f"line {index}\n")
            self.assertIsNotNone(self.backups.save([self.a], message=f"step {index}"))

        removed = self.backups.prune(keep=2)
        self.assertEqual(removed, 3)

        history = self.backups.list(limit=10)
        self.assertEqual(len(history), 2)
        self.assertEqual([s.message for s in history], ["step 4", "step 3"])
        self.assertEqual(self.backups.show(history[0].rev, "a.rpy"), b"line 4\n")
        self.assertEqual(self.backups.show(history[-1].rev, "a.rpy"), b"line 3\n")

    def test_prune_is_a_no_op_when_history_is_short(self) -> None:
        self.backups.save([self.a], message="first")
        self.assertEqual(self.backups.prune(keep=5), 0)
        self.assertEqual(len(self.backups.list()), 1)

    def test_size_reports_the_object_database(self) -> None:
        self.backups.save([self.a], message="first")
        self.assertGreater(self.backups.size(), 0)

    def test_non_ascii_paths_round_trip_unquoted(self) -> None:
        """A Chinese filename must survive save/list/show/restore verbatim.

        ``git log --name-only`` renders a non-ASCII path as
        ``"tl/chinese/\\347\\254\\254..."`` unless ``core.quotePath=false``, and
        ``ls-tree`` does the same without ``-z``. Either way the name coming
        back is the escaped spelling, which no longer names an existing file.
        """
        path = os.path.join(self.tl, "\u7b2c\u4e00\u7ae0.rpy")
        payload = "\ufefftranslate chinese start_1:\r\n    e \"\u4f60\u597d\"\r\n"
        self._write(path, payload)

        snapshot = self.backups.save([path], message="cjk")
        self.assertEqual(self.backups.list()[0].files, ["\u7b2c\u4e00\u7ae0.rpy"])
        self.assertEqual(self.backups.show(snapshot.rev, "\u7b2c\u4e00\u7ae0.rpy"),
                         self._read(path))

        self._write(path, "clobbered\n")
        written = self.backups.restore(snapshot.rev)
        self.assertEqual([os.path.basename(p) for p in written], ["\u7b2c\u4e00\u7ae0.rpy"])
        self.assertEqual(self._read(path), payload.encode("utf-8"))


@unittest.skipUnless(gitbackup.available(), "git is not installed")
class TestGitVersion(unittest.TestCase):
    def test_version_is_a_three_tuple(self) -> None:
        version = gitbackup.git_version()
        self.assertIsNotNone(version)
        self.assertEqual(len(version), 3)
        self.assertTrue(all(isinstance(part, int) for part in version))

    def test_available_agrees_with_the_version_threshold(self) -> None:
        version = gitbackup.git_version()
        expected = version is not None and version >= (2, 15, 0)
        self.assertEqual(gitbackup.available(), expected)


class TestLooksLikeGitRepo(unittest.TestCase):
    def test_a_plain_directory_is_not_a_repository(self) -> None:
        root = _util.tempdir_for(self)
        self.assertFalse(gitbackup.GitBackups(root).exists())
