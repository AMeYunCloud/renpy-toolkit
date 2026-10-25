"""``renpy-toolkit`` 命令行入口。

设计取舍
--------
* **只用标准库 argparse**，不引入 click/typer：这个包要能塞进汉化组的
  ``python -m`` 流程，也要能在只有 Ren'Py 自带 Python 的机器上跑。
* **每个子命令一个函数**，方便测试时直接调，不必过 argv。
* **默认 dry-run 是刻意的**：所有会写文件的子命令默认只报告将改什么，
  显式 ``--write`` 才落盘。汉化工程一旦写错就是几百行错位，人的确认比
  少敲一个参数重要得多。
* 退出码：``0`` 成功 / ``1`` 校验发现问题 / ``2`` 用法错误 /
  ``3`` 环境缺依赖或找不到 SDK。方便进 CI。

子命令一览::

    renpy-toolkit rpa  list    <archive|game-dir>
    renpy-toolkit rpa  extract <archive> <outdir> [--filter PATTERN]
    renpy-toolkit rpa  pack    <out.rpa> <srcdir> [--version 2|3] [--key HEX]
    renpy-toolkit tl   stats   <tl-dir>
    renpy-toolkit tl   verify  <tl-dir> [--strict]
    renpy-toolkit tl   missing <tl-dir>
    renpy-toolkit tl   table   <tl-dir> -o out.csv [--format csv|tsv|json|po|xlsx]
    renpy-toolkit tl   apply   <tl-dir> <table.csv> [--write]
    renpy-toolkit tl   sort    <tl-dir> [--index] [--write]
    renpy-toolkit tl   split   <tl-dir> <game-dir> [--write]
    renpy-toolkit tl   hardcoded scan|show|apply <tl-dir> --language L [--write]
    renpy-toolkit sdk  translate <basedir> <lang> [--sdk PATH] [--count]
    renpy-toolkit sdk  lint      <basedir> [--sdk PATH]
    renpy-toolkit sdk  dialogue  <basedir> <lang> [--sdk PATH]
    renpy-toolkit font check  <font...> --tl <tl-dir>
    renpy-toolkit project new  <name> [--dest DIR] [--resolution 1920x1080]
    renpy-toolkit project audit <project-dir>
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import sys
from typing import Iterator, Sequence

from . import __version__
from .errors import (
    DependencyError,
    InvalidRequestError,
    SDKNotFoundError,
    ToolkitError,
)
from .errors import VerifyError
from . import safeio

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3

PROG = "renpy-toolkit"


# ---------------------------------------------------------------------------
# argparse 骨架
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Ren'Py 游戏制作与汉化工具集（.rpa / .rpyc / tl / 字体 / SDK）",
        epilog="所有会写文件的子命令默认只做 dry-run，加 --write 才真正落盘。",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="检查这份拷贝在本机能否正常工作（写临时目录，不碰任何游戏）",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="只输出错误")
    sub = parser.add_subparsers(dest="group", metavar="<group>")

    # ---------------------------------------------------------------- rpa
    rpa = sub.add_parser("rpa", help=".rpa 归档读写")
    rpa_sub = rpa.add_subparsers(dest="action", metavar="<action>")

    p = rpa_sub.add_parser("list", help="列出归档内容")
    p.add_argument("archive", help=".rpa 文件，或含 *.rpa 的 game 目录")
    p.add_argument("--long", action="store_true", help="显示大小与偏移")
    p.add_argument("--filter", default=None, help="只显示路径含该子串的条目")

    p = rpa_sub.add_parser("extract", help="解包到目录")
    p.add_argument("archive")
    p.add_argument("outdir")
    p.add_argument("--filter", default=None)
    p.add_argument("--no-overwrite", action="store_true", help="已存在则跳过")

    p = rpa_sub.add_parser("pack", help="把目录打成 .rpa")
    p.add_argument("output", help="输出 .rpa 路径")
    p.add_argument("srcdir", help="源目录（其内容会成为归档根）")
    p.add_argument("--version", type=int, choices=(2, 3), default=3)
    p.add_argument("--key", default=None, help="十六进制 key，默认沿用 0xDEADBEEF")
    p.add_argument("--prefix", action="store_true", help="RPAv3 使用 prefix 混淆")

    # ----------------------------------------------------------------- tl
    tl = sub.add_parser("tl", help="翻译文件（tl/<lang>）操作")
    tl_sub = tl.add_subparsers(dest="action", metavar="<action>")

    p = tl_sub.add_parser("stats", help="每个文件的块数与覆盖率")
    p.add_argument("tl_dir")

    p = tl_sub.add_parser("verify", help="完整性校验（空译文/标签/引号/换行）")
    p.add_argument("tl_dir")
    p.add_argument("--strict", action="store_true", help="警告也算失败")
    p.add_argument("--no-stutter", action="store_true", help="不检查结巴表达")
    p.add_argument("--fullwidth", action="store_true", help="检查全角标点")
    p.add_argument("--allow-empty", action="store_true",
                   help="空译文不算错误（刚生成的骨架用这个）")
    p.add_argument("--expect-dialogue", type=int, default=None, metavar="N",
                   help="断言对白块总数为 N（用引擎 lint 报的数字对账）")
    p.add_argument("--game-dir", default=None, metavar="DIR",
                   help="游戏的脚本目录；给了就读出工程自定义文本标签，"
                        "避免把合法项目标签报成拼写错误")
    p.add_argument("--max-items", type=int, default=200,
                   help="最多列多少条问题（默认 200）")

    p = tl_sub.add_parser("missing", help="列出还没翻译的条目")
    p.add_argument("tl_dir")
    p.add_argument("--language", default=None)

    p = tl_sub.add_parser("table", help="导出翻译表格")
    p.add_argument("tl_dir")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--format", default=None,
                   choices=("json", "csv", "tsv", "po", "xlsx"))
    p.add_argument("--language", default="chinese")

    p = tl_sub.add_parser("apply", help="把表格里的译文回填到 tl 文件")
    p.add_argument("tl_dir")
    p.add_argument("table")
    p.add_argument("--format", default=None,
                   choices=("json", "csv", "tsv", "po", "xlsx"))
    p.add_argument("--write", action="store_true", help="真正写盘（默认 dry-run）")
    p.add_argument("--allow-empty", action="store_true",
                   help="连空译文也写（默认跳过，避免清空已有译文）")
    p.add_argument("--no-backup", action="store_true", help="不生成 .bak")

    # ``tl sort``: order blocks by their mirrored ``# game/x.rpy:NNN`` comment.
    # A one-pair-per-block layout is what Ren'Py itself generates, and the
    # multi-pair hand-merged files are what ``tl split`` turns into it.
    p = tl_sub.add_parser(
        "sort", help="按 # game/x.rpy:NNN 注释排序（可生成 Event Index）"
    )
    p.add_argument("tl_dir")
    p.add_argument("--index", action="store_true",
                   help="写入 Event Index 注释块（每个 label 一条，指向首块行号）")
    p.add_argument("--index-threshold", type=int, default=1,
                   help="至少多少个块才写索引（默认 1）")
    p.add_argument("--sections", action="store_true",
                   help="按 label 插入 '# --- <label> ---' 分段标记")
    p.add_argument("--write", action="store_true", help="真正写盘（默认 dry-run）")
    p.add_argument("--no-backup", action="store_true", help="不生成 .bak")

    p = tl_sub.add_parser(
        "split", help="把一条多 old/new 的 strings 块拆成标准形态"
    )
    p.add_argument("tl_dir")
    p.add_argument("game_dir", help="game/ 源目录，用于反查字面量出处")
    p.add_argument("--language", default=None, help="覆盖块里缺语言时用的语言")
    p.add_argument("--write", action="store_true", help="真正写盘（默认 dry-run）")
    p.add_argument("--no-backup", action="store_true", help="不生成 .bak")

    # ``tl init``: clone an existing tl/<lang> (or tl/None) skeleton to a new
    # language with every translation emptied.  This is the no-SDK path to a
    # starting point -- the official ``translate`` command needs a working
    # launcher, and shipped games often have only .rpyc left.
    p = tl_sub.add_parser(
        "init", help="从已有 tl/<lang> 克隆出空译文骨架（无需 SDK）"
    )
    p.add_argument("basedir", help="含 game/ 的目录，或 game/ 本身")
    p.add_argument("language", help="目标语言目录名，如 chinese")
    p.add_argument("--source", default=None,
                   help="作为模板的 tl/<lang> 目录（默认自动挑选）")
    p.add_argument("--force", action="store_true",
                   help="覆盖已存在的目标文件（默认永不覆盖）")
    p.add_argument("--write", action="store_true", help="真正写盘（默认 dry-run）")

    p = tl_sub.add_parser(
        "gen",
        help="用游戏自带运行时生成全量 tl 骨架（对白 + 界面字符串，rpyc-only 可用）",
    )
    p.add_argument("basedir", help="含 game/ 的目录，或 game/ 本身")
    p.add_argument("language", help="目标语言目录名，如 chinese")
    p.add_argument("--outdir", default=None,
                   help="输出目录（默认写到 <game>/tl/<language>）")
    p.add_argument("--runtime", default=None,
                   help="游戏自带的 lib/py3-*/python.exe 或 <name>.py")
    p.add_argument("--no-strings", action="store_true",
                   help="只生成对白块，不扫描界面字符串")
    p.add_argument("--include-identifiers", action="store_true",
                   help="保留形如 scene_editor 的下划线标识符（可能是界面 key，"
                        "但绝大多数是跳转标签，会显著增加噪声）")
    p.add_argument("--dev-tool-dirs", default=None, metavar="NAME[,NAME...]",
                   help="用逗号分隔的目录名替换默认的开发工具目录集合"
                        "（默认 %s）。游戏把工具放在自己的目录名下时用这个，"
                        "不必改默认值"
                        % ",".join(_default_dev_tool_dirs()))
    p.add_argument("--timeout", type=int, default=1800, help="超时秒数")
    p.add_argument("--clean-scratch", action="store_true",
                   help="跑完删除临时探针目录（默认保留，便于复查 rtk_strings.json）。"
                        "临时目录若落在游戏安装目录内，建议开启")
    p.add_argument("--write", action="store_true",
                   help="写到 <game>/tl/<language>（默认 dry-run，只报数量）")

    # ``tl context``: one English word meaning different things on different
    # screens.  Ren'Py's string table is global, so this either needs a
    # ``{#context}`` tag in the source or a runtime shim; both are here.
    p = tl_sub.add_parser(
        "context",
        help="同一字符串跨屏含义不同时的消歧（{#context} 标签或运行时补丁）",
    )
    p.add_argument("dump", help="rtk-strings3 探针输出的 JSON")
    p.add_argument("--language", required=True, help="tl/<语言> 的目录名")
    p.add_argument("--tl-dir", default=None,
                   help="补丁写入的目录（默认与 dump 无关，必须显式指定）")
    p.add_argument("--min-screens", type=int, default=2,
                   help="至少出现在几个屏幕才算歧义（默认 2）")
    p.add_argument("--only", action="append", default=None, metavar="TEXT",
                   help="只处理这些字符串（可重复）")
    p.add_argument("--ignore", action="append", default=None, metavar="TEXT",
                   help="已知无需消歧的字符串，从报告里去掉（可重复）。"
                        "用于单个小写词：判不出是界面文案还是标识符，"
                        "所以交给调用方决定而不是猜")
    p.add_argument("--ignore-file", default=None, metavar="PATH",
                   help="从文件读 --ignore 列表（一行一条，# 开头为注释）")
    p.add_argument("--write", action="store_true", help="真正写盘（默认 dry-run）")
    p.add_argument("--show-all", action="store_true",
                   help="列出全部歧义字符串，而不只是 --only 指定的")

    # ``tl untranslatable``: strings the engine can never look up, because an
    # f-string is assembled before the translation happens.  Reported from the
    # same dump ``tl context`` reads.
    p = tl_sub.add_parser(
        "untranslatable",
        help="报告运行时才拼装、翻译表永远命中不了的字符串（f-string）",
    )
    p.add_argument("dump", help="rtk-gen 探针输出的 JSON（rtk_strings.json）")
    p.add_argument("--show-all", action="store_true",
                   help="列出全部条目，而不只是前 20 条")

    # ``tl fix-fstrings``: turn the untranslatable report into files that make
    # those strings translatable, without touching the game's own source.
    p = tl_sub.add_parser(
        "fix-fstrings",
        help="为 f-string 生成可翻译的文件（不修改游戏源码）",
    )
    p.add_argument("dump", help="rtk-gen 探针输出的 JSON（rtk_strings.json）")
    p.add_argument("--language", required=True, help="tl/<语言> 的目录名")
    p.add_argument("--outdir", required=True,
                   help="把生成的文件写到这里（通常先写到临时目录再拷进游戏）")
    p.add_argument("--values", action="append", metavar="FIELD=v1,v2",
                   help="枚举某个字段的全部取值，可重复；枚举齐全时改用精确条目"
                        "（不需要在运行期打补丁）")
    p.add_argument("--only", action="append", metavar="FILE:LINE",
                   help="只处理指定位置，可重复（默认处理全部）")
    p.add_argument("--translate", action="append", metavar="TEMPLATE=NEW",
                   help="给某个模板指定译文，可重复；不指定则右端先留原文占位")
    p.add_argument("--write", action="store_true", help="真正写盘（默认 dry-run）")
    p.add_argument("--show-all", action="store_true",
                   help="列出全部条目，而不只是前 20 条")

    p = tl_sub.add_parser("hardcoded", help="硬编码文本清单（不依赖 _() 判别）")
    hard_sub = p.add_subparsers(dest="hardcoded_action", metavar="<action>")
    p = hard_sub.add_parser(
        "scan", help="从 tl gen 的探针 dump 建清单（只收未包 _() 的字面量）")
    p.add_argument("tl_dir")
    p.add_argument("--language", required=True, help="tl/<语言> 的目录名")
    p.add_argument("--dump", metavar="JSON",
                   help="rtk_strings.json 路径（默认在 <game>/.renpy-toolkit-gen 下自动找）")
    p.add_argument("--from-tl", action="store_true",
                   help="改从 TL 目录里的 strings 块重建（没有 dump 时的退路，"
                        "会连原生 _() 串一起收进来）")
    p.add_argument("--write", action="store_true", help="真正写盘（默认只报告）")

    p = hard_sub.add_parser("show", help="打印清单内容")
    p.add_argument("tl_dir")
    p.add_argument("--language", required=True)
    p.add_argument("--removed", action="store_true", help="只列出源文已找不到的条目")

    p = hard_sub.add_parser("apply", help="给清单命中的块加 # hardcoded: 注释")
    p.add_argument("tl_dir")
    p.add_argument("--language", required=True)
    p.add_argument("--write", action="store_true", help="真正写盘（默认 dry-run）")

    p = tl_sub.add_parser("glossary", help="术语字典：校验译名一致性 / 全局改名")
    gl_sub = p.add_subparsers(dest="glossary_action", metavar="<action>")

    p = gl_sub.add_parser("check", help="对照术语表检查译文里的译名")
    p.add_argument("tl_dir")
    p.add_argument("--terms", action="append", metavar="PATH",
                   help="术语表路径，可重复指定以叠加多份"
                        "（插件自带 renpy_toolkit/data/ui-terms.tsv 是引擎 UI 的中文译名）")
    p.add_argument("--ui-terms", action="store_true", dest="ui_terms",
                   help="额外带上插件内置的 Ren'Py 标准 UI 术语表")
    p.add_argument("--category", action="append", metavar="NAME",
                   help="只检查某类术语，可重复（character/item/place/...）")
    p.add_argument("--scope", choices=("common", "project"),
                   help="只检查某个作用域的条目：common 可跨工程复用，"
                        "project 只对本工程成立（默认全部）")
    p.add_argument("--file", action="append", metavar="NAME",
                   help="只检查某个 TL 文件，可重复")
    p.add_argument("--missing", action="store_true",
                   help="同时报告「原文有该词但译文没用规定译名」（噪声较高）")
    p.add_argument("--drift", action="store_true",
                   help="同时报告「译文里出现了禁用写法但该词在整句里」（噪声较高）")
    p.add_argument("--strict", action="store_true",
                   help="有任何 error 时以非零码退出")

    p = gl_sub.add_parser("list", help="打印术语表")
    p.add_argument("--terms", action="append", metavar="PATH",
                   help="术语表路径，可重复指定以叠加多份")
    p.add_argument("--ui-terms", action="store_true", dest="ui_terms",
                   help="额外带上插件内置的 Ren'Py 标准 UI 术语表")
    p.add_argument("--category", action="append", metavar="NAME")
    p.add_argument("--scope", choices=("common", "project"),
                   help="只列某个作用域的条目（默认全部）")
    p.add_argument("--banned", action="store_true", help="只列有禁用写法的条目")

    p = gl_sub.add_parser("rename", help="在全部译文里把一个译名换成另一个")
    p.add_argument("tl_dir")
    p.add_argument("old", help="要被替换掉的现有译名")
    p.add_argument("new", help="新译名")
    p.add_argument("--file", action="append", metavar="NAME",
                   help="只改指定文件，可重复")
    p.add_argument("--no-backup", action="store_true", help="不写 .bak")
    p.add_argument("--write", action="store_true", help="真正写盘（默认 dry-run）")

    p = tl_sub.add_parser(
        "port", help="把已有译本按标识符搬到新骨架上（重生成 TL 后不丢旧译文）"
    )
    p.add_argument("skeleton_dir", help="刚生成、还是原文占位的 TL 目录")
    p.add_argument("reference_dir", help="旧译本目录，例如 game/tl/Chinese")
    p.add_argument("--file", action="append", metavar="NAME",
                   help="只搬指定文件，可重复")
    p.add_argument("--no-backup", action="store_true", help="不写 .bak")
    p.add_argument("--write", action="store_true", help="真正写盘（默认 dry-run）")
    p.add_argument("--show-skipped", action="store_true",
                   help="列出因源文改动而跳过的块，最多 40 条")

    p = tl_sub.add_parser(
        "polish",
        help="直接应用润色批处理脚本（*.py 数据字典，不执行代码）",
    )
    p.add_argument("tl_dir", help="要改的 TL 目录")
    p.add_argument("scripts", nargs="+", metavar="SCRIPT",
                   help="一个或多个 polish_script_*.py")
    p.add_argument("--file", action="append", metavar="NAME",
                   help="只改指定 TL 文件，可重复")
    p.add_argument("--no-backup", action="store_true", help="不写 .bak")
    p.add_argument("--write", action="store_true", help="真正写盘（默认 dry-run）")
    p.add_argument("--show-problems", action="store_true",
                   help="列出全部未命中原因，最多 40 条")

    # ---------------------------------------------------------------- sdk
    sdk = sub.add_parser("sdk", help="调用 Ren'Py SDK 命令行")
    sdk_sub = sdk.add_subparsers(dest="action", metavar="<action>")

    def add_sdk_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--sdk", default=None, help="SDK 目录或 renpy.exe/renpy.sh 路径")
        p.add_argument("--timeout", type=int, default=None, help="超时秒数")

    p = sdk_sub.add_parser("find", help="定位 SDK 启动器")
    add_sdk_common(p)

    p = sdk_sub.add_parser("translate", help="生成/更新 tl/<lang>（增量，不动已有译文）")
    p.add_argument("basedir")
    p.add_argument("language", nargs="?", default="chinese")
    p.add_argument("--count", action="store_true", help="只报缺失数量，不写文件")
    p.add_argument("--strings-only", action="store_true")
    p.add_argument("--empty", action="store_true", help="生成空字符串而不是原文")
    add_sdk_common(p)

    p = sdk_sub.add_parser("lint", help="lint（--error-code --no-orphan-tl）")
    p.add_argument("basedir")
    add_sdk_common(p)

    p = sdk_sub.add_parser("dialogue", help="导出对白表")
    p.add_argument("basedir")
    p.add_argument("language", nargs="?", default=None)
    p.add_argument("--strings", action="store_true")
    p.add_argument("--notags", action="store_true")
    add_sdk_common(p)

    p = sdk_sub.add_parser("extract-strings", help="导出官方 JSON")
    p.add_argument("basedir")
    p.add_argument("language", nargs="?", default="chinese")
    p.add_argument("destination")
    add_sdk_common(p)

    p = sdk_sub.add_parser("merge-strings", help="导入官方 JSON（隐含 compile）")
    p.add_argument("basedir")
    p.add_argument("language", nargs="?", default="chinese")
    p.add_argument("source")
    p.add_argument("--replace", action="store_true")
    add_sdk_common(p)

    p = sdk_sub.add_parser("compile", help="编译 .rpy → .rpyc")
    p.add_argument("basedir")
    add_sdk_common(p)

    # --------------------------------------------------------------- font
    font = sub.add_parser("font", help="字体缺字检查")
    font_sub = font.add_subparsers(dest="action", metavar="<action>")
    p = font_sub.add_parser("check", help="检查字体是否覆盖译文所有字形")
    p.add_argument("fonts", nargs="+", help="字体文件（.ttf/.otf/.ttc）")
    p.add_argument("--tl", dest="tl_dir", required=True, help="tl/<lang> 目录")
    p.add_argument("--extra-text", default=None, help="额外要检查的文本文件（UTF-8）")

    p = font_sub.add_parser("map", help="审计 config.font_replacement_map（硬编码字体有无被替换）")
    p.add_argument("project_dir", help="工程根目录或 game 目录")
    p.add_argument("--require-bold", action="store_true",
                   help="要求每个字体也有 bold 映射（否则 {b} 会掉回原字体）")
    p.add_argument("--require-italic", action="store_true",
                   help="要求每个字体也有 italic 映射")
    p.add_argument("--all-states", action="store_true",
                   help="要求 regular/bold/italic/bold+italic 四种状态全覆盖（最严）")
    p.add_argument("--emit", metavar="LANGUAGE", default=None,
                   help="打印一份可直接粘贴的 translate <LANGUAGE> python 块模板")

    # ------------------------------------------------------------ decompile
    dec = sub.add_parser("decompile", help=".rpyc → .rpy（只读游戏也能拿到源码）")
    dec_sub = dec.add_subparsers(dest="action", metavar="<action>")

    p = dec_sub.add_parser("file", help="反编译单个 .rpyc 到 stdout")
    p.add_argument("rpyc")
    p.add_argument("-o", "--output", default=None, help="写入文件而不是打印")
    p.add_argument("--list-slots", action="store_true", help="只列出容器里的槽位")

    p = dec_sub.add_parser("dir", help="批量反编译目录下的 .rpyc")
    p.add_argument("root", help="含 .rpyc 的目录（如 game 或 game/tl/<lang>）")
    p.add_argument("-o", "--dest", default=None, help="输出目录，默认与源同目录")
    p.add_argument("--pattern", default="*.rpyc")
    p.add_argument("--overwrite", action="store_true", help="覆盖已存在的 .rpy")
    p.add_argument("--backup", action="store_true", help="覆盖前把旧 .rpy 存成 .bak")
    p.add_argument("--write", action="store_true", help="真正写盘（默认 dry-run）")

    p = dec_sub.add_parser("verify", help="反编译往返校验（标识符/字符串/对白）")
    p.add_argument("tl_dir")
    p.add_argument("--against", default=None,
                   help="用重编译后的 .rpyc 目录做比对（更强，需要 Ren'Py）")

    # ------------------------------------------------------------- project
    project = sub.add_parser("project", help="工程脚手架与体检")
    project_sub = project.add_subparsers(dest="action", metavar="<action>")

    p = project_sub.add_parser("new", help="生成一个最小可运行的 Ren'Py 工程")
    p.add_argument("name")
    p.add_argument("--dest", default=".", help="父目录（默认当前目录）")
    p.add_argument("--resolution", default="1920x1080",
                   choices=("1280x720", "1600x900", "1920x1080", "2560x1440"))
    p.add_argument("--language", default="chinese")
    p.add_argument("--author", default=None)
    p.add_argument("--overwrite", action="store_true")

    p = project_sub.add_parser("audit", help="静态体检（图片缺失/归档重叠/行尾）")
    p.add_argument("project_dir")
    p.add_argument("--no-archives", action="store_true", help="跳过 .rpa 检查")

    return parser


# ---------------------------------------------------------------------------
# 输出助手
# ---------------------------------------------------------------------------
class _Out:
    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet

    def say(self, message: str = "") -> None:
        if not self.quiet:
            print(message)

    def warn(self, message: str) -> None:
        print(message, file=sys.stderr)


def _split_dirs(value: str | None) -> tuple[str, ...] | None:
    """``"a,b"`` -> ``("a", "b")``; ``None`` stays ``None``.

    ``None`` means "use the module default", which is deliberately different
    from an empty tuple (that would disable the developer-tooling check).
    """
    if value is None:
        return None
    names = tuple(part.strip() for part in value.split(",") if part.strip())
    return names or None


def _default_dev_tool_dirs() -> tuple[str, ...]:
    """The default set, imported lazily so ``--help`` stays cheap."""
    from .tlgen import _DEV_TOOL_DIRS

    return _DEV_TOOL_DIRS


# ---------------------------------------------------------------------------
# rpa
# ---------------------------------------------------------------------------
def _open_archive(path: str):
    from .rpa import RenPyArchive, is_rpa

    if os.path.isdir(path):
        archives = sorted(
            os.path.join(path, n) for n in os.listdir(path) if n.lower().endswith(".rpa")
        )
        if not archives:
            raise InvalidRequestError(f"{path}: no .rpa file inside")
        if len(archives) > 1:
            raise InvalidRequestError(
                f"{path}: several archives found, pass one explicitly: "
                + ", ".join(os.path.basename(a) for a in archives)
            )
        path = archives[0]
    if not is_rpa(path):
        raise InvalidRequestError(f"{path}: not an RPA archive")
    return RenPyArchive.open(path)


def cmd_rpa(args: argparse.Namespace, out: _Out) -> int:
    from .rpa import RenPyArchive

    if args.action == "list":
        archive = _open_archive(args.archive)
        names = sorted(archive.names())
        if args.filter:
            names = [n for n in names if args.filter in n]
        if args.long:
            entries = archive.entries
            for name in names:
                entry = entries[name]
                out.say(f"{entry.stored_length:>10}  {name}")
        else:
            for name in names:
                out.say(name)
        out.say(f"\n{len(names)} entrie(s) in "
                f"{os.path.basename(archive.file or args.archive)}")
        return EXIT_OK

    if args.action == "extract":
        archive = _open_archive(args.archive)
        os.makedirs(args.outdir, exist_ok=True)
        count = 0
        for name in sorted(archive.names()):
            if args.filter and args.filter not in name:
                continue
            # Names come from the archive index; refuse anything that would
            # land outside --outdir instead of trusting the index.
            dest = safeio.safe_join(
                args.outdir, *name.split("/"), what=f"{args.archive}: entry"
            )
            if args.no_overwrite and os.path.exists(dest):
                continue
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with open(dest, "wb") as handle:
                handle.write(archive.read(name))
            count += 1
            out.say(dest)
        out.say(f"\n{count} file(s) extracted to {args.outdir}")
        return EXIT_OK

    if args.action == "pack":
        if not os.path.isdir(args.srcdir):
            raise InvalidRequestError(f"{args.srcdir}: not a directory")
        key = int(args.key, 16) if args.key else None
        archive = RenPyArchive.create(
            args.output, version=args.version, key=key, prefixes=args.prefix
        )
        added = 0
        for root, _dirs, files in os.walk(args.srcdir):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, args.srcdir).replace("\\", "/")
                with open(full, "rb") as handle:
                    archive.add(rel, handle.read())
                added += 1
        archive.save(args.output)
        out.say(f"packed {added} file(s) into {args.output}")
        return EXIT_OK

    raise InvalidRequestError("rpa: choose list / extract / pack")


# ---------------------------------------------------------------------------
# tl
# ---------------------------------------------------------------------------
def cmd_tl(args: argparse.Namespace, out: _Out) -> int:
    from . import tables, tl as tlmod, verify

    if args.action == "stats":
        files = tlmod.parse_tl_dir(args.tl_dir)
        total_blocks = total_strings = 0
        for tl in files:
            counts = tl.counts()
            total_blocks += counts["translate"]
            total_strings += sum(len(b.strings) for b in tl.blocks if b.kind == "strings")
            out.say(
                f"{os.path.basename(tl.path):<40} "
                f"translate={counts['translate']:<5} "
                f"strings={sum(len(b.strings) for b in tl.blocks if b.kind == 'strings'):<5} "
                f"{'BOM' if tl.has_bom else '   '}"
            )
        out.say(f"\n{len(files)} file(s), {total_blocks} block(s), {total_strings} string(s)")
        return EXIT_OK

    if args.action == "verify":
        report = verify.verify_tl_dir(
            args.tl_dir,
            check_stutter=not args.no_stutter,
            check_fullwidth=args.fullwidth,
            allow_empty=args.allow_empty,
            expect_dialogue=args.expect_dialogue,
            game_dir=args.game_dir,
        )
        out.say(report.text(max_items=args.max_items))
        failed = bool(report.errors) or (args.strict and bool(report.warnings))
        return EXIT_FINDINGS if failed else EXIT_OK

    if args.action == "missing":
        entries = tlmod.find_missing(args.tl_dir, language=args.language)
        for entry in entries:
            where = f"{os.path.basename(entry.path)}:{entry.lineno}"
            ident = entry.identifier or entry.context or "-"
            out.say(f"{where}  [{ident}]  {entry.old}")
        out.say(f"\n{len(entries)} missing item(s)")
        return EXIT_OK

    if args.action == "table":
        rows = tables.collect_rows(args.tl_dir)
        tables.export_table(
            rows, args.output, format=args.format, language=args.language
        )
        out.say(f"exported {len(rows)} row(s) to {args.output}")
        return EXIT_OK

    if args.action == "apply":
        rows = tables.read_table(args.table, format=args.format)
        result = tables.apply_rows(
            rows,
            args.tl_dir,
            dry_run=not args.write,
            only_filled=not args.allow_empty,
            backup=not args.no_backup,
        )
        for path in result.written:
            out.say(("wrote " if args.write else "would write ") + path)
        for problem in result.problems:
            out.warn("  " + problem)
        out.say(result.summary())
        if not args.write and result.written:
            out.say("(dry-run; re-run with --write to apply)")
        return EXIT_FINDINGS if result.problems else EXIT_OK

    raise InvalidRequestError("tl: choose stats / verify / missing / table / apply")


# ---------------------------------------------------------------------------
# tl 的整理类命令（sort / split / hardcoded）
# ---------------------------------------------------------------------------
def _iter_tl_files(tl_dir: str) -> Iterator[str]:
    """Every ``.rpy`` under *tl_dir*; empty when the directory is missing."""
    if not os.path.isdir(tl_dir):
        return
    for root, dirs, files in os.walk(tl_dir):
        dirs[:] = sorted(d for d in dirs if d not in ("__pycache__",))
        for name in sorted(files):
            if name.endswith(".rpy"):
                yield os.path.join(root, name)


def cmd_tl_init(args: argparse.Namespace, out: _Out) -> int:
    from .fresh import plan_fresh_tl

    result = plan_fresh_tl(
        args.basedir,
        args.language,
        source=args.source,
        force=args.force,
        dry_run=not args.write,
    )
    out.say(result.summary())
    listing = result.files or result.planned
    for path in listing:
        out.say("  " + path)
    if not args.write and listing:
        out.say("(dry-run; re-run with --write to create the files)")
    return EXIT_OK if listing else EXIT_FINDINGS


def cmd_tl_gen(args: argparse.Namespace, out: _Out) -> int:
    """Generate a full TL skeleton from the game's own runtime."""
    import shutil

    from .tlgen import clean_scratch_tree, generate_tl

    root = os.path.abspath(args.basedir)
    if os.path.basename(root).lower() == "game":
        root = os.path.dirname(root)
    target = args.outdir or os.path.join(root, "game", "tl", args.language)

    # No outdir on purpose: the generator picks a scratch base that the *game's*
    # interpreter can write to, which is not necessarily where we can write.
    # It also clears its output directory, so pointing it at tl/<lang> would
    # wipe existing translations.
    result = generate_tl(
        args.basedir,
        args.language,
        runtime=args.runtime,
        with_strings=not args.no_strings,
        identifiers=args.include_identifiers,
        dev_tool_dirs=_split_dirs(args.dev_tool_dirs),
        clean_scratch=args.clean_scratch,
        timeout=args.timeout,
    )
    out.say(result.summary())
    if result.decompiled:
        out.say(f"  {result.decompiled} .rpyc had to be decompiled to read strings")
    if result.strings_json:
        out.say(f"  raw string dump: {result.strings_json}")
    if result.fstrings:
        # Not a warning about our own work: these are strings the engine can
        # never translate, because an f-string is assembled before the lookup.
        out.say(
            f"  {result.fstrings} f-string(s) cannot be translated as written "
            f"(assembled at runtime; needs a source change)")
        for row in result.fstring_rows[:10]:
            out.say(f"    {row.get('file')}:{row.get('line')}  {row.get('text')}")
        if len(result.fstring_rows) > 10:
            out.say(f"    ... and {len(result.fstring_rows) - 10} more")

    if result.decompile_failures:
        # These scripts yielded no strings at all; saying so keeps a silent
        # coverage hole from being mistaken for a game with less text.
        out.say(
            f"  {len(result.decompile_failures)} script(s) could not be decompiled "
            f"(their strings are missing from this run)"
        )
        for path, reason in result.decompile_failures[:10]:
            out.say(f"    {path}: {reason}")
        if len(result.decompile_failures) > 10:
            out.say(f"    ... and {len(result.decompile_failures) - 10} more")

    fresh, kept = [], 0
    for rel in result.files:
        # ``rel`` comes from the game's own script filenames (via the probe).
        # A malicious game can name a script ``../../x.rpy``, so refuse to
        # install anything that would land outside the target directory.
        dest = safeio.safe_join(target, rel, what=f"generated TL entry {rel!r}")
        if os.path.exists(dest):
            kept += 1
            continue
        fresh.append(rel)
        if args.write:
            os.makedirs(os.path.dirname(dest) or target, exist_ok=True)
            shutil.copy2(os.path.join(result.outdir, rel), dest)

    verb = "wrote" if args.write else "would write"
    out.say(f"  {verb} {len(fresh)} file(s) to {target}")
    if kept:
        out.say(f"  {kept} file(s) already present, left untouched")

    # Cleanup comes last on purpose: the scratch tree *is* where the generated
    # files live, so removing it before the copy above would leave nothing to
    # install. ``owned`` is false when the caller supplied --outdir, because
    # then they chose the output location and may still want the dump.
    if args.clean_scratch:
        if os.path.abspath(result.outdir) == os.path.abspath(target):
            out.say("  --clean-scratch skipped: the probe output is the install target")
        elif clean_scratch_tree(result.scratch_root):
            out.say("  temporary probe directory removed")

    if not args.write:
        out.say("(dry-run; re-run with --write to install into tl/<language>)")
    return EXIT_OK if (fresh or kept) else EXIT_FINDINGS


