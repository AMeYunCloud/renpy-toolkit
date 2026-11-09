# renpy-toolkit

一套**不依赖 Ren'Py 本体**的 Python 工具包，覆盖 Ren'Py 游戏的**制作辅助**与**汉化**两条链路。
纯标准库即可运行（`fontTools` / `openpyxl` 是可选增强），因此可以装在翻译工位、CI、
或没有 SDK 的机器上。

```bash
cd plugin/dsh-renpy-toolkit
python -m renpy_toolkit --help
python -m unittest discover -s tests -t tests -q     # 519 个用例
```

## 目录

- [模块地图](#模块地图)
- [相关项目与参考](#相关项目与参考)
- [为什么不是「包一层官方 CLI」](#为什么不是包一层官方-cli)
- [设计约束](#设计约束都是踩过的坑)
- **汉化链路**
  - [全量骨架生成 `tl gen`](#全量骨架生成tl-gen) —— 入口，`.rpyc`-only 也有效
  - [反编译器](#只有-rpyc-的游戏也能读源码反编译器)
  - [导出 → 翻译 → 回填 → 校验](#汉化导出--翻译--回填--校验)
  - [备份：`--backup-mode` 与 `tl backup`](#备份--backup-mode-与-tl-backup)
  - [译文整理 `sort` / `split` / `hardcoded`](#译文整理sort--split--hardcoded)
  - [游戏更新后不丢译文 `port`](#游戏更新后不丢译文port)
  - [术语字典与润色脚本 `glossary` / `polish`](#术语字典与润色脚本glossary--polish)
  - [跨屏多义字符串 `context`](#跨屏多义字符串context)
  - [永远翻不了的字符串 `untranslatable` / `fix-fstrings`](#永远翻不了的字符串untranslatable--fix-fstrings)
- **制作辅助**
  - [脚手架与体检 `project`](#制作脚手架与体检)
  - [SDK 桥接 `sdk`](#sdk-桥接)
- **资源与字体**
  - [归档与编译产物 `rpa` / `rpyc`](#归档与编译产物)
  - [字体验证与硬编码字体替换 `font`](#字体验证与硬编码字体替换)
- [开发](#开发)

## 模块地图

```
renpy_toolkit/                      29 个模块
├── __init__.py     包门面，__all__ 汇总
├── errors.py       统一异常层级（ToolkitError 为根）
│
│  ── 汉化：从发行版到可交付译文 ──
├── tlgen.py        ★ 全量骨架生成（驱动游戏自带运行时，对白 + 界面串一次拿全）
├── decompile.py    .rpyc → .rpy 反编译（白名单 pickle 虚拟机，从不 pickle.loads）
├── port.py         ★ 把旧译本按「标识符 + 源文本」搬到新骨架（游戏更新不丢译文）
├── polish.py       ★ 直接应用润色批处理脚本（ast.literal_eval，从不 exec）
├── glossary.py     ★ 术语字典：一致性校验 + 全局改名（terms.tsv 手改即生效）
├── tl.py           translate / strings / python 块的解析与回填（含三引号多行值）
├── fresh.py        从零造 tl/<lang> 骨架（无 SDK、只有 .rpyc 时用）
├── sorttl.py       按 `# game/x.rpy:NNN` 排序 + Event Index / 分段标记
├── stringsplit.py  一个 strings 块塞多条 old/new → 拆成标准形态
├── hardcoded.py    硬编码文本清单（不依赖 _() 判别）
├── contextmap.py   跨屏多义字符串的消歧（{#context} 或运行时补丁）
├── fstringfix.py   运行时拼装的串（f-string）→ 不改源码的补丁
├── fontmap.py      硬编码字体的替换映射（config.font_replacement_map）
├── protect.py      Ren'Py 文本标签与占位符保护
├── verify.py       TL 完整性与标识符正确性校验
├── tables.py       译文表导入导出（JSON / CSV / XLSX / PO）
├── backup.py       备份策略调度（.bak / 影子 Git 仓库 / 不备份）
├── gitbackup.py    影子仓库实现（hash-object + commit-tree，不碰用户仓库）
│
│  ── 制作 ──
├── scaffold.py     新工程脚手架
├── audit.py        静态工程体检
├── sdk.py          Ren'Py SDK CLI 驱动
│
│  ── 资源 ──
├── rpa.py          .rpa 归档读写（RPAv2 / RPAv3，含 XOR 混淆与 prefix）
├── rpyc.py         .rpyc slot 解压、结构校验、源码新鲜度判定
├── fonts.py        字体 cmap 覆盖度与缺字检测
├── safeio.py       有界解压、safe_join（归档条目/语言名不得越界）
│
├── cli.py          argparse 命令行入口
└── __main__.py     python -m renpy_toolkit
```

## 相关项目与参考

本工具包的设计与规则大量依据下列项目；其中的事实（标识符生成、RPA 格式、`.rpyc`
容器、标签表）都是从这些来源逐条核实后重实现的，**没有 import 它们的代码**。

| 项目 | 用途 |
| --- | --- |
| [renpy/renpy](https://github.com/renpy/renpy) | 引擎本体。`renpy/translation/`（标识符与字符串表）、`renpy/translation/generation.py`（官方 `translate` 生成器）、`renpy/text/extras.py`（标签表）、`renpy/loader.py`（归档优先级）都是本工具规则的**权威依据** |
| [Ren'Py 官方文档](https://www.renpy.org/doc/html/) | 翻译机制、CLI 参数、自定义文本标签的语义 |
| [CensoredUsername/unrpyc](https://github.com/CensoredUsername/unrpyc) | `.rpyc` 反编译的参考实现。`decompile.py` 的容器解析与 AST 语义对齐它，但改为 `pickletools` 白名单虚拟机（不执行 pickle） |
| [Dclef/renpy-tl](https://github.com/Dclef/renpy-tl) | 中文本地化的实战清单。`verify.py` 的检查项取舍、字数与标点规则、字体替换做法从中取材 |
| [ZYKsslm/RenPy-Tutorial](https://github.com/ZYKsslm/RenPy-Tutorial) | 中文教程，制作侧（脚手架、界面、发行）的入门参考 |

> 也读过 [rpatool](https://github.com/Shizmob/rpatool) 的归档实现（本仓库只保留了格式规范，
> 见 [`docs/rpa-format-spec.md`](../../docs/rpa-format-spec.md)）。

## 为什么不是「包一层官方 CLI」

官方 CLI（`renpy.sh <basedir> translate <lang>`）只做**静态提取** Say / Menu / `_()`，
不收 `Text()`、`show text`、`stack_notify` 里的字符串；社区工具还会按「有效长度 < 8」
之类规则静默丢弃短句。两者都**不会告诉你漏了什么**。所以本工具包的核心是把
「翻译单元 → 译文 → 回填」做成可校验、可对账的流程，而不是再封一层提取器。

`tl gen` 走的是另一条路：它把游戏**自己的运行时**跑起来，从引擎内存里取翻译单元
（`renpy.game.script.translator.file_translates` 与 SL2 屏幕树），所以

- 对白块数与引擎自己的 `lint` 报告**逐数字吻合**（实测 58,249 / 58,249）；
- `.rpyc`-only 的发行版也能拿到全量（官方 `translate` 命令在这种情况下只能生成
  `common.rpy`，因为它只扫磁盘上真实存在的 `.rpy`）；
- 界面字符串来自 AST 走查而非正则扫描，`text "..."` 这类没包 `_()` 的硬编码也能收到。

## 设计约束（都是踩过的坑）

- **字节级安全**：写 `.rpy` 一律二进制读写，保留 BOM 与**逐行**行尾；不统一行尾，
  不重排缩进。未修改的文件 `to_bytes()` 与原文件**逐字节相同**（有测试守着）。
- **不做静默降级**：缺 `fontTools` / `openpyxl` 时抛带安装提示的 `ImportError`；
  无法安全定位写入点时抛 `UnsafeWriteError` 而不是猜。
- **不破坏现场**：写盘前先保留旧内容，方式由 `--backup-mode` 决定（默认 `bak`
  在同目录留 `<名>.bak`；`git` 用影子仓库，可列出/回退到任意一次；`none` 不备份）；
  `sdk.py` 默认 `dry_run=True`，只返回命令行。
- **转义按 Ren'Py 语义**：译文里的换行写成两个字符 `\n`（真实换行会劈开语句、
  触发 `MISSING_TRANS_LINE` / `UNCLOSED_QUOTE`）；含 `\"` 的值自动转成 raw string。
  注意校验器看的是**盘上原文**（`SayLine.raw` / `StringPair.new_raw`），不是解码后的
  值——解码之后合法 `\n` 与非法真换行是同一个字符，检查解码值会把每一行正确译文
  都误报。
- **块外散落语句要报错**：真换行会把一条 say 劈成两半，后半句掉出 `translate` 块。
  这种文件仍「读得动」，覆盖率会显示 `0/0` 假装全绿，所以 `verify` 用
  `STRAY_STATEMENT` 显式报出来。
- **标识符不重造**：只从现有 `translate` 块读标识符，绝不自己拼 md5。
- **骨架用原文占位，不用空串**：`new ""` 不是「未翻译」，它是**有效译文**。
  `renpy/translation/__init__.py` 的 `StringTranslator.translate()` 里
  `new = self.translations.get(old, None); if new is not None: return new` ——
  空串不是 `None`，所以切到该语言后文字会**变成空白**。骨架因此写原文，
  未翻译处显示英文而不是消失；`verify` 用 `UNTRANSLATED` 警告标出这些位置。

## 全量骨架生成（`tl gen`）

这是整条汉化链路的入口。它对**只发 `.rpyc` 的发行版**同样有效。

```bash
# 默认 dry-run，只报数量
python -m renpy_toolkit tl gen <game> chinese

# 落到独立目录（推荐：先看产物再决定）
python -m renpy_toolkit tl gen <game> chinese --outdir work/tl --write

# 直接写进游戏
python -m renpy_toolkit tl gen <game> chinese --write
```

实测（58,249 句的发行版，`game/` 下只有 50 个 `.rpyc`，无 `.rpy`）：

```
chinese: 58249 translate block(s), 2687 string(s) in 50 file(s)
wrote 53 file(s)            耗时 19.6s
```

引擎 `lint` 对账：`The chinese translation contains 58,249 dialogue blocks` —— 与原文完全一致。

生成的块形如：

```
# game/script.rpy:56
translate chinese start_a55bd194:

    # mc "{i}{size=-6}Why wouldn't I? {w}My name is... [mc]."
    mc "{i}{size=-6}Why wouldn't I? {w}My name is... [mc]."
```

> **`--write` 只补缺失文件**：对已存在的输出目录再跑一次，会打印
> `53 file(s) already present, left untouched` 并**跳过全部内容**。想看到新策略的
> 产物必须换一个 `--outdir`，否则会误判覆盖率。

### 为什么需要跑游戏

`tl gen` 会在游戏目录下建一个临时探针目录（`<game>/.renpy-toolkit-gen/`），写入一个
注册了自定义命令的 `.rpy`，通过 `RENPY_SEARCHPATH` 注入，然后用游戏自带的
`lib/py3-*/python.exe` 启动。探针把结果写成 JSON，主进程读回并生成 TL。全程**不改
游戏脚本**；默认保留该目录（里面是 `rtk_strings.json`，`tl hardcoded` / `tl
untranslatable` 还要用），加 `--clean-scratch` 则跑完删掉。

落点不是写死的。游戏自带的解释器常比调用方受限（自建运行时、沙箱），本机的
`%TEMP%` 对它未必可写，所以按 `RTK_SCRATCH` → `--outdir` 父目录 → `%TEMP%` →
`~/.renpy-toolkit` → `<game>/.renpy-toolkit-gen` → 当前工作目录的顺序逐个探测，取
第一个**游戏自己的解释器**真能写进去的。当前工作目录排最后是有原因的：写不了
`%TEMP%` 的运行时会把当前目录当作自己的临时目录，一旦排在前面，几万行剧情就会落进
调用方所站的那棵树里。要指定位置就设 `RTK_SCRATCH`。

### 没有游戏运行时怎么办（`tl init`）

`tl gen` 需要能跑起游戏（Windows / Linux / macOS 的发行版都行，只要 `lib/` 里的
Python 可执行）。如果拿不到运行时——比如只有一堆散落的 `.rpyc`，或者跨平台不方便
跑——用 `tl init` 从**已有的** `tl/<lang>` 克隆一份空骨架：

```bash
python -m renpy_toolkit tl init <game> chinese --source game/tl/Spanish --write
```

它只复制结构（块头、标识符、源注释），译文全部换成原文占位。缺点很明显：
**能生成多少完全取决于源语言 TL 覆盖了多少**，所以 `tl gen` 永远是首选。

## 只有 `.rpyc` 的游戏也能读源码：反编译器

发行版游戏通常只留 `.rpyc`，没有 `.rpy` 源码。没有源码，`tl` 的一整套
「解析 → 导出 → 回填 → 校验」都无从谈起，所以这里实现了一个反编译器。

```bash
# 先看会产出什么，不写盘
python -m renpy_toolkit decompile dir game/tl/chinese -o work/tl

# 确认后落盘（已存在的 .rpy 默认不覆盖，覆盖前先备份旧内容）
python -m renpy_toolkit decompile dir game/tl/chinese -o work/tl --write

# 校验：每个 .rpyc 解出来的标识符 / strings / say 数量必须和源一致
python -m renpy_toolkit decompile verify game/tl/chinese
python -m renpy_toolkit decompile verify work/tl          # 也接受已落盘的源码目录
```

**安全性**：`.rpyc` 的内容是 Python pickle，而 `pickle.loads` 遇到
`GLOBAL os.system` 就会直接执行——把一份恶意游戏丢进工具链，等于把机器交出去。
所以 `decompile.py` **不使用 `pickle.loads`**，而是用 `pickletools.genops`
逐条 opcode 喂给自己写的白名单虚拟机：只实例化 `renpy.ast` / `renpy.atl` /
`renpy.parameter` / `renpy.sl2.slast` 等已知模块里的类，其它 `GLOBAL`
一律抛 `RpycError`。有测试 `test_os_system_is_refused` 守着这条线。

**验证方式**（实测：53 个 TL 文件的发行版）：

| 层级 | 做法 | 结果 |
| --- | --- | --- |
| 标识符 | AST 与反编译文本逐块比对 | 67,453 个标识符，0 差异 |
| 真实编译 | 把反编译出的 .rpy 交给 Ren'Py 自己 `compile` | exit 0，产出 54 个 .rpyc |
| 语义 | 原 .rpyc 与重编译 .rpyc 的 AST 事实比对 | 0 个文件不同 |

反编译**不可能完美**：`if`/`while`/`show`/`play` 之外的冷门语句、屏幕语言的
部分写法会被写成 `# TODO: unsupported node <kind>` 注释并计数上报。翻译工作
不依赖这些节点（译文只挂在 `translate` 块上），但**别指望拿它还原一份可发行的
完整源码**——它的定位是「让汉化能开始，并且能证明没漏东西」。

## 汉化：导出 → 翻译 → 回填 → 校验

```bash
# 0. 先看现状：每个文件的块数、已译/未译、覆盖率
python -m renpy_toolkit tl stats game/tl/chinese

# 只看还没翻译的条目（--kind 过滤 say / strings）
python -m renpy_toolkit tl missing game/tl/chinese

# 1. 把 tl/chinese 全部翻译单元导出成表（PO 便于接 Poedit / Weblate / LLM）
python -m renpy_toolkit tl table game/tl/chinese -o strings.po

# 2. 翻译后回填（默认 dry-run，加 --write 才落盘，并先备份旧内容）
python -m renpy_toolkit tl apply game/tl/chinese strings.po            # 预览
python -m renpy_toolkit tl apply game/tl/chinese strings.po --write    # 落盘

# 3. 校验：空译文、标签/占位符不符、行尾混用、字符串冲突、结巴丢失、全角引号
python -m renpy_toolkit tl verify game/tl/chinese --strict --fullwidth
```

退出码：`0` 通过 / `1` 发现问题 / `2` 用法错误 / `3` 环境不具备（缺 git、缺 Ren'Py SDK 等），可直接进 CI。

`tl verify` 的检查项都对着**真实成品译料**调过误报，依据记录在
[`docs/text-tags-and-interpolation.md`](../../docs/text-tags-and-interpolation.md)。
拿一份已上线的成品译料（56 文件 / 57,552 块 / 2,043 条 unique old）当基准，
全量只剩 **2 条报错，且逐条核对都是该工程的真缺陷**；自己生成的骨架是 **0 条**。

几条容易被误判、已写进规则的引擎事实：

- `{image=...}` / `{w}` / `{p}` 等**不接受闭合标签**（`renpy/text/extras.py: text_tags`
  里映射为 `False`，写 `{/image}` 引擎直接抛异常）。把它们当未闭合标签会误报合法的
  `new "\"魔法\"?{image=bg/icons/item_text.png}"`。
- `[...]` 自 Ren'Py 8.2.0 起是 **Python 表达式**，`[player[money]]` 是**一个**插值，不是两个。
- `[[` 与 `{{` 是转义，不是插值/标签 —— 源码里描述语法的
  `old "type filenames(ex: variable, ... or [[variable, variable])"` 就是例子。
- 三引号值（`new """..."""`）里的真换行是正文段落，不是被劈开的字面量。
- 中文译文常把英文的 “Human” 还原成玩家名 `[mc]`，这在源文本里没有，属正当译法（豁免）。

### 用引擎自己的 lint 对账

`tl verify` 数的是文件里的 `translate <lang> <id>:` 块。想确认跟引擎看到的一致，
就断言那个数字：

```bash
# 引擎 lint 报 "The chinese translation contains 58,249 dialogue blocks"
python -m renpy_toolkit tl verify game/tl/chinese --expect-dialogue 58249
```

不符时报 `error[BLOCK_COUNT]`，并给出差额。这一条能在几分钟内定位
「某个源文件漏生成 / 生成到了子目录没被统计」这类问题。

### 写译文的正确入口（Python API）

命令行之外要直接改文件时，用这两个函数，**不要**给 `StringPair.new` 赋值：

```python
from renpy_toolkit import tl

doc = tl.parse_tl_file("game/tl/chinese/script.rpy")
tl.apply_new_text(doc, "start_a55bd194", "早上好。")     # 对白，按标识符
tl.set_string_new(doc, "Start Game", "开始游戏")          # strings，按 old
doc.write()                                              # 落盘前先备份旧内容
```

- 两者都接收**解码后的文本**：要换行传真换行，要引号直接传 `"`，函数内部负责转义
  （传字面 `\n` 会被再转义一层，写成 `\\n`）。
- `pair.new = ...` 只改内存里的解析值。`TlFile.render()` 是从行列表重建文件的，
  所以那样改**不会写进文件**——这是最容易踩的一个坑。
- `set_string_new(..., language="chinese", index=1)` 可在一门语言内、或同一
  `old` 出现多次时精确指定目标。

## 译文整理（`sort` / `split` / `hardcoded`）

```bash
# 按源码行号重排块顺序，可选生成 Event Index
python -m renpy_toolkit tl sort game/tl/chinese --index --write

# 一个 strings 块里塞了多条 old/new 的，拆成「一条一个块」的标准形态
# 第二个参数是 game/ 源目录，用来反查每条字面量的出处
python -m renpy_toolkit tl split game/tl/chinese game --write

# 硬编码文本清单：从探针 dump 建，只收没包 _() 的字面量
python -m renpy_toolkit tl hardcoded scan game/tl/chinese --language chinese --write
python -m renpy_toolkit tl hardcoded apply game/tl/chinese --language chinese --write
```

`tl split` 会保留每条的 `# game/x.rpy:NNN` 源注释——这是后面 `sort` 能排序、
`hardcoded` 能定位的前提。

### 硬编码文本：改 TL，不改源码

「硬编码」指源码里直接写、没有 `_()` 包裹的可见文本。**本工具一律不改源码**：
`old` 就是原始字面量，补丁只有 `tl/<lang>/*.rpy` 加 `tl/<lang>/hardcoded.json`，
和源码解耦。这样做的依据是引擎的真实行为——界面里的 `text "..."` 字面量在渲染时
一定会经过 `renpy.substitutions.substitute(..., translate=True)`，落到
`StringTranslator.translate()`，**与源码有没有 `_()` 无关**。所以给未包裹的字面量
补一条 `strings` 条目，效果和把它包进 `_()` 完全一样，但不用碰游戏文件。

`scan` 默认从 `tl gen` 打印的探针 dump 建清单（`--dump PATH` 指定，或从 TL 目录上溯
找 `.renpy-toolkit-gen`）。只收 `wrapped=False` 且非引擎来源的行，条数与 `tl gen`
实际写进 TL 的集合逐条一致（实测 1,858 条）。没有 dump 时用 `--from-tl` 退化重建，
但它分不出原生 `_()` 串，清单会变成普通台账。

`apply` 在块头之上插一行 `# hardcoded: <context>` 标记，`context` 取屏幕名或
提取方式（`pycode` / `pyexpr` / `showtext` / `sl` / `additional`）。重复 `scan` 会
保留手改过的 `added` 日期与已有译文，源码里已消失的条目标 `removed: true` 而不是删除。

**改源码只是备选**：确实需要精确控制时，写法是 `_("You bought 1 [item]!").format(item=item)`，
`_()` 返回后 `.format` 才展开；写成 `_("...{item}...")` 无效，因为 `renpy/minstore.py`
里 `_` 的函数体就是 `return s`。代价是补丁绑死游戏版本，升级就要重改源码，所以不作为默认路径。

**Event Index 按 label 折叠，不是每块一条。** Ren'Py 的标识符形如
`<label>_<md5[:8]>`（同一 label 里哈希撞车时追加 `_1`、`_2`），索引只记每个
label **第一次出现**的行号，一个 label 一行。这不是省事，是量级差别：
一个 57,414 块的真实工程，折叠前是 57,414 条 / 1.8 MB（占 TL 体积 18.7%），
折叠后 651 条 / 15.3 KB（0.15%），**压缩 121 倍**；而 651 条正好等于某成品
工程手工维护的 41 个文件里 39 个的条目数（label 集合与顺序逐一吻合，
另 2 个是它自己的索引过期了）。

折叠规则是正则 `^(.+?)_[0-9a-f]{8}(_\d+)?$`，非贪婪——`night_1_5d9497a5_2`
必须归到 `night_1` 而不是 `night_1_5d9497a5`。只截掉最后一段 `_(8位hex)` 会
把每个哈希撞车的块当成新 label，索引反而更大。

`--sections` 的 `# --- <label> ---` 标记用同一套规则，所以两者永远对齐。

## 备份：`--backup-mode` 与 `tl backup`

改译文是反复小步的动作，所以每次写盘前都会先保留旧内容。保留方式由一个开关
决定，所有写盘命令（`tl apply` / `tl sort` / `tl split` / `tl port` / `tl polish` /
`tl hardcoded apply` / `tl context` / `rpa pack` / `decompile dir` …）都走同一条路：

| 模式 | 落点 | 什么时候用 |
| --- | --- | --- |
| `bak`（默认） | 目标旁 `<名>.bak` | 只想留一份可立刻对比的副本 |
| `git` | `<tl>/.renpy-toolkit.git` 影子仓库 | 想回退到任意一次改动，又不想在译文目录里堆副本 |
| `none` | 不备份 | 文件已在别的版本控制里，或写的是可再生的中间产物 |
| `auto` | 有 `git` 就用 `git`，否则 `bak` | 一次性脚本里不想写死 |

```bash
# 全局开关（也可用环境变量 RTK_BACKUP 指定）
python -m renpy_toolkit --backup-mode git tl polish game/tl/chinese -s batch.py --write

# 看备份历史 / 取回某个版本 / 只保留最近 20 个
python -m renpy_toolkit tl backup list game/tl/chinese --files
python -m renpy_toolkit tl backup show game/tl/chinese 2930389e script.rpy
python -m renpy_toolkit tl backup restore game/tl/chinese 2930389e --write
python -m renpy_toolkit tl backup prune game/tl/chinese --keep 20 --write
```

**没有 git 时 `--backup-mode git` 自动降级为 `bak`**，并在 stderr 打一行
`warning:` 说明，命令照常跑完（退出码不变）。理由：`.bak` 本身就是可用的备份，
需要的东西一样都不用装，没必要因为一条环境问题把「我只想改个译文」拦住。降级必须
出声——否则用户会以为快照在攒，等需要回退时才发现没有。

真正无法降级的是**操作快照**的命令：`tl backup list/show/restore/save/prune/size`
在没有 git 时报错并以退出码 3 结束，因为不存在「`show` 某个 revision」的 `.bak` 版本。

**为什么用影子仓库而不是用户自己的仓库**：TL 目录很可能已经在一个 git 仓库里，
往那儿 `commit` 会改别人的提交历史。影子仓库是一个独立的 `--git-dir`，对象库和
index 都在 `.renpy-toolkit.git` 里，**译文目录本身除了那个点目录不会多出任何东西**，
也不会出现 `.git`。它用 `hash-object -w` + `commit-tree` 直接造提交，不碰工作区、
不跑 `git add`。

每个快照存的是**完整树**（不是 diff），所以任意一个版本都能整份取回。三个必须
按字节保真的地方都已经处理掉：`core.autocrlf` 会让 Git for Windows 把 CRLF 存成
LF（用 `--no-filters` + `-c core.autocrlf=false` 关掉），读回走 `cat-file blob`
而不是 `git show`（后者会过 smudge 过滤），写回是二进制。

`.rpa` 归档动辄几个 GB，`bak` 模式做分块复制不读进内存；`git` 模式要哈希整份文件，
这是可回退快照的代价，调用方自己选的模式。

`tl backup` 没有 `init`：备份库在第一次真正写盘时自动创建，单独初始化只会留下一个
空仓库让人误以为已经备份过了。

## 游戏更新后不丢译文（`port`）

重新生成骨架后，旧译文要搬回来。**不能按 `old` 文本匹配**：一句 `"Okay."` 在剧本
里可能出现几百次，搬错位置比不搬更糟。`port` 用两道锁：

1. **标识符相同**——`start_636ae3f5` 是 `label + md5(code)`，源码没改就不会变；
2. **镜像的 `# e "..."` 源文本逐行相同**——挡住标识符撞车这种极端情况。

源文改过的块**不搬**，记进 `skipped` 让你人看。实测在一个 58,249 块的工程上，
骨架里搬进 53,093 句（91%），166 块因源码变动跳过。

```bash
python -m renpy_toolkit tl port game/tl/chinese_new game/tl/Chinese_old --write --show-skipped
```

## 术语字典与润色脚本（`glossary` / `polish`）

术语放一份 TSV（`work/glossary/terms.tsv`，五列：source / target / category /
banned / note），改一行就是改一条规则，不用写脚本：

```bash
# 一致性校验：禁用写法（error）+ 该译没译（--missing）
python -m renpy_toolkit tl glossary check game/tl/chinese --terms work/glossary/terms.tsv

# 全局改名：只改译文，不碰 old / 镜像注释 / 标识符
python -m renpy_toolkit tl glossary rename game/tl/chinese "旧译名" "新译名" --write
```

攒下来的 `polish_script_*.py` 也能直接喂进来——它们是**数据**（模块级
字典），所以用 `ast.literal_eval` 读，不 `exec`：

```bash
python -m renpy_toolkit tl polish game/tl/chinese ../润色/polish_script_block03_fumiko.py
```

`translations_by_text_verified` 的「文本 + 源行号」双重定位被完整保留：
同一句 `mc "嗯……"` 在 20 个块里出现时，只有声明的那一行会被改，其余报
`declared for source line 763, but this block's source is 142`。

## 跨屏多义字符串（`context`）

Ren'Py 的字符串表是全局的：一个 `old` 只能有一个 `new`，重复注册会抛
`A translation for "..." already exists`。`Enter`（对白"进来"）与 `Enter`
（键盘帮助"回车键"）因此无法各自翻译。官方出口是 `{#context}` 标签，但它
要求改源码——对只发 `.rpyc` 的游戏不可用。运行时补丁绕开了这个限制：

```bash
# 1. 从运行中的引擎导出全部可翻译文本（含屏幕归属），同时打印字符串 dump 路径
python -m renpy_toolkit tl gen <game> chinese

# 2. 看哪些串真的需要拆分（只有跨屏的才列出来）
python -m renpy_toolkit tl context <dump.json> --language chinese --show-all

# 3. 为指定串生成运行时补丁（默认 dry-run）
python -m renpy_toolkit tl context <dump.json> --language chinese \
       --tl-dir game/tl/chinese --only Continue --only "Go back" --write
```

生成的 `tl/<lang>/_rtk_context_<lang>.rpy` 是 `translate <lang> python:` 块，
包装 `renpy.translation.translate_string`，按
`renpy.display.screen._current_screen` 查表；未登记的屏幕自动回退通用译文。
把里面各屏的值改成不同措辞即可。

报告里会混进**单个小写词**：`viewport`、`sound`、`show` 可能是界面文案，也可能是
标识符（`how=pyexpr` 时尤其难分）。实测同一批词的来源完全重叠——真实译过的
`skip`/`language` 与噪声 `viewport`/`vertical` 都有 `pyexpr` 行——所以没有可靠的
语法判据，工具不猜，交给调用方：

```bash
--ignore viewport --ignore sound      # 逐条列出
--ignore-file skip.txt                # 一行一条，# 开头为注释
```

报头给的是**真实总数**（`130 string(s) ... (showing 20)`）而不是截断后的条数；
`--show-all` 列出全部。纯标签串（`{w=0.5}{nw}`、`{image=check_text}`）不参与歧义
报告——它们渲染的是停顿或图片，拆成两键只会得到两个看起来一样的键。

实测（43 条跨屏串、80 个屏幕）：同一 `Continue` 在不同屏幕返回不同译文，
未列屏幕回退，`lint` 通过。

## 永远翻不了的字符串（`untranslatable` / `fix-fstrings`）

f-string 在**翻译查表之前**就被求值了。`renpy/substitutions.py` 的
`substitute()` 先调 `translate_string(s)`，之后才展开 `[var]`；所以
`f"You bought 1 {item_name}!"` 到查表时已经是 `"You bought 1 Sandwich!"`，
与源码键永不相等。

**这意味着给它写 `strings` 条目是无效的**（不少汉化工程里有这种条目，永远不生效）。
先看有多少、是哪些：

```bash
python -m renpy_toolkit tl untranslatable <dump.json> --show-all
```

```
11 string(s) are assembled at runtime and cannot be translated as written:

  shop.rpy:189
    f'You bought 1 {item_name}!'
  ...
```

然后用 `fix-fstrings` 生成**不需要改源码**的补丁。它按可枚举性分两条路：

```bash
python -m renpy_toolkit tl fix-fstrings <dump.json> \
    --language chinese --outdir game/tl/chinese \
    --values item_name=Sandwich,Juice,Honey \
    --write
```

- **exact**：字段值都列全了 → 为每个成品句子写一条 `strings` 条目。
  引擎查表用的是句子本身，所以这类条目**真的会生效**。
- **pattern**：值列不全（物品名有量级）→ 写一个 `translate chinese python:`
  块，包装 `renpy.translation.translate_string`，用 `^You bought 1 (.+)!$`
  匹配成品句子再拼译文。它先翻被捕获的字段，所以 `Sandwich` 有词条时会
  跟着变中文；切走语言时自动让位（`translate python` 块不会被撤销，所以
  补丁里必须查实时 `preferences.language`）。
- **manual**：`f"{value!r}"`、`f"{x:.0f}"`、`f"{int(a*b)}%"` 这类无法从文本
  反推的，只能改源码。命令会把它们单独列出来。

两条路都**只读游戏、只往 `--outdir` 写新文件**；补丁文件带 `# renpy-toolkit:`
标记，重复生成会整份重写，遇到同名的手写文件则拒绝覆盖。

实测（9 条候选）：3 处 `You bought 1 {item_name}!` 用 `--values` 枚举 20 个
物品名 → 60 个句子塌成 40 条唯一 `strings` 条目（**必须去重**：同一 `old`
注册两次引擎会抛 `A translation for "..." already exists`，游戏直接起不来）；
`Item '{item}' ...`、`+ {n} exp!` 等 3 条走 pattern；2 条 `updater_screen` 的
`:.0f` 报 manual。

把两份产物拷进 `game/tl/chinese/` 后实测（`lint` exit 0、无重复报错）：

```
exact   'You bought 1 Sandwich!'            -> '你买了 1 个三明治！'
exact   '\nError: Sandwich not found...'    -> '\n错误：全局变量里找不到Sandwich。'
pattern "Item 'Sandwich' does not exist..." -> '物品“Sandwich”不在背包里。'
pattern '+ 250 exp!'                        -> '+250 经验！'
  —     'You bought 1 Sandwich today, twice.' -> 原样（锚定模式未误伤）
```

`write_fixes` 会先读目标目录里其它 TL 文件的 `old`，已经有人翻译过的句子
直接跳过——重复注册等于让玩家的游戏在启动时崩掉，这不该靠人去发现。

真需要改源码时，写法是 `_("You bought 1 [item]!").format(item=item)`——
注意**不能**写成 `_("You bought 1 {item}!")`，`_(...)` 只是标记函数
（`renpy/minstore.py` 里它的函数体就是 `return s`），`.format` 不会查表。

## 制作：脚手架与体检

```bash
python -m renpy_toolkit project new MyGame --resolution 1920x1080 --language chinese
python -m renpy_toolkit project audit MyGame
```

`project audit` 里三条检查是汉化场景专有的，都不需要引擎：

- `force-archives`（**error**）：源码里写了 `config.force_archives = True`。
  `renpy/loader.py` 是「磁盘优先于归档」，但这个开关一开就反过来——发行版的
  `game/archive.rpa` 会盖住我们写进 `game/tl/<lang>/` 的补丁，游戏照常启动、
  界面全英文、**一声不响**。被测游戏的 `archive.rpa` 有 2.3 GB / 21,857 条目
  （实测 `tl/` 前缀 0 条、`.rpyc` 0 条），所以松散 TL 确实生效；但作者加一行
  开关就能让整份汉化失效。
- `duplicate-translation`（**error**）：同一语言内同一个 `old` 出现两次。
  引擎 `StringTranslator.add()` 直接抛 `A translation for "..." already exists`，
  游戏进不到主菜单。键是 `(语言, old)`，跨语言重复合法。最常见的成因就是
  生成器不幂等——追加而不是重建，第二次运行把每个 strings 块再堆一遍。
- `runtime-strings`（**warning**）：源码里出现
  `config.translate_additional_strings_callbacks`。官方文档把它定义为「翻译系统
  搜索字符串时调用的回调列表，每个回调返回 `(filename, linenumber, string)`」，
  也就是说游戏**在运行时**注册字符串。`tl gen` 读的是编译后的语句图，看不到
  这类键，那些键会一直显示英文。这条警告是在提示：覆盖率数字是上界，不是承诺。

### 只放了脚本的工作副本

`tl` 目录、译文、笔记都留在工程里、美术却还在 `archive.rpa` 里，是很常见的
工作副本形态。这种工程里每个 `show` 都找不到图，如果逐条报会有两万多条噪声
（实测一份 58,249 句的工程报了 20,591 条）。`audit` 在「一个图片文件都没有」
且未解析名字超过 25 个时只报一条，说明这里无法核验图片名、请改用完整构建。

顺带一个同类根因的坑：`<project>/game/` 存在时，工程根目录**不再**被当作游戏
目录扫描。两者是替代关系而不是并集——把根目录也扫进去，会把工作副本旁边的笔记、
截图、探针输出全部算成游戏资源（实测 8,285 个文件 vs 228 个，扫描 40 秒 vs 3 秒，
并且截图名污染图片索引，让真正的图看起来缺失）。

## SDK 桥接

```bash
python -m renpy_toolkit sdk find  /path/to/renpy-sdk
python -m renpy_toolkit sdk translate MyGame chinese --count   # 只报缺多少
python -m renpy_toolkit sdk lint      MyGame
```

## 归档与编译产物

```bash
python -m renpy_toolkit rpa list game/archive.rpa --long
python -m renpy_toolkit rpa extract game/archive.rpa out/ --filter images/
python -m renpy_toolkit rpa pack built.rpa out/ --version 3
```

`.rpa` 的索引是 pickle，所以读取走白名单 `Unpickler`（只放行内建容器类型），
解压走 `safeio.decompress_limited`（默认 64 MiB 上限），条目名走 `safe_join`
（`../` 越界直接抛 `UnsafePathError`，防 Zip Slip）。

`.rpyc` 的新鲜度判定在库里（`rpyc.is_fresh`），不单独开子命令——它只在
「改了 .rpy 却忘了删 .rpyc」这种误判场景下有意义，交给 `audit` 与脚本调用即可。
判定只看末 16 字节的源文件 md5，与 mtime、文件体积无关。

## 字体验证与硬编码字体替换

```bash
# 缺字检测：字体 cmap 是否覆盖 TL 里出现的全部字符
python -m renpy_toolkit font check game/fonts/SourceHanSans.ttf --tl game/tl/chinese

# 硬编码字体审计：源码里直接写死字体路径的，有没有被 config.font_replacement_map 覆盖
python -m renpy_toolkit font map <game> --all-states

# 打印一份可直接粘贴的 translate <lang> python 块模板
python -m renpy_toolkit font map <game> --emit chinese
```

`config.font_replacement_map` 只按字体路径匹配，所以补丁写在
`translate <lang> python:` 块里即可生效，不用改游戏源码。`font map` 只做审计和
出模板——把哪几行粘贴进 TL 由人决定，它不替你写进游戏。

## 图形界面（独立的 exe 发行线）

图形界面**不在这个包里**：源码在仓库顶层 `exe/`（`exe/gui.py` + `exe/ui/index.html`），
只由 `tools/build_exe.py` 打进 Windows exe。插件与 skill 都只用命令行、从不 import
它，所以 `dist/plugin`、便携 zip 和 skill 包里没有任何界面代码。

界面不重写逻辑：每个按钮都翻译成一次 `cli.main(argv)` 调用，界面与命令行的行为
（`--write` 语义、备份模式、退出码）永远一致。窗口先扫「exe 所在目录 + 一级子目录」
里的 Ren'Py 游戏让你选一个；除「生成汉化骨架」写进 `game/tl/<语言>/` 外，其余产物
都落在 exe 旁边的 `renpy-toolkit-数据/`。

打 exe（必须在装了 PyInstaller 的解释器里跑，本机既定位置
`E:\Programs\renpy-toolkit-build\venv`；`fontTools` 与 `openpyxl` 也要装在同一个解释器）：

```bash
python tools/build_exe.py --clean      # 打单文件 exe，并跑 --selftest 自检
python tools/build_exe.py --verify     # 只自检已有产物
```

## 开发

### 依赖

必需：Python 3.10+，仅标准库。
可选：`fontTools`（`fonts.py` / `fontmap.py` 真实 cmap 解析）、`openpyxl`（XLSX 读写）。
两者都是 MIT，`pip install renpy-toolkit[all]` 一次装齐；**exe 形态会把它们一起打包**，
所以 exe 里这两个功能开箱可用，而一键包 / skill 保持纯标准库。

### 测试

```bash
cd plugin/dsh-renpy-toolkit && python -m unittest discover -s tests -t tests -q
```

（必须用 `discover -s tests -t tests`；`python -m unittest tests.test_x` 会因为解析不到
`_util` 而报 `ModuleNotFoundError`。测试是纯 unittest，不需要 pytest。）

当前 **519 个用例全绿**，约 62 秒。测试把「踩过的坑」直接写成断言：BOM/行尾保真、
重复标识符消歧、单行块与多 say 块拒写、字面 `\n`、raw string、标签/占位符一个
不能多也一个不能少、三引号多行值往返字节一致、自闭合标签不算未闭合、
嵌套下标插值算一个原子、`[[` 不当插值；`test_decompile.py` 另加反编译器的
容器解析、恶意 pickle 拒绝、`(multiple=)` 保留、空 say 发射、strings 块重建；
`test_tlgen.py` 覆盖 f-string 识别（含 `format_spec` 不算字段）、字符串过滤器、
探针加载器、探针自扫描排除与 scratch 目录归属判定；`test_safeio.py` 覆盖有界解压
与路径逃逸（Zip Slip）；`test_tables_verify.py` 覆盖 CSV/XLSX 公式注入；
`test_contextmap.py` 还会把生成的补丁代码真的执行一遍，喂进引号、反斜杠、换行、
控制字符、`"); import os; os.system(...)` 这类形状，确认它们只当数据落进表里，
既没让文件变成非法 Python，也没跑出代码——**游戏文本是别人可控的，而它被拼进
的是 Python 字面量**；
`test_glossary.py` / `test_port.py` / `test_polish.py` 覆盖术语检查、旧译本搬运
与润色脚本应用；`test_cli_tidy.py` 覆盖 `rpa pack/list/extract` 三个命令的
端到端往返（这三条分支曾经完全没有测试，结果 `pack` 不带 `--key` 会写出读不回来
的归档、`list` 读不存在的 `archive.path`、`list --long` 把 `entries` 字典当函数调）。

### 发布

`tools/` 下的脚本用来保证「在用的」「发出去的」「交到别人手上的」不是三份不同的代码：

```bash
python tools/build_release.py            # 生成 dist/plugin（VERSION + MANIFEST.sha256）
python tools/build_release.py --verify   # 重新哈希，逐文件比对
python tools/build_release.py --sync     # 用发布版覆盖 plugin/dsh-renpy-toolkit/
python tools/build_portable.py --zip     # 生成 dist/portable 一键包与 .zip
python tools/build_exe.py --clean        # 生成 dist/exe（onedir + onefile）并自检
python tools/check_portable.py           # 在干净解压副本上验收一键包
python tools/make_release.py --dry-run    # 预览发版（版本校验 + 重建 + tag + Release）
python tools/make_release.py              # 真的发版（推 tag + 上传两个 zip）
python tools/check_skill.py              # SKILL.md 里的 flag/子命令必须真实存在
python tools/check_readme.py             # README 的目录锚点与相对链接必须能解析
```

`plugin/dsh-renpy-toolkit/` 是唯一真源，`dist/` 是构建产物（`.gitignore` 排除）。
保留这份副本只为能逐字节比对，不是第二份需要手工维护的代码。

这一层目录以发行名命名，是因为工具包要能整目录拷走单独验证：把
`plugin/dsh-renpy-toolkit/` 复制到任意位置，`cd` 进去跑
`python -m unittest discover -s tests -t tests -q` 与 `python -m renpy_toolkit --version`
就能确认它在脱离仓库其它部分时仍然可用。

#### 一键包（`build_portable.py`）

面向「只想用、不想碰 Python 工程」的场景：解压一个 zip、双击一个文件就能用，
不要 `pip`、不要 `cd`、不设环境变量。

```
renpy-toolkit.py         所有平台共用入口
renpy-toolkit.cmd        Windows 双击启动（也可把游戏文件夹拖上去）
renpy-toolkit.sh         POSIX 启动器
自检.cmd / selfcheck.sh  一条命令验证这份拷贝可用
README.txt               纯文本快速上手
renpy_toolkit/           包本体（复用 build_release 的过滤器，不含缓存）
```

三条启动器不含独立逻辑，全部转交给 `renpy-toolkit.py`。代价是**行尾必须按文件类型区分**：
`.cmd` 用 CRLF，否则 `cmd.exe` 会把未终结的行与下一行拼在一起，`goto :label` 找不到标签，
整份脚本退化成一片 `'xxx' is not recognized as an internal or external command`。
这个坑真的踩到了——第一版全部用 LF 写出，`.cmd` 完全不可用；现在 `CRLF_SUFFIXES`
决定每个文件的行尾。

配套的 `--selftest` 是这条形态的关键：它检查 Python 版本、能否导入包、可选依赖在不在、
`.rpa` 写入/读回是否一致、翻译文件能否解析，最后打印 `self-check passed` 或指明哪一项挂了。
全程只写临时目录，不会碰用户要汉化的游戏。它发现过两个真 bug：把目录传给 `add()`
（该方法的参数是 bytes 内容）、以及 `save()` 后没有 `close()` 导致 Windows 上临时目录删不掉。

`build_release.py` 在复制后会主动清掉目标树里的 `__pycache__` 与 `*.pyc`：
`_walk_files` 本来就不复制它们，于是残留的缓存对 `--verify` 完全隐形——
在发布目录里跑一次测试就会留下这种垃圾，只能由构建过程自己扫掉。

`.pyc` 无法永久消除：第一次 import 就会重建，所以「纯净」的准确含义是
**交付瞬间不含缓存**，不是永远不含。想验证又不弄脏交付树，就把发布版复制到
临时目录里跑（`--version` 这类会 import 包的命令同理）：

```powershell
$tmp = Join-Path $env:TEMP "rptk-check"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item dist\plugin $tmp -Recurse
Push-Location $tmp
python -m unittest discover -s tests -t tests -q     # 519 tests
Pop-Location
Remove-Item $tmp -Recurse -Force
```

### 相关文档

- [`docs/custom-text-tags.md`](../../docs/custom-text-tags.md) — 自定义文本标签的语义，以及标签表对齐引擎的真 bug
- [`docs/text-tags-and-interpolation.md`](../../docs/text-tags-and-interpolation.md) — 文本标签与插值（引擎侧实证），verify 规则的误报基准
- [`docs/rpyc-decompile.md`](../../docs/rpyc-decompile.md) — `.rpyc` 格式与反编译实现
- [`docs/rpa-format-spec.md`](../../docs/rpa-format-spec.md) — RPA 归档格式
- [`docs/official-cli-and-translation.md`](../../docs/official-cli-and-translation.md) — 翻译机制与 CLI 参数（官方文档核实）
- [`docs/renpy-engine-notes.md`](../../docs/renpy-engine-notes.md) — 引擎架构与核心机制
- [`docs/renpy-source-verification.md`](../../docs/renpy-source-verification.md) — 从 renpy 源码逐行核实的接口
- [`docs/renpy-translation-ecosystem.md`](../../docs/renpy-translation-ecosystem.md) — 汉化生态参考
- [`docs/renpy-tutorial-notes.md`](../../docs/renpy-tutorial-notes.md) — 制作侧入门参考
