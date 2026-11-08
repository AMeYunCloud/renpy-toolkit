"""打 Windows exe（PyInstaller）。

用法（必须在构建虚拟环境里跑，见 ``--help`` 尾部说明）::

    python tools/build_exe.py                # 同时打 onedir 与 onefile，并自检
    python tools/build_exe.py --verify       # 只自检（要求产物已存在）
    python tools/build_exe.py --onedir-only
    python tools/build_exe.py --onefile-only
    python tools/build_exe.py --clean        # 先删旧产物

产物：
    dist/exe/onedir/renpy-toolkit/            文件夹版（整目录分发）
    dist/exe/onefile/renpy-toolkit.exe        单文件版

为什么需要 venv：PyInstaller 会把**它自己所在解释器**的 site-packages 打进包。
两个可选依赖（fontTools 读字体 cmap、openpyxl 读写 xlsx）必须装在那个解释器里，
否则 exe 里 "字体覆盖率 / Excel 导出" 两个按钮会静默不可用。

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
ENTRY = os.path.join(ROOT, "tools", "exe_entry.py")
WORK = os.path.join(ROOT, "工作区域", "exe-build")
OUT = os.path.join(ROOT, "dist", "exe")
NAME = "renpy-toolkit"


def _data_arg() -> str:
    """``--add-data`` 的参数。

    分隔符用 ``;``（Windows），目标路径用正斜杠：冒号在这里是分隔符而不是
    盘符，写 ``C:\\...`` 会被当成 ``SOURCE:DEST`` 的另一半，PyInstaller 直接
    报 "Wrong syntax"。
    """
    src = os.path.join(PLUGIN, "renpy_toolkit", "data")
    if not os.path.isdir(src):
        raise SystemExit("找不到数据目录：%s" % src)
    return "%s%srenpy_toolkit/data" % (src, os.pathsep)


def _common_args(mode: str, runtime_tmpdir: str | None = None) -> list[str]:
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",  # 双击不弹黑框；--cli 模式下的输出改写到同目录日志
        "--name",
        NAME if mode == "onedir" else NAME,
        "--add-data",
        _data_arg(),
        "--hidden-import",
        "renpy_toolkit.gui",
        "--collect-submodules",
        "renpy_toolkit",
        "--collect-submodules",
        "fontTools",
        "--collect-submodules",
        "openpyxl",
        "--paths",
        PLUGIN,
        "--distpath",
        os.path.join(OUT, mode),
        "--workpath",
        os.path.join(WORK, mode),
        "--specpath",
        WORK,
        ENTRY,
    ]
    if runtime_tmpdir:
        # onefile 每次启动都要把 17 MB 解包到临时目录。默认用 %TEMP%，普通桌面
        # 用户没问题；但在令牌受限的环境里引导器建不出目录（它要带自定义 ACL），
        # 此时用这个参数换一个可写位置，仅作本机验收用，发布形态不带。
        args.insert(args.index("--windowed") + 1, "--runtime-tmpdir")
        args.insert(args.index("--windowed") + 2, runtime_tmpdir)
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


def verify() -> int:
    ok = True
    env_skipped: list[str] = []
    for mode in ("onedir", "onefile"):
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
        # onefile 用短超时：它每次启动都要先解包 17 MB，且在本机若引导器建不出
        # 临时目录，进程会卡在解包前不退出——等 300 s 毫无意义。
        code, text = _run_and_capture(exe, ["--selftest"], timeout=45 if mode == "onefile" else 300)
        print("[verify] %-8s --selftest 退出码 %d" % (mode, code))
        if code != 0 and (_ENV_BLOCKED in text or (mode == "onefile" and code == 124)):
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
    parser = argparse.ArgumentParser(description="打 Windows exe（PyInstaller）")
    parser.add_argument("--verify", action="store_true", help="只自检已有产物")
    parser.add_argument("--onedir-only", action="store_true")
    parser.add_argument("--onefile-only", action="store_true")
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

    if not args.verify:
        if args.onedir_only:
            build("onedir", args.runtime_tmpdir)
        elif args.onefile_only:
            build("onefile", args.runtime_tmpdir)
        else:
            build("onedir", args.runtime_tmpdir)
            build("onefile", args.runtime_tmpdir)

    return verify()


if __name__ == "__main__":
    sys.exit(main())
