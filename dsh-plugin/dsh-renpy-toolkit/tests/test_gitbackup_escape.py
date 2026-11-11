"""A tampered shadow repo must not be able to write above the backup root.

``restore()`` reads its file list out of the object database. ``git mktree``
refuses a *name* containing a slash, but it **accepts a subtree named ``..``**,
and ``ls-tree -r`` then reports ``../owned.rpy``. Since restore joins those
names onto the backup root, that is the same class of bug as Zip Slip: a
poisoned repository yields a write outside the directory the user pointed at.

This test builds that repository by hand with mktree/commit-tree -- the ordinary
``GitBackups.save`` path can only record paths inside ``root`` -- then asserts
that restore refuses instead of writing the file.

Every mktree line is passed as *bytes*. Feeding it as text makes Python write
``\\r\\n`` on Windows, so the entries end up called ``..\\r`` and the case under
test never happens (that mistake made a first version of this file assert
against a path that could not escape).
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import unittest

import _util
from renpy_toolkit import gitbackup


def _git(git_dir: str, args: list[str], *, data: bytes | None = None) -> subprocess.CompletedProcess:
    argv = ["git", "--git-dir", git_dir,
            "-c", "user.name=t", "-c", "user.email=t@t", *args]
    return subprocess.run(argv, input=data, capture_output=True, check=False)


@unittest.skipUnless(gitbackup.available(), "git is not installed")
class TestRestoreCannotEscape(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _util.tempdir_for(self)
        self.tl = os.path.join(self.root, "tl", "chinese")
        os.makedirs(self.tl)
        self.backups = gitbackup.GitBackups(self.tl)
        self.backups.init()

    def _poisoned_revision(self) -> str:
        """Commit whose only top-level tree entry is a subtree called ``..``."""
        source = os.path.join(self.root, "payload.txt")
        with open(source, "wb") as handle:
            handle.write(b"payload\n")
        blob = _git(self.backups.git_dir, ["hash-object", "-w", "--no-filters", source])
        self.assertEqual(blob.returncode, 0, blob.stderr)
        sha = blob.stdout.decode().strip()

        inner = _git(self.backups.git_dir, ["mktree"],
                     data=b"100644 blob " + sha.encode() + b"\towned.rpy\n")
        self.assertEqual(inner.returncode, 0, inner.stderr)
        inner_tree = inner.stdout.decode().strip()

        outer = _git(self.backups.git_dir, ["mktree"],
                     data=b"040000 tree " + inner_tree.encode() + b"\t..\n")
        self.assertEqual(outer.returncode, 0, outer.stderr)
        outer_tree = outer.stdout.decode().strip()

        commit = _git(self.backups.git_dir,
                      ["commit-tree", outer_tree, "-m", "poisoned"])
        self.assertEqual(commit.returncode, 0, commit.stderr)
        rev = commit.stdout.decode().strip()

        # Prove the tree really reports a traversing path; without this the
        # assertions below would be checking a hypothetical.
        listing = _git(self.backups.git_dir, ["ls-tree", "-r", "--name-only", rev])
        self.assertEqual(listing.stdout.decode().strip(), "../owned.rpy")

        _git(self.backups.git_dir, ["update-ref", "refs/heads/main", rev])
        _git(self.backups.git_dir, ["update-ref", "HEAD", "refs/heads/main"])
        return rev

    def _escaped(self) -> list[str]:
        """Any ``owned.rpy`` written outside the TL directory."""
        return [p for p in glob.glob(os.path.join(self.root, "**", "owned.rpy"), recursive=True)
                if not os.path.abspath(p).startswith(os.path.abspath(self.tl) + os.sep)]

    def test_restore_refuses_to_write_outside_the_root(self) -> None:
        rev = self._poisoned_revision()
        with self.assertRaises(Exception) as caught:
            self.backups.restore(rev)
        message = str(caught.exception).lower()
        self.assertTrue("outside" in message or "越界" in message or "นอก" in message,
                        f"unexpected message: {caught.exception}")
        self.assertEqual(self._escaped(), [], "the payload must not be written")

    def test_dry_run_also_refuses(self) -> None:
        """A dry run must not print a path it would never be allowed to write,
        so the check runs before the dry-run branch, not inside it."""
        rev = self._poisoned_revision()
        with self.assertRaises(Exception):
            self.backups.restore(rev, dry_run=True)

    def test_a_harmless_repo_still_restores(self) -> None:
        """The hardening must not break the normal path."""
        path = os.path.join(self.tl, "a.rpy")
        with open(path, "wb") as handle:
            handle.write(b"translate x:\n")
        snapshot = self.backups.save([path], message="normal")
        with open(path, "wb") as handle:
            handle.write(b"changed\n")
        written = self.backups.restore(snapshot.rev)
        # realpath, not abspath: on Windows a long user name under %TEMP% comes
        # back in 8.3 form (NAME~1) and the two spellings never compare equal.
        self.assertEqual([os.path.realpath(p) for p in written],
                         [os.path.realpath(path)])
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"translate x:\n")


if __name__ == "__main__":
    unittest.main()