def cmd_tl_sort(args: argparse.Namespace, out: _Out) -> int:
    from .sorttl import sort_tl_path

    written = 0
    for path in _iter_tl_files(args.tl_dir):
        result = sort_tl_path(
            path,
            write_index=args.index,
            index_threshold=args.index_threshold,
            dry_run=not args.write,
            backup=not args.no_backup,
            sections=getattr(args, "sections", False),
        )
        if not result.changed:
            continue
        written += 1
        out.say(
            f"{'wrote' if args.write else 'would write'} "
            f"{os.path.relpath(path, args.tl_dir)}: "
            f"{result.blocks} block(s), {result.skipped_strings} strings block(s) kept"
        )
        for note in result.notes:
            out.say("  " + note)
    if not written:
        out.say("nothing to sort")
        return EXIT_OK
    if not args.write:
        out.say("(dry-run; re-run with --write to apply)")
    return EXIT_OK


def cmd_tl_split(args: argparse.Namespace, out: _Out) -> int:
    from .stringsplit import normalise_tl_file
    from . import tl as tlmod

    written = 0
    for path in _iter_tl_files(args.tl_dir):
        tl = tlmod.parse_tl_file(path)
        if args.language:
            language = args.language
        else:
            language = next((b.language for b in tl.blocks if b.language), None)
        if not language:
            out.warn(f"{os.path.basename(path)}: no language, skipped (use --language)")
            continue
        result = normalise_tl_file(tl, game_dir=args.game_dir)
        if not result.blocks_in:
            continue
        written += 1
        out.say(
            f"{'wrote' if args.write else 'would write'} "
            f"{os.path.relpath(path, args.tl_dir)}: "
            f"{result.blocks_in} multi-pair block(s) -> "
            f"{result.blocks_out} block(s), {result.unresolvable} without a source"
        )
        for note in result.notes:
            out.say("  " + note)
        if args.write:
            # ``del`` keeps the language attribute honest in case a block had
            # none and ``args.language`` supplied it.
            tl.blocks = []
            from .tl import _parse_into

            rebuilt = tlmod.parse_tl_text(result.text, path)
            rebuilt.write(path, backup=not args.no_backup)
    if not written:
        out.say("nothing to split")
        return EXIT_OK
    if not args.write:
        out.say("(dry-run; re-run with --write to apply)")
    return EXIT_OK


