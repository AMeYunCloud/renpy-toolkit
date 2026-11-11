---
name: renpy-toolkit
description: >
  Ren'Py 游戏汉化与制作工具。MUST USE when the task involves a Ren'Py game's
  translation/localization or its build: 汉化 / 翻译 Ren'Py 游戏 / 生成 tl 翻译文件 /
  校验译文 / 术语统一 / 字体缺字 / 硬编码文本 / 游戏更新后保留译文 /
  .rpa 解包打包 / .rpyc 反编译 / 只有 rpyc 没有源码 / 新建 Ren'Py 工程 /
  改名或修复翻译标识符 / translate 块与 strings 块。
  Also use when the user mentions renpy, .rpy, .rpyc, .rpa, tl/chinese,
  translate block, strings block, or asks to localize a visual novel built
  with Ren'Py.

  Covers: 从零生成全量翻译骨架（含只有 .rpyc 的发行版）、完整性校验、导出/回填表格、
  术语字典、润色批处理、字体替换与缺字检测、跨屏歧义消解、运行时拼接字符串、
  工程脚手架与体检、归档与编译产物。
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
metadata:
  trigger: 汉化/翻译/校验 Ren'Py 游戏，或处理 .rpyc / .rpa / tl 目录
  source: 随 renpy-toolkit 1.0.0 发布版构建（引擎规则取自 renpy/renpy 源码实证）
  revision: "2026-10-24"
---

# Ren'Py 汉化与制作

工具本体是一个独立的 Python 包 `renpy_toolkit`（27 个模块，零第三方依赖），
在仓库的 `dsh-plugin/dsh-renpy-toolkit/` 下（构建产物在 `dist/plugin/`）。**不要 `cd` 进去再跑** ——
用随附启动器，它在任何工作目录下都能用：

```bash
python <skill>/scripts/rptk.py --help
python <skill>/scripts/rptk.py tl --help
```

首次使用先确认工具可用：

```bash
python <skill>/scripts/rptk.py --version     # 期望 renpy-toolkit 1.0.0
```

找不到发布版时启动器会打印试过的路径；设 `RPTK_HOME` 指向含 `renpy_toolkit/`
的目录即可。

---

## 先判断属于哪一类任务

| 用户说的 | 走哪条路 |
| --- | --- |
| 「汉化这个游戏」「翻译全部文本」 | A. 从零生成骨架 |
| 「这个游戏只有 rpyc」 | A + 反编译（`decompile`） |
| 「翻译好了帮我检查」 | B. 校验 |
| 「还差多少没翻」 | B（`tl missing` / `tl stats`） |
| 「导出成表格给译者」 | C. 表格往返 |
| 「改错了想回到上一版 / 别覆盖我的译文」 | C2. 备份与回退 |
| 「人名/道具译名不统一」 | D. 术语字典 |
| 「游戏更新了，旧译不要丢」 | E. port |
| 「更新后多出来一堆 untranslated」 | E |
| 「中文TL太乱，排一下序 / 拆一下」 | E（`tl sort` / `tl split`） |
| 「批量改一批译法」 | E（`tl polish`） |
| 「界面文字没翻译」 | A（strings 层）+ G |
| 「有些句子死活翻译不过来」 | F. 运行时拼装 |
| 「同一句话在不同界面要不同译法」 | G（`tl context`） |
| 「字体显示成方块 / 缺字」 | H. 字体 |
| 「拆这个 rpa / 打包」 | I. 归档 |
| 「新建一个 Ren'Py 工程」 | J. 脚手架 |
| 「我有官方 SDK，想用它」 | K. sdk |

---

## A. 从零生成全量翻译骨架

官方 `renpy.sh <game> translate <lang>` 只扫**磁盘上真实存在的 `.rpy`**，所以对只发
`.rpyc` 的发行版只能生成 `common.rpy` —— 几十句，其余全丢。社区工具按「有效长度 < 8」
之类规则静默丢句。两者都不会告诉用户漏了什么。

`tl gen` 走另一条路：**把游戏自己的运行时跑起来**，从引擎内存里取翻译单元
（`renpy.game.script.translator.file_translates` 与 SL2 屏幕树）。按构造就不依赖源码。

