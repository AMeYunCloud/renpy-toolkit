"""PyInstaller 入口：双击打开窗口，带参数时按命令行执行。

**（已停止维护）** 属于已停止维护的 exe 发行线，只为参考与复现保留；
日常使用请走插件（pip / 便携 zip）或 agent skill，两条线都只用命令行。

为什么需要这个文件：包自带的 `renpy-toolkit.py` 靠 ``sys.path`` 找到同级的
``renpy_toolkit`` 包，冻结之后那套路径推理全部失效。入口脚本放在构建时能
被 PyInstaller 看到的顶层位置，只做一件小事——决定"起窗口还是跑命令"。

窗口模式下 Windows 不分配控制台，``sys.stdout`` 是 ``None``；此时任何
``print`` 都会抛异常。所以先把空流接到同目录的日志文件，再往下走。
"""

from __future__ import annotations

import os
import sys

DATA_DIR_NAME = "renpy-toolkit-数据"
LOG_NAME = "renpy-toolkit-cli.log"


def _log_dir() -> str:
    """所有输出都收进 exe 旁边的数据目录，保持 exe 所在文件夹整洁。"""
    try:
        base = os.path.dirname(os.path.abspath(sys.executable))
    except Exception:
        base = os.getcwd()
    path = os.path.join(base, DATA_DIR_NAME, "日志")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return base
    return path


def _ensure_streams():
    """窗口模式下把 stdout/stderr 接到文件，返回需要关闭的句柄（或 None）。

    同时把三个流的编码钉成 UTF-8：冻结后默认编码跟随系统区域设置（中文
    Windows 上是 cp936），一旦输出被重定向到管道或日志就会变成乱码，而
    中文是这个工具的全部界面语言。
    """
    handle = None
    if sys.stdout is None or sys.stderr is None:
        handle = open(os.path.join(_log_dir(), LOG_NAME), "w", encoding="utf-8", errors="replace")
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

        path = os.path.join(_log_dir(), "renpy-toolkit-窗口错误.log")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("Ren'Py 汉化工具包 启动失败\n\n")
                traceback.print_exception(exc_type, exc, tb, file=fh)
        except OSError:
            pass

    sys.excepthook = hook


def _test_open(url: str) -> int:
    """诊断用：逐个尝试打开 URL 的方式，打印各自结果（写到数据目录日志）。"""
    import ctypes
    import subprocess
    import time

    lines = []

    def log(msg: str) -> None:
        lines.append(msg)

    log("url = %s" % url)
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "open", url, None, None, 1)
        log("ShellExecuteW -> %s" % rc)
    except Exception as exc:  # noqa: BLE001
        log("ShellExecuteW raised %r" % exc)
    time.sleep(6)
    try:
        proc = subprocess.Popen(
            ["cmd", "/c", "start", "", url],
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log("cmd start(no window) rc=%s" % proc.wait(timeout=15))
    except Exception as exc:  # noqa: BLE001
        log("cmd start raised %r" % exc)
    time.sleep(6)
    try:
        proc = subprocess.Popen(["cmd", "/c", "start", "", url])
        log("cmd start(inherit) rc=%s" % proc.wait(timeout=15))
    except Exception as exc:  # noqa: BLE001
        log("cmd start(inherit) raised %r" % exc)
    time.sleep(6)
    try:
        import webbrowser

        log("webbrowser -> %s" % webbrowser.open(url, new=2))
    except Exception as exc:  # noqa: BLE001
        log("webbrowser raised %r" % exc)
    time.sleep(6)
    path = os.path.join(_log_dir(), "打开浏览器-诊断.log")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    sys.stderr.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test-open":
        _ensure_streams()
        sys.exit(_test_open(sys.argv[2]))
    sys.exit(main())
