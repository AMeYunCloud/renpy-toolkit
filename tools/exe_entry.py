"""PyInstaller 入口：双击打开窗口，带参数时按命令行执行。

为什么需要这个文件：包自带的 `renpy-toolkit.py` 靠 ``sys.path`` 找到同级的
``renpy_toolkit`` 包，冻结之后那套路径推理全部失效。入口脚本放在构建时能
被 PyInstaller 看到的顶层位置，只做一件小事——决定"起窗口还是跑命令"。

窗口模式下 Windows 不分配控制台，``sys.stdout`` 是 ``None``；此时任何
``print`` 都会抛异常。所以先把空流接到同目录的日志文件，再往下走。
"""

from __future__ import annotations

import os
import sys

LOG_NAME = "renpy-toolkit-cli.log"


def _ensure_streams():
    """窗口模式下把 stdout/stderr 接到文件，返回需要关闭的句柄（或 None）。

    同时把三个流的编码钉成 UTF-8：冻结后默认编码跟随系统区域设置（中文
    Windows 上是 cp936），一旦输出被重定向到管道或日志就会变成乱码，而
    中文是这个工具的全部界面语言。
    """
    handle = None
    if sys.stdout is None or sys.stderr is None:
        try:
            base = os.path.dirname(os.path.abspath(sys.executable))
        except Exception:
            base = os.getcwd()
        handle = open(os.path.join(base, LOG_NAME), "w", encoding="utf-8", errors="replace")
        sys.stdout = handle
        sys.stderr = handle
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    return handle


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _install_crash_hook()

    # 显式 `--cli` 与任何看起来像选项的参数都按命令行处理：这样同一个 exe
    # 既能双击用，也能被脚本调用，冻结版的 --selftest 还能被构建脚本验收。
    # `--ui` 是反向开关：强制走窗口，余下参数交给 gui.main（验收用的
    # `--ui --port 8731 --no-browser`）。
    use_gui = bool(argv) and argv[0] in ("--ui", "/ui")
    use_cli = bool(argv) and not use_gui and (
        argv[0] in ("--cli", "/cli") or argv[0].startswith("-")
    )
    if use_gui:
        argv = argv[1:]
    if use_cli:
        if argv[0] in ("--cli", "/cli"):
            argv = argv[1:]
        handle = _ensure_streams()
        try:
            from renpy_toolkit import cli

            return cli.main(argv)
        finally:
            if handle is not None:
                handle.close()

    try:
        import gui
    except Exception as exc:  # 极端环境：gui 模块都导入不了（缺失文件、权限）
        handle = _ensure_streams()
        try:
            sys.stderr.write(
                "无法启动窗口：%r\n请改用命令行：renpy-toolkit.exe --cli --help\n" % (exc,)
            )
        finally:
            if handle is not None:
                handle.close()
        return 3

    handle = _ensure_streams()
    try:
        return gui.main(argv)
    finally:
        if handle is not None:
            handle.close()


def _install_crash_hook() -> None:
    """把未捕获异常写进同目录日志。

    窗口模式没有控制台，PyInstaller 只会弹一个"Failed to execute script"，
    内容全丢。用户既看不懂也没法反馈，所以我们自己落一份可读的 traceback。
    """

    def hook(exc_type, exc, tb):
        import traceback

        try:
            base = os.path.dirname(os.path.abspath(sys.executable))
        except Exception:
            base = os.getcwd()
        path = os.path.join(base, "renpy-toolkit-窗口错误.log")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("Ren'Py 汉化工具包 启动失败\n\n")
                traceback.print_exception(exc_type, exc, tb, file=fh)
        except OSError:
            pass

    sys.excepthook = hook


if __name__ == "__main__":
    sys.exit(main())