```bash
python <skill>/scripts/rptk.py tl gen <game-dir> chinese --outdir <tl-out> --write
# 可选：--sections --index 额外写分块注释与 Event Index（默认关，标签由源码推断，
#       可能与实际剧情块有偏差，以源码为准）
# 可选：--no-hardcoded 不把未包 _() 的界面/按钮/物品名收进 strings（默认收，推荐）
```

要点：

- `<game-dir>` 是**含 `game/` 的目录**（有 `<name>.py` 启动器那一层），不是 `game/` 本身。
  传 `game/` 本身也能识别。
- `--outdir` **必须是全新目录**。对已存在的目录，`--write` 只补缺失文件并打印
  `N file(s) already present, left untouched` —— 用它判断覆盖率会得到假结果。
- 默认对白与界面字符串一起生成；只要对白层用 `--no-strings`。
- 首次跑会启动游戏一次，正常几秒到几十秒（实测 58,249 句的发行版约 20 秒）。
- 生成后**立刻**用 B 校验，并把 `--expect-dialogue <N>` 钉住块数。
- 临时探针目录默认保留（里面有 `rtk_strings.json`，G/F 两步要用）；落在游戏安装
  目录里时加 `--clean-scratch`。落点按 `RTK_SCRATCH` → `--outdir` 父目录 → `%TEMP%`
  → `~/.renpy-toolkit` → `<game>/.renpy-toolkit-gen` → 当前目录的顺序探测，取第一个
  游戏解释器真能写的；要指定就设 `RTK_SCRATCH`。

### 拿不到游戏运行时时的退路：`tl init`

`tl gen` 要能启动游戏。机器上没有对应解释器、或游戏被改坏到跑不起来时，用
`tl init` 从**已有的 `tl/<lang>` 克隆**出一个空骨架（不需要 SDK、不需要启动游戏）：

```bash
python <skill>/scripts/rptk.py tl init <game-dir> chinese --source <existing-tl-dir> --write
```

它默认**永不覆盖**已存在的目标文件，要覆盖显式加 `--force`。

它输出的三类东西要分别对待：

1. `translate <lang> <identifier>:` 块 —— 对白，**译文先照抄原文**
2. `translate <lang> strings:` 块 —— 界面/菜单字符串，同样照抄原文
3. 结尾的 f-string 清单 —— **翻译表永远命中不了**，见 F

### 为什么骨架里要照抄原文，而不是留空

留空（`new ""` / `e ""`）不是「未翻译」的标记，而是**主动擦掉文字**：

```python
# renpy/translation/__init__.py  StringTranslator.translate()
new = self.translations.get(old, None)
if new is not None:
    return new          # "" 不是 None，直接返回空串
```

实测探针确认：注册空串后 `translate_string("X")` 返回 `''`，界面变空白。
引擎自己的 `translate` 命令默认也用 `null_filter`（恒等），只有显式 `--empty` 才留空。
所以**不要**为了「看起来像没翻译」而清空译文；要标记未翻译用 `tl verify` 的
`UNTRANSLATED`（译文==原文）判定。

### 只有 `.rpyc` 的游戏：先反编译

`decompile` 是命令组，三个动作：

```bash
# 单个文件到 stdout（或 -o 写文件）
python <skill>/scripts/rptk.py decompile file <path/to/x.rpyc> [-o <out.rpy>]

# 整个目录批量（默认 dry-run，必须加 --write 才落盘）
python <skill>/scripts/rptk.py decompile dir <game-or-tl-dir> [-o <dest>] --write [--overwrite] [--backup]

# 往返校验：反编译出的 TL 是否保住了标识符/字符串/对白
python <skill>/scripts/rptk.py decompile verify <tl-dir>
```

`.rpyc` 是 `b"RENPY RPC2"` + slot 表，每个 slot 是 zlib 压缩的 pickle。
本工具用 `pickletools` 白名单虚拟机解析，**不调用 `pickle.loads`**（那等于执行任意代码）。

反编译结果有用的地方是「读作者写法、找硬编码文本、理解结构」；**不要**把它当作
「可以重新编译回游戏」的源码 —— 未支持的节点会输出 `# TODO: unsupported node X`。

---

## B. 校验

```bash
python <skill>/scripts/rptk.py tl verify <tl-dir> \
    --expect-dialogue 58249 \
    --game-dir <game-dir> \
    --strict --fullwidth
```