def cmd_tl_context(args: argparse.Namespace, out: _Out) -> int:
    """Report -- and optionally patch -- strings that differ by screen.

    Ren'Py keeps one string table for the whole game, so ``Enter`` can only
    have one translation unless something supplies context.  Two mechanisms
    are supported and this command reports both:

    * a ``{#tag}`` embedded in the source literal (needs the .rpy sources),
    * a ``translate <lang> python:`` shim that reads the current screen name
      (works on games that ship only .rpyc).

    The shim is the one written here.  Tags are shown as a suggestion because
    applying them means editing game source, which this command never does.
    """
    from . import contextmap

    dump = args.dump
    if not os.path.exists(dump):
        raise InvalidRequestError(f"no such dump: {dump}")

    rows = contextmap.load_rows(dump)

    # The dump has one row per *occurrence*, which is what makes the screen
    # union meaningful.  filter_strings collapses to one row per text (the
    # string table is global), so it is used only to decide which texts are
    # worth translating at all -- not to group.
    try:
        from . import tlgen

        keep = {r["text"] for r in tlgen.filter_strings(rows, engine=False)}
    except Exception:  # pragma: no cover - probe format drift
        keep = None

    only = set(args.only or ())
    ignore = set(args.ignore or ())
    if args.ignore_file:
        if not os.path.exists(args.ignore_file):
            raise InvalidRequestError(f"no such ignore file: {args.ignore_file}")
        with open(args.ignore_file, "r", encoding="utf-8-sig") as handle:
            for line in handle:
                item = line.strip()
                if item and not item.startswith("#"):
                    ignore.add(item)
    found = contextmap.find_ambiguities(rows, keep=keep, ignore=ignore)
    total_found = len(found)
    if only:
        found = [item for item in found if item.text in only]
    elif not args.show_all and total_found > 20:
        # Without a selection, show only the smallest useful set: the caller
        # asked for "what needs handling", not a full survey.
        found = found[:20]

    if not found:
        out.say("no ambiguous strings found")
        return EXIT_OK

    # Report the real total, not the truncated length: printing "20 string(s)"
    # while 130 exist made --ignore look like it did nothing.
    if len(found) == total_found:
        out.say(f"{total_found} string(s) appear on more than one screen:\n")
    else:
        out.say(f"{total_found} string(s) appear on more than one screen "
                f"(showing {len(found)}):\n")
    for item in found:
        out.say("  " + item.describe())
    if len(found) < total_found:
        out.say("")
        out.say(f"pass --show-all to list the remaining "
                f"{total_found - len(found)}, or --only TEXT to pick one")
    out.say("")
    out.say("To split these, either tag the source literal:")
    out.say('    "Continue"  ->  "{#rtk-main_menu}Continue"')
    out.say("or generate the runtime shim (works on .rpyc-only games):")
    out.say(f"    tl context {dump} --language {args.language} "
            f"--tl-dir <game>/tl/{args.language} --only Continue --write")

    if not only:
        return EXIT_FINDINGS

    tl_dir = args.tl_dir
    if not tl_dir:
        raise InvalidRequestError(
            "writing the context patch needs --tl-dir "
            "(the tl/<language> directory to place it in)")
    if not os.path.isdir(tl_dir):
        raise InvalidRequestError(f"no such tl directory: {tl_dir}")

    # Existing translations become the fallback for every screen not listed,
    # so the shim only has to carry the exceptions.  Read them back from the
    # tl files rather than asking the user to restate them.
    current: dict[str, str] = {}
    for file_path in _iter_tl_files(tl_dir):
        tl = tlmod.parse_tl_file(file_path)
        for block in tl.blocks:
            if block.kind != "strings":
                continue
            for pair in block.strings:
                if pair.old and pair.new:
                    current.setdefault(pair.old, pair.new)

    overrides: dict[tuple[str, str], str] = {}
    for item in found:
        have = current.get(item.text)
        if not have:
            out.say(f"! {item.text!r}: no translation yet, cannot split it")
            continue
        for screen in item.screens:
            # The default translation is already in the string table, so only
            # screens that need something *different* belong in the shim.
            overrides[(screen, item.text)] = have

    if not overrides:
        out.say("\nnothing to write: add the base translation first")
        return EXIT_FINDINGS

    path = contextmap.write_patch(
        tl_dir, args.language, overrides,
        header="Split translations for strings that mean different things\n"
               "on different screens.  Generated by renpy-toolkit; safe to\n"
               "delete.",
        dry_run=not args.write,
    )
    if args.write:
        out.say(f"\nwrote {path} ({len(overrides)} override(s))")
        out.say("Edit the values there to give each screen its own wording.")
    else:
        out.say(f"\ndry run -- would write {path} ({len(overrides)} override(s))")
        out.say("re-run with --write to create it")
    return EXIT_OK


