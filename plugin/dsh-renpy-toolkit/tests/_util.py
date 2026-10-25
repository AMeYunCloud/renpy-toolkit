"""测试共用夹具，只用标准库（不引入 pytest）。

为什么这里不用 ``tempfile.mkdtemp()``
-------------------------------------
``tempfile.mkdtemp()`` 用 0700 的私有 ACL 创建目录，会**覆盖掉**沙箱本该继承的
授权，于是新建目录里连自己的文件都写不进去（``PermissionError``）。
``os.makedirs()`` 不改动 ACL，因此拿到的是继承而来的写权限，本模块统一用它。

临时目录的位置
--------------
优先用系统临时目录下的 ``<tmp>/rptk-tests/``（不污染项目树）。只有在系统临时
目录不可写时才退回项目树根目录。两种情况都用 ``SCRATCH_PREFIX`` 前缀命名，
``_purge()`` 只清理这个前缀，不会碰到别的文件。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile

TESTS_ROOT = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(TESTS_ROOT)
WORKSPACE = os.path.dirname(PLUGIN_ROOT)
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

#: Prefix for every scratch directory / file created by the fixtures.
SCRATCH_PREFIX = "_rptk_tmp_"


def _pick_scratch_root() -> str:
    """System temp dir when writable, else the workspace root."""
    candidate = os.path.join(tempfile.gettempdir(), "rptk-tests")
    try:
        os.makedirs(candidate, exist_ok=True)
        probe = os.path.join(candidate, ".writable")
        with open(probe, "wb") as handle:
            handle.write(b"1")
        os.remove(probe)
        return candidate
    except OSError:
        return WORKSPACE


SCRATCH_ROOT = _pick_scratch_root()


SAMPLE_COMMON = '''\ufeff# TODO: Translation updated at 2024-01-01 00:00

translate chinese strings:

    # game/screens.rpy:100
    old "Start Game"
    new ""

    # game/screens.rpy:101
    old "Load"
    new ""

# game/script.rpy:7
translate chinese start_636ae3f5:

    # e "Hello, world!"
    e ""

# game/script.rpy:9
translate chinese start_9e4cbe22:

    # e "You have {image=heart.png} [points] points."
    e ""
'''

SAMPLE_ONE_BLOCK = (
    "# game/script.rpy:7\n"
    "translate chinese start_636ae3f5:\n"
    "\n"
    '    # e "Hello, world!"\n'
    '    e ""\n'
)


def sample_translated() -> str:
    return (
        "\ufefftranslate chinese strings:\n"
        "\n"
        '    old "Start Game"\n'
        '    new "开始游戏"\n'
        "\n"
        '    old "Load"\n'
        '    new "读取"\n'
        "\n"
        "# game/script.rpy:7\n"
        "translate chinese start_636ae3f5:\n"
        "\n"
        '    # e "Hello, world!"\n'
        '    e "你好，世界！"\n'
        "\n"
        "# game/script.rpy:9\n"
        "translate chinese start_9e4cbe22:\n"
        "\n"
        '    # e "You have {image=heart.png} [points] points."\n'
        '    e "你有 {image=heart.png} [points] 点。"\n'
    )


def _purge() -> None:
    """Remove every scratch entry left behind by a previous *process*.

    Called once at import time and once at exit, never between tests: a global
    purge would destroy the fixture of an enclosing context manager.

    ``WORKSPACE`` is swept too, purely to clean up trees written by older
    versions of these fixtures (they used to drop files next to the package).
    Only names carrying :data:`SCRATCH_PREFIX` are touched.
    """
    for root in {SCRATCH_ROOT, WORKSPACE}:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if not name.startswith(SCRATCH_PREFIX):
                continue
            target = os.path.join(root, name)
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    os.remove(target)


_counter = 0


def _tmpdir() -> str:
    """Create a fresh writable directory under the scratch root."""
    global _counter
    _counter += 1
    path = os.path.join(SCRATCH_ROOT, f"{SCRATCH_PREFIX}{os.getpid()}_{_counter}")
    os.makedirs(path, exist_ok=True)
    return path


@contextlib.contextmanager
def temp_dir():
    """Yield a fresh writable directory; removed afterwards.

    Only *this* directory is cleaned up on exit. Purging every sibling scratch
    directory would delete the fixtures of an enclosing ``temp_tl()``, which is
    exactly the ``with temp_tl(..) as base, temp_dir() as out:`` pattern used
    all over the table/verify tests.
    """
    path = _tmpdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def scratch_file(content: str, suffix: str = ".rpy") -> str:
    """Write ``content`` to a fresh scratch file and return its path."""
    global _counter
    root = _tmpdir()
    path = os.path.join(root, f"f{_counter}{suffix}")
    with open(path, "wb") as handle:
        handle.write(content.encode("utf-8"))
    return path


@contextlib.contextmanager
def temp_tl(files: dict[str, str]):
    """Materialise a ``tl/<lang>`` tree and yield the **directory** path.

    ``files`` is ``{relative/name.rpy: content}``; the returned directory is a
    drop-in replacement for a real translation directory.
    """
    with temp_dir() as root:
        for name, content in files.items():
            target = os.path.join(root, name.replace("/", os.sep))
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(content.encode("utf-8"))
        yield root


def tempdir_for(case: object) -> str:
    """Create a scratch directory that ``case.addCleanup`` removes afterwards.

    ``setUp`` methods cannot use ``with``, and a bare ``tempfile.mkdtemp`` there
    leaks one directory per test run into the system temp dir (the fixture has
    no way to know it should delete it). This routes every directory through
    the same prefixed scratch root, so ``_purge`` also catches a crashed run.
    """
    directory = _tmpdir()
    case.addCleanup(shutil.rmtree, directory, True)
    return directory


def scratch_path(suffix: str = ".rpy") -> str:
    """Return an unused path directly under the scratch root.

    Unlike :func:`scratch_file` this allocates no subdirectory, which keeps the
    flat ``_rptk_tmp_*`` layout the exit sweep walks. Callers write the bytes
    themselves.
    """
    global _counter
    _counter += 1
    return os.path.join(SCRATCH_ROOT, f"{SCRATCH_PREFIX}flat_{os.getpid()}_{_counter}{suffix}")


def write(path: str, content: str) -> str:
    """Write text at ``path`` (parents created), returning ``path``."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content.encode("utf-8"))
    return path


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def read_text(path: str) -> str:
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8-sig")


# Start from a clean slate: leftovers from a crashed run are not interesting,
# and they would pollute the workspace root listing.
_purge()

# ...and sweep again at exit. ``_purge()`` above only runs on import, so without
# this an ordinary successful run could still leave scratch directories behind
# (a fixture whose context manager was interrupted, for example).
import atexit  # noqa: E402  (kept next to the hook it documents)

atexit.register(_purge)