退出码：`0` 通过 / `1` 发现问题 / `2` 用法错误 / `3` 环境不具备（缺 git、缺 SDK 等），可直接进 CI。

`--game-dir` 用于发现游戏自定义的文本标签（`config.custom_text_tags`），
否则自定义标签会被当未知标签报出来。

检查项与常见误判（都是对着真实成品译料调过的）：

- **标签平衡** —— `{image=...}` / `{w}` / `{p}` 等**不接受闭合标签**，写 `{/image}`
  引擎直接抛异常。把它们当「未闭合」会误报大量合法译文。
- **内容标签** —— `{color=#X}` / `{size=N}` 这类承载表现效果，整对丢掉会让那一行
  排版变了，所以按「多了/少了」双向报。
- **`{=style}`** —— 标签名为空、argument 是样式名。它**不带 `=` 前缀的名字**，
  解析时容易整类漏掉；本工具按原文 `chunk.text` 判定。
- **插值** —— 自 Ren'Py 8.2.0 起 `[...]` 是 **Python 表达式**，
  `[player[money]]` 是**一个**插值不是两个。`[mc]`（玩家名）算正当译法，豁免。
- **转义** —— `[[` 与 `{{` 是转义而不是插值/标签。
- **三引号** —— `new """..."""` 里的真换行是正文段落，不是被劈开的字面量。
- **引号** —— 原文照抄的占位行不参与引号检查（否则原文自带的弯引号会全变成误报）。

`tl verify` 数的是文件里的 `translate <lang> <id>:` 块。想跟引擎对账就断言数字：

```bash
# 用游戏自带的运行时跑 lint（需要能启动游戏）
& "<game>/lib/py3-windows-x86_64/python.exe" "<game>/<name>.py" <game> lint
```

期望两行块数**完全相同**：

```
The game contains 58,249 dialogue blocks, ...
The chinese translation contains 58,249 dialogue blocks, ...
```

> 打包发行版的自带解释器常常写不了 `%TEMP%`（实测 `FileNotFoundError: No usable
> temporary directory`）。跑之前把 `TMP`/`TEMP` 指到一个可写目录。

### 还差哪些没翻

```bash
python <skill>/scripts/rptk.py tl missing <tl-dir> --language chinese
```

只列**译文仍等于原文**的条目（与 `tl verify` 的 `UNTRANSLATED` 同源判定），
按文件分组。表格式的进度清点用 `tl stats`；两者都不需要启动游戏。

---

## C. 表格往返

```bash
# 导出（PO 便于接 Poedit / Weblate / LLM；也支持 csv/tsv/xlsx/json）
python <skill>/scripts/rptk.py tl table <tl-dir> -o strings.po
python <skill>/scripts/rptk.py tl apply <tl-dir> strings.po           # 预览
python <skill>/scripts/rptk.py tl apply <tl-dir> strings.po --write   # 落盘（先备份旧内容）
```

`apply` 默认 dry-run。表格里的 `source` 是**原文**，`target` 是译文；只按 `id`
（翻译标识符）对齐，不按文本匹配 —— 一句 `Okay.` 可能出现几百次，按文本回填必然错行。

CSV/XLSX 导出时以 `=` `+` `-` `@` 开头的单元格会前置一个 `'`（公式注入防护），
读回时自动还原。

---

## C2. 备份与回退

每次写盘前都会保留旧内容，方式由全局开关 `--backup-mode` 决定（也可用环境变量
`RTK_BACKUP`）：

| 模式 | 落点 | 用处 |
| --- | --- | --- |
| `bak`（默认） | 目标旁 `<名>.bak` | 立刻能对比的一份副本 |
| `git` | `<tl>/.renpy-toolkit.git` 影子仓库 | 回退到任意一次改动，译文目录不堆副本 |
| `none` | 不备份 | 已在别的版本控制里 |
| `auto` | 有 `git` 用 `git`，否则 `bak` | 脚本里不想写死 |

```bash
python <skill>/scripts/rptk.py --backup-mode git tl polish <tl-dir> -s batch.py --write

python <skill>/scripts/rptk.py tl backup list <tl-dir> --files
python <skill>/scripts/rptk.py tl backup show <tl-dir> <rev> script.rpy
python <skill>/scripts/rptk.py tl backup restore <tl-dir> <rev> --write
python <skill>/scripts/rptk.py tl backup prune <tl-dir> --keep 20 --write
```

