"""基于 Git 的备份，取代 ``.bak`` 副本。

为什么要它
----------
改译文是**反复小步**的动作：一次 ``--write`` 动几十上百个文件，一个语言目录
动辄几千个 ``.bak``。它们和 ``.rpy`` 同名同目录，真正的代价有三个：

1. **``.bak`` 会污染目录**。它们混在 TL 目录里，``ls`` / 编辑器搜索 / 打包发布
   都要额外排除；删掉它们又会失去回退能力，所以实际上没人敢删。
2. **每次覆盖只留一份**。第二次改就把第一份 ``.bak`` 覆盖掉，改到第 N 步时
   想回到第 N-2 步已经没有副本了。
3. **没有命名约定**。``x.rpy.bak`` 是覆盖前还是覆盖后？哪次操作写的？谁的？
   出问题时这些信息全靠回忆。

Git 天生解决这三件事，只是不该用「用户自己的仓库」：TL 目录可能已经在某个
仓库里，往那里 ``commit`` 会改用户的提交历史。所以这里用的是**影子仓库**
（shadow repository）：一个独立的 ``--git-dir``，对象库和 index 都在里面，
工作区仍是用户的 TL 目录。

做法（不用 libgit2、不装依赖，只调 ``git`` 命令）
-------------------------------------------------
- ``git --git-dir=<影子> init`` 建库，**不设** ``core.worktree``，所以它没有
  自己的检出目录，不会被误当成正常仓库使用。
- 每次保存：把目标文件的内容 ``hash-object -w`` 进对象库（直接写 blob，
  不碰 index、不碰用户文件），用 ``update-index --cacheinfo`` 把它们登记到
  index，再 ``write-tree`` → ``commit-tree`` → ``update-ref`` 造一个提交。
  父提交取当前 ``HEAD``，所以历史是线性可追溯的。
- 回退：``git --git-dir=<影子> show <rev>:<path>`` 取回内容，逐字节写回。

为什么不 ``git add <目录>``：那要求影子仓库有 ``core.worktree`` 指向用户
目录，一旦设错就会在用户目录里建 ``.git``，或者把整棵树都登记进去（包含
与本工具无关的文件、以及那个 4 GB 的游戏）。只登记调用方明确交代的文件，
既可控又快。

只用标准库 + 系统 ``git``；``git`` 不在时 :func:`available` 返回 False，
调用方据此回退到 ``.bak`` 或干脆不备份，而不是抛异常打断一次写盘。

预期行为
--------
- ``GitBackups(dir).save([path])`` 第一次调用会建库并造一个提交；
  同样的内容再保存一次返回 ``None``（无变化就不造提交）。
- ``backups.list()`` 给出 ``[Snapshot(rev, when, message, files)]``，
  ``restore(rev)`` 把它们逐字节写回原路径。
- 所有写操作都在影子仓库里，用户的 TL 目录除了本来要改的那个文件之外
  **不会多出任何东西**。
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess

from .errors import ToolkitError
from .safeio import safe_join

__all__ = ["GitBackups", "GitUnavailableError", "Snapshot", "available", "git_version"]


#: 影子仓库默认位置（相对被备份目录）。
DEFAULT_DIRNAME = ".renpy-toolkit.git"

#: 提交者身份。不读用户 gitconfig：备份不该因为用户没配 ``user.email`` 而失败。
#: ``core.autocrlf`` 必须显式关掉 —— Git for Windows 的系统级 gitconfig 默认
#: 是 ``true``，它会让 ``hash-object`` 在存 blob 时把 CRLF 换成 LF、``show``
#: 取回来时再换一遍。译文文件的行尾是逐文件保留的（有的 LF、有的 CRLF），
#: 被这么转一次，备份就不再是用户当时的那份文件了。
_CONFIG = (
    "-c", "core.autocrlf=false",
    "-c", "core.safecrlf=false",
    "-c", "core.eol=lf",
    # Without this, `log --name-only` renders any non-ASCII path as
    # "tl/chinese/\350\277\231.rpy" and `tl backup list --files` shows mojibake
    # instead of the filename the user recognises. `ls-tree` is read with -z,
    # which already bypasses quoting.
    "-c", "core.quotePath=false",
)

_IDENTITY = (
    "-c", "user.name=renpy-toolkit",
    "-c", "user.email=renpy-toolkit@localhost",
)


class GitUnavailableError(ToolkitError):
    """``git`` 不在 PATH 上，或版本太旧跑不动这里用到的子命令。"""


def _run(args: list[str], *, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _run_bytes(args: list[str], *, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Same as :func:`_run` but without any decoding.

    Needed wherever the stdout **is** file content (``git show``). ``text=True``
    would take the platform's universal-newline translation with it, quietly
    turning CRLF into LF -- and a TL file that loses its line endings is no
    longer the file the user had.
    """
    return subprocess.run(args, cwd=cwd, capture_output=True, check=False)