def cmd_tl_untranslatable(args: argparse.Namespace, out: _Out) -> int:
    """Report strings that the engine can never translate.

    An f-string is assembled at runtime, and Ren'Py translates *before* it
    interpolates (``renpy/substitutions.py`` calls ``translate_string`` and only
    then expands ``[var]``). By the time the finished sentence exists the lookup
    is long over, so a ``strings`` entry keyed on it can never match.

    This is a source problem, not a translation problem: the fix is to write a
    static literal with ``[var]`` placeholders and format it, or to translate
    the template explicitly. ``tl fix-fstrings`` covers both cases without
    editing the game's source; editing it is only needed for the ``manual``
    rows that command reports.
    """
    from . import tlgen

    dump = args.dump
    if not os.path.exists(dump):
        raise InvalidRequestError(f"no such dump: {dump}")

    rows = tlgen.filter_fstrings(tlgen.load_fstrings(dump))
    if not rows:
        out.say("no untranslatable f-strings found")
        return EXIT_OK

    out.say(f"{len(rows)} string(s) are assembled at runtime and cannot be "
            f"translated as written:\n")
    shown = rows if args.show_all else rows[:20]
    for row in shown:
        out.say(f"  {row.get('file')}:{row.get('line')}")
        out.say(f"    {row.get('text')}")
    if len(shown) < len(rows):
        out.say(f"  ... and {len(rows) - len(shown)} more "
                f"(use --show-all to list them)")
    out.say("")
    out.say("An f-string is evaluated before the translation lookup, so no")
    out.say("strings entry can ever match it. Two ways forward, neither of which")
    out.say("needs the game's source:")
    out.say("")
    out.say("  tl fix-fstrings <dump> --language chinese --outdir <dir> --write")
    out.say("      exact rows  -> strings entries for every finished sentence")
    out.say("      pattern rows-> a translate <lang> python: block that rewrites")
    out.say("                     the finished sentence at runtime")
    out.say("")
    out.say("Editing the source is only needed for the 'manual' rows that command")
    out.say("reports (a format spec or an expression cannot be rebuilt from text):")
    out.say('    f"You bought 1 {item}!"')
    out.say('    _("You bought 1 [item]!").format(item=item)')
    return EXIT_FINDINGS


