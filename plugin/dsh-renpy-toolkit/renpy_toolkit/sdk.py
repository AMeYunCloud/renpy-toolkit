"""驱动 Ren'Py SDK 的命令行（这是「权威生成」的唯一途径）。

核心立场
--------
翻译标识符（``start_636ae3f5``）是 ``md5(code + "\\r\\n")[:8]`` 派生的，
``code`` 的构造规则（谁说话、文本的转义形态、同行 ``with`` 是否并入）都能
从源码读出来 —— 但**能算不等于应该算**。一旦我们自己算出标识符，就必须永远
跟着引擎的规则走，任何一次引擎改动都会让整个项目的译文错位，而且是静默的。

所以本模块只做一件事：**调用官方 CLI，读它的产物**。

    renpy.sh <basedir> translate <lang>        生成/更新骨架（增量，不动已有译文）
    renpy.sh <basedir> translate <lang> --count 只报缺失数
    renpy.sh <basedir> dialogue <lang>          导出对白表
    renpy.sh <basedir> extract_strings <lang> f.json
    renpy.sh <basedir> merge_strings <lang> f.json
    renpy.sh <basedir> lint --error-code --no-orphan-tl
    renpy.sh <basedir> compile

⚠️ 官方明确声明 CLI **不是稳定接口**，会随版本变化。因此：

* 所有参数集中在 :data:`COMMANDS` 里，升级 SDK 时只改这一处；
* 解析输出时**不依赖具体措辞**（用退出码 + 计数 + 产物文件是否存在判断），
  输出只做人看的日志；
* 命令失败时把完整 stdout/stderr 放进异常，方便贴给用户。

# 探测路径（Windows / macOS / Linux）
:func:`find_sdk` 依次尝试
1. 显式传入的路径；
2. ``$RENPY_SDK`` / ``%RENPY_SDK%``；
3. ``PATH`` 上的 ``renpy.exe`` / ``renpy.sh`` / ``renpy``；
4. 常见安装位置（Windows 的 ``C:\\renpy-*``、``%LOCALAPPDATA%``；macOS 的
   ``/Applications/renpy*``；Linux 的 ``~/renpy-*``、``/opt/renpy*``）。

依赖：仅标准库。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .errors import InvalidRequestError, SDKError, SDKNotFoundError, SDKTimeoutError

__all__ = [
    "COMMANDS",
    "CommandResult",
    "RenPySDK",
    "find_sdk",
    "is_project",
]

#: Everything we call, in one place, because the CLI is explicitly unstable.
COMMANDS: dict[str, tuple[str, ...]] = {
    "translate": ("translate",),            # + <lang> [--options]
    "dialogue": ("dialogue",),              # + <lang> [--text|--strings]
    "extract_strings": ("extract_strings",),  # + <lang> <dest.json>
    "merge_strings": ("merge_strings",),    # + <lang> <src.json>
    "lint": ("lint",),                      # [file] [--error-code]
    "compile": ("compile",),
    "run": ("run",),
    "add_from": ("add_from",),
}

#: 默认超时（秒）。``translate`` 在大项目上可能跑几分钟。
DEFAULT_TIMEOUT = 1800


@dataclass
class CommandResult:
    """One finished SDK invocation."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def tail(self, lines: int = 40) -> str:
        text = (self.stdout + "\n" + self.stderr).strip()
        parts = text.splitlines()
        return "\n".join(parts[-lines:])

    def check(self) -> "CommandResult":
        """Raise :class:`SDKError` if the command failed; else return self."""
        if not self.ok:
            raise SDKError(
                f"command failed (exit {self.returncode}): {' '.join(self.argv)}\n"
                f"--- output ---\n{self.tail()}"
            )
        return self

    def missing_count(self) -> int | None:
        """Extract ``N`` from the ``--count`` output.

        The wording is not part of the contract, so we take the first integer
        that appears and treat any failure to find one as "unknown".
        """
        import re

        for line in (self.stdout + "\n" + self.stderr).splitlines():
            match = re.search(r"\b(\d+)\b", line)
            if match:
                return int(match.group(1))
        return None


def _candidates_from_env() -> list[str]:
    out: list[str] = []
    for name in ("RENPY_SDK", "RENPY_HOME", "RENPY_PATH"):
        value = os.environ.get(name)
        if value:
            out.append(value)
    return out


