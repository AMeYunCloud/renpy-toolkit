"""renpy_toolkit — Ren'Py 游戏制作与汉化工具集.

模块划分::

    rpa      .rpa 归档读写（RPAv2 / RPAv3，含 XOR 混淆与 prefix 切片）
    rpyc     .rpyc slot 解压 / 结构校验 / 新鲜度判断（只认源文件 md5）
    decompile .rpyc → .rpy 反编译（白名单 pickle VM，绝不 pickle.loads）
    tl       .rpy 的 translate / strings / python 块解析与安全回填
    sdk      Ren'Py SDK CLI 驱动（translate / dialogue / extract_strings /
             merge_strings / lint / compile）
    tables   翻译表格导入导出（JSON / CSV / TSV / PO / XLSX）
    protect  Ren'Py 文本标签与占位符保护（机翻流水线的安全带）
    fonts    字体 cmap 覆盖度与中文字形缺字检测
    fontmap  config.font_replacement_map 审计：硬编码字体有没有被替换、四种
             (bold, italics) 状态是否齐全、映射目标是否真在盘上
    verify   TL 完整性与覆盖度校验（找"看着翻了其实没生效"）
    sorttl   TL 块按 # game/x.rpy:NNN 注释重排 + Event Index 生成
    stringsplit  多 old/new 的 strings 块拆成一条一块的标准形态
    hardcoded    硬编码文本清单（独立 JSON，不靠 _() 判别身份）
    contextmap   跨屏多义字符串消歧（{#context} 或运行时补丁）
    fstringfix   f-string 运行期拼装串 → 不改源码的补丁（strings / python 块）
    tlgen    全量骨架生成（驱动游戏自带运行时从内存取全部翻译单元）
    fresh    从已有 tl/<lang> 克隆新语言（无运行时时的备用路线）
    audit    工程静态体检（图片缺失 / 归档重叠 / 行尾混用）
    scaffold 最小可运行工程脚手架
    safeio   有界 zlib 解压（压缩炸弹防护）+ safe_join（条目不得越界）
    cli      命令行入口（``python -m renpy_toolkit``）

设计原则（都在具体模块的 docstring 里展开了，这里只列结论）::

1. **能调官方 CLI 就不自己算**。翻译标识符由引擎的 md5 规则派生，自己算
   等于把整个项目的译文押在一条逆向结论上，而且错了是静默的。
2. **写文件前先证明能写**。词法解析不了、块结构不唯一、行数会变、引号不
   配对 —— 一律拒绝写入并报错，绝不产出"半合法"的 .rpy。
3. **默认 dry-run**。所有会改文件的子命令都要显式 ``--write``。
4. **UTF-8 BOM 与行尾逐文件保留**。汉化工程里混着别人留下的 CRLF 文件，
   统一行尾会让整份 diff 变成重写。
5. **模块只依赖标准库**；``openpyxl`` / ``fonttools`` 按需 import，缺失时抛
   :class:`errors.DependencyError`，消息里带 pip 命令。
6. **把不受信任的输入当不受信任的输入**。``.rpa`` 索引是 pickle，只走白名单
   unpickler（:mod:`rpa`）；``.rpyc`` 是 pickle，只走 pickletools 白名单 VM
   （:mod:`decompile`）；三处 zlib 解压全部走 :mod:`safeio` 的有界接口，
   免得几百字节的压缩炸弹把内存吃光。

本包不依赖 Ren'Py 本体，也不 import 任何 Cython 模块：``.rpyc`` 只做 slot
级解压与字符串提取，不解 pickle（那需要引擎自己的类定义）。
"""

__version__ = "1.0.0"

__all__ = [
    "audit",
    "cli",
    "contextmap",
    "decompile",
    "errors",
    "fontmap",
    "fonts",
    "fresh",
    "fstringfix",
    "hardcoded",
    "protect",
    "rpa",
    "rpyc",
    "safeio",
    "scaffold",
    "sdk",
    "sorttl",
    "stringsplit",
    "tables",
    "tl",
    "tlgen",
    "verify",
]