def cmd_tl_fix_fstrings(args: argparse.Namespace, out: _Out) -> int:
    """Generate files that make runtime-assembled strings translatable.

    Two routes, both additive and neither touching the game's source:

    * ``exact`` -- when every field's values are enumerated with ``--values``,
      the finished sentences go into a ``translate <lang> strings:`` block.  A
      screen rendering one of those sentences looks it up again, so this works.
    * ``pattern`` -- otherwise a ``translate <lang> python:`` shim rewrites the
      finished sentence from a pattern.

    Rows that can use neither (a format spec, a conversion, or an expression in
    a field) are reported as needing a source change rather than silently
    skipped, because a pattern would capture an already-rendered value and could
    not reproduce it.
    """
    from . import fstringfix
    from . import tlgen

    dump = args.dump
    if not os.path.exists(dump):
        raise InvalidRequestError(f"no such dump: {dump}")

    only: set[tuple[str, int]] | None = None
    if args.only:
        only = set()
        for spec in args.only:
            path, _, line = spec.rpartition(":")
            if not path or not line.strip().isdigit():
                raise InvalidRequestError(
                    f"--only needs FILE:LINE but got {spec!r}")
            only.add((path, int(line)))

    rows = tlgen.filter_fstrings(tlgen.load_fstrings(dump))
    # Read the strings already registered in the target directory: repeating one
    # of them is not a merge, it is a startup crash.  The files this command
    # owns are excluded so a second run does not consider its own output.
    already = fstringfix.existing_olds(
        args.outdir,
        exclude=(fstringfix.strings_filename(args.language),
                 fstringfix.patch_filename(args.language)),
    )
    fixes = fstringfix.analyse(
        rows,
        values=fstringfix.parse_values(args.values),
        only=only,
        already=already,
    )
    if not fixes:
        out.say("no f-string needs a fix here")
        return EXIT_OK

    shown = fixes if args.show_all else fixes[:20]
    out.say(f"{len(fixes)} string(s) are assembled at runtime:\n")
    for fix in shown:
        out.say(f"  {fix.file}:{fix.line}  [{fix.route}] {fix.source}")
        out.say(f"    {fix.reason}")
    if len(shown) < len(fixes):
        out.say(f"  ... and {len(fixes) - len(shown)} more (use --show-all)")
    out.say("")

    translations: dict[str, str] = {}
    for spec in args.translate or []:
        template, sep, new = spec.partition("=")
        if not sep or not template:
            raise InvalidRequestError(
                f"--translate needs TEMPLATE=NEW but got {spec!r}")
        translations[template] = new

    actionable = [fix for fix in fixes if fix.route in ("exact", "pattern")]
    manual = [fix for fix in fixes if fix.route == "manual"]
    if manual:
        out.say(f"{len(manual)} row(s) cannot be fixed without editing the "
                f"game's source:")
        for fix in manual[:10] if not args.show_all else manual:
            out.say(f"  {fix.file}:{fix.line}  {fix.reason}")
        if len(manual) > 10 and not args.show_all:
            out.say(f"  ... and {len(manual) - 10} more (use --show-all)")
        out.say("")

    if not actionable:
        out.say("nothing can be generated for the remaining rows")
        return EXIT_FINDINGS

    paths = fstringfix.write_fixes(
        args.outdir, args.language, actionable,
        header=None, translations=translations, dry_run=not args.write,
    )
    for path in paths:
        out.say(f"{'wrote' if args.write else 'would write'} {path}")
    if not args.write:
        out.say("")
        out.say("dry run: pass --write to create these files, then review them")
        out.say("and copy them into the game's tl/<language>/ directory.")
    return EXIT_FINDINGS if manual else EXIT_OK


