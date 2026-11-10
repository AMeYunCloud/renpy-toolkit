"""图形界面（exe 分发形态专用）：本地 HTTP + 浏览器 app 窗口。

**（已停止维护）** 这条 exe 发行线已停止维护：不再随新版本发布、不再接受修复。
保留代码只为参考；新功能与修复只进插件与 skill 两条线。想用界面请自行构建，
产物未经当前版本验证。

本文件属于**仓库顶层 `exe/`**，不随 `renpy_toolkit` 包分发——插件与 skill
都只用命令行，从不 import 它。界面是一份本地 HTML（同目录 `ui/index.html`），
由本进程内的 HTTP 服务提供，再用系统的 Edge/Chrome 以 ``--app`` 模式开一个
无地址栏窗口。这样界面能做成网页的样子，而**不需要把 pythonnet / WebView2
绑进 exe**——那套东西在 PyInstaller 单文件包里会因为 CLR 程序集解析失败而
打不开窗口。

所有操作仍然只转成对 :func:`renpy_toolkit.cli.main` 的调用，界面与命令行
行为永远一致（含 ``--write`` 语义、备份模式、退出码）。
"""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field

from renpy_toolkit import engine

CONFIG_NAME = "config.json"
DEFAULT_LANGUAGE = "chinese"
BACKUP_MODES = ("bak", "git", "none")
# 除 TL 注入（写进游戏 game/tl/）外，其余产物一律集中到这个目录。
OUTPUT_DIR_NAME = "renpy-toolkit-数据"
# 一级子目录里出现这些名字就不当游戏候选：它们是工具自己的产物目录。
_SKIP_DIRS = frozenset(
    {"工作区域", "可清理文件", "成品提交", "node_modules", "__pycache__", OUTPUT_DIR_NAME}
)
# 退出码 → 人话。命令行只给数字，界面要给用户能读懂的原因。
EXIT_HINTS = {
    0: "成功",
    1: "有检查项未通过，看日志里的明细",
    2: "命令用法有误（属于工具内部问题，请反馈）",
    3: "环境不具备：缺 git，或缺能运行游戏的 Ren'Py 运行时",
}


# ---------------------------------------------------------------------------
# 纯逻辑（无界面依赖，便于单元测试）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Game:
    """一个可被处理的 Ren'Py 游戏。

    ``root`` 是含 ``game/`` 的那个目录——也就是命令行里 ``tl gen`` 要的
    ``basedir``，不是 ``game/`` 本身。
    """

    root: str
    name: str
    launcher: str | None = None

    @property
    def game_dir(self) -> str:
        return os.path.join(self.root, "game")

    @property
    def tl_root(self) -> str:
        return os.path.join(self.game_dir, "tl")

    def tl_dir(self, language: str) -> str:
        return os.path.join(self.tl_root, language)

    def describe(self) -> str:
        bit = os.path.basename(self.launcher) if self.launcher else "无启动脚本"
        return f"{self.name}    [{self.root}]    {bit}"


def find_launcher(root: str) -> str | None:
    """找启动脚本：优先 ``.py``（能确定用哪个解释器跑探针），否则 ``.exe``。"""
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return None
    pys = [
        n
        for n in entries
        if n.lower().endswith(".py")
        and n.lower() not in ("setup.py", "conftest.py")
        and os.path.isfile(os.path.join(root, n))
    ]
    if pys:
        return os.path.join(root, pys[0])
    exes = [
        n for n in entries if n.lower().endswith(".exe") and os.path.isfile(os.path.join(root, n))
    ]
    return os.path.join(root, exes[0]) if exes else None


def has_game_interpreter(root: str) -> bool:
    """``lib/py3-*/python.exe``（Windows）或 ``lib/py3-*/bin/python3``。"""
    lib = os.path.join(root, "lib")
    if not os.path.isdir(lib):
        return False
    try:
        subs = os.listdir(lib)
    except OSError:
        return False
    for name in subs:
        if not name.lower().startswith("py3-"):
            continue
        base = os.path.join(lib, name)
        for rel in ("python.exe", os.path.join("bin", "python3")):
            if os.path.isfile(os.path.join(base, rel)):
                return True
    return False


_LAST_REJECTED: list[dict] = []


def looks_like_game(path: str) -> bool:
    """判定标准：**必须是 Ren'Py 引擎的游戏**，光有 ``game/`` 目录不算。

    证据链见 :mod:`engine`：``RENPY RPC2`` 编译脚本 / ``RPA-3.0`` 归档 /
    ``renpy/`` 运行时 / ``lib/py3-*`` 自带解释器 / 启动脚本引用 renpy /
    ``game/`` 里的 Ren'Py 源码结构。任一条命中才算。

    这条检查是硬性的：本工具每一步都假设目标是 Ren'Py，对着别的目录跑会报一堆
    看不懂的错，甚至把对方的 ``game/`` 当成 TL 输出目标。
    """
    return engine.detect(path).is_renpy


def discover_games(base: str, depth: int = 1) -> list[Game]:
    """扫 ``base`` 自身与其一级子目录（``depth=1``）。

    不递归到底：用户把一键包丢在游戏目录旁边是常态，全盘递归既慢又容易
    把别人的工程卷进来。扫到的每个目录都独立成一个候选，互不影响。
    """
    found: list[Game] = []
    rejected: list[dict] = []

    def add(path: str, name: str) -> None:
        if not os.path.isdir(os.path.join(path, "game")):
            # 候选必须是「含 game/ 的目录」；game/ 本身不是候选
            return
        found_det = engine.detect(path)
        if found_det.is_renpy:
            found.append(Game(root=os.path.abspath(path), name=name, launcher=find_launcher(path)))
        elif os.path.isdir(os.path.join(path, "game")):
            # 有 game/ 但不是 Ren'Py：记下来，界面上把原因告诉用户
            rejected.append({"name": name, "path": os.path.abspath(path), "reason": found_det.summary()})

    add(base, os.path.basename(os.path.abspath(base)) or base)
    if depth <= 0:
        return found
    try:
        subs = sorted(os.listdir(base))
    except OSError:
        return found
    for name in subs:
        if name.startswith(".") or name in _SKIP_DIRS:
            continue
        path = os.path.join(base, name)
        if os.path.isdir(path):
            add(path, name)
    global _LAST_REJECTED
    _LAST_REJECTED = rejected
    return found


def config_path(base: str) -> str:
    """配置也放数据目录里（旧版本写在 exe 旁边，读的时候两处都认）。"""
    return os.path.join(base, OUTPUT_DIR_NAME, CONFIG_NAME)


