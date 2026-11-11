#!/usr/bin/env python3
"""Build the portable ("one-click pack") distribution of renpy-toolkit.

The package itself is pure standard library, so "portable" does not mean
bundling a Python interpreter -- it means unpacking a zip and double-clicking
a file, with no ``pip``, no ``cd``, no environment variables, and no knowledge
of where the package lives on ``sys.path``.

    python tools/build_portable.py             # build dist/portable/
    python tools/build_portable.py --zip       # also write the .zip archive
    python tools/build_portable.py --verify    # build, then run the self-check

What ends up in ``dist/portable/renpy-toolkit-<version>/``::

    renpy-toolkit.py          entry point for every target platform
    renpy-toolkit.cmd         Windows double-click launcher
    renpy-toolkit.sh          POSIX launcher (chmod +x)
    renpy_toolkit/            the package, copied from dsh-plugin/dsh-renpy-toolkit/
    README.txt                plain-text quick start (zip-friendly)
    自检.cmd / selfcheck.sh   run the built-in verification
    VERSION, MANIFEST.sha256  same provenance as dist/plugin

Why a generated entry point instead of shipping ``scripts/rptk.py``: that
launcher resolves the package relative to *its own* location inside the skill
tree. Here the package sits next to the entry point, so the lookup is trivial,
but the console encoding fix and the "explain what went wrong" behaviour still
have to be there -- a user who double-clicks a .cmd gets a window that closes
instantly on an unhandled traceback.

The three launcher files contain no logic of their own: they hand over to
``python renpy-toolkit.py``. Keeping logic in one Python file means Windows and
POSIX behave identically, and there is one place to fix a bug.

Source of truth is ``dsh-plugin/dsh-renpy-toolkit/`` (via ``dist/plugin`` when it is
up to date); see ``tools/build_release.py``. This script imports the file filter
from it so the two distributions can never disagree about what counts as a
releasable file.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # tools/
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from build_release import (  # noqa: E402  (path set up above)
    DST_PLUGIN,
    ROOT,
    SRC_PLUGIN,
    VERSION,
    _sha256,
    _walk_files,
    write_manifest_tree,
)

DIST = os.path.join(ROOT, "dist")
NAME = "renpy-toolkit-%s" % VERSION
DST = os.path.join(DIST, "portable", NAME)
ZIP_PATH = os.path.join(DIST, "portable", NAME + ".zip")


# ---------------------------------------------------------------------------
# Generated text files
# ---------------------------------------------------------------------------

ENTRY_POINT = '''#!/usr/bin/env python3
"""renpy-toolkit {version} -- portable entry point.

Runs the CLI from the package directory that sits next to this file. Nothing is
installed, nothing is written outside the arguments you pass, and no
environment variable has to be set.

    python renpy-toolkit.py --help
    python renpy-toolkit.py tl verify <game>/game/tl/chinese

On Windows, double-click ``renpy-toolkit.cmd`` instead; on macOS / Linux use
``./renpy-toolkit.sh``.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _fail(message):
    sys.stderr.write("renpy-toolkit: %s\\n" % message)
    return 2


def _force_utf8():
    """Windows consoles default to a legacy code page; the CLI prints Chinese.

    Only reconfigures streams that already exist and never raises, so piping
    into another program or redirecting to a file keeps working.
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


def main(argv):
    if sys.version_info < (3, 10):
        return _fail(
            "Python 3.10 or newer is required, this is %d.%d"
            % sys.version_info[:2]
        )

    if HERE not in sys.path:
        sys.path.insert(0, HERE)

    try:
        from renpy_toolkit.cli import main as cli_main
    except ImportError as exc:
        return _fail(
            "cannot import the bundled package (%s).\\n"
            "Make sure renpy_toolkit/ sits next to this file and that you "
            "unzipped the whole archive." % exc
        )

    return cli_main(argv)


if __name__ == "__main__":
    _force_utf8()
    sys.exit(main(sys.argv[1:]))
'''

CMP_LAUNCHER = r"""@echo off
rem renpy-toolkit {version} -- Windows launcher.
rem Drag and drop a folder onto this file, or double-click to see the help.
setlocal
set "HERE=%~dp0"

if "%~1"=="" goto :help

rem "python" first (a real install), then the py launcher (Windows Store or
rem python.org without PATH), then "python3" (MSYS2 / Cygwin / conda).
where python >nul 2>nul && goto :runpython
where py >nul 2>nul && goto :runpy
where python3 >nul 2>nul && goto :runpython3

echo.
echo [renpy-toolkit] Python was not found on PATH.
echo.
echo   Install Python 3.10 or newer from https://www.python.org/downloads/
echo   and tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 2

:runpython
python "%HERE%renpy-toolkit.py" %*
goto :done

:runpy
py -3 "%HERE%renpy-toolkit.py" %*
goto :done