def cmd_tl_glossary(args: argparse.Namespace, out: _Out) -> int:
    from . import glossary

    action = getattr(args, "glossary_action", None)
    if action is None:
        raise InvalidRequestError("tl glossary: choose check / list / rename")

    all_terms: list[glossary.Term] = []
    if getattr(args, "ui_terms", False):
        all_terms.extend(glossary.load_terms(glossary.UI_TERMS_PATH))
    for path in args.terms or ():
        all_terms.extend(glossary.load_terms(path))
    if not all_terms:
        raise InvalidRequestError(
            "no glossary given: pass --terms PATH (repeatable) and/or --ui-terms"
        )
    scope = getattr(args, "scope", None)
    terms = [t for t in all_terms if scope is None or t.scope == scope]

    if action == "list":
        wanted = terms
        if args.category:
            wanted = [t for t in wanted if t.category in set(args.category)]
        if args.banned:
            wanted = [t for t in wanted if t.banned]
        for term in wanted:
            line = f"{term.source}  ->  {term.target}  [{term.category}/{term.scope}]"
            if term.banned:
                line += "  banned: " + ", ".join(term.banned)
            out.say(line)
        out.say(f"{len(wanted)} of {len(all_terms)} term(s)")
        return EXIT_OK

    if action == "rename":
        result = glossary.apply_rename(
            args.tl_dir,
            args.old,
            args.new,
            dry_run=not args.write,
            backup=not args.no_backup,
            files=args.file,
        )
        out.say(f"{args.old!r} -> {args.new!r}: {result.summary()}")
        for problem in result.problems[:10]:
            out.say("  " + problem)
        if result.problems:
            out.say(f"  ... and {len(result.problems) - 10} more problem(s)")
        if not args.write:
            out.say("")
            out.say("dry run: pass --write to apply it")
        return EXIT_FINDINGS if result.problems else EXIT_OK

    # action == "check"
    report = glossary.check_terms(
        args.tl_dir,
        terms,
        categories=args.category,
        files=args.file,
        report_missing=args.missing,
        report_drift=args.drift,
    )
    out.say(report.text())
    if args.strict and report.errors:
        return EXIT_FINDINGS
    return EXIT_OK


def cmd_tl_port(args: argparse.Namespace, out: _Out) -> int:
    from . import port

    result = port.port_translations(
        args.skeleton_dir,
        args.reference_dir,
        dry_run=not args.write,
        backup=not args.no_backup,
        files=args.file,
    )
    out.say(result.summary())
    for rel in result.files:
        out.say("  " + rel)
    if result.skipped:
        shown = result.skipped if args.show_skipped else result.skipped[:10]
        out.say("")
        out.say("skipped (the script changed, so the old translation no longer fits):")
        for item in shown:
            out.say("  " + item)
        if len(shown) < len(result.skipped):
            out.say(f"  ... and {len(result.skipped) - len(shown)} more "
                    f"(pass --show-skipped to list them all)")
    if not args.write:
        out.say("")
        out.say("dry run: pass --write to apply it")
    return EXIT_FINDINGS if result.skipped else EXIT_OK


def cmd_tl_polish(args: argparse.Namespace, out: _Out) -> int:
    from . import polish

    total_replaced = 0
    total_problems = 0
    for script in args.scripts:
        batch = polish.read_polish_script(script)
        out.say(f"{os.path.basename(script)}: {batch.entries} entry/-ies")
        result = polish.apply_polish(
            args.tl_dir,
            batch,
            dry_run=not args.write,
            backup=not args.no_backup,
            files=args.file,
        )
        out.say("  " + result.summary())
        total_replaced += result.replaced
        total_problems += len(result.problems)
        shown = result.problems if args.show_problems else result.problems[:10]
        for problem in shown:
            out.say("  ! " + problem)
        if len(shown) < len(result.problems):
            out.say(f"  ... and {len(result.problems) - len(shown)} more "
                    f"(pass --show-problems to list them all)")
    out.say("")
    out.say(f"total: {total_replaced} line(s) replaced, {total_problems} problem(s)")
    if not args.write:
        out.say("dry run: pass --write to apply it")
    return EXIT_FINDINGS if total_problems else EXIT_OK