def _candidate_roots() -> list[str]:
    """Best-effort list of directories that might contain an SDK."""
    roots: list[str] = []
    if sys.platform.startswith("win"):
        for drive in ("C:", "D:", "E:"):
            roots.append(f"{drive}{os.sep}")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.append(local)
        user = os.environ.get("USERPROFILE")
        if user:
            roots.append(user)
    else:
        roots.extend(["/Applications", os.path.expanduser("~"), "/opt", "/usr/local"])
    return roots


def _exe_names() -> tuple[str, ...]:
    if sys.platform.startswith("win"):
        return ("renpy.exe",)
    return ("renpy.sh", "renpy")


def find_sdk(
    explicit: str | None = None,
    *,
    basedir: str | None = None,
    scan_roots: bool = True,
) -> str:
    """Locate the Ren'Py SDK launcher and return its absolute path.

    *explicit* may point at either the launcher itself or an SDK directory
    (the one containing ``renpy.exe`` / ``renpy.sh``). *basedir* lets us also
    accept a game directory that has its own ``renpy/`` runtime next to it.
    """
    def accept(path: str) -> str | None:
        if not path:
            return None
        if os.path.isfile(path):
            return os.path.abspath(path)
        if os.path.isdir(path):
            for name in _exe_names():
                candidate = os.path.join(path, name)
                if os.path.isfile(candidate):
                    return os.path.abspath(candidate)
            # 有的发行版把启动器放在 renpy/ 子目录里
            for name in _exe_names():
                candidate = os.path.join(path, "renpy", name)
                if os.path.isfile(candidate):
                    return os.path.abspath(candidate)
        return None

    for source in (explicit, *_candidates_from_env()):
        found = accept(source or "")
        if found:
            return found

    for name in _exe_names():
        found = shutil.which(name)
        if found:
            return os.path.abspath(found)

    # 游戏目录里自带运行时（作者套壳发行 / 汉化包常见）
    if basedir:
        for probe in (basedir, os.path.dirname(os.path.abspath(basedir))):
            for name in _exe_names():
                candidate = os.path.join(probe, name)
                if os.path.isfile(candidate):
                    return os.path.abspath(candidate)
            candidate = os.path.join(probe, "lib", "py3-windows-x86_64", "renpy.exe")
            if os.path.isfile(candidate):  # pragma: no cover - exotic layout
                return os.path.abspath(candidate)

    if scan_roots:
        import glob

        patterns: list[str] = []
        for root in _candidate_roots():
            for name in _exe_names():
                patterns.append(os.path.join(root, "renpy*", name))
                patterns.append(os.path.join(root, "renpy*", "renpy", name))
        for pattern in patterns:
            try:
                matches = sorted(glob.glob(pattern))
            except OSError:  # 无权限的根目录
                continue
            if matches:
                # 取版本号最大的（目录名 renpy-8.3.7-sdk 排序友好）
                return os.path.abspath(matches[-1])

    raise SDKNotFoundError(
        "cannot find the Ren'Py SDK launcher (renpy.exe/renpy.sh). "
        "Pass --sdk PATH, set the RENPY_SDK environment variable, or put it on PATH."
    )


def is_project(path: str) -> bool:
    """True when *path* looks like a Ren'Py game project (has a ``game/`` dir)."""
    if not os.path.isdir(path):
        return False
    if os.path.isdir(os.path.join(path, "game")):
        return True
    # 少数工程把脚本直接放在根目录
    try:
        return any(
            name.endswith(".rpy") for name in os.listdir(path)
        )
    except OSError:
        return False