影子仓库是独立的 `--git-dir`，**不会在译文目录里创建 `.git`**，也不会 commit 到
用户自己的仓库。每个快照存完整树，CRLF 与 BOM 按字节保真。`tl backup` 没有 `init`：
第一次真正写盘时自动建库。

**没有 git 时**：`--backup-mode git` 自动降级为 `bak`，stderr 打一行 `warning:`，
命令照常跑完。这是刻意的——`.bak` 本身就是可用的备份，不该因为环境问题拦住一次
改译文。但 `tl backup list/show/restore` 这类**操作快照**的命令会报错退出（码 3），
因为没有 `.bak` 版的「show 某个 revision」。降级不代表可以假设快照存在：要回退到
历史版本，先确认 `tl backup list` 能跑。

---

## D. 术语字典

字典是一个 TSV，六列：

```
source<TAB>target<TAB>category<TAB>banned<TAB>scope<TAB>note
```

- `banned` 列写「禁用译法」，用 `|` 分隔
- `scope` 取 `common`（任何游戏都适用：引擎 UI、物种、装备、敬称）或
  `project`（只对本工程成立：任务名、角色梗）。**默认 `project`** ——
  把工程专属词误当通用规则会扩散到别的工程，反过来只是少复用一次。
- 格子里的真换行要写成 `\n`，制表符写 `\t`（本工具读写自动转义）

```bash
python <skill>/scripts/rptk.py tl glossary check <tl-dir> --ui-terms \
    --terms <dict.tsv> --missing --drift
python <skill>/scripts/rptk.py tl glossary list --terms <dict.tsv> --scope common
```

工具自带一份 `renpy_toolkit/data/ui-terms.tsv`（167 条，全是 `renpy/common/`
引擎文案，跨工程可复用），用 `--ui-terms` 带上。

**译名一致性有个陷阱**：仅大小写不同的两条源词不能用同一个大小写不敏感匹配器
（例如 `Slime`=生物 vs `slime`=体液，混用会在另一种含义的行上产生几十条误报）。
本工具对这类冲突自动降级为精确匹配。

---

## E. 游戏更新后不丢译文（port）

```bash
python <skill>/scripts/rptk.py tl port <old-tl-dir> <new-tl-dir> [--write]
```

按**翻译标识符**把旧译本搬到新骨架上 —— 标识符是 `label + md5(代码)`，源码没变就不会变。
按文本匹配会把译文放到错误的行上。

换代后通常会剩下一批「新骨架里有、旧译本没有」的块：那是**真的新增文本**，
不是搬运失败。反过来「旧译本有、新骨架没有」的是作者删掉的文本，可以归档掉。

### 整理排版（sort / split）

```bash
# 按 # game/x.rpy:NNN 的源文件与行号排序，可同时写 Event Index 与分段标记
python <skill>/scripts/rptk.py tl sort <tl-dir> [--index] [--sections] [--write]

# 把「一个 strings 块塞多条 old/new」拆成标准形态（每个字面量一个块）
python <skill>/scripts/rptk.py tl split <tl-dir> <game-dir> [--write]
```

`sort` 只在**同一个** `translate python:` / `translate style:` 块周围建立屏障、不跨过它 ——
切语言时这类块是按文件顺序执行的，重排会改变最终生效的样式与变量。
`--index`/`--sections` 写出来的是纯注释，引擎完全不读，删掉不影响加载。

`split` 需要 `<game-dir>` 来反查每个字面量的原始出处，以便给拆出来的块补
`# game/x.rpy:NNN` 注释。

### 批量润色脚本（polish）

逐行改几万块太慢，实践中会攒出一批一次性的 `polish_script_*.py`。`polish` 把它们
当**数据字典**执行（不 `exec` 任何代码）：

```bash
python <skill>/scripts/rptk.py tl polish <tl-dir> <script.py> [...] [--file NAME] [--write]
```

默认 dry-run，并汇总命中/未命中；加 `--show-problems` 列出未命中原因（最多 40 条）。

---

## F. 翻译表永远命中不了的字符串

