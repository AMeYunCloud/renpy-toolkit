"""tkinter 图形界面（exe 分发形态专用）。

命令行是给脚本的，exe 是给人用的。这个模块把同一条流水线包成"选游戏 → 点按钮"，
所有实际操作都转成对 :func:`renpy_toolkit.cli.main` 的调用，因此界面与命令行
行为永远一致（含 ``--write`` 语义、备份模式、退出码），不存在两套实现。

放在包内而不是单独的脚本目录，是为了让冻结后的 exe 能直接
``from renpy_toolkit import gui``；但 **不要**把 ``gui`` 加进 ``__all__``——
有些环境没有 tkinter，星号导入会连带失败。命令行入口只在需要时才 import 它。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass, field

CONFIG_NAME = "config.json"
DEFAULT_LANGUAGE = "chinese"
BACKUP_MODES = ("bak", "git", "none")
# 一级子目录里出现这些名字就不当游戏候选：它们是工具自己的产物目录。
_SKIP_DIRS = frozenset({"工作区域", "可清理文件", "成品提交", "node_modules", "__pycache__"})


# ---------------------------------------------------------------------------
# 纯逻辑（无 tkinter 依赖，便于单元测试）
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
    """找启动脚本。

    Ren'Py 发行的游戏根目录里一定有 ``game/``，启动器则有两种常见形态：
    ``<Name>.py``（配 ``lib/py3-*/python.exe``）或已打包的 ``<Name>.exe``。
    优先 ``.py``：有它才能确定用哪个解释器跑探针。
    """
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
        n
        for n in entries
        if n.lower().endswith(".exe") and os.path.isfile(os.path.join(root, n))
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


def looks_like_game(path: str) -> bool:
    """判定标准：有 ``game/`` 子目录，且能看出是个 Ren'Py 发行版。

    "能看出"= 有启动脚本、或自带解释器、或 ``game/`` 里有 ``.rpy``/``.rpyc``。
    最后一条是为了覆盖"只留了 game 目录、启动器被改名或删掉"的情况。
    """
    game = os.path.join(path, "game")
    if not os.path.isdir(game):
        return False
    if find_launcher(path) or has_game_interpreter(path):
        return True
    try:
        for name in os.listdir(game):
            if name.lower().endswith((".rpy", ".rpyc", ".rpa")):
                return True
    except OSError:
        return False
    return False


def discover_games(base: str, depth: int = 1) -> list[Game]:
    """扫 ``base`` 自身与其一级子目录（``depth=1``）。

    不递归到底：用户把一键包丢在游戏目录旁边是常态，全盘递归既慢又容易
    把别人的工程卷进来。扫到的每个目录都独立成一个候选，互不影响。
    """
    found: list[Game] = []

    def add(path: str, name: str) -> None:
        if looks_like_game(path):
            found.append(Game(root=os.path.abspath(path), name=name, launcher=find_launcher(path)))

    if looks_like_game(base):
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
    return found


def config_path(base: str) -> str:
    return os.path.join(base, CONFIG_NAME)


def load_config(base: str) -> dict:
    """读配置；坏文件/不存在都退回空字典，绝不让界面起不来。"""
    try:
        with open(config_path(base), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(base: str, data: dict) -> str | None:
    """写配置，返回 None 表示成功，否则返回错误说明。

    exe 放在只读目录（Program Files、光盘）时写不进去属于正常情况：
    记忆是便利功能，失败不该弹错误框打断主流程。
    """
    try:
        os.makedirs(base, exist_ok=True)
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

    @property
    def game_dir(self) -> str:
        return os.path.join(self.root, "game")

    @property
    def tl_dir(self) -> str:
        return os.path.join(self.game_dir, "tl", self.language)


def build_action(key: str, ctx: Ctx) -> tuple[list[str] | None, list[list[str]]]:
    """把界面动作翻译成命令行参数。

    返回 ``(预览参数, [写盘参数...])``：

    * 预览参数为 ``None`` 表示这个动作没有"dry-run"形态，只能直接执行；
    * 写盘参数为空列表表示这是一个只读动作。

    把这条映射单独抽成纯函数，是因为"界面点一下到底跑了哪条命令"必须
    能被单元测试覆盖——否则改个按钮就可能悄悄改了行为。
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
        # 导出表格没有 dry-run：目标文件就是你选的那个路径，覆盖前由界面确认。
        return None, [["tl", "table", tl, "-o", ctx.xlsx or "", "--format", "xlsx", "--language", lang]]
    if key == "apply":
        return (
            ["tl", "apply", tl, ctx.xlsx or ""],
            [["tl", "apply", tl, ctx.xlsx or "", "--write"]],
        )
    if key == "port":
        return (
            ["tl", "port", tl, ctx.reference or ""],
            [["tl", "port", tl, ctx.reference or "", "--write", "--show-skipped"]],
        )
    if key == "glossary":
        return ["tl", "glossary", "check", tl, "--ui-terms"], []
    if key == "hardcoded":
        # 先建清单再打标记。两步都是写盘动作，一起确认一次。
        return (
            ["tl", "hardcoded", "scan", tl, "--language", lang],
            [
                ["tl", "hardcoded", "scan", tl, "--language", lang, "--write"],
                ["tl", "hardcoded", "apply", tl, "--language", lang, "--write"],
            ],
        )
    if key == "backup_list":
        return ["tl", "backup", "list", tl], []
    if key == "backup_save":
        return None, [["tl", "backup", "save", tl, "--message", "GUI 手动快照", "--write"]]
    if key == "font_check":
        return ["font", "check", "--tl", tl, *ctx.fonts], []
    if key == "font_map":
        return ["font", "map", root, "--emit", lang], []
    if key == "rpa_list":
        return ["rpa", "list", ctx.game_dir], []
    if key == "decompile":
        dest = ctx.dest or os.path.join(root, "工作区域", "反编译")
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
    {"key": "table", "group": "翻译", "label": "导出 Excel 表格", "hint": "导出一份可发给译者的 xlsx"},
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


