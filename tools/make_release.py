#!/usr/bin/env python3
"""Publish a GitHub release with the portable and skill archives attached.

    python tools/make_release.py --dry-run     # show what would be uploaded
    python tools/make_release.py               # build, tag, push, create release
    python tools/make_release.py --notes-only  # regenerate notes, no upload

Why a script rather than a documented ``gh release create`` command: a release
has to satisfy several properties at once, and getting one wrong is silent.

* The tag must match ``plugin/dsh-renpy-toolkit/VERSION``. A release whose name
  says 1.0.0 while the archive reports 1.0.1 is worse than no release at all.
* Both archives must be freshly built from the current tree, never stale files
  left over in ``dist/``.
* ``dist/`` is git-ignored, so the archives reach GitHub only as release assets.
  That is the point: no binary ever enters the git history.
* Notes come from ``CHANGELOG.md`` so there is one place to edit.

``gh`` must be authenticated; the script checks that before touching anything.
Nothing is pushed unless ``--push`` is given (the default), and every step
prints the exact command it runs.

On a machine behind a TLS-inspecting proxy, ``git push`` can fail with::

    schannel: next InitializeSecurityContext failed:
    CRYPT_E_NO_REVOCATION_CHECK (0x80092012)

That is Windows schannel being unable to reach the certificate revocation
list, not a credential problem: ``git ls-remote`` fails identically while
``gh api`` works, because gh uses its own TLS stack. Neither
``-c http.schannelCheckRevoke=false`` nor a local config entry changes it.

The actionable workaround is to skip only the *tag* push, since ``gh`` can do
the rest without git:

    $env:GIT_SSL_NO_VERIFY = "true"   # this process only; never write it to config
    git push origin refs/tags/<tag>

then create the release. Passing a tag that is already on the remote makes
``gh release create --verify-tag`` succeed without any upload of git objects,
so the flow is: push the tag once, then run this script with ``--no-push``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from build_release import SRC_PLUGIN, VERSION  # noqa: E402
from build_portable import SKILL_NAME, SKILL_ZIP, ZIP_PATH  # noqa: E402

TAG = "v" + VERSION
REPO_URL = "https://github.com/AMeYunCloud/renpy-toolkit"
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")


def _git() -> str:
    """Locate git.

    A fresh Windows install puts git on PATH but the change only reaches
    processes started afterwards. Rather than require a new shell, look in the
    default install location as a fallback.
    """
    found = shutil.which("git")
    if found:
        return found
    for candidate in (
        r"E:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files\Git\cmd\git.exe",
    ):
        if os.path.exists(candidate):
            return candidate
    raise SystemExit("git not found; install it or add it to PATH")


def _gh() -> str:
    found = shutil.which("gh")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\GitHub CLI\gh.exe",
        r"/usr/local/bin/gh",
        "/opt/homebrew/bin/gh",
    ):
        if os.path.exists(candidate):
            return candidate
    raise SystemExit("gh not found; install https://cli.github.com/")


def _run(argv: list[str], *, dry: bool, capture: bool = False) -> str:
    printable = " ".join(argv)
    print("  $ %s" % printable)
    if dry:
        return ""
    proc = subprocess.run(
        argv,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
        raise SystemExit("command failed (exit %d): %s\n%s"
                         % (proc.returncode, printable, detail))
    return (proc.stdout or "") if capture else ""


def _version_consistency() -> list[str]:
    """Everything that names a version must agree with plugin/dsh-renpy-toolkit/VERSION."""
    problems: list[str] = []

    version_file = os.path.join(SRC_PLUGIN, "VERSION")
    if os.path.exists(version_file):
        with open(version_file, encoding="utf-8") as fh:
            on_disk = fh.read().strip()
        if on_disk != VERSION:
            problems.append("plugin/dsh-renpy-toolkit/VERSION says %r but build_release reports %r"
                            % (on_disk, VERSION))

    init_py = os.path.join(SRC_PLUGIN, "renpy_toolkit", "__init__.py")
    if os.path.exists(init_py):
        with open(init_py, encoding="utf-8") as fh:
            text = fh.read()
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
        if not match:
            problems.append("no __version__ in plugin/dsh-renpy-toolkit/renpy_toolkit/__init__.py")
        elif match.group(1) != VERSION:
            problems.append("__init__.__version__ is %r, expected %r"
                            % (match.group(1), VERSION))

    pyproject = os.path.join(ROOT, "pyproject.toml")
    if os.path.exists(pyproject):
        with open(pyproject, encoding="utf-8") as fh:
            text = fh.read()
        match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.M)
        if not match:
            problems.append("no version in pyproject.toml")
        elif match.group(1) != VERSION:
            problems.append("pyproject version is %r, expected %r"
                            % (match.group(1), VERSION))

    if not os.path.exists(CHANGELOG):
        problems.append("missing CHANGELOG.md")
    else:
        with open(CHANGELOG, encoding="utf-8") as fh:
            if "## [%s]" % VERSION not in fh.read():
                problems.append("CHANGELOG.md has no '## [%s]' section" % VERSION)

    return problems


def release_notes() -> str:
    """Take the CHANGELOG section for this version as the release body."""
    with open(CHANGELOG, encoding="utf-8") as fh:
        text = fh.read()
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## [%s]" % VERSION):
            start = i
            break
    if start is None:
        raise SystemExit("CHANGELOG.md has no section for %s" % VERSION)

    body: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    # Link definitions at the bottom of the changelog belong to the file, not
    # to one section; leaving them in would render a stray "[1.0.0]:" line.
    body = [line for line in body if not re.match(r"^\[[^\]]+\]:\s*\S+", line)]
    cleaned = "\n".join(body).strip()
    footer = (
        "\n\n---\n\n"
        "**Portable pack** (`%s`): unzip anywhere, then run "
        "`自检.cmd` (Windows) or `./selfcheck.sh` to confirm it works. "
        "No pip, no environment variables.\n\n"
        "**Skill bundle** (`%s`): unzip into your harness's skills "
        "directory, then point `RPTK_HOME` at the unpacked portable folder.\n"
        % (os.path.basename(ZIP_PATH), os.path.basename(SKILL_ZIP))
    )
    return cleaned + footer


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the commands without running them")
    ap.add_argument("--no-push", action="store_true",
                    help="create the tag and release locally only")
    ap.add_argument("--draft", action="store_true", help="create a draft release")
    ap.add_argument("--notes-only", action="store_true",
                    help="print the release notes and exit")
    args = ap.parse_args(argv)

    print("release %s" % TAG)

    if args.notes_only:
        print(release_notes())
        return 0

    problems = _version_consistency()
    if problems:
        print("version check FAILED:")
        for item in problems:
            print("  %s" % item)
        return 1
    print("  version check: VERSION, __version__, pyproject, CHANGELOG all say %s"
          % VERSION)

    if not args.dry_run:
        gh = _gh()
        _run([gh, "auth", "status"], dry=False, capture=True)

    # Always rebuild: a stale archive in dist/ would silently ship old code.
    print("  rebuilding archives")
    if not args.dry_run:
        for script, flag in (("build_portable.py", "--zip"),):
            _run([sys.executable, os.path.join(HERE, script), flag],
                 dry=False, capture=True)

    for path in (ZIP_PATH, SKILL_ZIP):
        if not os.path.exists(path):
            print("  MISSING: %s" % path)
            return 1
        print("  asset: %s (%d bytes)" % (path, os.path.getsize(path)))

    notes_path = os.path.join(ROOT, "dist", "release-notes-%s.md" % VERSION)
    if not args.dry_run:
        os.makedirs(os.path.dirname(notes_path), exist_ok=True)
        with open(notes_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(release_notes())
        print("  notes: %s" % notes_path)

    git = _git()
    if not args.no_push:
        # Annotated tag: releases reference it, and it records who released what.
        _run([git, "tag", "-a", TAG, "-F", notes_path], dry=args.dry_run)
        _run([git, "push", "origin", TAG], dry=args.dry_run)

    gh = _gh()
    create = [
        gh, "release", "create", TAG,
        "--repo", "AMeYunCloud/renpy-toolkit",
        "--title", "%s — renpy-toolkit %s" % (TAG, VERSION),
        "--notes-file", notes_path,
        ZIP_PATH,
        SKILL_ZIP,
    ]
    if args.draft:
        create.insert(3, "--draft")
    # --verify-tag refuses to create the release if the tag is already on the
    # remote pointing elsewhere; that is the failure we want, not a silent
    # duplicate.
    create.insert(3, "--verify-tag")
    _run(create, dry=args.dry_run)

    print()
    if args.dry_run:
        print("dry run: nothing was tagged, pushed or uploaded")
    else:
        print("released: %s/releases/tag/%s" % (REPO_URL, TAG))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
