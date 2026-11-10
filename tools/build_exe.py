"""打 Windows exe（PyInstaller）。

**（已停止维护）** exe 发行线已停止维护：这个脚本只为参考与复现保留，
不再随新版本更新，也不保证与当前插件版本兼容。日常使用请走插件与 skill。

用法（必须在构建虚拟环境里跑，见 ``--help`` 尾部说明）::

    python tools/build_exe.py               # 默认只打 onefile，并自检
    python tools/build_exe.py --both        # onedir + onefile 都打
    python tools/build_exe.py --verify      # 只自检（要求产物已存在）
    python tools/build_exe.py --onedir-only
    python tools/build_exe.py --clean       # 先删旧产物

产物：
    dist/exe/onefile/renpy-toolkit.exe        单文件版（发布形态）
    dist/exe/onedir/renpy-toolkit/            文件夹版（仅调试用，不发布）

为什么发单文件：文件夹版有一千多个碎片文件，用户拷来拷去容易漏；单文件只有一个
exe，双击即用。代价是每次启动要把包解到临时目录（约 1–3 秒）。

为什么需要 venv：PyInstaller 会把**它自己所在解释器**的 site-packages 打进包。
两个依赖必须装在那个解释器里，否则对应按钮在 exe 里会静默不可用：
``fontTools``（字体覆盖率）、``openpyxl``（Excel 导出）。界面用系统浏览器打开
本地服务，不需要 pywebview 之类的原生依赖。

本机既定位置：E:\\Programs\\renpy-toolkit-build\\venv
    uv venv E:\\Programs\\renpy-toolkit-build\\venv --python 3.12
    uv pip install --python <venv>\\Scripts\\python.exe pyinstaller openpyxl fonttools
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugin", "dsh-renpy-toolkit")
EXE_DIR = os.path.join(ROOT, "exe")
ENTRY = os.path.join(ROOT, "tools", "exe_entry.py")
WORK = os.path.join(ROOT, "工作区域", "exe-build")
OUT = os.path.join(ROOT, "dist", "exe")
NAME = "renpy-toolkit"


def _data_arg() -> str:
    """``--add-data`` 的参数（包内数据：内置 UI 术语表）。

    分隔符用 ``;``（Windows），目标路径用正斜杠：冒号在这里是分隔符而不是
    盘符，写 ``C:\\...`` 会被当成 ``SOURCE:DEST`` 的另一半，PyInstaller 直接
    报 "Wrong syntax"。
    """
    src = os.path.join(PLUGIN, "renpy_toolkit", "data")
    if not os.path.isdir(src):
        raise SystemExit("找不到数据目录：%s" % src)
    return "%s%srenpy_toolkit/data" % (src, os.pathsep)


def _ui_data_arg() -> str:
    """界面 HTML 的 ``--add-data`` 参数。

    界面源码在仓库顶层 ``exe/ui/``，不属于 ``renpy_toolkit`` 包——插件与 skill
    只用命令行，不该把图形界面代码一起分发。冻结时落到 ``<_MEIPASS>/ui/``，
    对应 ``gui.html_path()`` 的 ``_MEIPASS`` 分支。
    """
    src = os.path.join(EXE_DIR, "ui")
    if not os.path.isdir(src):
        raise SystemExit("找不到界面目录：%s" % src)
    return "%s%sui" % (src, os.pathsep)


def _optional_flags() -> list[str]:
    """按"装了的才打进去"追加依赖相关的收集参数。

    界面走的是"本地 HTTP + 系统浏览器 app 窗口"，不依赖 pywebview / pythonnet，
    所以这里只需要确认两个数据处理库在不在——缺了就在构建时提醒，免得用户
    双击 exe 之后才发现那两个按钮不能用。
    """
    flags: list[str] = []
    for mod in ("fontTools", "openpyxl"):
        try:
            __import__(mod)
        except Exception:
            print("[warn] 当前解释器没有 %s：对应功能在 exe 里不可用" % mod)
    return flags


def _common_args(mode: str, runtime_tmpdir: str | None = None) -> list[str]:
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",  # 双击不弹黑框；--cli 模式下的输出改写到同目录日志
        "--name",
        NAME,
        "--add-data",
        _data_arg(),
        "--add-data",
        _ui_data_arg(),
        "--hidden-import",
        "gui",
        "--collect-submodules",
        "renpy_toolkit",
        "--collect-submodules",
        "fontTools",
        "--collect-submodules",
        "openpyxl",
        "--paths",
        PLUGIN,
        "--paths",
        EXE_DIR,
        "--distpath",
        os.path.join(OUT, mode),
        "--workpath",
        os.path.join(WORK, mode),
        "--specpath",
        WORK,
    ]
    args += _optional_flags()
    if runtime_tmpdir:
        # onefile 每次启动都要解包。默认用 %TEMP%，普通桌面用户没问题；但在
        # 令牌受限的环境里引导器建不出目录（它要带自定义 ACL），此时用这个
        # 参数换一个可写位置，仅作本机验收用，发布形态不带。
        args += ["--runtime-tmpdir", runtime_tmpdir]
    args.append(ENTRY)
    return args


def build(mode: str, runtime_tmpdir: str | None = None) -> None:
    args = _common_args(mode, runtime_tmpdir)
    if mode == "onefile":
        args.insert(args.index("--windowed") + 1, "--onefile")
    print("[build] %s" % mode)
    print("        " + " ".join(args))
    proc = subprocess.run(args, cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit("PyInstaller 失败（%s），退出码 %d" % (mode, proc.returncode))


def _exe_path(mode: str) -> str:
    if mode == "onedir":
        return os.path.join(OUT, "onedir", NAME, NAME + ".exe")
    return os.path.join(OUT, "onefile", NAME + ".exe")


def _run_and_capture(exe: str, args: list[str], timeout: int = 300) -> tuple[int, str]:
    """跑一次冻结 exe 并取输出。

    不用 ``subprocess.run(capture_output=True)``：PyInstaller 的 onefile/窗口版
    在 Windows 上会派生一个子进程，父进程退出后**管道句柄仍被子进程持有**，
    ``communicate()`` 会一直等 EOF 直到超时——表现就是"命令明明跑完了却永远
    不返回"。重定向到文件再读，绕开管道生命周期问题。
    """
    import tempfile

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.txt")
        err = os.path.join(tmp, "err.txt")
        with open(out, "w", encoding="utf-8") as fo, open(err, "w", encoding="utf-8") as fe:
            try:
                proc = subprocess.run(
                    [exe, *args],
                    stdout=fo,
                    stderr=fe,
                    stdin=subprocess.DEVNULL,
                    env=env,
                    timeout=timeout,
                )
                code = proc.returncode
            except subprocess.TimeoutExpired:
                return 124, "TIMEOUT after %ds" % timeout
        text = ""
        for path in (out, err):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text += fh.read()
            except OSError:
                pass
    return code, text


_ENV_BLOCKED = "Could not create temporary directory"


def verify(modes: tuple[str, ...] = ("onefile",)) -> int:
    ok = True
    env_skipped: list[str] = []
    for mode in modes:
        exe = _exe_path(mode)
        if not os.path.isfile(exe):
            print("[verify] %-8s 缺少产物：%s" % (mode, exe))
            ok = False
            continue
        size = os.path.getsize(exe)
        total = size
        if mode == "onedir":
            folder = os.path.dirname(exe)
            for base, _dirs, files in os.walk(folder):
                total += sum(os.path.getsize(os.path.join(base, f)) for f in files)
        print("[verify] %-8s %s  exe=%.1f MB  合计=%.1f MB"
              % (mode, exe, size / 1e6, total / 1e6))

        # 自检：冻结后包是否完整（模块数、RPA 往返、TL 解析、命令表）
        # onefile 用短超时：它每次启动都要先解包，且在本机若引导器建不出
        # 临时目录，进程会卡在解包前不退出——等 300 s 毫无意义。
        code, text = _run_and_capture(exe, ["--selftest"], timeout=45 if mode == "onefile" else 300)
        print("[verify] %-8s --selftest 退出码 %d" % (mode, code))
        # onefile 引导器要建一个带自定义安全描述符的临时目录，令牌受限时会被拒。
        # 三种表现都算环境受限：打印了那句错误、超时 124、或进程在 Python 起来
        # 之前就死掉（退出码为负 / 0xFFFFFFFF 且没有任何输出——包体有问题时会
        # 留下 traceback 或至少半行自检文本）。
        env_blocked = _ENV_BLOCKED in text
        if mode == "onefile" and not env_blocked:
            env_blocked = code in (124,) or (code != 0 and not text.strip())
        if code != 0 and env_blocked:
            # onefile 引导器要建一个带自定义安全描述符的临时目录；令牌受限时会
            # 被拒绝（普通桌面用户不会）。这是环境问题，不是包体问题：
            # 用 --runtime-tmpdir 指向可写目录重打同一份代码即可跑通。
            print("[verify] %-8s 环境受限：引导器无法创建临时目录"
                  "（本次会话令牌不允许带自定义 ACL 的目录）。"
                  "请用 --runtime-tmpdir <可写目录> 重建后再验，或到普通桌面环境验证。"
                  % mode)
            env_skipped.append(mode)
            continue
        if code != 0:
            ok = False
            print(text)
        elif "self-check passed" not in text:
            ok = False
            print("[verify] %-8s 自检没有打印 self-check passed：\n%s" % (mode, text))

        # 命令行那一半也得能跑（不是窗口版就废掉 CLI）
        code, _text = _run_and_capture(exe, ["--cli", "--version"], timeout=120)
        print("[verify] %-8s --cli --version 退出码 %d" % (mode, code))
        if code != 0:
            ok = False
    if env_skipped:
        print("[verify] 环境跳过：%s（包体未判失败）" % ", ".join(env_skipped))
    print("[verify] %s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="打 Windows exe（PyInstaller）",
        epilog="必须在装了 pyinstaller/openpyxl/fonttools 的构建虚拟环境里跑：\n"
               "  E:\\Programs\\renpy-toolkit-build\\venv\\Scripts\\python.exe tools\\build_exe.py",
    )
    parser.add_argument("--verify", action="store_true", help="只自检已有产物")
    parser.add_argument("--both", action="store_true", help="onedir 与 onefile 都打")
    parser.add_argument("--onedir-only", action="store_true")
    parser.add_argument("--onefile-only", action="store_true", help="（默认就是它）")
    parser.add_argument("--clean", action="store_true", help="先删 dist/exe 与工作目录")
    parser.add_argument("--verbose", action="store_true", help="打印随附的诊断信息")
    parser.add_argument("--runtime-tmpdir", metavar="DIR", default=None,
                        help="onefile 解包目录（默认 %%TEMP%%）。仅在本机令牌受限、"
                             "引导器建不出临时目录时用来验收，发布形态不要带")
    args = parser.parse_args(argv)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("error: 当前解释器没有 PyInstaller。请用构建虚拟环境：")
        print("  E:\\Programs\\renpy-toolkit-build\\venv\\Scripts\\python.exe tools\\build_exe.py")
        return 3

    if args.verbose:
        import PyInstaller

        print("[env] python   = %s" % sys.executable)
        print("[env] pyinstaller = %s" % PyInstaller.__version__)
        for name in ("fontTools", "openpyxl"):
            try:
                mod = __import__(name)
                print("[env] %s = %s" % (name, getattr(mod, "__version__", "?")))
            except Exception as exc:
                print("[env] %s 缺失（%s）→ 该功能在 exe 里不可用" % (name, exc))

    if args.clean:
        for path in (OUT, WORK):
            if os.path.isdir(path):
                print("[clean] %s" % path)
                shutil.rmtree(path)

    modes: tuple[str, ...] = ("onefile",)
    if not args.verify:
        if args.onedir_only:
            modes = ("onedir",)
        elif args.both:
            modes = ("onedir", "onefile")
        for mode in modes:
            build(mode, args.runtime_tmpdir)

    return verify(modes if not args.verify else ("onefile",))


if __name__ == "__main__":
    sys.exit(main())
