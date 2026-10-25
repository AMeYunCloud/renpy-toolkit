"""Check that every flag / sub-subcommand SKILL.md promises actually exists.

Godoc-style: it greps SKILL.md for `rptk.py ...` command lines, then asks the
CLI's own --help output whether each flag is real. A flag that only appears in
the markdown would otherwise be an unusable instruction.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # tools/
ROOT = os.path.dirname(HERE)                               # repository root
SKILL = os.path.join(ROOT, "skill", "renpy-toolkit", "SKILL.md")
LAUNCHER = os.path.join(ROOT, "skill", "renpy-toolkit", "scripts", "rptk.py")

# Things SKILL.md promises, grouped by the command line that must accept them.
REQUIRED = {
    ("tl", "verify"): ["--expect-dialogue", "--game-dir", "--strict", "--fullwidth"],
    ("tl", "gen"): ["--outdir", "--write", "--no-strings", "--clean-scratch"],
    ("tl", "table"): ["-o"],
    ("tl", "apply"): ["--write"],
    ("tl", "port"): ["--write"],
    ("tl", "fix-fstrings"): ["--language", "--outdir", "--values", "--write"],
    ("tl", "glossary", "check"): ["--ui-terms", "--terms", "--missing", "--drift"],
    ("tl", "glossary", "list"): ["--terms", "--scope"],
    ("tl", "hardcoded", "scan"): ["--language"],
    ("tl", "context"): ["--language", "--tl-dir", "--write", "--only",
                        "--ignore", "--ignore-file", "--show-all"],
    ("tl", "untranslatable"): ["--show-all"],
    ("font", "check"): ["--tl", "--extra-text"],
    ("font", "map"): ["--emit"],
    ("rpa", "extract"): ["--no-overwrite"],
    ("rpa", "pack"): ["--version", "--prefix", "--key"],
    ("project", "new"): ["--dest"],
}

# Sub-subcommands that must exist under a group.
SUBS = {
    ("font",): ["check", "map"],
    ("rpa",): ["list", "extract", "pack"],
    ("project",): ["new", "audit"],
    ("tl", "glossary"): ["check", "list"],
    ("tl", "hardcoded"): ["scan", "show", "apply"],
}


def _child_env() -> dict[str, str]:
    """Environment for the ``--help`` probes, with bytecode writing disabled.

    The launcher imports the package straight out of the release tree, so every
    probe would otherwise leave a ``__pycache__`` behind -- i.e. the checker
    would dirty the very artifact it is meant to validate. ``-B`` alone is not
    enough because the interpreter may still honour a stale cache on read; the
    variable stops the write, which is what actually creates the directory.
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def help_text(args: list[str]) -> str:
    proc = subprocess.run(
        [sys.executable, LAUNCHER, *args, "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_child_env(),
    )
    return (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    problems: list[str] = []

    for path, flags in REQUIRED.items():
        text = help_text(list(path))
        for flag in flags:
            if flag not in text:
                problems.append("%s: missing flag %s" % (" ".join(path), flag))

    for path, names in SUBS.items():
        text = help_text(list(path))
        for name in names:
            if not re.search(r"^\s+%s\b" % re.escape(name), text, re.M):
                problems.append("%s: missing sub-command %s" % (" ".join(path), name))

    # Every literal `rptk.py <group> <action>` line in the skill must resolve.
    with open(SKILL, encoding="utf-8") as fh:
        skill = fh.read()
    seen = set()
    for match in re.finditer(r"rptk\.py ((?:[a-z][a-z-]*)(?:\s+[a-z][a-z-]*)*)", skill):
        words = match.group(1).split()
        if not words or words[0] in ("--version", "--help"):
            continue
        key = tuple(words[:2])
        if key in seen:
            continue
        seen.add(key)
        proc = subprocess.run(
            [sys.executable, LAUNCHER, *words, "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=_child_env(),
        )
        if proc.returncode != 0:
            problems.append("SKILL.md mentions a command that does not resolve: %s"
                            % " ".join(words))

    print("checked %d flag group(s), %d sub-command group(s), %d skill command(s)"
          % (len(REQUIRED), len(SUBS), len(seen)))
    if problems:
        print("PROBLEMS (%d):" % len(problems))
        for item in problems:
            print("  %s" % item)
        return 1
    print("OK: every documented flag and command exists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