@dataclass
class RenPySDK:
    """A thin, well-behaved wrapper around one SDK executable."""

    executable: str
    """Absolute path to ``renpy.exe`` / ``renpy.sh``."""

    timeout: int = DEFAULT_TIMEOUT
    env: Mapping[str, str] = field(default_factory=dict)
    """Extra environment variables (e.g. ``RENPY_LANGUAGE`` for the
    unsanctioned-string-capture workflow)."""

    @classmethod
    def discover(cls, sdk: str | None = None, *, basedir: str | None = None) -> "RenPySDK":
        return cls(executable=find_sdk(sdk, basedir=basedir))

    @property
    def directory(self) -> str:
        return os.path.dirname(self.executable)

    # ------------------------------------------------------------- execution
    def run(
        self,
        basedir: str,
        args: Sequence[str],
        *,
        timeout: int | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Run ``<exe> <basedir> <args...>`` and capture output."""
        if not os.path.isdir(basedir):
            raise InvalidRequestError(f"{basedir}: not a directory")
        argv = [self.executable, basedir, *args]
        merged_env = dict(os.environ)
        merged_env.update(self.env)
        if env:
            merged_env.update(env)
        # Ren'Py 在 Windows 上会弹一个控制台/窗口；--compile 之类不会，但为了
        # 稳定地把输出拿回来，统一走管道。
        merged_env.setdefault("PYTHONIOENCODING", "utf-8")
        import time

        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd or basedir,
                env=merged_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SDKTimeoutError(
                f"command timed out after {timeout or self.timeout}s: {' '.join(argv)}"
            ) from exc
        except OSError as exc:
            raise SDKError(f"cannot run {self.executable}: {exc}") from exc
        return CommandResult(
            argv=argv,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration=time.monotonic() - started,
        )

    def _run_command(
        self,
        command: str,
        basedir: str,
        extra: Sequence[str] = (),
        **kwargs: object,
    ) -> CommandResult:
        if command not in COMMANDS:
            raise InvalidRequestError(
                f"unknown SDK command {command!r}; known: {', '.join(COMMANDS)}"
            )
        return self.run(basedir, [*COMMANDS[command], *extra], **kwargs)  # type: ignore[arg-type]

    # -------------------------------------------------------------- translate
    def translate(
        self,
        basedir: str,
        language: str,
        *,
        count_only: bool = False,
        strings_only: bool = False,
        common_only: bool = False,
        empty: bool = False,
        extra: Sequence[str] = (),
        **kwargs: object,
    ) -> CommandResult:
        """Generate/update ``tl/<language>``.

        This is **non-destructive and incremental**: existing translations are
        never touched, only missing blocks are appended. Safe to re-run after
        every script change.
        """
        args: list[str] = [language]
        if count_only:
            args.append("--count")
        if strings_only:
            args.append("--strings-only")
        if common_only:
            args.append("--common-only")
        if empty:
            args.append("--empty")
        args.extend(extra)
        return self._run_command("translate", basedir, args, **kwargs)

    def missing_count(self, basedir: str, language: str, **kwargs: object) -> int | None:
        """How many items still lack a translation (``translate --count``)."""
        result = self.translate(basedir, language, count_only=True, **kwargs)
        if not result.ok:
            return None
        return result.missing_count()

    # --------------------------------------------------------------- dialogue
    def dialogue(
        self,
        basedir: str,
        language: str | None = None,
        *,
        text: bool = True,
        strings: bool = False,
        notags: bool = False,
        escape: bool = False,
        **kwargs: object,
    ) -> CommandResult:
        args: list[str] = []
        if language:
            args.append(language)
        if text:
            args.append("--text")
        if strings:
            args.append("--strings")
        if notags:
            args.append("--notags")
        if escape:
            args.append("--escape")
        return self._run_command("dialogue", basedir, args, **kwargs)

    # ------------------------------------------------------- strings JSON 通道
    def extract_strings(
        self,
        basedir: str,
        language: str,
        destination: str,
        *,
        merge: bool = True,
        force: bool = False,
        **kwargs: object,
    ) -> CommandResult:
        args = [language, destination]
        if merge:
            args.append("--merge")
        if force:
            args.append("--force")
        return self._run_command("extract_strings", basedir, args, **kwargs)

    def merge_strings(
        self,
        basedir: str,
        language: str,
        source: str,
        *,
        reverse: bool = False,
        replace: bool = False,
        **kwargs: object,
    ) -> CommandResult:
        """Import a JSON file. Note: this **implies ``--compile``**."""
        args = [language, source]
        if reverse:
            args.append("--reverse")
        if replace:
            args.append("--replace")
        return self._run_command("merge_strings", basedir, args, **kwargs)

    # ------------------------------------------------------------------ misc
    def lint(
        self,
        basedir: str,
        *,
        error_code: bool = True,
        no_orphan_tl: bool = True,
        **kwargs: object,
    ) -> CommandResult:
        args: list[str] = []
        if error_code:
            args.append("--error-code")
        if no_orphan_tl:
            args.append("--no-orphan-tl")
        return self._run_command("lint", basedir, args, **kwargs)

    def compile(self, basedir: str, **kwargs: object) -> CommandResult:
        return self._run_command("compile", basedir, (), **kwargs)

    def add_from(self, basedir: str, **kwargs: object) -> CommandResult:
        """Add ``from`` clauses to bare ``call`` statements (edits scripts!)."""
        return self._run_command("add_from", basedir, (), **kwargs)

    def info(self) -> str:
        return f"Ren'Py SDK launcher: {self.executable}"

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.info()
