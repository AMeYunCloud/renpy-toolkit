# Ren'Py 制作与汉化工具包（renpy-toolkit）

一套面向 Ren'Py 游戏的**制作辅助**与**汉化**工具包，纯标准库实现，不依赖 Ren'Py 本体。

最初的任务是学习三个上游仓库，然后沉淀成可复用的工具：

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
│       ├── renpy_toolkit/  27 个模块
│       ├── tests/          505 个 unittest 用例
│       ├── data/ui-terms.tsv   内置通用 UI 术语表（167 条）
│       ├── VERSION         版本号
│       └── README.md       完整的模块说明、用法与设计约束
├── skills/renpy-toolkit/   同功能的 agent skill（DSH 插件形态下由插件自动注册）
│   ├── SKILL.md            按任务选路表 + 硬规则
│   └── scripts/rptk.py     启动器（不依赖 cwd 与安装位置）
├── docs/                   调研与实证笔记（引擎事实、格式规范、四轮安全审查）
├── tools/
│   ├── build_release.py    构建 dist/plugin 并写 MANIFEST.sha256
│   ├── build_portable.py   构建 dist/portable 一键包（含 .zip）
│   ├── check_portable.py   在干净解压副本上验收一键包
│   ├── check_skill.py      校验 SKILL.md 承诺的 flag 与子命令真实存在
│   ├── check_readme.py     校验 README 的目录锚点与相对链接
│   └── check_filter.py     对一份真实译料评估候选过滤规则的误伤
└── dist/                   构建产物（不入库）
    ├── plugin/             供 skill 调用的包目录
    └── portable/           一键包目录 + renpy-toolkit-<version>.zip
```

`plugin/dsh-renpy-toolkit/` 是唯一真源，`dist/` 由 `tools/build_*.py` 生成、被 `.gitignore`
排除——发布版存在的意义只是让「在用的」与「发出去的」能逐字节比对。

工具包单独装在一个以发行名命名的目录里（`plugin/dsh-renpy-toolkit/`），是为了能整目录
拷到别处单独验证，而不必连带整个仓库。

用法见 [`plugin/dsh-renpy-toolkit/README.md`](plugin/dsh-renpy-toolkit/README.md)（工具包）与
[`skills/renpy-toolkit/SKILL.md`](skills/renpy-toolkit/SKILL.md)（skill）。

## 安装

三种形态，按需要选一种：

| 形态 | 适合谁 | 怎么开始 |
| --- | --- | --- |
| **一键包**（便携 zip） | 只想用，不想碰 Python 工程 | 解压，双击 `自检.cmd` 确认可用，然后 `python renpy-toolkit.py --help` |
| **pip 安装** | 想当命令行工具用 | `pip install .`，得到 `renpy-toolkit` 命令 |
| **agent skill** | 让 AI 助手自己会做汉化 | 把 `skills/renpy-toolkit/` 放进 skills 目录 |

```bash
git clone https://github.com/AMeYunCloud/renpy-toolkit.git
cd renpy-toolkit
pip install .              # 得到 renpy-toolkit 命令
pip install '.[all]'       # 附带 fontTools（字体覆盖率）与 openpyxl（XLSX 读写）
```

不装也能用：`cd plugin/dsh-renpy-toolkit && python -m renpy_toolkit --help`。三种形态背后的代码是同一份，
依赖都仅标准库；上面两个是可选能力，缺失时会给出可读的 `DependencyError` 而不是 import 失败。

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
| [`docs/renpy-engine-notes.md`](docs/renpy-engine-notes.md) | 引擎架构 |
| [`docs/official-cli-and-translation.md`](docs/official-cli-and-translation.md) | 翻译机制与官方 CLI（文档核实） |
| [`docs/renpy-source-verification.md`](docs/renpy-source-verification.md) | 从引擎源码逐行核实的接口 |
| [`docs/rpyc-decompile.md`](docs/rpyc-decompile.md) | `.rpyc` 二进制格式与反编译实现 |
| [`docs/rpa-format-spec.md`](docs/rpa-format-spec.md) | RPA 归档格式（rpatool 源码核实） |
| [`docs/text-tags-and-interpolation.md`](docs/text-tags-and-interpolation.md) | 文本标签/插值/空译文的引擎实证 |
| [`docs/custom-text-tags.md`](docs/custom-text-tags.md) | 自定义文本标签的官方语义 |
| [`docs/renpy-translation-ecosystem.md`](docs/renpy-translation-ecosystem.md) | 汉化生态与工具链调研 |
| [`docs/renpy-tutorial-notes.md`](docs/renpy-tutorial-notes.md) | 制作侧入门要点 |

## 开发

```bash
cd plugin/dsh-renpy-toolkit
python -m unittest discover -s tests -t tests -q     # 505 passed
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
python -m unittest discover -s tests -t tests -q     # 505 tests
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
