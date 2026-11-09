"""备份策略：``.bak`` 副本 / Git 影子仓库 / 不备份，三选一。

为什么需要一层调度
------------------
工具里原本有十来处「写盘前先复制一份 ``x.rpy.bak``」。散落各处意味着没有
统一开关：想把它们全换成 Git 备份，得改十几个函数签名。这里把「备份」收敛
成一个模块级的策略，写入点只调用 :func:`keep`，不关心落地形态。

三种模式
--------
``bak``
    传统行为，在目标旁边写 ``<名>.bak``。**默认值**，因为它不依赖任何外部命令。
``git``
    用 :mod:`renpy_toolkit.gitbackup` 的影子仓库记录。好处是不在译文目录里
    堆副本、每次改动都有独立版本、可列出/回退到任意一次。**机器上没有 git 时
    自动降级为 ``bak``**，并打印一行说明，不会中断命令。
``none``
    不备份。只在调用方明确知道自己在做什么时用（例如已经在自己的仓库里，
    或者写的是可再生的中间产物）。

``auto``
    明确表达「有 git 就用 git，否则用 .bak」。``git`` 模式本身在缺 git 时也会
    降级成 ``bak``，所以两者现在的落地行为相同；区别只在措辞：``auto`` 是
    「随机器而定」，``git`` 是「我要 git」，此时降级必须出声告知。

为什么缺 git 不报错而是降级
---------------------------
报错能保证「不会静默没有备份」，但代价是一个只想改译文的用户被一条环境问题拦住。
``.bak`` 本身就是可用的备份形态，需要的东西一样都不用装。所以选择：降级 + 明确
告知。真正无法降级的是**操作快照**的命令（``tl backup list/show/restore``）——
那类命令由 :func:`git_required` 拒绝，因为没有 ``.bak`` 版的「show 某个 revision」。

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

import hashlib
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
    "downgrade_note",
    "get_mode",
    "git_required",
    "keep",
    "repo_parent",
    "reset_mode",
    "set_mode",
    "store_for",
    "tree_of",
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

    Asking for ``git`` on a machine without git degrades to ``bak`` instead of
    failing. The reasoning: a backup that silently does not happen is the worst
    outcome, but refusing to run at all is only marginally better -- the user's
    immediate goal was to edit a translation, and ``.bak`` copies are a working
    form of backup that needs nothing installed. So the mode is resolved to
    something usable and :func:`downgrade_note` reports what changed, which the
    CLI prints. Callers that must have git (``tl backup``, which cannot operate
    on ``.bak`` files) check :func:`git_required` themselves.
    """
    global _mode
    normalised = (mode or "").strip().lower()
    if normalised not in MODES:
        raise ToolkitError(
            "unknown backup mode %r; expected one of: %s"
            % (mode, ", ".join(MODES))
        )
    if normalised == MODE_GIT and not _git_available():
        normalised = MODE_BAK
    elif normalised == MODE_AUTO:
        normalised = MODE_GIT if _git_available() else MODE_BAK
    _mode = normalised
    return _mode


def downgrade_note(requested: str | None) -> str | None:
    """One line explaining a ``git`` -> ``bak`` downgrade, or ``None``.

    Silent fallback would leave the user believing snapshots exist when they do
    not, so every entry point that resolves a mode prints this.
    """
    wanted = (requested or "").strip().lower()
    if wanted != MODE_GIT:
        return None
    if get_mode() != MODE_BAK:
        return None
    return (
        "找不到可用的 git（2.15 以上），本次备份改用 .bak 副本；"
        "需要快照历史请装 git 并放进 PATH"
    )


def git_required(feature: str = "该命令") -> None:
    """Raise unless git is usable.

    Used by features that operate *on* snapshots rather than taking them --
    ``tl backup list/show/restore``. There the fallback is meaningless: there is
    no ``.bak`` equivalent of "show revision ca0777d7".
    """
    if _git_available():
        return
    raise MissingToolError(
        "git（2.15 以上）",
        f"{feature}需要快照历史，无法回落到 .bak；请装 git 并放进 PATH",
    )


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


def tree_of(path: str) -> str:
    """The translation tree a *path* belongs to.

    Walking up to the nearest ``tl`` directory keeps one shadow repository per
    translation tree, so a snapshot taken while editing one language can also
    restore the others. Without a ``tl`` ancestor the path's own directory is
    used, which is still a sane unit for a one-off ``.rpy`` edit.

    *path* may be a file or a directory. ``tl backup`` passes the translation
    directory itself, so a directory is treated as the starting point instead
    of its parent -- otherwise ``tl/chinese`` would resolve to ``game/`` and
    two languages would share one repository.
    """
    start = os.path.abspath(path)
    if not os.path.isdir(start):
        start = os.path.dirname(start) or os.getcwd()
    probe = start
    while True:
        if os.path.basename(probe).lower() == "tl":
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return start
        probe = parent


def repo_parent(tree: str) -> str:
    """Directory that holds the shadow repository for *tree*.

    ``RTK_BACKUP_DIR`` overrides the location, one subdirectory per tree (named
    by a hash of the tree path). The GUI uses it so that everything the tool
    writes lives in a single folder beside the exe instead of dropping
    ``.renpy-toolkit.git`` into the game the user is translating.
    """
    tree = os.path.abspath(tree)
    override = os.environ.get("RTK_BACKUP_DIR")
    if override:
        digest = hashlib.sha1(tree.encode("utf-8")).hexdigest()[:12]
        return os.path.join(os.path.abspath(override), digest)
    return tree


def backup_root(path: str) -> str:
    """Directory a Git backup of *path* covers. Kept as the public entry name."""
    return repo_parent(tree_of(path))


def store_for(tree: str):
    """A :class:`gitbackup.GitBackups` for *tree*.

    The work tree is always the translation tree -- ``restore`` writes files
    relative to it -- while the repository itself may sit elsewhere when
    ``RTK_BACKUP_DIR`` is set.
    """
    from . import gitbackup

    tree = os.path.abspath(tree)
    parent = repo_parent(tree)
    if parent == tree:
        return gitbackup.GitBackups(tree)
    return gitbackup.GitBackups(tree, git_dir=os.path.join(parent, gitbackup.DEFAULT_DIRNAME))


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
        # Reachable only if git vanished mid-run (set_mode already degrades to
        # bak). Falling back is better than losing the backup entirely.
        target = dest + ".bak"
        if previous is not None:
            with open(target, "wb") as handle:
                handle.write(previous)
        else:
            with open(dest, "rb") as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
        return target
    store = store_for(tree_of(dest))
    snapshot = store.save([dest], message=message or f"before writing {os.path.basename(dest)}")
    if snapshot is None:
        # Content identical to the last snapshot: nothing new to record.
        return None
    return snapshot.rev[:8]


def restore_list(root: str, *, limit: int = 20):
    """Convenience passthrough used by the CLI."""
    return store_for(tree_of(root)).list(limit=limit)