:runpython3
python3 "%HERE%renpy-toolkit.py" %*
goto :done

:done
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo [renpy-toolkit] exited with code %CODE%.
    pause
)
exit /b %CODE%

:help
echo renpy-toolkit {version} -- Ren'Py translation and authoring toolkit
echo.
echo   Drag a game folder onto this file to inspect it.
echo   For the full command list, run:
echo.
echo       python renpy-toolkit.py --help
echo.
echo   Common starting points:
echo.
echo       python renpy-toolkit.py tl stats   game\game\tl\chinese
echo       python renpy-toolkit.py tl verify  game\game\tl\chinese
echo       python renpy-toolkit.py tl gen     game chinese --outdir tl --write
echo       python renpy-toolkit.py project audit game
echo.
echo   Run 自检.cmd  to verify this copy works on your machine.
echo.
pause
exit /b 0
"""

SH_LAUNCHER = """#!/bin/sh
# renpy-toolkit {version} -- POSIX launcher.
# No arguments: print the help. Otherwise forward everything to the CLI.
set -e
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ $# -eq 0 ]; then
    cat <<'EOF'
renpy-toolkit {version} -- Ren'Py translation and authoring toolkit

  python3 renpy-toolkit.py --help
  python3 renpy-toolkit.py tl verify <game>/game/tl/chinese

  Run ./selfcheck.sh to verify this copy works.
EOF
    exit 0
fi

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$HERE/renpy-toolkit.py" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$HERE/renpy-toolkit.py" "$@"
fi

echo "renpy-toolkit: python3 not found on PATH" >&2
echo "Install Python 3.10 or newer (https://www.python.org/downloads/)." >&2
exit 2
"""

SELFCHECK_CMD = r"""@echo off
rem renpy-toolkit {version} -- verify this copy on this machine.
setlocal
set "HERE=%~dp0"
where python >nul 2>nul && goto :runpython
where py >nul 2>nul && goto :runpy
echo [renpy-toolkit] Python was not found on PATH.
pause
exit /b 2

:runpython
python "%HERE%renpy-toolkit.py" --version
python "%HERE%renpy-toolkit.py" --selftest
goto :done

:runpy
py -3 "%HERE%renpy-toolkit.py" --version
py -3 "%HERE%renpy-toolkit.py" --selftest
goto :done

:done
echo.
if "%ERRORLEVEL%"=="0" (
    echo [renpy-toolkit] this copy works.
) else (
    echo [renpy-toolkit] self-check FAILED - see the messages above.
)
pause
exit /b %ERRORLEVEL%
"""

SELFCHECK_SH = """#!/bin/sh
# renpy-toolkit {version} -- verify this copy on this machine.
set -e
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
"$PY" "$HERE/renpy-toolkit.py" --version
"$PY" "$HERE/renpy-toolkit.py" --selftest
echo "[renpy-toolkit] this copy works."
"""

README_TXT = """renpy-toolkit {version}
================================

Ren'Py 游戏的汉化与制作工具包。纯标准库实现，不需要 pip 安装，不需要
Ren'Py 本体，也不需要联网。


一、先决条件
------------

只需要 Python 3.10 或更新版本：

    Windows   https://www.python.org/downloads/
              安装时务必勾选 "Add python.exe to PATH"
    macOS     brew install python3   （或从 python.org 下载）
    Linux     sudo apt install python3 （或其他发行版的等价命令）

不需要安装 fontTools / openpyxl。缺少它们时只有「字体覆盖率」与
「XLSX 表格」这两个功能会给出可读的 DependencyError，其余功能照常。


二、验证这份拷贝能不能用
------------------------

    Windows      双击 自检.cmd
    macOS/Linux  ./selfcheck.sh

它会打印版本号并跑一轮自检。看到 "this copy works" 就说明环境没问题。


三、怎么用
----------

所有命令都是「python renpy-toolkit.py <命令>」这一种形式。

    Windows       python renpy-toolkit.py --help
    macOS/Linux   ./renpy-toolkit.sh --help

把游戏文件夹直接拖到 renpy-toolkit.cmd 上会打印帮助；它不会擅自改动
任何文件——所有会写盘的命令默认都是 dry-run，需要你显式加 --write。