def actions_by_group() -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for spec in ACTION_SPECS:
        groups.setdefault(spec["group"], []).append(spec)
    return groups


# ---------------------------------------------------------------------------
# 界面
# ---------------------------------------------------------------------------
def base_dir() -> str:
    """配置文件的落点：冻结后是 exe 所在目录，否则是当前目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.getcwd()


class App:
    def __init__(self, root_window, base: str | None = None) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.win = root_window
        self.base = base or base_dir()
        self.config = load_config(self.base)
        self.games: list[Game] = []
        self.game: Game | None = None
        self.ctx: Ctx | None = None
        self.busy = False
        self.buttons: list = []

        self.win.title("Ren'Py 汉化工具包")
        self.win.geometry("980x720")
        self.win.minsize(860, 600)
        try:
            self.win.option_add("*Font", ("Microsoft YaHei UI", 10))
        except Exception:
            pass

        self._build()
        self._rescan()

    # -- 布局 ---------------------------------------------------------------
    def _build(self) -> None:
        ttk, tk = self.ttk, self.tk
        outer = ttk.Frame(self.win, padding=10)
        outer.pack(fill="both", expand=True)

        # 1. 选游戏
        sel = ttk.LabelFrame(outer, text=" 第一步：选择目标游戏 ", padding=8)
        sel.pack(fill="x")
        row = ttk.Frame(sel)
        row.pack(fill="x")
        self.game_var = tk.StringVar()
        self.game_box = ttk.Combobox(row, textvariable=self.game_var, state="readonly", width=88)
        self.game_box.pack(side="left", fill="x", expand=True)
        self.game_box.bind("<<ComboboxSelected>>", lambda _e: self._on_pick())
        ttk.Button(row, text="重新扫描", command=self._rescan).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="浏览…", command=self._browse).pack(side="left", padx=(6, 0))

        self.hint_var = tk.StringVar()
        ttk.Label(sel, textvariable=self.hint_var, foreground="#666").pack(fill="x", pady=(6, 0))

        opts = ttk.Frame(sel)
        opts.pack(fill="x", pady=(6, 0))
        ttk.Label(opts, text="语言目录名：").pack(side="left")
        self.lang_var = tk.StringVar(value=str(self.config.get("language") or DEFAULT_LANGUAGE))
        ttk.Entry(opts, textvariable=self.lang_var, width=14).pack(side="left")
        ttk.Label(opts, text="   备份方式：").pack(side="left")
        self.backup_var = tk.StringVar(value=str(self.config.get("backup_mode") or "bak"))
        ttk.Combobox(
            opts, textvariable=self.backup_var, state="readonly", width=8, values=list(BACKUP_MODES)
        ).pack(side="left")
        ttk.Label(
            opts, text="   （bak = 原文件旁留 .bak，git = 影子仓库快照）", foreground="#666"
        ).pack(side="left")

        # 2. 功能按钮
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(8, 0))
        for group, specs in actions_by_group().items():
            box = ttk.LabelFrame(body, text=" %s " % group, padding=6)
            box.pack(fill="x", pady=(0, 6))
            line = ttk.Frame(box)
            line.pack(fill="x")
            for spec in specs:
                btn = ttk.Button(
                    line,
                    text=spec["label"],
                    width=16,
                    command=lambda k=spec["key"]: self._run_action(k),
                )
                btn.pack(side="left", padx=(0, 6), pady=2)
                self.buttons.append(btn)
                ttk.Label(line, text=spec["hint"], foreground="#777").pack(side="left", padx=(0, 14))

        tools = ttk.Frame(body)
        tools.pack(fill="x", pady=(0, 6))
        ttk.Button(tools, text="打开输出目录", command=self._open_outdir).pack(side="left")
        ttk.Button(tools, text="打开游戏目录", command=self._open_game).pack(side="left", padx=(6, 0))
        ttk.Button(tools, text="清空日志", command=self._clear_log).pack(side="left", padx=(6, 0))
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(tools, textvariable=self.status_var, foreground="#0a5").pack(side="left", padx=(14, 0))

        # 3. 日志
        logbox = ttk.LabelFrame(outer, text=" 运行日志 ", padding=6)
        logbox.pack(fill="both", expand=True, pady=(6, 0))
        self.log = tk.Text(logbox, wrap="word", height=16, state="disabled", font=("Consolas", 9))
        scroll = ttk.Scrollbar(logbox, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.log.tag_configure("err", foreground="#c00")
        self.log.tag_configure("ok", foreground="#070")
        self.log.tag_configure("cmd", foreground="#06c")

    # -- 游戏发现 -----------------------------------------------------------
    def _rescan(self) -> None:
        self.games = discover_games(self.base, depth=1)
        labels = [g.describe() for g in self.games]
        self.game_box.configure(values=labels)
        if not labels:
            self.game_var.set("")
            self.hint_var.set(
                "在 %s 及其一级子目录里没有找到 Ren'Py 游戏。点「浏览…」直接指定游戏所在目录。" % self.base
            )
            self._set_game(None)
            return
        if len(labels) == 1:
            self.hint_var.set("找到 1 个游戏，已自动选好；也可以点「浏览…」换一个。")
        else:
            self.hint_var.set("找到 %d 个游戏，请选择要处理的那一个（一次只处理一个）。" % len(labels))
        # 有记忆就预选，否则要求用户自己选——一次只处理一个，不猜。
        want = self.config.get("last_root")
        idx = 0
        for i, g in enumerate(self.games):
            if want and os.path.normcase(g.root) == os.path.normcase(str(want)):
                idx = i
                break
        self.game_box.current(idx)
        self._on_pick()

    def _on_pick(self) -> None:
        i = self.game_box.current()
        if 0 <= i < len(self.games):
            self._set_game(self.games[i])

    def _set_game(self, game: Game | None) -> None:
        self.game = game
        if game is None:
            self.ctx = None
            self._set_buttons(False)
            return
        self.ctx = Ctx(
            root=game.root,
            language=self.lang_var.get().strip() or DEFAULT_LANGUAGE,
        )
        self._set_buttons(True)
        self.config["last_root"] = game.root
        self._save_config()
        self._log("已选择：%s\n  game 目录：%s\n  tl 目录：%s\n" % (game.root, game.game_dir, self.ctx.tl_dir))

    def _browse(self) -> None:
        from tkinter import filedialog

        path = filedialog.askdirectory(title="选择游戏所在目录（含 game/ 的那一层）")
        if not path:
            return
        path = os.path.abspath(path)
        if not looks_like_game(path):
            # 允许用户指到 game/ 本身或再上一层，尽量把他想要的那层找出来。
            cand = os.path.dirname(path)
            if looks_like_game(cand):
                path = cand
            elif looks_like_game(os.path.dirname(cand)):
                path = os.path.dirname(cand)
            else:
                self._log("这个目录不像 Ren'Py 游戏（缺少 game/ 或启动器）：%s\n" % path, "err")
                return
        game = Game(root=path, name=os.path.basename(path), launcher=find_launcher(path))
        if all(os.path.normcase(g.root) != os.path.normcase(game.root) for g in self.games):
            self.games.append(game)
            self.game_box.configure(values=[g.describe() for g in self.games])
        self.game_box.current(len(self.games) - 1)
        self._on_pick()

    # -- 日志与状态 ---------------------------------------------------------
    def _log(self, text: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        start = self.log.index("end-1c")
        self.log.insert("end", text)
        if tag:
            self.log.tag_add(tag, start, "end-1c")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled and not self.busy else "disabled"
        for btn in self.buttons:
            btn.configure(state=state)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.status_var.set("正在执行…" if busy else "就绪")
        self._set_buttons(bool(self.game))
        self.win.configure(cursor="watch" if busy else "")

    def _open_outdir(self) -> None:
        if not self.ctx:
            return
        path = os.path.join(self.ctx.game_dir, "tl")
        target = path if os.path.isdir(path) else self.ctx.root
        self._open_path(target)

    def _open_game(self) -> None:
        if self.ctx:
            self._open_path(self.ctx.root)

    def _open_path(self, path: str) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606 - 打开目录是明确的产品行为
            else:
                import subprocess

                subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            self._log("打不开目录：%s (%s)\n" % (path, exc), "err")

    def _save_config(self) -> None:
        self.config["language"] = self.lang_var.get().strip() or DEFAULT_LANGUAGE
        self.config["backup_mode"] = self.backup_var.get()
        err = save_config(self.base, self.config)
        if err:
            self.status_var.set("配置未能保存（不影响使用）")

    # -- 执行 ---------------------------------------------------------------
    def _global_prefix(self) -> list[str]:
        mode = self.backup_var.get()
        return ["--backup-mode", mode] if mode in BACKUP_MODES else []

    def _run_cli(self, argv: list[str], then=None) -> None:
        """在后台线程跑一次 CLI，把 stdout/stderr 收进日志。

        用线程是为了让界面不假死：``tl gen`` 要跑游戏本体，几十秒起步。
        tkinter 不是线程安全的，所以只有这里读管道，界面更新一律经
        ``win.after`` 回主线程。
        """
        import io

        from . import cli

        full = self._global_prefix() + argv
        self._log("$ renpy-toolkit %s\n" % " ".join(full), "cmd")
        self._set_busy(True)

        def work() -> None:
            buf_out, buf_err = io.StringIO(), io.StringIO()
            saved = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = buf_out, buf_err
            try:
                code = cli.main(full)
            except BaseException as exc:  # GUI 不能因为一次异常就退出
                code = 99
                buf_err.write("\n[内部错误] %r\n" % (exc,))
            finally:
                sys.stdout, sys.stderr = saved
            text = buf_out.getvalue() + buf_err.getvalue()

            def finish() -> None:
                self._set_busy(False)
                if text:
                    self._log(text if text.endswith("\n") else text + "\n", "err" if code else None)
                self._log("[退出码 %d]\n\n" % code, "ok" if code == 0 else "err")
                if then is not None:
                    then(code, text)

            self.win.after(0, finish)

        threading.Thread(target=work, daemon=True).start()

    def _run_action(self, key: str) -> None:
        if not self.ctx or self.busy:
            return
        from tkinter import filedialog, messagebox

        ctx = self.ctx
        ctx.language = self.lang_var.get().strip() or DEFAULT_LANGUAGE
        self._save_config()

        # 需要用户先给路径的几个动作。
        if key == "table":
            path = filedialog.asksaveasfilename(
                title="导出到",
                defaultextension=".xlsx",
                initialfile="%s.xlsx" % ctx.language,
                filetypes=[("Excel 工作簿", "*.xlsx")],
            )
            if not path:
                return
            ctx.xlsx = path
        elif key == "apply":
            path = filedialog.askopenfilename(
                title="选择填好译文的表格", filetypes=[("表格", "*.xlsx *.csv *.tsv *.json *.po"), ("全部", "*.*")]
            )
            if not path:
                return
            ctx.xlsx = path
        elif key == "port":
            path = filedialog.askdirectory(title="选择旧译本目录（如 game/tl/Chinese）")
            if not path:
                return
            ctx.reference = os.path.abspath(path)
        elif key == "decompile":
            path = filedialog.askdirectory(title="反编译输出到哪里（取消则用 工作区域\\反编译）")
            ctx.dest = os.path.abspath(path) if path else ""
        elif key == "font_check":
            paths = filedialog.askopenfilenames(
                title="选择字体文件（可多选）", filetypes=[("字体", "*.ttf *.otf *.ttc")]
            )
            if not paths:
                return
            ctx.fonts = tuple(os.path.abspath(p) for p in paths)

        try:
            preview, writes = build_action(key, ctx)
        except KeyError:
            return
        if not preview and not writes:
            return
        if not writes:
            self._run_cli(preview)
            return

        # 会写盘：先跑 dry-run，把"将写入什么"摆出来，再问一次。
        def after_preview(code: int, text: str) -> None:
            detail = "\n".join(line for line in text.strip().splitlines()[-8:] if line.strip())
            if not messagebox.askyesno(
                "确认执行",
                "即将写入文件。\n\n%s\n\n输出目录：\n%s\n\n继续吗？" % (detail or "（无预览输出）", ctx.tl_dir),
                default="no",
            ):
                self._log("已取消。\n\n")
                return
            self._run_sequence(writes)

        if preview:
            self._run_cli(preview, then=after_preview)
        else:
            after_preview(0, "")

    def _run_sequence(self, argvs: list[list[str]]) -> None:
        """按顺序跑多条命令；任一条失败就停，不再往下写。"""
        queue = list(argvs)

        def step() -> None:
            if not queue:
                return
            argv = queue.pop(0)

            def after(code: int, _text: str) -> None:
                if code != 0:
                    self._log("上一步未成功，后续写入已停止。\n\n", "err")
                    return
                step()

            self._run_cli(argv, then=after)

        step()


def main(argv: list[str] | None = None) -> int:
    """启动图形界面。tkinter 缺失时打印说明并返回环境错误码。"""
    try:
        import tkinter as tk
    except ImportError:
        sys.stderr.write(
            "error: this build has no tkinter, so the window cannot open.\n"
            "Use the command line instead: renpy-toolkit --help\n"
        )
        return 3
    base = argv[0] if argv else None
    win = tk.Tk()
    App(win, base)
    win.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
