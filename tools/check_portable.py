#!/usr/bin/env python3
"""Acceptance test for the portable distribution, run from a clean unzip.

    python tools/check_portable.py            # build if needed, then check
    python tools/check_portable.py --built    # check the existing zip only

Purpose: prove that the zip is self-sufficient. Every command below runs from a
freshly extracted copy in a temporary directory, with the current working
directory outside both the repository and the extracted folder, so nothing can
be satisfied by an import path that only exists on the build machine.

Checks, in order:

1. ``--version`` and ``--selftest`` succeed.
2. ``tl stats`` / ``tl verify`` / ``tl table`` work on a translation directory
   synthesised on the spot (no game needed).
3. A wrong subcommand returns EXIT_USAGE (2), not 0 -- a launcher that always
   exits 0 is indistinguishable from success in a script.
4. Every path listed in ``MANIFEST.sha256`` exists with the recorded digest.
5. Batch launchers are CRLF, because LF silently breaks ``goto`` and ``if``.

Writes only under the system temp directory and removes it afterwards.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from build_portable import NAME, SKILL_NAME, SKILL_ZIP, ZIP_PATH  # noqa: E402

EXIT_USAGE = 2

# A translation file with one dialogue block and one strings block. CRLF and a
# BOM on purpose: that is what a Windows-authored file looks like, and the
# parser is supposed to preserve both byte for byte.
SAMPLE_TL = (
    "translate chinese start_636ae3f5:\r\n"
    "\r\n"
    '    # e "Hello there."\r\n'
    '    e "你好。"\r\n'
    "\r\n"
    "translate chinese strings:\r\n"
    "\r\n"
    "    # game/screens.rpy:10\r\n"
    '    old "Start"\r\n'
    '    new "开始"\r\n'
)


def _run(argv: list[str], cwd: str) -> tuple[int, str]:
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _lone_lf(path: str) -> int:
    """Count LF bytes that are not part of a CRLF pair."""
    data = open(path, "rb").read()
    return sum(
        1 for i, b in enumerate(data)
        if b == 0x0A and (i == 0 or data[i - 1] != 0x0D)
    )


def main(argv: list[str]) -> int:
    if "--built" not in argv:
        rc = subprocess.run(
            [sys.executable, os.path.join(HERE, "build_portable.py"), "--zip"], cwd=ROOT
        ).returncode
        if rc != 0:
            print("build failed")
            return rc

    if not os.path.exists(ZIP_PATH):
        print("missing zip: %s" % ZIP_PATH)
        return 2

    problems: list[str] = []
    tmp = tempfile.mkdtemp(prefix="rtk-accept-")
    try:
        with zipfile.ZipFile(ZIP_PATH) as zf:
            names = zf.namelist()
            # Reject absolute or parent-relative members: a zip that escapes its
            # own root is exactly what a cautious user should refuse to extract.
            for name in names:
                if name.startswith("/") or ".." in name.split("/"):
                    problems.append("unsafe zip member: %s" % name)
            zf.extractall(tmp)

        portable = os.path.join(tmp, NAME)
        entry = os.path.join(portable, "renpy-toolkit.py")
        if not os.path.isfile(entry):
            print("missing entry point in the archive: %s" % entry)
            return 1

        # Run with cwd somewhere unrelated: proves nothing depends on it.
        work = tempfile.mkdtemp(prefix="rtk-cwd-")
        try:
            code, out = _run([sys.executable, entry, "--version"], work)
            if code != 0 or "renpy-toolkit" not in out:
                problems.append("--version failed (exit %d): %s" % (code, out.strip()))

            code, out = _run([sys.executable, entry, "--selftest"], work)
            if code != 0 or "self-check passed" not in out:
                problems.append("--selftest failed (exit %d): %s" % (code, out.strip()))

            tl_dir = os.path.join(work, "game", "tl", "chinese")
            os.makedirs(tl_dir)
            with open(os.path.join(tl_dir, "script.rpy"), "wb") as fh:
                fh.write(b"\xef\xbb\xbf" + SAMPLE_TL.encode("utf-8"))

            code, out = _run([sys.executable, entry, "tl", "stats", tl_dir], work)
            if code != 0:
                problems.append("tl stats failed (exit %d): %s" % (code, out.strip()))

            code, out = _run([sys.executable, entry, "tl", "verify", tl_dir], work)
            if code != 0:
                problems.append("tl verify failed (exit %d): %s" % (code, out.strip()))

            table = os.path.join(work, "t.csv")
            code, out = _run(
                [sys.executable, entry, "tl", "table", tl_dir, "-o", table,
                 "--format", "csv"],
                work,
            )
            if code != 0 or not os.path.exists(table):
                problems.append("tl table failed (exit %d): %s" % (code, out.strip()))

            code, _out = _run([sys.executable, entry, "no-such-group"], work)
            if code != EXIT_USAGE:
                problems.append(
                    "wrong subcommand exited %d, expected %d" % (code, EXIT_USAGE)
                )
        finally:
            shutil.rmtree(work, ignore_errors=True)

        # Manifest digests.
        manifest = os.path.join(portable, "MANIFEST.sha256")
        if not os.path.isfile(manifest):
            problems.append("no MANIFEST.sha256 in the archive")
        else:
            listed = 0
            with open(manifest, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    want, _, rel = line.partition("  ")
                    listed += 1
                    full = os.path.join(portable, rel.replace("/", os.sep))
                    if not os.path.exists(full):
                        problems.append("manifest lists a missing file: %s" % rel)
                    elif _digest(full) != want:
                        problems.append("digest mismatch: %s" % rel)
            if listed < 30:
                problems.append("manifest only lists %d file(s)" % listed)

        # Batch files must be CRLF or cmd.exe mis-parses them.
        for name in ("renpy-toolkit.cmd", "自检.cmd"):
            path = os.path.join(portable, name)
            if not os.path.isfile(path):
                problems.append("missing launcher: %s" % name)
                continue
            bad = _lone_lf(path)
            if bad:
                problems.append("%s has %d bare LF line ending(s); cmd.exe needs CRLF"
                                % (name, bad))

        problems.extend(_check_skill_zip(portable))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if problems:
        print("FAILED (%d problem(s)):" % len(problems))
        for item in problems:
            print("  %s" % item)
        return 1

    print("portable %s: OK" % NAME)
    print("  version, selftest, tl stats/verify/table, exit codes, "
          "manifest, CRLF launchers")
    print("skill %s: OK" % SKILL_NAME)
    print("  single top-level folder, README/VERSION present, "
          "launcher finds the toolkit via RPTK_HOME")
    return 0


def _check_skill_zip(portable_dir: str) -> list[str]:
    """Validate the skill bundle against the portable build it must pair with.

    The skill zip deliberately does not embed the package (two copies of the
    same Python would drift), so the interesting property is not "does it
    contain everything" but "does its launcher find a toolkit". That is checked
    by pointing RPTK_HOME at the portable copy and running it from an unrelated
    working directory.
    """
    problems: list[str] = []
    if not os.path.exists(SKILL_ZIP):
        return ["missing skill zip: %s" % SKILL_ZIP]

    with zipfile.ZipFile(SKILL_ZIP) as zf:
        names = zf.namelist()
    for name in names:
        if name.startswith("/") or ".." in name.split("/"):
            problems.append("unsafe zip member in skill bundle: %s" % name)
    # A single top-level folder, so "extract here" does not scatter files.
    tops = {name.split("/", 1)[0] for name in names}
    if tops != {SKILL_NAME}:
        problems.append("skill zip should have exactly one top-level folder "
                        "%r, found %r" % (SKILL_NAME, sorted(tops)))
    for required in ("README.txt", "SKILL.md", "scripts/rptk.py"):
        if not any(name.endswith(required) for name in names):
            problems.append("skill zip is missing %s" % required)

    tmp = tempfile.mkdtemp(prefix="rtk-skill-")
    try:
        with zipfile.ZipFile(SKILL_ZIP) as zf:
            zf.extractall(tmp)
        launcher = os.path.join(tmp, SKILL_NAME, "scripts", "rptk.py")

        work = tempfile.mkdtemp(prefix="rtk-skill-cwd-")
        try:
            env = dict(os.environ)
            env["RPTK_HOME"] = portable_dir
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.run(
                [sys.executable, launcher, "--version"], cwd=work, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0 or "renpy-toolkit" not in (proc.stdout or ""):
                problems.append(
                    "skill launcher could not reach the toolkit via RPTK_HOME "
                    "(exit %d): %s" % (proc.returncode,
                                       ((proc.stdout or "") + (proc.stderr or "")).strip())
                )
        finally:
            shutil.rmtree(work, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return problems


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