def cmd_tl_hardcoded(args: argparse.Namespace, out: _Out) -> int:
    from . import hardcoded
    from . import tl as tlmod

    action = getattr(args, "hardcoded_action", None)
    if action is None:
        raise InvalidRequestError("tl hardcoded: choose scan / show / apply")

    language = args.language
    path = hardcoded.ledger_path(args.tl_dir, language)

    if action == "scan":
        if args.from_tl:
            # Fallback when no probe dump is at hand: rebuild from the strings
            # blocks already in the TL directory.  This cannot tell a hardcoded
            # literal from a native `_()` one, so the ledger becomes a plain
            # inventory rather than a hardcoded-only record.
            entries: dict[str, hardcoded.HardcodedEntry] = {}
            for file_path in _iter_tl_files(args.tl_dir):
                tl = tlmod.parse_tl_file(file_path)
                rel = os.path.relpath(file_path, args.tl_dir).replace(os.sep, "/")
                for block in tl.blocks:
                    if block.kind != "strings":
                        continue
                    anchor = block.source_comment or rel
                    for pair in block.strings:
                        if pair.old:
                            entries[pair.old] = hardcoded.HardcodedEntry(
                                old=pair.old,
                                new=pair.new or "",
                                source=anchor,
                                context="strings",
                            )
            fresh = hardcoded.HardcodedLedger(language=language)
            for entry in entries.values():
                fresh.add(entry)
        else:
            dump = _find_probe_dump(args.dump, args.tl_dir)
            rows = _load_probe_rows(dump)
            # Keep the ledger in lockstep with what `tl gen` actually wrote:
            # filter_strings drops image paths, colour codes, dev tooling and
            # duplicates, and an unfiltered ledger would list ~2x the real set.
            from . import tlgen
            game_rows = [row for row in rows if not row.get("engine")]
            keep = {str(row.get("text"))
                    for row in tlgen.filter_strings(game_rows, engine=False)}
            fresh = hardcoded.from_rows(rows, language=language, keep=keep)

        # Fold into the existing ledger instead of replacing it: hand-typed
        # ``added`` dates and translations must survive a rescan.
        existing = hardcoded.load_ledger(path, language=language)
        ledger = hardcoded.merge_ledger(existing, fresh) if existing.entries else fresh
        gone = sum(1 for entry in ledger.entries if entry.removed)
        if args.write:
            written = hardcoded.save_ledger(ledger, path)
            out.say(f"scanned {len(ledger.entries)} hardcoded string(s) -> {written}")
            if gone:
                out.warn(f"{gone} entr(ies) no longer appear in the source (removed: true)")
        else:
            out.say(f"would record {len(ledger.entries)} hardcoded string(s) "
                    f"({len(fresh.entries)} seen now, {gone} gone)")
            out.say("(dry-run; re-run with --write to save)")
        return EXIT_OK

    if action == "show":
        if not os.path.exists(path):
            out.say(f"no ledger at {path} (run 'tl hardcoded scan --write' first)")
            return EXIT_OK
        ledger = hardcoded.load_ledger(path, language=language)
        shown = 0
        for entry in ledger.entries:
            if args.removed and not entry.removed:
                continue
            flag = " [gone]" if entry.removed else ""
            out.say(f"{entry.source or '-':<28} {entry.context or '-':<14} "
                    f"{entry.old}{flag}")
            shown += 1
        out.say(f"\n{shown} entr(ies)")
        return EXIT_OK

    # apply
    if not os.path.exists(path):
        raise InvalidRequestError(f"{path}: no ledger (run 'tl hardcoded scan --write' first)")
    ledger = hardcoded.load_ledger(path, language=language)
    written = 0
    for file_path in _iter_tl_files(args.tl_dir):
        with open(file_path, "rb") as handle:
            raw = handle.read()
        text, _encoding, has_bom = _decode_bom(raw)
        updated = hardcoded.annotate_blocks(text, ledger)
        if updated == text:
            continue
        written += 1
        out.say(("wrote " if args.write else "would write ") + os.path.relpath(file_path, args.tl_dir))
        if args.write:
            payload = ("\ufeff" if has_bom else "") + updated
            with open(file_path, "wb") as handle:
                handle.write(payload.encode("utf-8"))
    if not written:
        out.say("no block matched the ledger")
        return EXIT_OK
    if not args.write:
        out.say("(dry-run; re-run with --write to apply)")
    return EXIT_OK


def _find_probe_dump(explicit: str | None, tl_dir: str) -> str:
    """Locate ``rtk_strings.json``: explicit path, or under the game directory."""
    if explicit:
        if not os.path.exists(explicit):
            raise InvalidRequestError(f"no such dump: {explicit}")
        return explicit
    # tl_dir is <game>/game/tl/<lang>; walk up to the project root and look for
    # the scratch tree the generator leaves behind.
    probe = os.path.abspath(tl_dir)
    for _ in range(5):
        probe = os.path.dirname(probe)
        if not probe or os.path.dirname(probe) == probe:
            break
        scratch = os.path.join(probe, ".renpy-toolkit-gen")
        if os.path.isdir(scratch):
            for root, _dirs, files in os.walk(scratch):
                if "rtk_strings.json" in files:
                    return os.path.join(root, "rtk_strings.json")
    raise InvalidRequestError(
        "no rtk_strings.json found; pass --dump PATH, or re-run 'tl gen' "
        "(it prints the dump path), or use --from-tl"
    )


def _load_probe_rows(dump: str) -> list[dict]:
    """Read the ``data`` array out of a probe dump."""
    with open(dump, "rb") as handle:
        data = json.loads(handle.read().decode("utf-8-sig"))
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise InvalidRequestError(f"{dump}: expected a 'data' array")
    return [row for row in rows if isinstance(row, dict)]


def _decode_bom(data: bytes) -> tuple[str, str, bool]:
    """Decode bytes, detecting a UTF-8 BOM without losing it."""
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8):].decode("utf-8", "replace"), "utf-8-sig", True
    return data.decode("utf-8", "replace"), "utf-8", False


# ---------------------------------------------------------------------------
# sdk
# ---------------------------------------------------------------------------
def cmd_sdk(args: argparse.Namespace, out: _Out) -> int:
    from .sdk import RenPySDK

    if args.action == "find":
        sdk = RenPySDK.discover(args.sdk)
        out.say(sdk.info())
        return EXIT_OK

    basedir = getattr(args, "basedir", None)
    sdk = RenPySDK.discover(args.sdk, basedir=basedir)
    out.say(sdk.info())
    kwargs: dict[str, object] = {}
    if args.timeout:
        kwargs["timeout"] = args.timeout

    if args.action == "translate":
        result = sdk.translate(
            args.basedir,
            args.language,
            count_only=args.count,
            strings_only=args.strings_only,
            empty=args.empty,
            **kwargs,
        )
        out.say(result.tail())
        if args.count:
            missing = result.missing_count()
            if missing is not None:
                out.say(f"missing: {missing}")
        return EXIT_OK if result.ok else EXIT_FINDINGS

    if args.action == "lint":
        result = sdk.lint(args.basedir, **kwargs)
        out.say(result.tail())
        return EXIT_OK if result.ok else EXIT_FINDINGS

    if args.action == "dialogue":
        result = sdk.dialogue(
            args.basedir,
            args.language,
            strings=args.strings,
            notags=args.notags,
            **kwargs,
        )
        out.say(result.tail())
        return EXIT_OK if result.ok else EXIT_FINDINGS

    if args.action == "extract-strings":
        result = sdk.extract_strings(
            args.basedir, args.language, args.destination, **kwargs
        )
        out.say(result.tail())
        return EXIT_OK if result.ok else EXIT_FINDINGS

    if args.action == "merge-strings":
        result = sdk.merge_strings(
            args.basedir, args.language, args.source, replace=args.replace, **kwargs
        )
        out.say(result.tail())
        return EXIT_OK if result.ok else EXIT_FINDINGS

    if args.action == "compile":
        result = sdk.compile(args.basedir, **kwargs)
        out.say(result.tail())
        return EXIT_OK if result.ok else EXIT_FINDINGS

    raise InvalidRequestError("sdk: choose find / translate / lint / dialogue / "
                              "extract-strings / merge-strings / compile")


# ---------------------------------------------------------------------------
# font
# ---------------------------------------------------------------------------
def cmd_font(args: argparse.Namespace, out: _Out) -> int:
    from . import fontmap, fonts

    if args.action == "map":
        required = ["regular"]
        if args.all_states:
            required = ["regular", "bold", "italic", "bold+italic"]
        else:
            if args.require_bold:
                required.append("bold")
                if args.require_italic:
                    required.extend(["italic", "bold+italic"])
            elif args.require_italic:
                required.extend(["italic"])

        report = fontmap.audit_fonts(args.project_dir, require_states=required)
        out.say(report.text())

        if args.emit:
            pairs = [(entry.key, entry.value) for entry in report.entries]
            if not pairs:
                raise InvalidRequestError("nothing to emit: the project has no font mappings")
            out.say("")
            out.say(fontmap.render_font_map_block(pairs, language=args.emit))
        return EXIT_OK if report.ok else EXIT_FINDINGS

    if args.action != "check":
        raise InvalidRequestError("font: choose check or map")

    texts = list(fonts.iter_project_texts(args.tl_dir))
    if args.extra_text:
        with open(args.extra_text, "rb") as handle:
            texts.append(handle.read().decode("utf-8-sig", "replace"))
    if not texts:
        out.warn("no translated text found; nothing to check")
        return EXIT_OK

    missing, per_font = fonts.check_fonts(args.fonts, texts)
    out.say(fonts.missing_report(missing, per_font))
    return EXIT_FINDINGS if missing else EXIT_OK


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------
def cmd_project(args: argparse.Namespace, out: _Out) -> int:
    if args.action == "new":
        from .scaffold import new_project

        result = new_project(
            args.name,
            dest=args.dest,
            resolution=args.resolution,
            language=args.language,
            author=args.author,
            overwrite=args.overwrite,
        )
        for path in result.files:
            out.say("  " + path)
        out.say(result.summary())
        return EXIT_OK

    if args.action == "audit":
        from .audit import scan_project

        report = scan_project(
            args.project_dir, include_archives=not args.no_archives
        )
        out.say(report.text())
        return EXIT_OK if report.ok else EXIT_FINDINGS

    raise InvalidRequestError("project: choose new / audit")