最常用的几条：

  # 1. 看看这份翻译做到哪了
  python renpy-toolkit.py tl stats  <game>/game/tl/chinese

  # 2. 校验译文（空译文、标签配平、行尾混用、字符串冲突……）
  python renpy-toolkit.py tl verify <game>/game/tl/chinese

  # 3. 从零生成一份完整汉化骨架（会启动游戏本身来读内存）
  python renpy-toolkit.py tl gen    <game> chinese --outdir tl --write
  #    <game> 是含 .exe 的目录，不是其中的 game/ 子目录

  # 4. 游戏更新后，把旧译文搬到新骨架上
  python renpy-toolkit.py tl port   <old-tl> <new-tl> --write

  # 5. 游戏自身体检（缺图、重复定义、会让汉化静默失效的配置）
  python renpy-toolkit.py project audit <game>

  # 6. 只有 .rpyc 没有 .rpy 的发行版，把脚本读回源码
  python renpy-toolkit.py decompile dir <game>/game -o out --write

  # 7. 拆开 / 打包 .rpa 归档
  python renpy-toolkit.py rpa list    <game>/game/archive.rpa
  python renpy-toolkit.py rpa extract <game>/game/archive.rpa <outdir>


四、两条容易踩的坑
------------------

1) 骨架里的 "new" 必须填真正的内容。
   留空字符串 new "" 不是「未翻译」——引擎会把它当成有效译文，
   于是那一行在游戏里变成空白。本工具生成的骨架一律用原文占位。

2) 语言名要和目录名一致。
   用 chinese 生成就装进 game/tl/chinese。装进 tl/other 会因为块头写的是
   translate chinese 而与原目录撞同一张翻译表，引擎直接报错起不来。


五、更多文档
------------

    README.md           端口、模块清单、设计约束
    docs/               引擎事实的实证笔记（文本标签、插值、.rpyc/.rpa 格式）
    MANIFEST.sha256     每个文件的 sha256，可用来核对完整性

许可证：MIT。详见 LICENSE。
"""


def text_files() -> dict[str, tuple[str, bool]]:
    """Return {relative name: (content, executable)} for the generated files."""
    return {
        "renpy-toolkit.py": (ENTRY_POINT.format(version=VERSION), True),
        "renpy-toolkit.cmd": (CMP_LAUNCHER.format(version=VERSION), False),
        "renpy-toolkit.sh": (SH_LAUNCHER.format(version=VERSION), True),
        "selfcheck.sh": (SELFCHECK_SH.format(version=VERSION), True),
        "自检.cmd": (SELFCHECK_CMD.format(version=VERSION), False),
        "README.txt": (README_TXT.format(version=VERSION), False),
    }


# ``cmd.exe`` parses line by line and treats a bare LF as part of the line:
# shipped with LF endings, ``goto :label`` cannot find its target, ``if`` blocks
# swallow the following line, and the whole script degenerates into a stream of
# "'xxx' is not recognized as an internal or external command". Batch files must
# be CRLF no matter what the build host prefers, so the ending is chosen per
# file type rather than globally.
CRLF_SUFFIXES = (".cmd", ".bat")


def _newline_for(name: str) -> str:
    return "\r\n" if name.lower().endswith(CRLF_SUFFIXES) else "\n"


def _write_text(path: str, text: str, *, executable: bool = False) -> None:
    name = os.path.basename(path)
    eol = _newline_for(name)
    # Normalise whatever the template used, then apply the platform-neutral
    # ending this file type requires.
    body = text.replace("\r\n", "\n").replace("\n", eol)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    if executable:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build() -> int:
    """Create dist/portable/<name>/ and return the number of package files."""
    if not os.path.isdir(SRC_PLUGIN):
        raise SystemExit("package source missing: %s" % SRC_PLUGIN)

    if os.path.isdir(DST):
        shutil.rmtree(DST)
    os.makedirs(DST, exist_ok=True)

    for name, (text, executable) in text_files().items():
        _write_text(os.path.join(DST, name), text, executable=executable)

    # Package files come from the release filter so both distributions agree
    # on what "releasable" means (no caches, no editor backups).
    count = 0
    for rel, full in _walk_files(os.path.join(SRC_PLUGIN, "renpy_toolkit")):
        target = os.path.join(DST, "renpy_toolkit", rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(full, target)
        count += 1

    _write_text(os.path.join(DST, "VERSION"), VERSION + "\n")
    write_manifest_tree(DST, VERSION, "python renpy-toolkit.py --selftest")
    return count


def make_zip() -> int:
    """Zip the portable directory; returns the archive size in bytes."""
    import zipfile

    if not os.path.isdir(DST):
        raise SystemExit("build first: %s does not exist" % DST)
    os.makedirs(os.path.dirname(ZIP_PATH), exist_ok=True)
    if os.path.exists(ZIP_PATH):
        os.remove(ZIP_PATH)

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for dirpath, dirnames, filenames in os.walk(DST):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                inner = os.path.relpath(full, os.path.dirname(DST))
                zf.write(full, inner.replace(os.sep, "/"))
    return os.path.getsize(ZIP_PATH)


# ---------------------------------------------------------------------------
# Skill bundle
# ---------------------------------------------------------------------------
#
# The skill is two files that already live in git, so why a second archive?
#
# Because installing a skill means copying a directory into a skills folder,
# and the people most likely to want that are not necessarily running ``git``.
# A zip also keeps the skill usable on its own: it ships the launcher and the
# document, and finds the toolkit through ``$RPTK_HOME`` or a sibling
# ``dsh-plugin/dsh-renpy-toolkit/``.
#
# The bundle deliberately does *not* embed the package. Duplicating 1 MB of
# Python into every skill zip would make the two copies drift, and the skill's
# launcher already has a documented search order. What it embeds instead is a
# README that names that order.

SKILL_SRC = os.path.join(ROOT, "skills", "renpy-toolkit")
SKILL_NAME = "renpy-toolkit-skill-%s" % VERSION
SKILL_ZIP = os.path.join(DIST, "skill", SKILL_NAME + ".zip")

SKILL_README = """renpy-toolkit skill {version} -- how to install
==========================================