def load_config(base: str) -> dict:
    """读配置；坏文件/不存在都退回空字典，绝不让界面起不来。

    先读数据目录里的，再退回旧版本写在 exe 旁边的 `config.json`。
    """
    for path in (config_path(base), os.path.join(base, CONFIG_NAME)):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def save_config(base: str, data: dict) -> str | None:
    """写配置，返回 None 表示成功，否则返回错误说明。

    exe 放在只读目录（Program Files、光盘）时写不进去属于正常情况：
    记忆是便利功能，失败不该打断主流程。
    """
    try:
        os.makedirs(os.path.dirname(config_path(base)), exist_ok=True)
        with open(config_path(base), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
    except OSError as exc:
        return str(exc)
    return None


@dataclass
class Ctx:
    """一次操作要用到的全部路径。界面选完游戏后填它，后面所有按钮共用。"""

    root: str
    language: str = DEFAULT_LANGUAGE
    xlsx: str | None = None
    reference: str | None = None
    dest: str | None = None
    fonts: tuple[str, ...] = field(default_factory=tuple)
    outdir: str | None = None

    @property
    def game_dir(self) -> str:
        return os.path.join(self.root, "game")

    @property
    def tl_dir(self) -> str:
        return os.path.join(self.game_dir, "tl", self.language)

    @property
    def output_root(self) -> str:
        return os.path.abspath(self.outdir or OUTPUT_DIR_NAME)

    @property
    def export_dir(self) -> str:
        return os.path.join(self.output_root, "导出")

    @property
    def decompile_dir(self) -> str:
        return os.path.join(self.output_root, "反编译")

    @property
    def scratch_dir(self) -> str:
        return os.path.join(self.output_root, "临时")

    @property
    def backup_dir(self) -> str:
        return os.path.join(self.output_root, "备份")

    @property
    def log_dir(self) -> str:
        return os.path.join(self.output_root, "日志")


FONT_SUFFIXES = (".ttf", ".otf", ".ttc")


def find_fonts(root: str, limit: int = 6) -> list[str]:
    """列出游戏目录下的字体文件。

    `font check` 至少需要一个字体路径；界面上让用户去填路径太繁琐，
    所以默认从 `game/` 里挑前几个字体做覆盖率检查。
    """
    game = os.path.join(root, "game")
    base = game if os.path.isdir(game) else root
    found: list[str] = []
    for cur, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in ("cache", "saves", "__pycache__")]
        for name in files:
            if name.lower().endswith(FONT_SUFFIXES):
                found.append(os.path.join(cur, name))
    found.sort()
    return found[:limit]


def build_action(key: str, ctx: Ctx) -> tuple[list[str] | None, list[list[str]]]:
    """把界面动作翻译成命令行参数。

    返回 ``(预览参数, [写盘参数...])``：预览为 ``None`` 表示没有 dry-run 形态；
    写盘为空列表表示只读动作。抽成纯函数是为了让"点一下跑了哪条命令"可被单测。
    """
    root, lang, tl = ctx.root, ctx.language, ctx.tl_dir
    if key == "audit":
        return ["project", "audit", root], []
    if key == "gen":
        return (
            ["tl", "gen", root, lang, "--outdir", tl],
            [["tl", "gen", root, lang, "--outdir", tl, "--write", "--clean-scratch"]],
        )
    if key == "verify":
        return ["tl", "verify", tl, "--max-items", "300"], []
    if key == "table":
        # 导出固定落到数据目录的“导出”子目录；回填时再选文件（可用「用最新导出的」）。
        out = os.path.join(ctx.export_dir, "%s.xlsx" % lang)
        return None, [["tl", "table", tl, "-o", out, "--format", "xlsx", "--language", lang]]
    if key == "apply":
        if not ctx.xlsx:
            raise FileNotFoundError("还没有选择要回填的 Excel：%s" % ctx.export_dir)
        return (
            ["tl", "apply", tl, ctx.xlsx],
            [["tl", "apply", tl, ctx.xlsx, "--write"]],
        )
    if key == "port":
        if not ctx.reference:
            raise FileNotFoundError("还没有选择旧译本目录：%s" % ctx.root)
        return (
            ["tl", "port", tl, ctx.reference],
            [["tl", "port", tl, ctx.reference, "--write", "--show-skipped"]],
        )
    if key == "glossary":
        return ["tl", "glossary", "check", tl, "--ui-terms"], []
    if key == "hardcoded":
        # 先建清单再打标记。清单从已生成的 TL 反推（--from-tl），
        # 因为 tl gen 的探针 dump 落在临时目录里、跑完就清掉了。
        return (
            ["tl", "hardcoded", "scan", tl, "--language", lang, "--from-tl"],
            [
                ["tl", "hardcoded", "scan", tl, "--language", lang, "--from-tl", "--write"],
                ["tl", "hardcoded", "apply", tl, "--language", lang, "--write"],
            ],
        )
    if key == "backup_list":
        return ["tl", "backup", "list", tl], []
    if key == "backup_save":
        return None, [["tl", "backup", "save", tl, "--message", "GUI 手动快照", "--write"]]
    if key == "font_check":
        # font check 要求至少一个字体位置参数；用户没指定时从游戏目录里找。
        fonts = list(ctx.fonts) or find_fonts(root)
        if not fonts:
            raise FileNotFoundError("没有找到字体文件（.ttf/.otf/.ttc）：%s" % ctx.game_dir)
        return ["font", "check", "--tl", tl, *fonts], []
    if key == "font_map":
        return ["font", "map", root, "--emit", lang], []
    if key == "rpa_list":
        return ["rpa", "list", ctx.game_dir], []
    if key == "decompile":
        dest = ctx.dest or ctx.decompile_dir
        return (
            ["decompile", "dir", ctx.game_dir, "--dest", dest],
            [["decompile", "dir", ctx.game_dir, "--dest", dest, "--write"]],
        )
    raise KeyError("unknown action: %s" % key)


# 动作元数据：界面按 group 分栏，顺序即按钮顺序。
ACTION_SPECS: tuple[dict, ...] = (
    {"key": "audit", "group": "准备", "label": "环境体检", "hint": "工程风险、重复译文、归档陷阱（只读）"},
    {"key": "gen", "group": "准备", "label": "生成汉化骨架", "hint": "跑游戏本体，把全部对白与界面文本抓成 tl 骨架"},
    {"key": "port", "group": "准备", "label": "搬运旧译文", "hint": "游戏更新后，按标识符把旧译本搬到新骨架"},
    {"key": "table", "group": "翻译", "label": "导出 Excel 表格", "hint": "导出一份可发给译者的 xlsx（存到数据目录）"},
    {"key": "apply", "group": "翻译", "label": "回填 Excel 译文", "hint": "把填好的 xlsx 写回 tl 文件"},
    {"key": "verify", "group": "检查", "label": "校验译文", "hint": "覆盖率、错误与警告清单（只读）"},
    {"key": "glossary", "group": "检查", "label": "术语一致性", "hint": "对照术语表检查译名是否统一（只读）"},
    {"key": "hardcoded", "group": "检查", "label": "硬编码文本", "hint": "扫描未包 _() 的界面文字并打标记"},
    {"key": "font_check", "group": "工具", "label": "字体覆盖率", "hint": "检查字体能否显示全部译文汉字"},
    {"key": "font_map", "group": "工具", "label": "字体替换模板", "hint": "生成 translate python 块，替换硬编码字体"},
    {"key": "backup_list", "group": "工具", "label": "查看备份", "hint": "列出译文快照（只读）"},
    {"key": "backup_save", "group": "工具", "label": "立即备份", "hint": "给当前所有 .rpy 拍一个快照"},
    {"key": "rpa_list", "group": "资源", "label": "查看归档内容", "hint": "列出 .rpa 里的条目（只读）"},
    {"key": "decompile", "group": "资源", "label": "反编译 .rpyc", "hint": "没有源码时从 .rpyc 还原 .rpy"},
)