`f"You bought 1 {item_name}!"` 这种 f-string 在进入查表之前就已经求值成
`"You bought 1 Sandwich!"`，所以为模板建 `old` 条目**永远命中不了**（这是常见的
无效条目来源）。

```bash
python <skill>/scripts/rptk.py tl untranslatable <dump>          # 报告
python <skill>/scripts/rptk.py tl fix-fstrings <dump> --language chinese \
    --outdir <tl-out> --values item_name=A,B,C --write
```

`fix-fstrings` **不改游戏源码**，两条路：

1. **exact** —— 能枚举结果串时，直接为每个具体结果串建 strings 条目
2. **pattern** —— 不能枚举时生成一个 `translate <lang> python:` 补丁块，包装
   `renpy.translation.translate_string`，按正则替换

关键：**必须先切语言**（`renpy.translation.change_language("<lang>")`）才能测
`translate <lang> python:` 里的代码 —— 那些块只在切语言时执行，直接 import 测不到。

若某条 f-string 确实需要精确控制，改源码的正确写法是

```python
_("You bought 1 [item]!").format(item=item)
```

**不是** `_("You bought 1 {item}!")` —— Python 的 `.format` 作用在 `_()` 之后，
而 `_()` 只是标记函数（`def _(s): return s`），不会替你插值。

---

## G. 硬编码文本

「硬编码」= 文本字面量直接写在 `.rpy` 里、没经过 `_()` 包裹。**它照样能被翻译**：
屏幕渲染任何字符串都会再过一次 `translate_string`，与源码里有没有 `_()` 无关。

```bash
python <skill>/scripts/rptk.py tl hardcoded scan <tl-dir> --language chinese
```

`tl gen` 默认已经从引擎 AST 里把这类字面量一并取出，所以多数情况下不需要单独处理；
`hardcoded` 子命令用于「已经有一份 TL、想知道哪些 strings 条目对应硬编码文本」。
它维护一份清单（`<tl-dir>/hardcoded.json`），`show` 打印、`apply` 给命中的块加
`# hardcoded: <context>` 注释。

### 同一字符串在不同屏幕含义不同

一个 `old` 只能有一个译文。同一个词在 A 屏是「进入」、在 B 屏是「回车键」时：

```bash
# dump 是 tl gen 留下的 rtk_strings.json
python <skill>/scripts/rptk.py tl context <dump> --language chinese            # 先看
python <skill>/scripts/rptk.py tl context <dump> --language chinese \
    --tl-dir <tl-dir> --write                                                  # 更新探测表
```

默认是 dry-run。两条路：源码里写 `{#context}` 标签（需要改源码），或者生成
`translate <lang> python:` 补丁按当前屏幕查表（不改源码）。
`{#ctx}` 标签在渲染时完全不可见，且 `translate()` 会先按精确键匹配、
再剥掉 `{#...}` 重查 —— 所以带标签的键和裸键可以共存。

报告里会混进**单个小写词**（`viewport`、`sound`、`show`）：它们有的是界面文案，
有的是标识符，语法上判不出来，所以不猜 —— 由你决定：

```bash
--ignore viewport --ignore sound          # 直接列
--ignore-file skip.txt                    # 一行一条，# 开头是注释
```

报头给的是**真实总数**（`130 string(s) ... (showing 20)`），不是截断后的条数；
`--show-all` 看全部。

---

## H. 字体：缺字与硬编码字体替换

```bash
python <skill>/scripts/rptk.py font check <fonts...> --tl <tl-dir>          # 缺字检测
python <skill>/scripts/rptk.py font check <fonts...> --tl <tl-dir> \
    --extra-text <notes.txt>                                                # 额外字符
python <skill>/scripts/rptk.py font map <project-dir> --emit chinese        # 审计+生成替换块
```

`<fonts...>` 是字体文件路径（`.ttf` / `.otf` / `.ttc`），`--tl` 指向
`tl/<lang>` 目录。

中文常见问题：原字体没有 CJK 字形 → 显示方块。`font check` 用 fontTools 读 cmap，
拿 TL 里实际用到的字符去比对，列出缺字。

字体替换的标准写法是在语言目录里覆盖（不改源码）：

```renpy
translate chinese python:
    config.font_replacement_map["fonts/original.ttf", False, False] = ("tl/chinese/fonts/zh.ttf", False, False)
```

