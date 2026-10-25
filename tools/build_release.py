#!/usr/bin/env python3
"""Build the renpy-toolkit release tree.

Run from anywhere:

    python tools/build_release.py            # build dist/plugin
    python tools/build_release.py --verify   # build, then re-hash and compare
    python tools/build_release.py --sync     # build, then replace ../plugin/dsh-renpy-toolkit in place

What it does, in order:

1. Copies ``plugin/dsh-renpy-toolkit/renpy_toolkit`` (the package), its ``tests``
   directory and ``README.md`` into ``dist/plugin``, skipping ``__pycache__`` /
   ``*.pyc`` so the release is reproducible across machines.
2. Sweeps anything the filter rejects out of the *target* tree. This is not
   redundant with step 1: ``_walk_files`` never copies a cache, so a stale
   ``__pycache__`` that already sits in the target is invisible to both the
   copy and to ``--verify``. Running a test inside the release tree creates
   exactly that, and only the build itself can notice and remove it.
3. Writes ``dist/plugin/VERSION`` and ``dist/plugin/MANIFEST.sha256``.
4. With ``--sync``, replaces the working copy in ``plugin/dsh-renpy-toolkit/``
   with the release tree so "what is in use" and "what was released" cannot
   drift apart.

The manifest is what makes the claim checkable: ``--verify`` recomputes every
digest and fails loudly if a file was edited after the manifest was written.

``dist/`` is a build artifact and is not tracked by git. The single source of
truth is ``plugin/dsh-renpy-toolkit/``; ``--sync`` exists so the two can be
compared byte for byte without either one quietly becoming authoritative.

Running the tests without dirtying the tree
-------------------------------------------

``.pyc`` files cannot be kept out permanently: the interpreter regenerates
them the first time the package is imported. "Clean" therefore means *no
cache at the moment of delivery*, not *never any cache*. To test the release
without leaving one behind, run the suite in a throwaway copy::

    $tmp = Join-Path $env:TEMP "rptk-check"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item dist\\plugin $tmp -Recurse
    Push-Location $tmp
    python -m unittest discover -s tests -t tests -q     # 436 tests
    Pop-Location
    Remove-Item $tmp -Recurse -Force

The same applies to any command that imports the package (``--version``
included): run it from the copy, or re-run this build afterwards to sweep the
cache away again.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys

VERSION = "1.0.0"

HERE = os.path.dirname(os.path.abspath(__file__))          # tools/
ROOT = os.path.dirname(HERE)                               # repository root

SRC_PLUGIN = os.path.join(ROOT, "plugin", "dsh-renpy-toolkit")
DST_PLUGIN = os.path.join(ROOT, "dist", "plugin")

SKIP_DIRS = {"__pycache__", ".git", ".mypy_cache", ".pytest_cache"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".bak", ".orig", ".rej", "~")


def _keep(path: str) -> bool:
    name = os.path.basename(path)
    if name in SKIP_DIRS:
        return False
    return not name.endswith(SKIP_SUFFIXES)


def _walk_files(base: str):
    """Yield (relative posix path, absolute path) for releasable files."""
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if _keep(d))
        for name in sorted(filenames):
            if not _keep(name):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            yield rel, full


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prune_ignored(base: str) -> int:
    """Delete anything under *base* that :func:`_keep` rejects.

    ``_walk_files`` skips ``__pycache__`` and ``*.pyc``, so a stale cache is
    invisible to both the copy and ``--verify`` -- which is exactly how junk
    survives in a "clean" release. Running tests inside the release tree
    regenerates that cache, so the build has to sweep it away instead of
    trusting the copy to have produced a pristine tree.
    """
    removed = 0
    for dirpath, dirnames, filenames in os.walk(base, topdown=True):
        for name in list(dirnames):
            if not _keep(name):
                shutil.rmtree(os.path.join(dirpath, name))
                dirnames.remove(name)
                removed += 1
        for name in filenames:
            if not _keep(name):
                os.remove(os.path.join(dirpath, name))
                removed += 1
    return removed


def copy_tree(src: str, dst: str) -> int:
    """Replace *dst* with a clean copy of *src*. Returns the file count."""
    if not os.path.isdir(src):
        raise SystemExit("source tree missing: %s" % src)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    count = 0
    for rel, full in _walk_files(src):
        target = os.path.join(dst, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(full, target)
        count += 1
    _prune_ignored(dst)
    return count


def write_manifest_tree(base: str, version: str, verify_hint: str) -> int:
    """Write ``MANIFEST.sha256`` beside the contents of *base*.

    Shared with ``tools/build_portable.py`` so both distributions carry the
    same kind of provenance record: a plain list of ``sha256  path`` lines that
    any tool can check without this script.
    """
    lines = []
    for rel, full in _walk_files(base):
        if rel == "MANIFEST.sha256":
            continue
        lines.append("%s  %s" % (_sha256(full), rel))
    text = "# renpy-toolkit %s -- sha256 of every released file\n" % version
    text += "# verify with: %s\n" % verify_hint
    text += "\n".join(lines) + "\n"
    path = os.path.join(base, "MANIFEST.sha256")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return len(lines)


def write_manifest() -> int:
    """Write MANIFEST.sha256 next to the release tree contents."""
    return write_manifest_tree(
        DST_PLUGIN, VERSION, "python tools/build_release.py --verify"
    )


def verify_manifest() -> list[str]:
    """Return a list of problems; empty means the tree matches the manifest."""
    path = os.path.join(DST_PLUGIN, "MANIFEST.sha256")
    if not os.path.exists(path):
        return ["no MANIFEST.sha256 in %s" % DST_PLUGIN]

    expected = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            digest, _, rel = line.partition("  ")
            expected[rel] = digest

    actual = {}
    for rel, full in _walk_files(DST_PLUGIN):
        if rel == "MANIFEST.sha256":
            continue
        actual[rel] = _sha256(full)

    problems = []
    for rel in sorted(set(expected) - set(actual)):
        problems.append("missing from release: %s" % rel)
    for rel in sorted(set(actual) - set(expected)):
        problems.append("unlisted file: %s" % rel)
    for rel in sorted(set(actual) & set(expected)):
        if actual[rel] != expected[rel]:
            problems.append("changed after manifest: %s" % rel)
    return problems


def main(argv: list[str]) -> int:
    verify = "--verify" in argv
    sync = "--sync" in argv

    print("building release %s" % VERSION)
    print("  source: %s" % SRC_PLUGIN)
    print("  target: %s" % DST_PLUGIN)

    count = copy_tree(SRC_PLUGIN, DST_PLUGIN)

    version_path = os.path.join(DST_PLUGIN, "VERSION")
    with open(version_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(VERSION + "\n")

    listed = write_manifest()
    print("  copied %d file(s), manifest lists %d" % (count, listed))

    if verify:
        problems = verify_manifest()
        if problems:
            print("VERIFY FAILED (%d problem(s)):" % len(problems))
            for item in problems:
                print("  %s" % item)
            return 1
        print("  verify: OK (every digest matches)")

    if sync:
        # Replace the working copy with the release, so a later edit to
        # plugin/dsh-renpy-toolkit/ shows up as a manifest mismatch instead of
        # silently diverging.
        synced = copy_tree(DST_PLUGIN, SRC_PLUGIN)
        print("  sync: replaced %s with the release (%d file(s))" % (SRC_PLUGIN, synced))
        drift = []
        for rel, full in _walk_files(DST_PLUGIN):
            if rel == "MANIFEST.sha256":
                continue
            mirror = os.path.join(SRC_PLUGIN, rel.replace("/", os.sep))
            if not os.path.exists(mirror) or _sha256(mirror) != _sha256(full):
                drift.append(rel)
        if drift:
            print("SYNC MISMATCH:")
            for item in drift:
                print("  %s" % item)
            return 1
        print("  sync: OK (working copy is byte-identical to the release)")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