def _text(value) -> str:
    """Decode git output that may be str (text mode) or bytes (byte mode)."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def git_version() -> tuple[int, int, int] | None:
    """Return the installed git version, or None when git is missing."""
    exe = shutil.which("git")
    if not exe:
        return None
    proc = _run([exe, "--version"])
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    # "git version 2.51.0.windows.1"
    for token in text.split():
        parts = token.split(".")
        if parts and parts[0].isdigit():
            numbers = []
            for part in parts[:3]:
                digits = "".join(ch for ch in part if ch.isdigit())
                numbers.append(int(digits) if digits else 0)
            while len(numbers) < 3:
                numbers.append(0)
            return tuple(numbers)  # type: ignore[return-value]
    return None


def available() -> bool:
    """True when a usable ``git`` is present.

    The threshold is 2.5: ``hash-object -w`` and ``commit-tree`` are ancient,
    but ``--no-optional-locks`` (used to keep concurrent runs from fighting
    over the index) lands in 2.15, and nothing older is worth supporting.
    """
    version = git_version()
    return version is not None and version >= (2, 15, 0)


class Snapshot:
    """One saved point in the backup history."""

    __slots__ = ("rev", "when", "message", "files")

    def __init__(self, rev: str, when: str, message: str, files: list[str]) -> None:
        self.rev = rev
        self.when = when
        self.message = message
        self.files = files

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Snapshot({self.rev[:8]!r}, {self.when!r}, {len(self.files)} files)"

    def summary(self) -> str:
        stamp = self.when.replace("T", " ")[:19]
        return f"{self.rev[:8]}  {stamp}  {len(self.files):>3} file(s)  {self.message}"


class GitBackups:
    """A shadow repository that records每一次改动前的文件内容。

    ``root`` is the directory whose files get backed up; ``git_dir`` defaults to
    ``<root>/.renpy-toolkit.git``. Nothing outside ``git_dir`` is touched.
    """

    def __init__(self, root: str, *, git_dir: str | None = None) -> None:
        self.root = os.path.abspath(root)
        self.git_dir = os.path.abspath(git_dir or os.path.join(self.root, DEFAULT_DIRNAME))
        self._exe = shutil.which("git")

    # ------------------------------------------------------------- plumbing
    def _git(self, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
        if not self._exe:
            raise GitUnavailableError(
                "git 不在 PATH 上；无法使用 Git 备份，请装 git 或改用 .bak"
            )
        argv = [
            self._exe,
            "--git-dir", self.git_dir,
            "--no-optional-locks",
            *_CONFIG,
            *_IDENTITY,
            *args,
        ]
        return _run(argv, cwd=cwd or self.root)

    def _git_bytes(self, *args: str) -> subprocess.CompletedProcess:
        """Like :meth:`_git` but keeps stdin/stdout as raw bytes."""
        if not self._exe:
            raise GitUnavailableError(
                "git 不在 PATH 上；无法使用 Git 备份，请装 git 或改用 .bak"
            )
        argv = [
            self._exe,
            "--git-dir", self.git_dir,
            "--no-optional-locks",
            *_CONFIG,
            *_IDENTITY,
            *args,
        ]
        return _run_bytes(argv, cwd=self.root)

    def _check(self, proc: subprocess.CompletedProcess, what: str) -> str:
        if proc.returncode != 0:
            detail = _text(proc.stderr or proc.stdout or "").strip().splitlines()
            tail = detail[-1] if detail else "exit %d" % proc.returncode
            raise ToolkitError(f"git {what} 失败：{tail}")
        return _text(proc.stdout or "").strip()

    def exists(self) -> bool:
        return os.path.isdir(self.git_dir) and os.path.isfile(
            os.path.join(self.git_dir, "HEAD")
        )

    def init(self) -> bool:
        """Create the shadow repository. Returns True when it was created."""
        if self.exists():
            return False
        parent = os.path.dirname(self.git_dir)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # No ``core.worktree``: the shadow repo must never look like a normal
        # checkout, or ``git status`` inside it would try to describe the TL
        # directory and a stray ``git clean`` could delete the user's work.
        proc = _run([self._exe or "git", "init", "--quiet", "--bare", self.git_dir])
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip()
            raise ToolkitError(f"无法创建 Git 备份库 {self.git_dir}：{detail}")
        # Bare repositories refuse to accept commits that touch paths unless
        # this is off; it also keeps ``hash-object``/``update-index`` symmetric.
        self._git("config", "core.bare", "false")
        self._git("config", "gc.auto", "0")
        # Belt and braces: the ``-c`` flags already neutralise the system-wide
        # ``core.autocrlf=true`` on every call, but a human poking at the shadow
        # repo by hand should get the same answer.
        self._git("config", "core.autocrlf", "false")
        self._git("config", "core.safecrlf", "false")
        return True

    def head(self) -> str | None:
        if not self.exists():
            return None
        proc = self._git("rev-parse", "--verify", "--quiet", "HEAD")
        if proc.returncode != 0:
            return None
        return (proc.stdout or "").strip() or None

    # ---------------------------------------------------------------- saving
    def _relative(self, path: str) -> str:
        rel = os.path.relpath(os.path.abspath(path), self.root)
        return rel.replace("\\", "/")

    def _tracked(self, path: str) -> str:
        """Reject anything outside ``root``: the shadow repo keys are relative."""
        full = os.path.abspath(path)
        try:
            common = os.path.commonpath([full, self.root])
        except ValueError:
            raise ToolkitError(f"{path} 不在备份目录 {self.root} 之内") from None
        if common != self.root:
            raise ToolkitError(f"{path} 不在备份目录 {self.root} 之内")
        return self._relative(full)

    def save(self, paths, *, message: str = "") -> Snapshot | None:
        """Record the current content of *paths*; None when nothing changed.

        The blobs are written straight into the object database with
        ``hash-object -w``, so the working tree is never modified and no
        ``.git`` directory appears next to the user's translations.

        The returned :class:`Snapshot` lists **the files passed in this call**.
        The commit itself carries the full tree (previous files included), which
        is why restoring an old revision reproduces the whole directory.
        """
        self.init()
        entries: dict[str, str] = {}
        for path in paths:
            full = os.path.abspath(path)
            if not os.path.isfile(full):
                continue
            rel = self._tracked(full)
            # ``--no-filters`` is the important flag: without it, ``autocrlf``
            # and any ``.gitattributes`` clean filter would rewrite the bytes on
            # the way into the object database.
            blob = self._check(
                self._git("hash-object", "-w", "--no-filters", "--", full),
                "hash-object",
            )
            entries[rel] = blob
        if not entries:
            return None

        parent = self.head()
        # Start from the parent's tree so a snapshot is a full picture, not a
        # diff: restoring any revision then yields every file at that moment.
        if parent:
            self._check(self._git("read-tree", parent), "read-tree")
        else:
            self._check(self._git("read-tree", "--empty"), "read-tree")
        for rel, blob in sorted(entries.items()):
            self._check(
                self._git("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}"),
                "update-index",
            )
        tree = self._check(self._git("write-tree"), "write-tree")

        if parent:
            parent_tree = self._check(self._git("rev-parse", f"{parent}^{{tree}}"), "rev-parse")
            if parent_tree == tree:
                return None

        stamp = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        body = message or f"backup {stamp}"
        argv = ["commit-tree", tree, "-m", body]
        if parent:
            argv += ["-p", parent]
        rev = self._check(self._git(*argv), "commit-tree")
        self._check(self._git("update-ref", "refs/heads/main", rev), "update-ref")
        self._check(self._git("update-ref", "HEAD", "refs/heads/main"), "update-ref")
        return Snapshot(rev, stamp, body, sorted(entries))

    def plan_all(self, *, suffixes=(".rpy", ".rpym")) -> list[str]:
        """Files :meth:`snapshot_all` would record, without writing anything.

        The CLI needs this for its dry run: calling ``snapshot_all`` to build a
        preview used to create the commit, so the subsequent ``--write`` pass
        found nothing left to do and reported "nothing changed".
        """
        paths = []
        for dirpath, dirnames, names in os.walk(self.root):
            dirnames[:] = sorted(
                d for d in dirnames if os.path.join(dirpath, d) != self.git_dir
            )
            for name in sorted(names):
                if name.endswith(suffixes):
                    paths.append(os.path.join(dirpath, name))
        return paths

    def snapshot_all(self, *, suffixes=(".rpy", ".rpym"), message: str = "") -> Snapshot | None:
        """Save every matching file under ``root`` (used before a bulk rewrite)."""
        return self.save(self.plan_all(suffixes=suffixes), message=message or "bulk snapshot")

    # ------------------------------------------------------------- history
    def list(self, *, limit: int = 20) -> list[Snapshot]:
        """Newest first. Empty when the repository has no commits yet."""
        if not self.exists() or self.head() is None:
            return []
        proc = self._git(
            "log",
            "--pretty=format:%H%x1f%cI%x1f%s%x1f",
            "--name-only",
            f"-{max(1, limit)}",
        )
        if proc.returncode != 0:
            return []
        out: list[Snapshot] = []
        rev = when = message = ""
        files: list[str] = []
        for line in (proc.stdout or "").splitlines():
            if "\x1f" in line:
                if rev:
                    out.append(Snapshot(rev, when, message, files))
                parts = line.split("\x1f")
                rev, when, message = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
                files = []
            elif line.strip():
                files.append(line.strip())
        if rev:
            out.append(Snapshot(rev, when, message, files))
        return out

    def show(self, rev: str, relpath: str) -> bytes:
        """Return the content of *relpath* as of *rev*, byte for byte.

        Reads the blob out of the object database directly (``cat-file blob``)
        rather than through ``git show``: ``cat-file`` has no checkout path, so
        no smudge filter and no autocrlf can touch it.
        """
        blob = self._git("rev-parse", f"{rev}:{relpath}")
        if blob.returncode != 0:
            detail = _text(blob.stderr or "").strip().splitlines()
            tail = detail[-1] if detail else "unknown revision or path"
            raise ToolkitError(f"取不回 {relpath} @ {rev[:8]}：{tail}")
        sha = _text(blob.stdout).strip()
        proc = self._git_bytes("cat-file", "blob", sha)
        if proc.returncode != 0:
            detail = _text(proc.stderr or "").strip().splitlines()
            tail = detail[-1] if detail else "missing blob"
            raise ToolkitError(f"取不回 {relpath} @ {rev[:8]}：{tail}")
        return proc.stdout

    def restore(self, rev: str, *, dry_run: bool = False) -> list[str]:
        """Write every file in *rev* back to its original path.

        Each file is written byte for byte, so BOM and line endings survive.
        Returns the list of paths actually written. A file already identical to
        the snapshot is left alone (its mtime is not bumped).

        Path names are re-validated before use even though :meth:`save` only
        ever records paths inside ``root``: ``ls-tree`` trusts whatever is in
        the object database, and a tree can hold a *subtree* named ``..``
        (``mktree`` rejects slashes in a name, but not a subtree called ``..``).
        That yields ``../owned.rpy`` from ``ls-tree -r``, so a tampered shadow
        repository could otherwise write above the backup root -- the same
        class of bug as Zip Slip.
        Names are read with ``-z``: without it git quotes any path holding a
        non-ASCII byte as ``"\\346\\227\\245\\346\\156.sup"``, and the escaped form
        is not a path we can join.
        """
        if not self.exists() or self.head() is None:
            raise ToolkitError("还没有任何备份，无法回退")
        listing = self._git_bytes("ls-tree", "-r", "--name-only", "-z", rev)
        if listing.returncode != 0:
            raise ToolkitError(f"没有这个备份版本：{rev}")
        names = [n.decode("utf-8", "surrogateescape")
                 for n in (listing.stdout or b"").split(b"\x00") if n.strip()]
        written: list[str] = []
        for rel in names:
            target = safe_join(self.root, rel, what=f"backup entry {rel!r}")
            data = self.show(rev, rel)
            if dry_run:
                written.append(target)
                continue
            previous = None
            if os.path.isfile(target):
                with open(target, "rb") as handle:
                    previous = handle.read()
                if previous == data:
                    continue
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(data)
            written.append(target)
        return written

    # --------------------------------------------------------------- misc
    def size(self) -> int:
        """Bytes occupied by the shadow repository."""
        total = 0
        for dirpath, _dirnames, names in os.walk(self.git_dir):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
        return total

    def prune(self, *, keep: int = 20) -> int:
        """Drop history older than *keep* snapshots. Returns commits removed.

        Every snapshot stores a **full tree**, not a diff, so the newest *keep*
        commits carry everything needed to restore any of them. Pruning rewrites
        those *keep* commits so the oldest becomes a root commit, then lets
        ``git gc --prune=now`` collect the blobs that only the dropped commits
        referenced. Rewriting is cheap: one ``commit-tree`` per kept snapshot.
        """
        head = self.head()
        if head is None:
            return 0
        keep = max(1, keep)
        total = self._git("rev-list", "--count", head)
        if total.returncode != 0:
            return 0
        try:
            count = int(_text(total.stdout).strip() or "0")
        except ValueError:
            return 0
        if count <= keep:
            return 0

        history = self.list(limit=keep)
        if len(history) < keep:
            return 0
        parent: str | None = None
        for snap in reversed(history):  # oldest kept first
            tree = self._check(self._git("rev-parse", f"{snap.rev}^{{tree}}"), "rev-parse")
            argv = ["commit-tree", tree, "-m", snap.message or "backup"]
            if parent:
                argv += ["-p", parent]
            parent = self._check(self._git(*argv), "commit-tree")
        assert parent is not None
        self._check(self._git("update-ref", "refs/heads/main", parent), "update-ref")
        self._check(self._git("update-ref", "HEAD", "refs/heads/main"), "update-ref")
        self._git("gc", "--quiet", "--prune=now")
        return count - keep
