#!/usr/bin/env python3
"""Run the bundled renpy-toolkit release without caring about the cwd.

The CLI is a plain package (``renpy_toolkit``) that must be imported with its
parent directory on ``sys.path``. Rather than telling the reader "cd somewhere
first", this launcher finds the release tree and runs it in-process.

    python scripts/rptk.py tl verify <tl-dir>
    python scripts/rptk.py --version

Resolution order for the release tree (first match wins):

1. ``$RPTK_HOME``  -- point at any directory that contains ``renpy_toolkit/``.
2. ``<skill>/../../plugin/dsh-renpy-toolkit``  -- the layout of a checkout.
3. ``<skill>/../../plugin``                    -- the pre-1.0.1 layout.
4. ``<skill>/plugin``                          -- a release copied next to the skill.

If none exists the launcher says which paths it tried instead of failing with a
bare ModuleNotFoundError.
"""

from __future__ import annotations

import os
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def candidates() -> list[str]:
    out = []
    env = os.environ.get("RPTK_HOME", "").strip()
    if env:
        out.append(os.path.abspath(env))
    # A checkout keeps the package one level down, inside a plugin folder that
    # names the distribution. The bare plugin/ form is kept so an older tree
    # still resolves instead of failing with a bare ModuleNotFoundError.
    out.append(
        os.path.abspath(
            os.path.join(SKILL_DIR, "..", "..", "plugin", "dsh-renpy-toolkit")
        )
    )
    out.append(os.path.abspath(os.path.join(SKILL_DIR, "..", "..", "plugin")))
    out.append(os.path.abspath(os.path.join(SKILL_DIR, "plugin")))
    return out


def find_release() -> str:
    tried = []
    for base in candidates():
        tried.append(base)
        package = os.path.join(base, "renpy_toolkit", "__init__.py")
        if os.path.isfile(package):
            return base
    sys.stderr.write("rptk: cannot find the renpy-toolkit release.\n")
    sys.stderr.write("rptk: looked for renpy_toolkit/__init__.py under:\n")
    for base in tried:
        sys.stderr.write("rptk:   %s\n" % base)
    sys.stderr.write("rptk: set RPTK_HOME to the directory that contains renpy_toolkit/.\n")
    raise SystemExit(2)


def _force_utf8() -> None:
    """Windows consoles default to GBK; the CLI prints Chinese.

    Only reconfigures a stream that already exists and refuses to raise, so
    redirecting to a file or piping to another tool keeps working.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: list[str]) -> int:
    _force_utf8()
    base = find_release()
    if base not in sys.path:
        sys.path.insert(0, base)

    from renpy_toolkit.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