# ---------------------------------------------------------------------------
# decompile
# ---------------------------------------------------------------------------
def cmd_decompile(args: argparse.Namespace, out: _Out) -> int:
    from . import decompile as dec

    if args.action == "file":
        if args.list_slots:
            for index, payload in enumerate(dec._slot_payloads(args.rpyc)):
                out.say(f"slot {index}: {len(payload)} bytes")
            return EXIT_OK
        text, unsupported = dec.decompile_file(args.rpyc)
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            out.say(f"wrote {args.output}")
        else:
            sys.stdout.write(text)
        if unsupported:
            out.warn(
                "unsupported nodes: "
                + ", ".join(f"{k}×{v}" for k, v in sorted(unsupported.items()))
            )
        return EXIT_OK

    if args.action == "dir":
        result = dec.decompile_dir(
            args.root,
            dest=args.dest,
            overwrite=args.overwrite,
            pattern=args.pattern,
            backup=args.backup,
            dry_run=not args.write,
        )
        for path in result.written:
            out.say("  + " + path)
        for path in result.skipped:
            out.say("  = " + path + "  (exists, --overwrite to replace)")
        out.say(result.summary())
        if result.unsupported:
            out.warn(
                "unsupported nodes: "
                + ", ".join(f"{k}×{v}" for k, v in sorted(result.unsupported.items()))
            )
        return EXIT_OK

    if args.action == "verify":
        from .verify import verify_decompile

        check = verify_decompile(args.tl_dir, against=args.against)
        out.say(check.text())
        return EXIT_OK if check.ok else EXIT_FINDINGS

    raise InvalidRequestError("decompile: choose file / dir / verify")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def _tl_actions() -> list[str]:
    """Return the ``tl`` sub-commands the parser actually registers.

    ``_TL_ACTION_HANDLERS`` only lists the actions that need their own
    dispatcher; ``stats``/``verify``/``missing``/``table``/``apply`` are served
    by ``cmd_tl`` itself. Reporting the dispatch table's length therefore
    understates the CLI, so read the parser instead.
    """
    parser = build_parser()
    for action in parser._actions:                      # noqa: SLF001 - argparse
        if isinstance(action, argparse._SubParsersAction):
            tl_parser = action.choices.get("tl")
            if tl_parser is None:
                continue
            for sub in tl_parser._actions:              # noqa: SLF001
                if isinstance(sub, argparse._SubParsersAction):
                    return sorted(sub.choices)
    return sorted(_TL_ACTION_HANDLERS)


def cmd_selftest(args: argparse.Namespace, out: _Out) -> int:
    """Check that this copy of the package actually works on this machine.

    Motivated by the portable distribution: a user unzips a folder and needs
    one command that says "this works" or names what is broken, instead of
    discovering a half-unpacked archive three commands later. Everything here
    runs against a temporary directory, so a self-check never writes into the
    game the user is about to translate.
    """
    import platform
    import tempfile

    from . import cli as _cli

    checks: list[tuple[str, str]] = []

    def note(name: str, detail: str) -> None:
        checks.append((name, detail))

    ok = True

    note("python", "%d.%d.%d (%s)"
         % (sys.version_info[0], sys.version_info[1], sys.version_info[2],
            platform.platform(terse=True)))

    # Package imports and version metadata.
    try:
        from . import __all__ as exported
        from . import rpa, rpyc, tl, verify  # noqa: F401
        note("import", "27 个子模块可用，导出 %d 个名字" % len(exported))
    except Exception as exc:                      # pragma: no cover
        note("import", "FAILED: %s" % exc)
        ok = False

    # Optional dependencies are allowed to be absent; report, never fail.
    for name in ("fontTools", "openpyxl"):
        try:
            __import__(name)
        except Exception:
            note("optional", "%s 未安装（字体覆盖率 / XLSX 表格不可用，其余功能正常）" % name)
        else:
            note("optional", "%s 已安装" % name)

    # Round-trip an archive: exercises zlib, the index pickle and the whitelist
    # Unpickler. Entries are handed in as bytes, so no game files are touched.
    # Note ``create()`` rather than the constructor: passing a path to
    # ``RenPyArchive`` means "open this existing archive".
    try:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, "t.rpa")
            archive = rpa.RenPyArchive.create(archive_path)
            archive.add("hello.txt", b"hello")
            archive.save()
            # save() leaves the write handle open by design (a caller may keep
            # appending); on Windows that keeps the file locked, so a self-check
            # that writes into a temp dir has to close it before cleanup.
            archive.close()
            reread = rpa.RenPyArchive.open(archive_path)
            payload = reread.read("hello.txt")
            reread.close()
        if payload != b"hello":
            raise AssertionError("round-trip returned %r" % (payload,))
        note("rpa", "RPAv3 写入 / 索引 / 读回 一致")
    except Exception as exc:
        note("rpa", "FAILED: %s" % exc)
        ok = False

    # Parse a tiny translation file and verify it.
    try:
        sample = (
            "translate chinese start_636ae3f5:\n"
            "\n"
            "    # e \"Hello.\"\n"
            "    e \"你好。\"\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "script.rpy")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(sample)
            parsed = tl.parse_tl_file(path)
            blocks = len(parsed.blocks)
            problems = len(verify.check_quotes("你好。"))
        if blocks != 1 or problems:
            raise AssertionError("blocks=%d problems=%d" % (blocks, problems))
        note("tl parse", "translate 块解析与引号检查正常")
    except Exception as exc:
        note("tl parse", "FAILED: %s" % exc)
        ok = False

    # The command table must actually contain what the help text promises.
    try:
        missing = [g for g in ("rpa", "tl", "sdk", "font", "decompile", "project")
                   if g not in _cli._HANDLERS]
        # Count what the parser actually offers, not what the dispatch table
        # happens to specialise: five tl actions (stats/verify/missing/table/
        # apply) share cmd_tl, so _TL_ACTION_HANDLERS alone undercounts them.
        actions = len(_tl_actions())
        if missing or actions < 10:
            raise AssertionError("missing groups=%r tl actions=%d" % (missing, actions))
        note("commands", "%d 个命令组、%d 个 tl 子命令" % (len(_cli._HANDLERS), actions))
    except Exception as exc:
        note("commands", "FAILED: %s" % exc)
        ok = False

    for name, detail in checks:
        out.say(f"  {name:<10} {detail}")

    if ok:
        out.say(f"self-check passed: {PROG} {__version__}")
        return EXIT_OK
    out.warn("self-check FAILED: see the lines marked FAILED above")
    return EXIT_ENVIRONMENT


# The release tree's generated entry point needs a command that answers "does
# this copy actually work here". It lives in the package rather than in the
# launcher so ``--selftest`` is available in every distribution.
_HANDLERS = {
    "rpa": cmd_rpa,
    "tl": cmd_tl,
    "sdk": cmd_sdk,
    "font": cmd_font,
    "decompile": cmd_decompile,
    "project": cmd_project,
}

# ``tl`` grew past the point where one function should own every action: the
# tidy-up commands (sort / split / hardcoded) each walk the directory tree.
_TL_ACTION_HANDLERS = {
    "init": cmd_tl_init,
    "gen": cmd_tl_gen,
    "sort": cmd_tl_sort,
    "split": cmd_tl_split,
    "hardcoded": cmd_tl_hardcoded,
    "glossary": cmd_tl_glossary,
    "context": cmd_tl_context,
    "untranslatable": cmd_tl_untranslatable,
    "fix-fstrings": cmd_tl_fix_fstrings,
    "port": cmd_tl_port,
    "polish": cmd_tl_polish,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    out = _Out(getattr(args, "quiet", False))

    if getattr(args, "selftest", False):
        return cmd_selftest(args, out)

    if not getattr(args, "group", None):
        parser.print_help()
        return EXIT_USAGE

    group = args.group
    if not getattr(args, "action", None):
        parser.parse_args([group, "--help"])
        return EXIT_USAGE

    handler = _HANDLERS[group]
    if group == "tl":
        handler = _TL_ACTION_HANDLERS.get(args.action, cmd_tl)
    try:
        return handler(args, out)
    except DependencyError as exc:
        out.warn(f"error: {exc}")
        return EXIT_ENVIRONMENT
    except (SDKNotFoundError,) as exc:
        out.warn(f"error: {exc}")
        return EXIT_ENVIRONMENT
    except VerifyError as exc:
        out.warn(f"error: {exc}")
        return EXIT_FINDINGS
    except (InvalidRequestError, ToolkitError) as exc:
        out.warn(f"error: {exc}")
        return EXIT_FINDINGS
    except KeyboardInterrupt:  # pragma: no cover
        out.warn("interrupted")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
