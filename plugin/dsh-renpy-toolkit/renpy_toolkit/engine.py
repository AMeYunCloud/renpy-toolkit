"""判定一个目录是不是 Ren'Py 引擎的游戏。

这个工具的每一步都假设目标是 Ren'Py：探针要把 ``.rpyc`` 喂给 Ren'Py 运行时、
``tl gen`` 要按 translate 块的格式产出 TL、``rpa`` 要按 RPA 归档格式解包。
对着一个非 Ren'Py 目录硬跑，最轻的结果是报一堆看不懂的错，最重的结果是把人家
的 ``game/`` 目录当成 TL 输出目标而写坏东西。所以入口处先做一次**严格**判定：
确定是 Ren'Py 才放行，否则明确拒绝并说明缺什么。

判定不靠目录名字，靠证据：

===========================  ==================================================
证据                         说明
===========================  ==================================================
``RENPY RPC2`` 脚本           ``game/`` 下的 ``.rpyc`` 开头 10 字节是 Ren'Py 的
                              编译脚本魔数 —— 最硬的证据，编译过的发行版也有
``RPA-3.0`` 归档              ``game/*.rpa`` 的归档魔数
Ren'Py 运行时目录             ``<root>/renpy/`` 里有 ``common/``/``display/``/``renpy.py``
自带解释器                    ``<root>/lib/py3-*/`` 里有 ``librenpython*`` 或 ``python.exe``
启动脚本提到 renpy            ``<root>/*.py`` 的文本里出现 ``renpy``（``import renpy``）
Ren'Py 源码结构               ``game/`` 里的 ``.rpy`` 含 ``label `` / ``translate `` / ``screen ``
===========================  ==================================================

只要命中前五条中的任意一条就算 Ren'Py（第六条是给"源码工程、还没编译、也没有
运行时"的兜底）。只有 ``game/`` 目录、里面塞着图片或随便几个文件，一律不算。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

RPC2_MAGIC = b"RENPY RPC2"
RPA_MAGICS = (b"RPA-3.0 ", b"RPA-2.0 ", b"RPA-1.0 ", b"RPA-4.0 ")
_SOURCE_KEYWORDS = ("label ", "translate ", "screen ", "init python")
_MAX_SCAN = 400


def _head(path: str, n: int) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def _iter_files(folder: str, suffix: str, cap: int = _MAX_SCAN):
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return
    seen = 0
    for name in names:
        if not name.lower().endswith(suffix):
            continue
        full = os.path.join(folder, name)
        if os.path.isfile(full):
            yield full
            seen += 1
            if seen >= cap:
                return


@dataclass
class Detection:
    """``detect()`` 的返回值。"""

    is_renpy: bool = False
    kind: str = "none"          # source / compiled / archive / none
    evidence: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.is_renpy:
            return "Ren'Py 游戏（%s）：%s" % (self.kind, "、".join(self.evidence))
        return "不是 Ren'Py 游戏：%s" % ("、".join(self.problems) or "没有找到任何 Ren'Py 证据")


def detect(path: str, *, game_dir: str | None = None) -> Detection:
    """判定 ``path``（游戏根，或直接给 ``game`` 目录）是不是 Ren'Py。"""
    result = Detection()
    root = os.path.abspath(path)
    game = game_dir or os.path.join(root, "game")
    if not os.path.isdir(game):
        # 允许直接把 game 目录传进来
        if os.path.basename(root).lower() == "game" and os.path.isdir(root):
            game = root
            root = os.path.dirname(root)
        else:
            result.problems.append("没有 game/ 目录")
            return result

    for item in _iter_files(game, ".rpyc"):
        if _head(item, len(RPC2_MAGIC)) == RPC2_MAGIC:
            result.evidence.append("编译脚本 %s" % os.path.basename(item))
            result.kind = "compiled"
            break

    if not result.evidence:
        for item in _iter_files(game, ".rpa"):
            if _head(item, 8) in RPA_MAGICS:
                result.evidence.append("RPA 归档 %s" % os.path.basename(item))
                result.kind = "archive"
                break

    renpy_pkg = os.path.join(root, "renpy")
    if os.path.isdir(renpy_pkg):
        for sub in ("common", "display", "renpy.py", "bootstrap.py", "exports.py"):
            if os.path.exists(os.path.join(renpy_pkg, sub)):
                result.evidence.append("Ren'Py 运行时 renpy/%s" % sub)
                result.kind = result.kind if result.kind != "none" else "runtime"
                break

    lib = os.path.join(root, "lib")
    if os.path.isdir(lib):
        try:
            subs = os.listdir(lib)
        except OSError:
            subs = []
        for name in subs:
            if not name.lower().startswith("py3-"):
                continue
            base = os.path.join(lib, name)
            try:
                inner = os.listdir(base)
            except OSError:
                inner = []
            hits = [n for n in inner if n.lower().startswith("librenpython")]
            if hits or os.path.isfile(os.path.join(base, "python.exe")):
                result.evidence.append("Ren'Py 自带解释器 lib/%s" % name)
                break

    try:
        for name in sorted(os.listdir(root)):
            if not name.lower().endswith(".py"):
                continue
            if name.lower() in ("setup.py", "conftest.py"):
                # 构建/测试脚本不是启动器，提到 renpy 也不算证据
                continue
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read(4000)
            except OSError:
                continue
            if "renpy" in text:
                result.evidence.append("启动脚本 %s 引用 renpy" % name)
                break
    except OSError:
        pass

    if not result.evidence:
        for item in _iter_files(game, ".rpy"):
            try:
                with open(item, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read(4000)
            except OSError:
                continue
            if any(k in text for k in _SOURCE_KEYWORDS):
                result.evidence.append("Ren'Py 源码 %s" % os.path.basename(item))
                result.kind = "source"
                break

    result.is_renpy = bool(result.evidence)
    if not result.is_renpy:
        result.problems.append("game/ 里没有 Ren'Py 编译脚本（RENPY RPC2）或 RPA 归档")
        result.problems.append("根目录没有 renpy/ 运行时、没有 lib/py3-*、启动脚本也不引用 renpy")
    return result


def require(path: str, *, what: str = "这个目录") -> Detection:
    """不是 Ren'Py 就抛错（错误消息直接给用户看）。"""
    from .errors import NotARenPyGameError

    found = detect(path)
    if not found.is_renpy:
        raise NotARenPyGameError(
            "%s 不是 Ren'Py 游戏，本工具只处理 Ren'Py 引擎的游戏。"
            "检查过：%s。请选择含有 game/ 且带有 .rpyc/.rpa 或 renpy/ 运行时的游戏根目录。"
            % (what, "；".join(found.problems))
        )
    return found


def is_renpy_game(path: str) -> bool:
    return detect(path).is_renpy


__all__ = ["Detection", "detect", "is_renpy_game", "require"]