1. Unpack this zip so that the resulting folder is ``renpy-toolkit/``.
2. Move that folder into your harness's skills directory. For DeepSeek Harness
   that is ``~/.dsh/skills/``; other harnesses use their own directory.
3. Point the skill at the toolkit. The launcher tries, in order:

       $RPTK_HOME/renpy_toolkit/__init__.py   (set RPTK_HOME to the folder
                                               that contains renpy_toolkit/)
       ../../dsh-plugin/dsh-renpy-toolkit/renpy_toolkit/__init__.py
                                              (a checkout's own plugin/)
       ./plugin/renpy_toolkit/__init__.py

   So a git checkout of renpy-toolkit needs nothing at all: the skill lives in
   ``skills/renpy-toolkit/`` and finds ``dsh-plugin/dsh-renpy-toolkit/`` two levels
   up. If you only have the portable zip, unpack it anywhere and set
   ``RPTK_HOME`` to that folder.

4. Test it:

       python <skills-dir>/renpy-toolkit/scripts/rptk.py --version

The skill document (SKILL.md) is written for an agent, not for a person: it is
a routing table from "what the user asked" to "which command to run", plus the
hard rules that are easy to get wrong (empty ``new`` blanks a line; language
name must match the tl/ directory; ``old`` needs the text tags copied over).

Getting the toolkit itself: https://github.com/AMeYunCloud/renpy-toolkit
"""


def build_skill() -> int:
    """Write dist/skill/<name>/ from skills/renpy-toolkit; returns file count."""
    import shutil as _shutil

    if not os.path.isdir(SKILL_SRC):
        raise SystemExit("skill source missing: %s" % SKILL_SRC)

    dst = os.path.join(DIST, "skill", SKILL_NAME)
    if os.path.isdir(dst):
        _shutil.rmtree(dst)
    os.makedirs(dst, exist_ok=True)

    count = 0
    for rel, full in _walk_files(SKILL_SRC):
        target = os.path.join(dst, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        _shutil.copy2(full, target)
        count += 1

    _write_text(os.path.join(dst, "README.txt"), SKILL_README.format(version=VERSION))
    _write_text(os.path.join(dst, "VERSION"), VERSION + "\n")
    return count


def make_skill_zip() -> int:
    """Zip dist/skill/<name>; returns the archive size in bytes."""
    import zipfile

    src = os.path.join(DIST, "skill", SKILL_NAME)
    if not os.path.isdir(src):
        raise SystemExit("build first: %s does not exist" % src)
    os.makedirs(os.path.dirname(SKILL_ZIP), exist_ok=True)
    if os.path.exists(SKILL_ZIP):
        os.remove(SKILL_ZIP)

    with zipfile.ZipFile(SKILL_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                # Keep a single top-level folder inside the zip so that
                # "extract here" produces a directory rather than loose files.
                inner = os.path.join(SKILL_NAME, os.path.relpath(full, src))
                zf.write(full, inner.replace(os.sep, "/"))
    return os.path.getsize(SKILL_ZIP)


def main(argv: list[str]) -> int:
    print("building portable %s" % VERSION)
    print("  source: %s/renpy_toolkit" % SRC_PLUGIN)
    print("  target: %s" % DST)

    count = build()
    generated = len(text_files()) + 1        # + VERSION
    print("  wrote %d generated file(s) + %d package file(s)" % (generated, count))

    if "--zip" in argv or "--verify" in argv:
        size = make_zip()
        print("  zip: %s (%d bytes)" % (ZIP_PATH, size))

    skill_count = build_skill()
    print("building skill bundle %s" % VERSION)
    print("  source: %s" % SKILL_SRC)
    print("  wrote %d file(s) + README.txt + VERSION" % skill_count)
    if "--zip" in argv or "--verify" in argv:
        skill_size = make_skill_zip()
        print("  zip: %s (%d bytes)" % (SKILL_ZIP, skill_size))

    if "--verify" in argv:
        print("  verify: run `python %s --selftest` inside the portable directory"
              % os.path.join(DST, "renpy-toolkit.py"))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