`font map --emit` 会从工程里扫出硬编码引用的字体路径，生成这个块。
注意 **`config.font_replacement_map` 只在启动时查一次**，运行时改无效。

---

## I. 归档与编译产物

```bash
python <skill>/scripts/rptk.py rpa list    <archive.rpa>
python <skill>/scripts/rptk.py rpa extract <archive.rpa> <outdir> [--no-overwrite]
python <skill>/scripts/rptk.py rpa pack    <output.rpa> <src-dir> [--version 3] [--prefix] [--key HEX]
```

注意 `pack` 的两个位置参数顺序是**先输出后源目录**，容易写反。
`--key` 省略时用 Ren'Py 默认的 `0xdeadbeef`；RPA-3.0 头部只留 8 位十六进制，
超出范围会直接报错而不是写出一个读不回来的归档。

`.rpa` 索引是 zlib + pickle。本工具用白名单 Unpickler（只放行 `builtins` 的
容器与字符串类型），并对解压设 64 MiB 上限 —— 恶意归档常常用超大解压流或
`__reduce__` 触发代码执行。解包时每个条目都过 `safe_join`，`../` 逃逸会被拒绝。

`.rpyc` 判断新旧**只看文件末尾 16 字节的源文件 md5**，与 mtime、体积都无关。
改了 `.rpy` 却没重新编译，游戏仍会加载旧 `.rpyc`；删 `.rpy` 时必须连 `.rpyc`
一起删，否则裸 `.rpyc` 照样被加载。

---

## J. 新建工程与体检

```bash
python <skill>/scripts/rptk.py project new MyGame --dest <parent-dir>
python <skill>/scripts/rptk.py project audit <game-dir>
```

`project new` 的项目名必须是**裸名字**，不能是路径（传路径会被拒绝并提示用 `--dest`）。
生成骨架里的 TL 用原文占位，不是空串。

`project audit` 三条检查：

- `missing-file` / `missing-image`（error）—— 脚本引用了不存在的资源
- `force-archives`（error）—— 作者设了 `config.force_archives = True` 会让磁盘上的
  `.rpy` 被归档里的旧版本覆盖，**整份汉化静默失效**（游戏照常启动、界面全英文、无报错）
- `runtime-strings`（warning）—— 用了 `config.translate_additional_strings_callbacks`
  在运行时注册字符串，静态提取看到的覆盖率是上界不是承诺

只放了脚本、没放资源的工作副本会被识别出来，资源检查合并成一条说明而不是刷几千条误报。

---

## K. 驱动官方 SDK（sdk）

装了 Ren'Py SDK 时，`sdk` 组把官方命令行原样包一层（它只做参数转交与超时，
不重新实现引擎逻辑）。`--sdk` 省略时自动在常见安装位置找 `renpy.exe` / `renpy.sh`：

```bash
python <skill>/scripts/rptk.py sdk find                      # 报出找到的 SDK 路径
python <skill>/scripts/rptk.py sdk translate <game-dir> chinese --count
python <skill>/scripts/rptk.py sdk lint      <game-dir>
python <skill>/scripts/rptk.py sdk dialogue  <game-dir> chinese --strings
python <skill>/scripts/rptk.py sdk extract-strings <game-dir> chinese strings.json
python <skill>/scripts/rptk.py sdk merge-strings   <game-dir> chinese strings.json --replace
python <skill>/scripts/rptk.py sdk compile    <game-dir>
```

**什么时候该用它、什么时候不该：**

- `sdk translate` 只扫磁盘上真实存在的 `.rpy`，对 `.rpyc`-only 的发行版几乎生成不出东西 ——
  那是 `tl gen` 的适用场景（A 节）。
- `sdk translate --count` 是**唯一**能问出「官方口径还缺多少」的方式，用来交叉核对
  `tl verify` 的统计。
- `sdk lint` 是权威对账工具（B 节），但它需要完整可运行的工程；改过源码后跑它，
  顺便能发现语法错误。
- `sdk extract-strings` / `merge-strings` 是官方的 JSON 往返，语料较小时够用；
  大批量仍建议走 C 节的 `tl table`（多了公式注入防护与按 id 对齐）。
