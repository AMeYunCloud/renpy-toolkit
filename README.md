# Ren'Py 制作与汉化工具包（renpy-toolkit）

一套面向 Ren'Py 游戏的**制作辅助**与**汉化**工具包，纯标准库实现，不依赖 Ren'Py 本体。

English: [`README.en.md`](README.en.md)

本工具包从四个上游项目沉淀而来：

| 上游 | 学到什么 |
| --- | --- |
| [renpy/renpy](https://github.com/renpy/renpy) | 引擎本体。翻译标识符生成、字符串表、官方 `translate` 生成器、文本标签表、归档加载优先级 |
| [Dclef/renpy-tl](https://github.com/Dclef/renpy-tl) | 中文本地化实战清单：检查项取舍、标点规则、字体替换 |
| [ZYKsslm/RenPy-Tutorial](https://github.com/ZYKsslm/RenPy-Tutorial) | 中文入门教程：制作侧路线 |
| [CensoredUsername/unrpyc](https://github.com/CensoredUsername/unrpyc) | `.rpyc` 反编译的参考实现（本工具改为白名单 pickle 虚拟机） |
| [Ren'Py 官方文档](https://www.renpy.org/doc/html/) | 翻译机制、CLI、自定义文本标签的语义 |

完整的上游清单与出处对照见 [`plugin/dsh-renpy-toolkit/README.md`](plugin/dsh-renpy-toolkit/README.md#相关项目与参考)。

## 目录结构

```
renpy-toolkit/
├── plugin/
│   └── dsh-renpy-toolkit/  renpy_toolkit —— 工具包真源（自包含，可整目录拷走）
│       ├── renpy_toolkit/  27 个模块（纯命令行，无界面代码）
│       ├── tests/          519 个 unittest 用例
│       ├── data/ui-terms.tsv   内置通用 UI 术语表（167 条）
│       ├── VERSION         版本号
│       └── README.md       完整的模块说明、用法与设计约束
├── skills/renpy-toolkit/   同功能的 agent skill（DSH 插件形态下由插件自动注册）
│   ├── SKILL.md            按任务选路表 + 硬规则
│   └── scripts/rptk.py     启动器（不依赖 cwd 与安装位置）
├── exe/                    图形界面（独立发行线，不随包分发）—（已停止维护）
│   ├── gui.py              本地 HTTP 服务 + 系统浏览器窗口
│   ├── ui/index.html       界面
│   └── tests/              界面的纯逻辑测试（30 例）
├── docs/                   参考笔记（引擎事实、格式规范）
├── tools/
│   ├── build_release.py    构建 dist/plugin 并写 MANIFEST.sha256
│   ├── build_portable.py   构建 dist/portable 一键包（含 .zip）
│   ├── build_exe.py        用 PyInstaller 打 Windows exe（单文件版）并自检（已停止维护）
│   ├── exe_entry.py        exe 入口：无参数开窗口，带参数走命令行（已停止维护）
│   ├── check_portable.py   在干净解压副本上验收一键包
│   ├── check_skill.py      校验 SKILL.md 承诺的 flag 与子命令真实存在
│   ├── check_readme.py     校验 README 的目录锚点与相对链接
│   └── check_filter.py     对一份真实译料评估候选过滤规则的误伤
└── dist/                   构建产物（不入库）
    ├── plugin/             供 skill 调用的包目录
    ├── portable/           一键包目录 + renpy-toolkit-<version>.zip
    └── exe/                Windows exe（onefile/renpy-toolkit.exe，已停止维护）
```

`plugin/dsh-renpy-toolkit/` 是唯一真源，`dist/` 由 `tools/build_*.py` 生成、被 `.gitignore`
排除——发布版存在的意义只是让「在用的」与「发出去的」能逐字节比对。

工具包单独装在一个以发行名命名的目录里（`plugin/dsh-renpy-toolkit/`），是为了能整目录
拷到别处单独验证，而不必连带整个仓库。

用法见 [`plugin/dsh-renpy-toolkit/README.md`](plugin/dsh-renpy-toolkit/README.md)（工具包）与
[`skills/renpy-toolkit/SKILL.md`](skills/renpy-toolkit/SKILL.md)（skill）。

## 三条独立的发行线

三份产物各自独立：**exe 不依赖插件与 skill，插件与 skill 也不含、不调用 exe**。

| 线 | 产物 | 适合谁 | 怎么开始 |
| --- | --- | --- | --- |
| **插件 / pip** | `renpy-toolkit` 命令 + 便携 zip（`renpy-toolkit-<version>.zip`） | 想在命令行或 DSH 插件里用 | `pip install .`（含 Excel/字体功能装 `[all]`）；或解压便携 zip 双击 `自检.cmd` 后 `python renpy-toolkit.py --help` |
| **agent skill** | `skills/renpy-toolkit/` | 让 AI 助手自己会做汉化 | 把整个目录放进 skills 目录 |
| **Windows exe**（已停止维护） | `renpy-toolkit-exe-<version>.zip` | 只想点按钮，不碰命令行 | 解压到游戏目录旁边，双击 `renpy-toolkit.exe` |


```bash
git clone https://github.com/AMeYunCloud/renpy-toolkit.git
cd renpy-toolkit
pip install .              # 得到 renpy-toolkit 命令
pip install '.[all]'       # 附带 fontTools（字体覆盖率）与 openpyxl（XLSX 读写）
```

不装也能用：`cd plugin/dsh-renpy-toolkit && python -m renpy_toolkit --help`。插件与 skill 走的是
同一份 Python 代码，默认依赖仅标准库；`fontTools` 与 `openpyxl` 是可选能力，缺失时会给出可读的
`DependencyError` 而不是 import 失败。**exe 是另一条线（已停止维护）**：源码在 `exe/`，由 `tools/build_exe.py`
把这两个可选依赖一起打包，所以那两个功能在 exe 里开箱可用；反过来，插件与 skill 不含也不调用界面代码。

### Windows exe（图形界面）—（已停止维护）

> **（已停止维护）** 这条线已停止维护：不再随新版本发布、不再修复。下面保留的是它当初的
> 形态说明，供参考；要用图形界面需自行 `python tools/build_exe.py --clean` 构建，产物未经
> 当前版本验证。日常请用上面两条线（插件 / pip 与 agent skill），它们只用命令行。

`python tools/build_exe.py` 产出**单文件版**（需要构建虚拟环境，见脚本头部说明）：

```
dist/exe/onefile/renpy-toolkit.exe    单文件版，就一个文件，双击即用
```

这条线与其他两条完全独立：界面源码在 `exe/`，产物里不含界面代码，插件与 skill 也从不调用 exe。

界面是一个浅色高对比网页，由 exe 起一个**只监听本机 127.0.0.1** 的小服务，再用系统自带的
Edge/Chrome 以「app 窗口」模式打开（没有地址栏，看起来就是个桌面程序；不联网、
不加载任何远程资源）。找不到这两个浏览器时，exe 会提示你改用 `--cli`。
双击后窗口自己扫「exe 所在目录 + 它的一级子目录」里所有 Ren'Py 游戏
（判定标准：有 `game/` 子目录，且有启动器 `.py`/`.exe`、或 `lib/py3-*/python.exe`、
或 `game/` 里有 `.rpy`/`.rpyc`/`.rpa`），列成卡片让你选一个。**0 个、1 个、多个都会
弹出这个窗口**——一次只处理选中的那一个，不碰其它游戏。自动扫描没找到时，点「浏览…」
手动指到游戏所在文件夹。

选完之后所有按钮都作用于这个游戏，不再问路径：

| 分组 | 按钮 |
| --- | --- |
| 准备 | 环境体检、生成汉化骨架、搬运旧译文 |
| 翻译 | 导出 Excel 表格、回填 Excel 译文 |
| 检查 | 校验译文、术语一致性、硬编码文本 |
| 工具 | 字体覆盖率、字体替换模板、查看备份、立即备份 |
| 资源 | 查看归档内容、反编译 `.rpyc` |

会写文件的按钮**先跑一遍 dry-run**，弹窗把「将写入几个目标、写到哪个目录」和预演输出
一起摆出来，点「确认执行」才真的落盘。语言目录名与备份方式（`bak` / `git` / `none`）在
窗口里选，选择会记在 exe 同目录的 `config.json`，下次启动预选上次的游戏。

**产物集中在一个目录里**：除了「生成汉化骨架」必须写进游戏 `game\tl\<语言>\`，
其余所有产物都落在 exe 旁边的 `renpy-toolkit-数据\`：

```
renpy-toolkit-数据\导出\      导出的 xlsx
renpy-toolkit-数据\反编译\    从 .rpyc 还原的 .rpy
renpy-toolkit-数据\备份\      译文快照（bak 副本或 git 影子仓库）
renpy-toolkit-数据\临时\      跑游戏探针时的临时目录
renpy-toolkit-数据\日志\
```

exe 自带 `fontTools`、`openpyxl`（字体覆盖率、Excel 两块开箱可用）；界面走系统浏览器，
不依赖 WebView2 或任何原生绑定。
它也能当命令行用：`renpy-toolkit.exe --cli --help`；窗口模式没有控制台，命令行输出改写
在 exe 同目录的 `renpy-toolkit-cli.log`。

`tl gen` 仍然需要游戏自带的解释器（`lib/py3-*/python.exe`），这是设计使然：
只有游戏自己的运行时里才有它那份 `renpy/` 引擎代码，本项目不去复刻引擎。

**单文件版的已知限制**：

- 每次启动都要把包解到临时目录，第一次打开比常规程序慢一两秒；
- 如果系统策略不允许程序在自己的临时目录下创建子目录（企业管控、部分安全软件、
  受限用户令牌），单文件版会打不开、或双击后一闪就没了。这时重建时指定解包目录：
  `python tools/build_exe.py --runtime-tmpdir D:\rt`
  （`--verify` 会把这种情况标成「环境受限」而不是失败）。

### 一键包（便携发布形态）

`python tools/build_portable.py --zip` 产出 `dist/portable/renpy-toolkit-<version>.zip`。
它不需要 `pip`、不需要 `cd`、不设任何环境变量，解压即可用：

```
renpy-toolkit.py          所有平台共用的入口
renpy-toolkit.cmd         Windows：双击即用，也可把游戏文件夹拖到它上面
renpy-toolkit.sh          macOS / Linux 启动器
自检.cmd / selfcheck.sh   一条命令验证这份拷贝在本机能不能用
README.txt                纯文本快速上手（zip 里也能直接读）
renpy_toolkit/            包本体
```

三个启动器不含独立逻辑，全部转交给 `renpy-toolkit.py`——这样 Windows 与 POSIX 行为一致，
出问题只有一处要改。反过来说也有代价：`.cmd` 必须用 CRLF 行尾，用 LF 写出来会让
`goto` 找不到标签、整份脚本退化成一片 “不是内部或外部命令”，所以行尾是按文件类型
决定的，不是全局统一。

`--selftest` 是这套形态的核心：它检查 Python 版本、能否导入包、可选依赖在不在、
`.rpa` 写入/读回是否一致、翻译文件能否解析，最后打印一行 `self-check passed`。
全程只写临时目录，不会碰你要汉化的游戏。

agent skill 不需要安装，把 `skills/renpy-toolkit/` 放进 skills 目录即可；启动器会依次
尝试 `$RPTK_HOME`、`<repo>/plugin`、`<skill>/plugin` 找工具包。

### 下载现成的包（Releases）

两个 zip 挂在 [Releases](https://github.com/AMeYunCloud/renpy-toolkit/releases) 上，不进 git 历史——它们是构建产物，
每改一行就提交一个二进制压缩包会让仓库迅速膨胀，而且历史里全是无法 diff 的 blob。

| 附件 | 内容 | 怎么用 |
| --- | --- | --- |
| `renpy-toolkit-<version>.zip` | 一键包（含包本体与三个启动器） | 解压到任意位置，先跑 `自检.cmd` / `./selfcheck.sh` |
| `renpy-toolkit-skill-<version>.zip` | skill（`SKILL.md` + 启动器 + 安装说明） | 解压到 skills 目录，再把 `RPTK_HOME` 指向解压后的一键包 |

skill 包**故意不内嵌包本体**：同一份 Python 复制两遍必然漂移，而启动器本来就有
文档化的查找顺序。它的 `README.txt` 里写清了顺序与最少配置方式。

发一个版本用一条命令（会先校验 `VERSION`/`__version__`/`pyproject`/`CHANGELOG`
四处版本号一致，再重建两个包、打注释 tag、推 tag、创建 Release 并上传附件）：

```bash
python tools/make_release.py --dry-run   # 先看要跑哪些命令
python tools/make_release.py             # 真的发布
```

## 核心能力

### 1. 全量骨架生成（`tl gen`）

官方 `translate` 命令只扫磁盘上真实存在的 `.rpy`，所以对**只发 `.rpyc` 的发行版
只能生成 `common.rpy`**；社区工具按「有效长度 < 8」之类规则静默丢句。两者都不会
告诉你漏了什么。

`tl gen` 走另一条路：**把游戏自己的运行时跑起来**，从引擎内存里取翻译单元
（`renpy.game.script.translator.file_translates` 与 SL2 屏幕树）。

```bash
cd plugin/dsh-renpy-toolkit
python -m renpy_toolkit tl gen <game> chinese --outdir work/tl --write
```

实测（58,249 句、`game/` 下只有 50 个 `.rpyc` 的发行版）：

```text
chinese: 58249 translate block(s), 2687 string(s) in 50 file(s)
wrote 53 file(s)            耗时 19.6s
```

引擎自己的 `lint` 报 `The chinese translation contains 58,249 dialogue blocks`
—— 与原文**逐数字吻合**。

骨架用**原文占位**而非空串：`new ""` 不是「未翻译」，
`StringTranslator.translate()` 里 `if new is not None: return new` 会让文字**变成空白**。

### 2. 二进制资产

| 能力 | 说明 |
| --- | --- |
| `.rpyc` → `.rpy` | `pickletools` 白名单虚拟机，**从不调用 `pickle.loads`**；三级验证 67,453 个标识符 0 差异 |
| `.rpa` 读写 | RPAv2 / RPAv3，XOR 混淆与 prefix 切片；索引走白名单 `Unpickler`，解压有 64 MiB 上限，条目名防 Zip Slip |

### 3. 译文质量

- **`verify`**：空译文、标签/占位符配平、行尾混用、字符串冲突、结巴丢失、全角引号、
  块外散落语句、未知标签。检查项都对着真实成品译料调过误报（依据见
  [`docs/text-tags-and-interpolation.md`](docs/text-tags-and-interpolation.md)）。
- **`port`**：游戏更新后把旧译文搬到新骨架，用「标识符 + 源文本」双重锁，
  源文改过的块不搬（实测 58,249 块里搬进 53,093 句）。
- **`glossary`**：术语字典驱动的一致性校验与全局改名。
- **`--backup-mode`**：所有写盘前先保留旧内容，`bak`（同目录 `.bak`）/ `git`
  （`<tl>/.renpy-toolkit.git` 影子仓库，可回退到任意一次）/ `none`。影子仓库是独立
  `--git-dir`，**不会在译文目录里建 `.git`，也不会 commit 到用户自己的仓库**；
  CRLF 与 BOM 按字节保真（`core.autocrlf` 与 `git show` 两个坑都已处理）。
  `tl backup list` / `show` / `restore` / `prune` / `size`。

### 4. 三类「翻不了」的文本

| 类型 | 表现 | 处理 |
| --- | --- | --- |
| **跨屏多义** | 一个 `old` 只能有一个 `new`，`Enter`（进来）与 `Enter`（回车键）无法各自翻译 | 官方出口是 `{#context}`（要改源码）；本工具生成 `translate <lang> python:` 运行时补丁，按 `_current_screen` 查表 |
| **f-string** | 在翻译查表**之前**就求值了，模板键永不命中——给它写 `strings` 条目是**无效**的 | `untranslatable` 报出来；`fix-fstrings` 按可枚举性生成 `strings` 条目或正则补丁，**不改源码** |
| **硬编码字体** | 字体路径字面量写死在源码里 | `font map` 审计覆盖率并出 `config.font_replacement_map` 模板 |

## 文档

| 文档 | 内容 |
| --- | --- |
| [`plugin/dsh-renpy-toolkit/README.md`](plugin/dsh-renpy-toolkit/README.md) | 模块说明、全部子命令、设计约束 |
| [`docs/README.md`](docs/README.md) | 参考文档索引 |
| [`docs/renpy-engine-notes.md`](docs/renpy-engine-notes.md) | 引擎架构与核心机制 |
| [`docs/official-cli-and-translation.md`](docs/official-cli-and-translation.md) | 翻译机制与官方 CLI（文档核实） |
| [`docs/renpy-source-verification.md`](docs/renpy-source-verification.md) | 从引擎源码逐行核实的接口 |
| [`docs/rpyc-decompile.md`](docs/rpyc-decompile.md) | `.rpyc` 格式与反编译实现 |
| [`docs/rpa-format-spec.md`](docs/rpa-format-spec.md) | RPA 归档格式 |
| [`docs/text-tags-and-interpolation.md`](docs/text-tags-and-interpolation.md) | 文本标签与插值（引擎侧实证） |
| [`docs/custom-text-tags.md`](docs/custom-text-tags.md) | 自定义文本标签（官方语义） |
| [`docs/renpy-translation-ecosystem.md`](docs/renpy-translation-ecosystem.md) | 汉化生态参考 |
| [`docs/renpy-tutorial-notes.md`](docs/renpy-tutorial-notes.md) | 制作侧入门参考 |
| [`docs/release-notes-1.0.0.md`](docs/release-notes-1.0.0.md) | 1.0.0 发布说明（中英双语） |

## 开发

```bash
cd plugin/dsh-renpy-toolkit
python -m unittest discover -s tests -t tests -q     # 519 passed
```

必需 Python 3.10+，仅标准库；可选 `fontTools`（字体 cmap）、`openpyxl`（XLSX 读写）。
测试必须是 `discover -s tests -t tests`——`python -m unittest tests.test_x` 会因解析不到
`_util` 报 `ModuleNotFoundError`。

构建发布版与校验 skill：

```bash
python tools/build_release.py            # 生成 dist/plugin（含 VERSION 与 MANIFEST.sha256）
python tools/build_release.py --verify   # 重新哈希逐个比对
python tools/build_release.py --sync     # 再用发布版覆盖 plugin/dsh-renpy-toolkit/，两边不许漂移
python tools/build_portable.py --zip     # 生成 dist/portable 一键包与 .zip
python tools/build_exe.py --clean        # 生成 dist/exe/onefile 单文件 exe，并跑 --selftest 自检（已停止维护）
python tools/check_portable.py           # 在干净解压副本上验收一键包（自检/子命令/退出码/清单/行尾）
python tools/check_skill.py              # 校验 SKILL.md 里写的每个 flag 与子命令真实存在
python tools/check_readme.py             # 校验 README 的目录锚点与相对链接
```

`.pyc` 无法永久消除（第一次 import 就重建），所以「纯净」= 交付瞬间不含缓存。
想在验证的同时不弄脏交付树，就把发布版复制到临时目录里跑：

```powershell
$tmp = Join-Path $env:TEMP "rptk-check"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item dist\plugin $tmp -Recurse
Push-Location $tmp
python -m unittest discover -s tests -t tests -q     # 519 tests
Pop-Location
Remove-Item $tmp -Recurse -Force
```

## 运行环境

- 需要 Python 3.10+，仅标准库；可选 `fontTools`（字体 cmap）、`openpyxl`（XLSX 读写）
- 无 pytest 依赖，测试是纯 unittest
- 端到端冒烟覆盖 CLI：`project new` → `project audit` → `tl verify`（含故意制造的坏译文，
  确认能报 `EMPTY`）→ `tl stats`

## 附录

### 关于早期抓取限制

项目的调研阶段是在一台网络受限的机器上做的，当时记录了可用通路（`renpy.org/doc/html`、
`gitcdn.top`、`gist.github.com` 可用；`raw.githubusercontent.com` 与常见 CDN 因证书失败）。
这只影响调研方式，与工具本身的运行无关。

### 关于本仓库的测试证据

`docs/` 里的规则全部来自对引擎源码与真实发行版的实测，而不是文档转述。每条被推翻过
的结论都保留了「先前的判断错在哪」——这部分是刻意的，比结论本身更有用。