def actions_by_group() -> list[dict]:
    """按 ``group`` 聚成 ``[{group, actions:[spec...]}, ...]``，保持声明顺序。"""
    order: list[str] = []
    buckets: dict[str, list[dict]] = {}
    for spec in ACTION_SPECS:
        if spec["group"] not in buckets:
            order.append(spec["group"])
            buckets[spec["group"]] = []
        buckets[spec["group"]].append(spec)
    return [{"group": g, "actions": buckets[g]} for g in order]


def explain_error(exc: BaseException) -> tuple[str, str]:
    """把异常翻成（一句话原因，一行怎么办）。

    工具自己的异常消息已经是中文、已经写清是哪条路径/哪个文件，直接透出即可；
    这里只补"接下来该做什么"，因为用户看到的往往只有这一行。
    """
    name = type(exc).__name__
    text = str(exc) or name
    # 按消息特征给出可执行的下一步。顺序有意义：越具体的越靠前。
    for needle, hint in _HINT_RULES:
        if needle in text:
            return text, hint
    if isinstance(exc, FileNotFoundError):
        return "找不到文件：%s" % (getattr(exc, "filename", None) or text), "确认这个路径还在，或重新扫描游戏。"
    if isinstance(exc, PermissionError):
        return "没有权限：%s" % (getattr(exc, "filename", None) or text), (
            "把这个一键包放到可写目录（例如桌面或游戏所在盘），或关掉正在占用该文件的程序。"
        )
    if isinstance(exc, OSError):
        return "%s：%s" % (name, text), "检查磁盘空间、路径长度与文件占用。"
    return "%s：%s" % (name, text), ""


# 消息特征 → 下一步。写成表而不是一串 if，是因为这些文案会随实测不断加。
_HINT_RULES: tuple[tuple[str, str], ...] = (
    ("还没有选择", "先在「参数」里选好要用的 Excel 或旧译本目录，再点这个按钮。"),
    ("没有找到字体文件", "把 .ttf/.otf/.ttc 放进游戏的 game/ 目录，或在「字体」里填上字体路径。"),
    ("找不到可用的 git", "装 Git 并把 git.exe 加进 PATH；或者把备份方式改成 bak。"),
    ("没有找到 .rpa", "把目标指到含 game\\ 子目录的游戏根目录，或直接指某一个 .rpa 文件。"),
    ("Several languages", "在「目标语言」里填具体语言名（例如 chinese）再试一次。"),
    ("没有启动脚本", "这个目录没有 <名字>.py 启动器，多半是母工程或测试副本；换正式发行版，或用「环境体检」先看它能做什么。"),
    ("does not exist", "确认路径存在，或先点「重新扫描」。"),
    ("No such file", "确认路径存在，或先点「重新扫描」。"),
)


# 退出码 1 里有几种其实不是"检查没过"，而是"没东西可处理"。
_INFORMATIONAL: tuple[str, ...] = (
    "没有找到 .rpa",
    "nothing to emit",
)


def _informational(text: str) -> bool:
    """命令以 1 退出但语义只是"没有可处理的内容"。"""
    body = text or ""
    return any(needle in body for needle in _INFORMATIONAL)


def output_dirs(ctx: Ctx) -> list[str]:
    """需要预先建出来的产物目录（不含 TL 注入目录）。"""
    return [ctx.export_dir, ctx.decompile_dir, ctx.scratch_dir, ctx.backup_dir, ctx.log_dir]


