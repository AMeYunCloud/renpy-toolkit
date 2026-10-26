"""备份策略：``.bak`` 副本 / Git 影子仓库 / 不备份，三选一。

为什么需要一层调度
------------------
工具里原本有十来处「写盘前先复制一份 ``x.rpy.bak``」。散落各处意味着没有
统一开关：想把它们全换成 Git 备份，得改十几个函数签名。这里把「备份」收敛
成一个模块级的策略，写入点只调用 :func:`keep`，不关心落地形态。

三种模式
--------
``bak``
    传统行为，在目标旁边写 ``<名>.bak``。**向后兼容的默认值**，因为它不依赖
    任何外部命令。
``git``
    用 :mod:`renpy_toolkit.gitbackup` 的影子仓库记录。好处是不在译文目录里
    堆副本、每次改动都有独立版本、可列出/回退到任意一次。
``none``
    不备份。只在调用方明确知道自己在做什么时用（例如已经在自己的仓库里，
    或者写的是可再生的中间产物）。

``auto``
    构造 :func:`keep` 的调用方可以要求 ``auto``：有 ``git`` 就用 ``git``，
    否则退回 ``bak``。**不要把它设成模块默认值** —— 默认值决定行为，不该
    取决于碰巧装没装 git。

选择方式（优先级从高到低）
--------------------------
1. 调用 :func:`set_mode`（脚本内直接指定）
2. 环境变量 ``RTK_BACKUP``（``bak`` / ``git`` / ``none`` / ``auto``）
3. 模块默认值 ``bak``

为什么 ``keep`` 需要 ``previous`` 参数
--------------------------------------
``.bak`` 是「覆盖前的内容」，而 Git 备份读的是**磁盘上的当前内容**。两者在
「调用方已经读过旧内容、准备覆盖」这个时机上一致，所以 :func:`keep` 接收
调用方已经读出来的 ``previous`` 字节，避免重复读盘、也避免在时间窗口里出现
「读到的是新内容」的错位。
"""

from __future__ import annotations

import os

from .errors import MissingToolError, ToolkitError

__all__ = [
    "MODE_AUTO",
    "MODE_BAK",
    "MODE_GIT",
    "MODE_NONE",
    "MODES",
    "backup_root",
    "describe",
    "get_mode",
    "keep",
    "reset_mode",
    "set_mode",
]

MODE_BAK = "bak"
MODE_GIT = "git"
MODE_NONE = "none"
MODE_AUTO = "auto"

#: Every accepted value, in the order the CLI help lists them.
MODES = (MODE_BAK, MODE_GIT, MODE_NONE, MODE_AUTO)

#: What a fresh process does before anyone calls :func:`set_mode`.
_DEFAULT_MODE = MODE_BAK
_mode: str | None = None


def get_mode() -> str:
    """The effective mode, honouring ``RTK_BACKUP`` when nothing was set.

    ``RTK_BACKUP=git`` is resolved through :func:`set_mode` on first use rather
    than returned verbatim, so the environment variable cannot smuggle in an
    unusable mode that :func:`set_mode` would have rejected.
    """
    if _mode is not None:
        return _mode
    raw = (os.environ.get("RTK_BACKUP") or "").strip().lower()
    if raw in MODES:
        return set_mode(raw)
    return _DEFAULT_MODE


def set_mode(mode: str) -> str:
    """Pin the mode for this process. Returns the mode actually in force.

    ``auto`` is resolved here (once), so a caller that asked for ``auto`` gets a
    stable answer for the rest of the run instead of a per-file decision.

    Asking for ``git`` when git cannot be found is refused **now**, not at the
    first write. Deferring the check sounds tidier but fails badly: a command
    that does not write anything (``tl verify``, ``tl stats``, ``tl missing``)
    would run to completion with exit code 0 while the user believes backups
    are being taken. Silently having no backup is the one outcome worth
    spending an error message on.
    """
    global _mode
    normalised = (mode or "").strip().lower()
    if normalised not in MODES:
        raise ToolkitError(
            "unknown backup mode %r; expected one of: %s"
            % (mode, ", ".join(MODES))
        )
    if normalised == MODE_AUTO:
        normalised = MODE_GIT if _git_available() else MODE_BAK
    elif normalised == MODE_GIT and not _git_available():
        raise MissingToolError(
            "git（2.15 以上）",
            "请装 git 并放进 PATH，或改用 --backup-mode bak",
        )
    _mode = normalised
    return _mode


def reset_mode() -> None:
    """Forget any pinned mode (tests use this between cases)."""
    global _mode
    _mode = None


def describe() -> str:
    """One line for ``--selftest`` / ``--version`` style output."""
    mode = get_mode()
    if mode == MODE_GIT:
        return "git (影子仓库，可回退到任意一次改动)"
    if mode == MODE_NONE:
        return "none (不备份)"
    return "bak (在目标旁边写 <名>.bak)"


def _git_available() -> bool:
    try:
        from . import gitbackup
    except Exception:  # pragma: no cover - import cannot realistically fail
        return False
    return gitbackup.available()


def backup_root(path: str) -> str:
    """Pick the directory a Git backup of *path* should cover.

    Walking up to the nearest ``tl`` directory keeps one shadow repository per
    translation tree, so a snapshot taken while editing one language can also
    restore the others. Without a ``tl`` ancestor the file's own directory is
    used, which is still a sane unit for a one-off ``.rpy`` edit.
    """
    directory = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    probe = directory
    while True:
        if os.path.basename(probe).lower() == "tl":
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return directory
        probe = parent


def keep(dest: str, previous: bytes | None = None, *, message: str = "") -> str | None:
    """Record the content of *dest* that is about to be overwritten.

    Returns a short description of what was kept (``"x.rpy.bak"`` or the backup
    revision), or ``None`` when nothing was recorded -- no previous content, or
    the mode is ``none``.

    ``previous`` is the bytes the caller is about to replace. Passing it avoids
    reading the file twice. When it is None the current content is read here,
    which is only correct if the caller has not written the file yet.

    A ``.rpa`` archive can be gigabytes, so the ``.bak`` branch copies in chunks
    when it has to read the file itself. The ``git`` branch has to hash the
    whole thing either way; that is the price of a restorable snapshot, and the
    caller chose the mode.
    """
    mode = get_mode()
    if mode == MODE_NONE:
        return None
    if not os.path.exists(dest):
        return None

    if mode == MODE_BAK:
        target = dest + ".bak"
        if previous is not None:
            with open(target, "wb") as handle:
                handle.write(previous)
            return target
        # Chunked copy: assemblies can be far larger than RAM.
        with open(dest, "rb") as src, open(target, "wb") as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
        return target

    # Git: the blob comes from the file on disk, so the caller must not have
    # overwritten it yet. ``tl.TlFile.write`` and friends all call this before
    # their own write, which is the case this module is built for.
    from . import gitbackup

    if not gitbackup.available():
        raise MissingToolError(
            "git（2.15 以上）",
            "请装 git 或改用 --backup-mode bak",
        )
    root = backup_root(dest)
    store = gitbackup.GitBackups(root)
    snapshot = store.save([dest], message=message or f"before writing {os.path.basename(dest)}")
    if snapshot is None:
        # Content identical to the last snapshot: nothing new to record.
        return None
    return snapshot.rev[:8]


def restore_list(root: str, *, limit: int = 20):
    """Convenience passthrough used by the CLI."""
    from . import gitbackup

    return gitbackup.GitBackups(root).list(limit=limit)