- `sdk compile` 会把 `.rpy` 编成 `.rpyc`。**改完 TL 后不要急着编译** ——
  裸 `.rpyc` 与 `.rpy` 同时存在时加载规则见 I 节，删错一个就会出现「改了没生效」。

`--timeout` 默认值对慢机器可能偏紧，跑大工程时显式给大一点。

---

## 硬规则（踩过坑，不要绕）

1. **写 TL 文件必须保留原文件的 BOM 与行尾**。逐文件保留，绝不统一。
   Python 侧：`raw.decode("utf-8-sig")` 读，写回 `("\ufeff" + text).encode("utf-8")`。
2. **译文里的换行写 `\n` 字面量**，不能写真实换行 —— 真换行会劈开译文行，
   触发 `MISSING_TRANS_LINE` / `UNCLOSED_QUOTE`。
3. **值里含 `\"` 时用 raw string**（`new r"..."`）。
4. **界面/strings 文本的引号保留源文的 ASCII 形态**；只有剧情译文行才用中文引号。
   不要把 `"` 换成全角或弯引号。
5. **`old` 必须连文本标签一起写全**（`{size=+10}` `{w}` `{image=...}` 都算 key 的一部分）。
   **多写源里没有的标签同样永不命中** —— 这是反向陷阱。
6. **字符串翻译是全局的**（同一语言的 `StringTranslator` 跨文件共用）。
   判缺漏必须全目录搜 `old`，按文件统计会大量假阳性。
7. **结巴、叠字、口癖是风格，不是错误**。不要「归一化」掉。
8. **不要为 f-string 建模板条目**（见 F）。想「看起来解决了」正是制造无效条目的方式。
9. **不要用体积判断 `.rpyc` 新旧**（见 I）。
10. 安卓侧：`store["renpy"]` 天生是 `renpy.exports` 门面，**禁止裸 `import renpy.xxx`**
    （会顶掉门面）。要用 `import renpy.loader as _loader` 或 `sys.modules[...]`。
    字体文件名必须是纯 ASCII。
11. **不要自己写 `.bak` 复制逻辑**。所有写盘都走 `backup.keep()`，模式由
    `--backup-mode` 决定；自己复制会绕过影子仓库，让 `tl backup restore` 看不到这次改动。

---

## 一次完整的汉化怎么走

```bash
S=<skill>/scripts/rptk.py
G=<game-dir>              # 含 game/ 的那一层
L=$G/game/tl/chinese

# 1 看现状（已有译本时）
python $S tl stats $L

# 2 生成全量骨架（全新目录！）
python $S tl gen $G chinese --outdir /tmp/tl-new --write

# 3 把旧译本搬过来（有旧译本时）
python $S tl port $L /tmp/tl-new --write

# 4 校验
python $S tl verify /tmp/tl-new --expect-dialogue <N> --game-dir $G

# 5 拿不准还缺什么时
python $S tl missing /tmp/tl-new --language chinese

# 6 装回游戏（先备份旧目录）
#    注意目录名必须与 --language 一致，否则 translate 块头与目录对不上，
#    引擎会报 `A translation for "X" already exists`
```

可选补充（按需插入上面流程）：

```bash
python $S tl hardcoded apply /tmp/tl-new --write      # 给无 _() 包裹的界面串打标记
python $S tl untranslatable <dump.json>               # f-string 清单
python $S tl fix-fstrings <dump.json> --language chinese --outdir /tmp/tl-new --write
python $S font check --tl /tmp/tl-new                 # 字体缺字
python $S tl sort /tmp/tl-new --index --sections --write
python $S tl glossary check /tmp/tl-new --ui-terms --terms <dict.tsv> --missing --drift
```

第 5 步是最容易翻车的地方：**输出目录名必须等于 `--language`**。
用 `chinese` 生成就装进 `game/tl/chinese`，装进 `tl/other` 会因为块头写的是
`translate chinese` 而与原有目录撞同一张表。

---

## 需要更多细节时

`dsh-plugin/dsh-renpy-toolkit/README.md` 有逐模块说明与设计依据；`docs/` 下有引擎事实的实证笔记
（文本标签、插值语法、`.rpyc` 格式、`.rpa` 格式、安全审查）。
判断规则有疑问时**优先看引擎源码**：`renpy/translation/`、`renpy/text/extras.py`、
`renpy/substitutions.py` 是全部规则的权威依据。