# ---------------------------------------------------------------------------
# 界面：本地 HTTP 服务 + 浏览器 app 窗口
# ---------------------------------------------------------------------------
def base_dir() -> str:
    """配置与数据目录的落点：冻结后是 exe 所在目录，否则是当前目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.getcwd())


def html_path() -> str:
    """界面 HTML 的位置：冻结后取 PyInstaller 的解包目录，开发时取本文件旁的 ui/。"""
    packed = getattr(sys, "_MEIPASS", None)
    if packed:
        return os.path.join(packed, "ui", "index.html")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "index.html")


def data_dir() -> str:
    """exe 产生的所有东西都放这一个目录里，保持 exe 旁边整洁。"""
    return os.path.join(base_dir(), OUTPUT_DIR_NAME)


def log_dir() -> str:
    return os.path.join(data_dir(), "日志")


def crash_log_path() -> str:
    return os.path.join(log_dir(), "renpy-toolkit-窗口错误.log")


def _log_crash(exc: BaseException) -> str:
    """把启动阶段的异常写进日志文件，返回路径。

    窗口模式没有控制台，异常只会变成一个一闪就没的对话框，用户拿不到线索。
    """
    path = crash_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("Ren'Py 汉化工具包 启动失败\n\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=fh)
    except OSError:
        return ""
    return path


def startup_log_path() -> str:
    return os.path.join(log_dir(), "renpy-toolkit-启动.log")


def _slog(message: str) -> None:
    """启动过程留痕：窗口模式下用户看不到任何输出，出问题只能靠这个文件。"""
    path = startup_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except OSError:
        pass


def show_error(title: str, lines: list[str], *, seconds: int = 20) -> None:
    """把启动失败的原因显示出来（windowed 模式没有控制台）。"""
    for line in lines:
        _slog("ERROR %s" % line)
    text = "\n".join(lines)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(title, text, parent=root)
        root.destroy()
        return
    except Exception:
        pass
    # tkinter 用不了就退回倒计时窗（至少不是一闪就没的对话框）
    try:
        blocking_notice(title, lines, seconds=seconds, exit_code=4)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 启动前的三道闸：危险路径 / 单实例 / 就绪探测
# ---------------------------------------------------------------------------
def is_dangerous_root(path: str) -> bool:
    """``path`` 是不是盘符根（``C:\\`` / ``E:\\``）。

    只有「盘符 + 反斜杠」才算。放在根目录跑，产物会散在盘根、误操作面也最大，
    所以直接拒绝。UNC 共享（``\\\\server\\share``）不在此列 —— 它不是盘符根。
    """
    drive, tail = os.path.splitdrive(os.path.abspath(path))
    if len(drive) != 2 or drive[1] != ":":
        return False
    return tail in ("\\", "/", "")


def _acquire_single_instance(name: str = "renpy-toolkit-gui") -> bool:
    """Windows 命名互斥体做单实例。已有实例在跑则返回 ``False``。

    非 Windows（或 API 不可用）一律放行 —— 这里只解决「双击两次开出两个窗口、
    两个进程抢同一个数据目录」这一实际问题，不做安全边界。
    """
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, "Local\\" + name)
        if not handle:
            return True
        already = 183  # ERROR_ALREADY_EXISTS
        if kernel32.GetLastError() == already:
            return False
        # 句柄必须活到进程结束，否则互斥体被回收，单实例就失效了。
        globals()["_SINGLETON_MUTEX"] = handle
        return True
    except Exception:
        return True


def wait_until_listening(port: int, timeout: float = 60.0) -> bool:
    """等服务真的能接受连接了再开浏览器。

    单文件包每次启动都要把 17 MB 解到临时目录，首次还要过一遍杀毒扫描，这段
    时间里服务可能还没起来。浏览器先打开就会看到「Failed to fetch」——用户看到
    的「第一次启动失败、第二次才好」多半就是它。所以先在后台等端口通，再开窗口。
    """
    deadline = time.time() + timeout
    started = time.time()
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.3):
                _slog("服务就绪 port=%d（等待 %.2fs）" % (port, time.time() - started))
                return True
        except OSError:
            time.sleep(0.1)
    _slog("服务就绪超时 port=%d（等了 %.1fs）" % (port, timeout))
    return False


def _ascii_path(path: str) -> bool:
    return all(ord(ch) < 128 for ch in path)


def short_path(path: str) -> str:
    """把含中文的路径换成 Windows 8.3 短路径（纯 ASCII）。

    Edge 的 ``--user-data-dir`` 拿到非 ASCII 路径时会忽略它、退回用户默认
    profile，于是扩展页会跟着一起开。短路径是 ASCII，能避开这个问题。
    """
    if os.name != "nt" or _ascii_path(path):
        return path
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
        # 8.3 短名在某些卷上是关闭的，这时 API 会原样返回（含中文），
        # 那就没有意义了，直接退回原路径。
        if n and _ascii_path(buf.value):
            return buf.value
        return path
    except Exception:
        return path


def _mkdtemp(dir: str, seconds: float = 6.0) -> str:
    """建临时目录，带超时。建不出来返回空串（不抛）。"""
    done = threading.Event()
    out: list[str] = [""]

    def work() -> None:
        try:
            out[0] = tempfile.mkdtemp(prefix="browser-", dir=dir)
        except Exception:
            out[0] = ""
        finally:
            done.set()

    threading.Thread(target=work, daemon=True).start()
    done.wait(seconds)
    return out[0]


def _makedirs(path: str, seconds: float = 5.0) -> bool:
    """建目录，但最多等 ``seconds`` 秒。

    受限账户或安全软件下，往 C:\\Users\\... 建目录可能长时间卡住而不是报错，
    这里用线程兜住，避免整个程序没有任何反应。
    """
    done = threading.Event()
    result: list[bool] = [False]

    def work() -> None:
        try:
            os.makedirs(path, exist_ok=True)
            result[0] = True
        except OSError:
            result[0] = False
        finally:
            done.set()

    threading.Thread(target=work, daemon=True).start()
    return done.wait(seconds) and result[0]


def browser_profile_root(outdir: str) -> str:
    """挑一个 profile 父目录。

    优先用数据目录（就在 exe 旁边，必然可写）。只有它的路径含非 ASCII 时才
    尝试 %LOCALAPPDATA% 或 8.3 短路径 —— Edge 拿到非 ASCII 的 --user-data-dir
    会忽略它、退回用户默认 profile，于是扩展页会跟着一起打开。

    注意顺序：%LOCALAPPDATA% 排在后面。某些受限账户/安全软件环境下往
    C:\\Users\\... 建目录会长时间卡住，而数据目录不会。
    """
    data = os.path.join(outdir, "临时", "browser")
    if _makedirs(data):
        # 数据目录已经建好了，就用它：这是唯一能确保「一定能写」的位置。
        # 路径含中文时 Edge 会忽略这个 profile（顶多多加载扩展），但至少不会
        # 出现「卡住没反应」。想避免扩展页可以用 --tab 模式。
        if _ascii_path(data):
            return data
        short = short_path(data)
        return short if _ascii_path(short) else data
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cand = os.path.join(local, "renpy-toolkit", "browser")
        if _ascii_path(cand) and _makedirs(cand):
            return cand
    return data


def browser_command(browser: str, url: str, profile: str) -> list[str]:
    """``--app`` 窗口的启动参数。

    两个关键点：
    * ``--user-data-dir`` 必须是**纯 ASCII** 路径。带中文的 profile 目录会让
      Edge 忽略它、退回用户自己的默认 profile —— 于是用户装了扩展就会连带
      打开一堆扩展页。所以下面的 profile 目录名一律用 ``browser-`` 前缀。
    * ``--disable-extensions`` 双保险：即使 profile 被忽略，也不加载扩展。
    """
    return [
        browser,
        "--app=%s" % url,
        "--window-size=1200,800",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--disable-features=msEdgeSidebarV2",
        "--user-data-dir=%s" % profile,
    ]


def open_in_default_browser(url: str) -> bool:
    """在用户**自己**的浏览器里打开一个普通页面。

    直接用浏览器可执行文件 + 一个 URL 参数启动（不带 `--app`、不带
    `--user-data-dir`），这样复用的是用户已有的 profile 与窗口，不会重新
    走一遍首次启动流程，也就不会连带打开一堆扩展页、也不会撞上 Edge 的
    「无法更新」提示页。

    走过 shell（`ShellExecuteW` / `cmd /c start`）在打包后的窗口态进程里
    可能返回成功却什么都不发生（实测 ShellExecuteW 返回 42、cmd 返回 0，
    但一个浏览器进程都没起来），所以优先直接 Popen 浏览器本体。
    """
    browser = find_browser()
    if browser:
        try:
            subprocess.Popen(
                [browser, url],
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except OSError:
            pass
    if os.name == "nt":
        try:
            import ctypes

            rc = ctypes.windll.shell32.ShellExecuteW(None, "open", url, None, None, 1)
            if int(rc) > 32:
                return True
        except Exception:
            pass
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", url],
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except OSError:
            pass
    if hasattr(os, "startfile"):
        try:
            os.startfile(url)  # noqa: S606 - 本地 loopback 地址
            return True
        except OSError:
            pass
    try:
        import webbrowser

        if webbrowser.open(url, new=2):
            return True
    except Exception:
        pass
    return False
def blocking_notice(
    title: str,
    lines: list[str],
    *,
    seconds: int = 10,
    exit_code: int = 6,
    button: str = "退出",
) -> None:
    """置顶模态提示：只有一个按钮，倒计时结束自动退出；关不掉，只能结束程序。

    用 tkinter 而不是网页弹窗：这是**启动前**的闸门，此时 HTTP 服务还没起，
    网页那套根本用不上；而且要求「无法通过其他方式关闭」，只有原生窗口能做
    （去掉标题栏 + 拦 Alt+F4 + 忽略 WM_DELETE_WINDOW）。
    """
    try:
        import tkinter as tk
    except Exception:  # pragma: no cover - 无 tkinter 的构建
        for line in lines:
            print(line, file=sys.stderr)
        raise SystemExit(exit_code)

    root = tk.Tk()
    root.title(title)
    try:
        root.overrideredirect(True)          # 没有标题栏 → 没有关闭按钮
        root.attributes("-topmost", True)    # 压住其它窗口
    except Exception:
        pass
    root.configure(bg="#fef3f2")
    width, height = 660, 340
    x = max(0, (root.winfo_screenwidth() - width) // 2)
    y = max(0, (root.winfo_screenheight() - height) // 2)
    root.geometry("%dx%d+%d+%d" % (width, height, x, y))
    root.protocol("WM_DELETE_WINDOW", lambda: None)   # 忽略关闭请求
    root.bind("<Alt-F4>", lambda _e: "break")         # 拦 Alt+F4
    root.bind("<Escape>", lambda _e: "break")

    def stop() -> None:
        try:
            root.destroy()
        finally:
            os._exit(exit_code)  # 立刻结束整个进程，不给二次确认的机会

    body = tk.Frame(root, bg="#fef3f2", padx=30, pady=24)
    body.pack(fill="both", expand=True)
    tk.Label(
        body, text=title, bg="#fef3f2", fg="#b42318", anchor="w",
        font=("Microsoft YaHei UI", 20, "bold"),
    ).pack(fill="x")
    for index, line in enumerate(lines):
        tk.Label(
            body, text=line, bg="#fef3f2", fg="#1b1f27", anchor="w",
            justify="left", wraplength=width - 90,
            font=("Microsoft YaHei UI", 13 if index == 0 else 11),
        ).pack(fill="x", pady=(16 if index == 0 else 8, 0))

    foot = tk.Frame(root, bg="#fef3f2")
    foot.pack(fill="x", side="bottom", padx=30, pady=20)
    counter = tk.Label(foot, bg="#fef3f2", fg="#5a6371", font=("Microsoft YaHei UI", 11))
    counter.pack(side="left")
    tk.Button(
        foot, text=button, command=stop, relief="flat", cursor="hand2",
        bg="#b42318", fg="white", activebackground="#8f1c13", activeforeground="white",
        font=("Microsoft YaHei UI", 12), padx=30, pady=7,
    ).pack(side="right")

    remaining = {"left": max(1, int(seconds))}
    counter.config(text="%d 秒后自动关闭" % remaining["left"])

    def tick() -> None:
        remaining["left"] -= 1
        if remaining["left"] <= 0:
            stop()
            return
        counter.config(text="%d 秒后自动关闭" % remaining["left"])
        root.after(1000, tick)

    root.after(1000, tick)
    root.mainloop()
    stop()


def find_browser() -> str | None:
    """找 Edge 或 Chrome：``--app`` 模式能把网页开成没有地址栏的窗口。"""
    candidates = [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ]
    for raw in candidates:
        path = os.path.expandvars(raw)
        if os.path.isfile(path):
            return path
    return None


class _Stream(io.TextIOBase):
    """把 ``cli.main`` 的打印按行推进日志缓冲。"""

    def __init__(self, sink, cls: str = ""):
        self._sink = sink
        self._cls = cls
        self._buf = ""

    def write(self, text: str) -> int:  # noqa: D102 - TextIOBase 协议
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._sink(line, self._cls)
        return len(text)

    def flush(self) -> None:  # noqa: D102
        if self._buf:
            self._sink(self._buf, self._cls)
            self._buf = ""


class _Tee(io.TextIOBase):
    """同时写到日志缓冲与内存（内存那份用来把预览文本回给页面）。"""

    def __init__(self, stream, capture: io.StringIO):
        self._stream = stream
        self._capture = capture

    def write(self, text: str) -> int:
        self._stream.write(text)
        self._capture.write(text)
        return len(text)

    def flush(self) -> None:
        self._stream.flush()


class Api:
    """页面通过 ``POST /api/call {"name":..., "args":[...]}`` 调到这里。

    每个方法都返回可直接 JSON 序列化的 dict；异常一律转成 ``{"error":...}``。
    """

    def __init__(self, base: str | None = None):
        self.base = os.path.abspath(base or base_dir())
        self.config = load_config(self.base)
        self.games: list[Game] = []
        self.game: Game | None = None
        self.language = str(self.config.get("language") or DEFAULT_LANGUAGE)
        self.backup_mode = str(self.config.get("backup_mode") or "bak")
        if self.backup_mode not in BACKUP_MODES:
            self.backup_mode = "bak"
        self.xlsx = str(self.config.get("xlsx") or "")
        self.reference = str(self.config.get("reference") or "")
        self.logs: list[tuple[str, str]] = []
        self._log_lock = threading.Lock()
        self._busy = threading.Lock()
        # 「在普通标签页里打开」模式没有窗口可等，靠页面上的「退出工具」按钮
        # 调 quit() 来结束进程。
        self.stop_event = threading.Event()
        self.mode = "app"
        # 最后一次收到请求的时间：浏览器进程消失后靠它判断「窗口真的关了」
        # 还是「启动器把窗口交给了另一个进程」。
        self.last_hit = time.time()
        self._logged_hit = False

    def touch(self) -> None:
        if not getattr(self, '_logged_hit', False):
            self._logged_hit = True
            _slog('收到第一个页面请求')
        self.last_hit = time.time()

    # -- 基础设施 ---------------------------------------------------------
    def emit(self, line: str, cls: str = "") -> None:
        with self._log_lock:
            self.logs.append((line, cls))
            if len(self.logs) > 5000:
                del self.logs[:1000]

    def since(self, n: int) -> dict:
        with self._log_lock:
            return {"lines": [{"t": t, "c": c} for t, c in self.logs[n:]], "next": len(self.logs)}

    @property
    def outdir(self) -> str:
        return os.path.join(self.base, OUTPUT_DIR_NAME)

    def _ctx(self) -> Ctx:
        return Ctx(
            root=self.game.root if self.game else "",
            language=self.language,
            outdir=self.outdir,
            xlsx=self.xlsx or None,
            reference=self.reference or None,
        )

    def _prepare_dirs(self) -> None:
        for path in output_dirs(self._ctx()):
            os.makedirs(path, exist_ok=True)

    def _payload(self, scan_info: str = "") -> dict:
        return {
            "games": [
                {"root": g.root, "name": g.name, "launcher": g.launcher, "describe": g.describe()}
                for g in self.games
            ],
            "current": (
                {"root": self.game.root, "name": self.game.name, "launcher": self.game.launcher}
                if self.game
                else None
            ),
            "version": _version(),
            "language": self.language,
            "backup_mode": self.backup_mode,
            "scan_info": scan_info,
            "outdir": self.outdir,
            "mode": self.mode,
            "xlsx": self.xlsx,
            "reference": self.reference,
            "groups": actions_by_group(),
        }

    def _rescan(self, base: str | None = None) -> int:
        self.games = discover_games(base or self.base, depth=1)
        if self.game and not any(g.root == self.game.root for g in self.games):
            self.game = None
        if self.game is None:
            last = str(self.config.get("last_game") or "")
            for g in self.games:
                if last and g.root == last:
                    self.game = g
                    break
            if self.game is None and len(self.games) == 1:
                self.game = self.games[0]
        return len(self.games)

    # -- 供页面调用 -------------------------------------------------------
    def bootstrap(self) -> dict:
        try:
            self._rescan()
            if self.game:
                self._prepare_dirs()
            n = len(self.games)
            payload = self._payload(("找到 %d 个游戏" % n) if n else "这个目录下没有游戏")
            if _LAST_REJECTED:
                payload["rejected"] = list(_LAST_REJECTED)
            if not n:
                payload["no_game_hint"] = (
                    "没有检测到 Ren'Py 游戏。请把 renpy-toolkit.exe 移到游戏文件夹旁边"
                    "（与游戏文件夹同级，或它的上一级），再重新打开本工具。"
                )
            return payload
        except Exception as exc:  # pragma: no cover - 极端环境
            msg, hint = explain_error(exc)
            return {"error": msg, "hint": hint, "games": [], "groups": actions_by_group()}

    def rescan(self) -> dict:
        try:
            n = self._rescan()
            return self._payload(("找到 %d 个游戏" % n) if n else "这个目录下没有游戏")
        except Exception as exc:
            msg, hint = explain_error(exc)
            return {"error": msg, "hint": hint}

    def scan(self, path: str) -> dict:
        """扫一个用户手填/挑选的目录：既可能是游戏目录，也可能是它的上一层。"""
        try:
            path = os.path.abspath(path)
            if not os.path.isdir(path):
                return {"error": "这个目录不存在：%s" % path, "hint": "检查路径拼写，或点「浏览…」。"}
            if looks_like_game(path):
                found = discover_games(os.path.dirname(path) or path, depth=1)
                self.games = found
                self.game = next(
                    (g for g in found if g.root == path),
                    Game(path, os.path.basename(path.rstrip("\\/")), find_launcher(path)),
                )
                if not any(g.root == path for g in self.games):
                    self.games.insert(0, self.game)
            else:
                self.games = discover_games(path, depth=1)
                self.game = self.games[0] if self.games else None
            if self.game:
                self._prepare_dirs()
                self._remember_game()
            n = len(self.games)
            return self._payload(("找到 %d 个游戏" % n) if n else "这个目录下没有游戏")
        except Exception as exc:
            msg, hint = explain_error(exc)
            return {"error": msg, "hint": hint}

    def select(self, path: str) -> dict:
        try:
            target = os.path.abspath(path)
            found_det = engine.detect(target)
            if not found_det.is_renpy:
                return {
                    "error": "这个目录不是 Ren'Py 游戏，本工具只处理 Ren'Py 引擎的游戏。",
                    "hint": found_det.summary(),
                }
            for g in self.games:
                if g.root == target:
                    self.game = g
                    break
            else:
                return {"error": "这个游戏已经不在扫描结果里了，点「重新扫描」再看。"}
            self._prepare_dirs()
            self._remember_game()
            return self._payload()
        except Exception as exc:
            msg, hint = explain_error(exc)
            return {"error": msg, "hint": hint}

    def save_prefs(
        self,
        language: str,
        backup_mode: str,
        xlsx: str | None = None,
        reference: str | None = None,
    ) -> dict:
        self.language = (language or DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE
        self.backup_mode = backup_mode if backup_mode in BACKUP_MODES else "bak"
        if xlsx is not None:
            self.xlsx = xlsx.strip()
            self.config["xlsx"] = self.xlsx
        if reference is not None:
            self.reference = reference.strip()
            self.config["reference"] = self.reference
        self.config["language"] = self.language
        self.config["backup_mode"] = self.backup_mode
        err = save_config(self.base, self.config)
        return {"ok": True, "error": err} if err else {"ok": True}

    def suggest_xlsx(self) -> dict:
        """把数据目录里最新的 xlsx 路径回填到界面（导出后直接能回填）。"""
        folder = os.path.join(self.outdir, "导出")
        try:
            names = [n for n in os.listdir(folder) if n.lower().endswith(".xlsx")]
        except OSError:
            names = []
        if not names:
            return {"path": "", "hint": "数据目录里还没有 xlsx，先点「导出 Excel 表格」。"}
        newest = max(names, key=lambda n: os.path.getmtime(os.path.join(folder, n)))
        self.xlsx = os.path.join(folder, newest)
        self.config["xlsx"] = self.xlsx
        save_config(self.base, self.config)
        return {"path": self.xlsx}

    def open_outdir(self) -> dict:
        try:
            os.makedirs(self.outdir, exist_ok=True)
            os.startfile(self.outdir)  # type: ignore[attr-defined]
            return {"ok": True}
        except Exception as exc:
            msg, hint = explain_error(exc)
            return {"error": msg, "hint": hint}

    def open_game(self) -> dict:
        if not self.game:
            return {"error": "还没选游戏。"}
        try:
            os.startfile(self.game.root)  # type: ignore[attr-defined]
            return {"ok": True}
        except Exception as exc:
            msg, hint = explain_error(exc)
            return {"error": msg, "hint": hint}

    def quit(self) -> dict:
        """页面上的「退出工具」按钮：停掉本地服务，让进程退出。"""
        self.stop_event.set()
        return {"ok": True}

    def ping(self) -> dict:
        """页面心跳。页面关掉之后心跳停止，主循环据此判断「用户已经走了」。"""
        return {"ok": True, "pid": os.getpid()}

    def run_action(self, key: str, confirm: bool = False) -> dict:
        """执行一个动作。``confirm=False`` 时只做预演，返回需要确认的内容。"""
        if not self.game:
            return {"error": "先在上面选一个游戏。"}
        if not self._busy.acquire(blocking=False):
            return {"error": "上一条命令还在跑，等它结束。"}
        try:
            ctx = self._ctx()
            self._prepare_dirs()
            preview, writes = build_action(key, ctx)
            if writes and not confirm:
                text = ""
                if preview is not None:
                    code, text = self._invoke(preview, ctx)
                    if code not in (0, 1):
                        return {"error": self._exit_message(code), "hint": EXIT_HINTS.get(code, "")}
                targets = sorted({_target_of(argv) for argv in writes})
                return {
                    "need_confirm": True,
                    "preview_text": text.strip() or "（无预演输出）",
                    "write_count": len(writes),
                    "write_targets": targets,
                }
            argv_list = writes or ([preview] if preview else [])
            if not argv_list:
                return {"ok": True, "code": 0, "hint": "这条命令不需要写文件。"}
            worst = 0
            last_text = ""
            for argv in argv_list:
                code, _text = self._invoke(argv, ctx)
                last_text = _text
                if code != 0:
                    worst = code
                    break
            if worst and _informational(last_text):
                # 例如游戏没有 .rpa 归档、工程没有硬编码字体：不是错误，只是没内容可处理。
                return {"ok": True, "code": 0, "hint": "没有可处理的内容（见日志）。"}
            return {"ok": worst == 0, "code": worst, "hint": EXIT_HINTS.get(worst, "")}
        except Exception as exc:
            msg, hint = explain_error(exc)
            return {"error": msg, "hint": hint}
        finally:
            self._busy.release()

    # -- 执行辅助 ---------------------------------------------------------
    def _remember_game(self) -> None:
        if not self.game:
            return
        self.config["last_game"] = self.game.root
        self.config["language"] = self.language
        self.config["backup_mode"] = self.backup_mode
        save_config(self.base, self.config)

    # 日志面板只显示首尾；中段几万行对用户是噪声，但丢了又没法排错，
    # 所以全量落到数据目录下的日志文件里。
    HEAD_LINES = 12
    TAIL_LINES = 90

    def _invoke(self, argv: list[str], ctx: Ctx) -> tuple[int, str]:
        """跑一条子命令。

        输出先全部收进内存，再由 :meth:`_flush_log` 决定显示哪部分：命令跑错时
        最有价值的是**最后几行**（错误与退出码），而 `rpa list` 这种会吐两万行，
        逐行推到页面上只会把错误淹没。
        """
        from renpy_toolkit import cli
        from renpy_toolkit import engine

        full = ["--backup-mode", self.backup_mode] + list(argv)
        os.environ["RTK_SCRATCH"] = ctx.scratch_dir
        os.environ["RTK_BACKUP_DIR"] = ctx.backup_dir
        buf: list[tuple[str, str]] = [("$ renpy-toolkit " + " ".join(argv), "cmd")]
        capture = io.StringIO()
        code = 0
        out = _Stream(lambda text, cls: buf.append((text, cls)))
        err = _Stream(lambda text, cls: buf.append((text, "err")))
        try:
            with contextlib.redirect_stdout(_Tee(out, capture)), contextlib.redirect_stderr(
                _Tee(err, capture)
            ):
                code = int(cli.main(full) or 0)
        except SystemExit as exc:  # argparse 出错时
            code = int(exc.code or 2) if isinstance(exc.code, int) else 2
        except Exception as exc:
            msg, hint = explain_error(exc)
            buf.append(("错误：" + msg, "err"))
            if hint:
                buf.append(("提示：" + hint, "warn"))
            buf.append((traceback.format_exc(limit=3), "dim"))
            self._flush_log(buf, argv, code or 2)
            return 2, capture.getvalue()
        if code:
            buf.append(("退出码 %d：%s" % (code, EXIT_HINTS.get(code, "")), "warn"))
        self._flush_log(buf, argv, code)
        return code, capture.getvalue()

    def _log_path(self, argv: list[str]) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        slug = "-".join(t for t in argv if not t.startswith("-"))[:48] or "run"
        slug = "".join(ch if (ch.isalnum() or ch in "-_.中") else "_" for ch in slug)
        return os.path.join(self.outdir, "日志", "%s-%s.log" % (stamp, slug))

    def _flush_log(self, buf: list[tuple[str, str]], argv: list[str], code: int) -> None:
        path = ""
        try:
            path = self._log_path(argv)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(text for text, _cls in buf) + "\n")
        except OSError:
            path = ""
        total = len(buf)
        if total <= self.HEAD_LINES + self.TAIL_LINES:
            for text, cls in buf:
                self.emit(text, cls)
        else:
            for text, cls in buf[: self.HEAD_LINES]:
                self.emit(text, cls)
            self.emit(
                "… 中间省略 %d 行（完整输出在下面的日志文件里）…"
                % (total - self.HEAD_LINES - self.TAIL_LINES),
                "dim",
            )
            for text, cls in buf[-self.TAIL_LINES :]:
                self.emit(text, cls)
        if path:
            self.emit("完整输出：%s" % path, "dim")

    def _exit_message(self, code: int) -> str:
        return "预演就没通过（退出码 %d）：%s" % (code, EXIT_HINTS.get(code, ""))

    # 方法白名单：只有这里列出的名字能被页面调用。
    EXPOSED = (
        "bootstrap", "rescan", "scan", "select", "save_prefs", "suggest_xlsx",
        "open_outdir", "open_game", "run_action", "quit", "ping",
    )


def _target_of(argv: list[str]) -> str:
    """粗略指出这条命令会写哪儿，用于确认弹窗。"""
    for flag in ("--outdir", "-o", "--dest"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv):
                return argv[i + 1]
    for token in argv:
        if os.sep in token or "/" in token:
            return token
    return "（见日志）"


def _version() -> str:
    try:
        from renpy_toolkit import __version__

        return str(__version__)
    except Exception:
        return ""


class _Handler(http.server.BaseHTTPRequestHandler):
    api: Api  # 由 make_server 注入
    page: bytes = b""

    def log_message(self, *args) -> None:  # noqa: D102 - 关掉默认访问日志
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _json(self, payload: dict) -> None:
        self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 协议
        self.api.touch()
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, self.page, "text/html; charset=utf-8")
            return
        if path == "/api/logs":
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            since = 0
            for part in query.split("&"):
                if part.startswith("since="):
                    try:
                        since = int(part[6:])
                    except ValueError:
                        since = 0
            self._json(self.api.since(since))
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/bye":
            # 页面关闭时用 sendBeacon 发过来，不能保证一定有响应体。
            self.api.quit()
            try:
                self.send_response(204)
                self.end_headers()
            except OSError:
                pass
            return
        self.api.touch()
        if self.path.split("?", 1)[0] != "/api/call":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8") or "{}")
            name = str(req.get("name") or "")
            args = req.get("args") or []
            if name not in Api.EXPOSED:
                self._json({"error": "未知操作：%s" % name})
                return
            result = getattr(self.api, name)(*args)
            self._json(result if isinstance(result, dict) else {"ok": True, "value": result})
        except Exception as exc:  # 兜住一切，页面永远拿到 JSON
            msg, hint = explain_error(exc)
            self._json({"error": msg, "hint": hint})


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_server(api: Api, port: int = 0) -> tuple[_Server, int]:
    page_path = html_path()
    with open(page_path, "rb") as fh:
        page = fh.read()

    class Handler(_Handler):
        pass

    Handler.api = api
    Handler.page = page
    srv = _Server(("127.0.0.1", port), Handler)
    return srv, srv.server_address[1]


def main(argv: list[str] | None = None) -> int:
    """起本地服务并开界面。

    启动顺序：危险路径闸 → 单实例闸 → 起服务 → 等就绪 → 开浏览器。

    **默认在用户自己的浏览器里开一个普通标签页**（``--tab`` 的行为）：复用它
    已经开着的浏览器进程，既不新建 profile、也不会连带打开扩展页。想单独一个
    无地址栏窗口时用 ``--app``。

    ``--port N`` 固定端口、``--no-browser`` 只起服务不拉浏览器 —— 这两个主要
    为自动化验收存在（冻结包在无桌面会话里也能被脚本驱动）。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    port_wanted = 0
    open_browser = True
    tab_mode = True          # 默认标签页模式，见上方 docstring
    for i, item in enumerate(argv):
        if item == "--port" and i + 1 < len(argv):
            port_wanted = int(argv[i + 1])
        elif item == "--no-browser":
            open_browser = False
        elif item == "--app":
            tab_mode = False
        elif item == "--tab":
            tab_mode = True

    # 闸 1：盘符根目录。产物会散在盘根、误操作面最大，直接拒绝。
    here = base_dir()
    _slog("启动：exe/base 目录 = %s" % here)
    if is_dangerous_root(here):
        blocking_notice(
            "危险路径：本工具停止工作",
            [
                "当前所在位置是磁盘根目录：%s" % here,
                "该路径下工具停止工作，以免把导出、备份、临时文件散落到盘根，"
                "也避免误删盘根上的其它数据。",
                "请把整个文件夹移到某个子目录再运行（例如 D:\\Games\\SomeGame\\ 旁边）。",
            ],
            seconds=10,
            exit_code=6,
        )
        return 6

    # 闸 2：单实例。双击两次会开出两个进程抢同一个数据目录，这里直接挡住。
    if not _acquire_single_instance():
        blocking_notice(
            "本工具已经在运行",
            [
                "已有一个 renpy-toolkit 窗口在使用中。",
                "请切换到那个窗口；如果看不到它，先在任务管理器里结束旧的 "
                "renpy-toolkit.exe 再重试。",
            ],
            seconds=8,
            exit_code=7,
        )
        return 7

    page = html_path()
    if not os.path.isfile(page):
        _slog("ERROR 找不到界面文件 %s" % page)
        print("图形界面不可用：找不到界面文件 %s" % page, file=sys.stderr)
        show_error("图形界面不可用", [
            "找不到界面文件：%s" % page,
            "包可能不完整（data/ui/index.html 缺失）。请重新解压完整的压缩包。",
        ])
        return 3
    try:
        api = Api()
        api.mode = "tab" if tab_mode else "app"
        srv, port = make_server(api, port_wanted)
        url = "http://127.0.0.1:%d/" % port
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        if not open_browser:
            # 验收/无界面模式：只跑服务，等外部脚本调 quit()（或杀掉进程）。
            print(url, flush=True)
            while not api.stop_event.wait(1.0):
                if time.time() - api.last_hit > 120.0:
                    _slog("无界面模式空闲 120s，自动退出")
                    break
            srv.shutdown()
            return 0
        # 等端口真的能连上再开浏览器：单文件包首次启动要解包 + 过杀毒扫描，
        # 抢在前面打开只会得到一个「Failed to fetch」的空窗口。
        if not wait_until_listening(port):
            api.emit("本地服务启动超时。", "err")
            srv.shutdown()
            show_error("本地服务没能启动起来", [
                "等了 60 秒，本地界面服务仍然没有响应（端口 %d）。" % port,
                "常见原因：安全软件拦住了本程序的本地监听，或解压目录被隔离。",
                "可以先把本工具加入杀毒/防火墙白名单，或改放到其它磁盘的子目录再试。",
                "启动日志：%s" % startup_log_path(),
            ])
            return 4

        browser = find_browser()
        procs: list = []
        profiles: list = []

        def _hit_within(seconds: float, since: float) -> bool:
            deadline = time.time() + seconds
            while time.time() < deadline:
                if api.last_hit > since:
                    return True
                if api.stop_event.wait(0.5):
                    return False
            return api.last_hit > since

        def _open_tab() -> bool:
            since = time.time()
            ok = open_in_default_browser(url)
            _slog("默认浏览器打开 %s -> %s" % (url, ok))
            return ok and _hit_within(25.0, since)

        def _open_app() -> bool:
            if not browser:
                _slog("没有可用的 Edge/Chrome，跳过独立窗口")
                return False
            # 先试「复用用户已有配置」的独立窗口：不带 --user-data-dir，
            # 因此不会走 Edge 的首次启动流程（那一步会拉出扩展页，或弹出
            # "无法更新 Microsoft Edge" 的提示页把界面顶掉）。
            since = time.time()
            _slog("先试独立窗口（复用已有配置）")
            try:
                p0 = subprocess.Popen(
                    [browser, "--app=%s" % url, "--no-first-run",
                     "--no-default-browser-check"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                procs.append(p0)
            except OSError as exc:
                _slog("复用配置的独立窗口起不来：%r" % (exc,))
            if _hit_within(12.0, since):
                _slog("界面已连接（app 模式，复用配置）")
                return True
            try:
                root = browser_profile_root(api.outdir)
                _slog("profile 父目录 = %s" % root)
                prof = _mkdtemp(root)
                if not prof:
                    _slog("建临时 profile 目录失败（超时或被拒）")
                    return False
            except Exception as exc:
                _slog("准备 profile 失败：%r" % (exc,))
                return False
            profiles.append(prof)
            _slog("启动独立窗口 %s  profile=%s" % (browser, prof))
            try:
                p = subprocess.Popen(
                    browser_command(browser, url, prof),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                _slog("拉起浏览器失败：%r" % (exc,))
                return False
            procs.append(p)
            _slog("浏览器进程 pid=%s" % p.pid)
            return _hit_within(12.0, since_start)

        since_start = time.time()
        order = ("tab", "app") if tab_mode else ("app", "tab")
        connected = False
        for how in order:
            if how == "tab":
                connected = _open_tab()
            else:
                connected = _open_app()
            if connected:
                _slog("界面已连接（%s 模式）" % how)
                break
            _slog("%s 模式没能连上，换另一种" % how)
        if not connected:
            api.emit("界面没能打开。", "err")
            srv.shutdown()
            show_error("界面没能打开", [
                "服务已经起来了（%s），但没能把界面打开到浏览器里。" % url,
                "可以手动把上面的地址复制到浏览器打开；",
                "或者直接改用命令行（--cli --help）。",
                "启动日志：%s" % startup_log_path(),
            ])
            return 5

        # 服务寿命不绑浏览器进程句柄：某些启动器把窗口交给已有进程后自身会立刻
        # 退出，用 proc.wait() 会连带把服务关掉，窗口就变成永远「连不上」。
        idle_limit = 6 * 3600.0
        quiet_limit = 40.0 if not procs else 120.0
        while True:
            if api.stop_event.wait(1.0):
                _slog("收到退出请求")
                break
            idle = time.time() - api.last_hit
            busy = getattr(api, "_busy", None)
            running = bool(busy and busy.locked())
            if not running and idle > quiet_limit:
                # 页面每 4 秒发一次心跳；连着 40 秒没有请求，说明那个标签页
                # 已经被关掉了（tab 模式下没有任何进程句柄可以等）。
                _slog("页面已关闭（%ds 无心跳），关闭服务" % int(idle))
                break
            if idle > idle_limit:
                _slog("空闲超过 %ds，自动关闭服务" % int(idle_limit))
                break
        srv.shutdown()
        for prof in profiles:
            shutil.rmtree(prof, ignore_errors=True)
        _slog("服务已关闭，退出")
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        # 有非 daemon 线程（HTTP 服务、tkinter）时 return 不一定能让进程退掉。
        os._exit(0)
    except Exception as exc:
        path = _log_crash(exc)
        print("图形界面启动失败：%s" % exc, file=sys.stderr)
        show_error("图形界面启动失败", [
            "本工具启动时出错：%s" % exc,
            "详细信息已写入：%s" % (path or startup_log_path()),
            "请把该日志发给作者，或改用命令行（--cli --help）。",
        ])
        return 4
