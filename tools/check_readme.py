"""Validate README table-of-contents anchors and relative links.

Run from anywhere::

    python tools/check_readme.py                  # checks the default files
    python tools/check_readme.py README.md docs/x.md

Checks, for every markdown file:

* every ``[text](#anchor)`` link resolves to a heading in the same file,
* every relative ``[text](path)`` link points at an existing file,
* every heading has a unique slug.

The slug rule is the one GitHub uses closely enough for this purpose: lowercase,
drop punctuation, spaces become hyphens, keep letters/digits and CJK.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT = [
    os.path.join(ROOT, "README.md"),
    os.path.join(ROOT, "plugin", "dsh-renpy-toolkit", "README.md"),
]

HEADING_RE = re.compile(r"^(#{1,6})\s+(?P<title>.+?)\s*$")
LINK_RE = re.compile(r"(?<!!)\[(?P<text>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")

# GitHub keeps letters and digits from any script (so CJK survives), plus
# hyphens and underscores; everything else is punctuation and is dropped.
# An explicit "keep" test is used instead of a punctuation list because the
# punctuation that actually appears in these headings is mostly CJK
# (「」（）：→)，which no ASCII-only list would catch.
def _keep(char: str) -> bool:
    return char.isalnum() or char in "-_"


def slugify(title: str) -> str:
    """Turn a heading into the anchor GitHub would generate for it."""
    # Inline code and explicit links inside a heading contribute their text.
    title = re.sub(r"`([^`]*)`", r"\1", title)
    title = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)
    out = []
    for char in title.strip().lower():
        if char.isspace():
            out.append("-")
        elif _keep(char):
            out.append(char)
    return "".join(out)


def headings(text: str) -> list[str]:
    """Slugs of every heading, ignoring anything inside a fenced code block."""
    found = []
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match:
            found.append(slugify(match.group("title")))
    return found


def check(path: str) -> list[str]:
    """Return the problems found in one file; empty means it is clean."""
    with open(path, "r", encoding="utf-8-sig") as handle:
        text = handle.read()

    problems = []
    slugs = headings(text)
    seen = set()
    for slug in slugs:
        if slug in seen:
            problems.append("duplicate heading slug: #%s" % (slug,))
        seen.add(slug)

    base = os.path.dirname(path)
    for match in LINK_RE.finditer(text):
        target = match.group("target")
        if target.startswith("#"):
            anchor = target[1:]
            if anchor and anchor not in seen:
                problems.append("dead anchor: %s" % (target,))
            continue
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            continue  # http:, mailto:, ...
        clean = target.split("#", 1)[0]
        if not clean:
            continue
        if not os.path.exists(os.path.join(base, clean)):
            problems.append("dead relative link: %s" % (target,))

    return problems


def main(argv: list[str]) -> int:
    paths = argv or DEFAULT
    failed = 0
    for path in paths:
        if not os.path.exists(path):
            print("%s: missing" % (path,))
            failed += 1
            continue
        problems = check(path)
        name = os.path.relpath(path, ROOT)
        if problems:
            failed += 1
            print("%s: %d problem(s)" % (name, len(problems)))
            for problem in problems:
                print("   %s" % (problem,))
        else:
            print("%s: OK (headings=%d)" % (name, len(headings_of(path))))
    return 1 if failed else 0


def headings_of(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8-sig") as handle:
        return headings(handle.read())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
