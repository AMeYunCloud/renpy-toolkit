# renpy-toolkit 1.0.0 — 发布说明 / Release Notes

面向 Ren'Py 游戏的汉化与制作辅助工具包。核心能力是**从零生成完整的汉化骨架**——
包括只有 `.rpyc`、拿不到源码的发行版。

A toolkit for Ren'Py authoring and localization. Its core capability is generating a
**complete translation skeleton from scratch**, including for releases that ship only
`.rpyc` files with no sources at all.

---

## 中文

### 这个版本解决什么

官方 `renpy <游戏> translate <语言>` 只遍历磁盘上的 `.rpy` 文件，所以对「作者只发了
`.rpyc`」的成品游戏产不出任何骨架——而这恰恰是汉化最常遇到的形态。

本工具改成**运行游戏本身**，从引擎内存里读 `translator.file_translates` 与 SL2 屏幕树，
因此不依赖源码。在一份 50 个 `.rpyc` 的发行版上实测：

| 指标 | 结果 |
|---|---|
| 对白块 | 58,249（引擎自带 `lint` 对原文与译文报的是同一个数字） |
| 字符串 | 约 2,700 条（界面文字、`show text`、菜单选项等） |
| 耗时 | 19.6 秒 |
| 覆盖率 | 与一份已上线成品译料对比，唯一原文串覆盖 93.0% |

### 三条独立的发行线

三份产物各自独立：exe 不依赖插件与 skill，插件与 skill 也不含、不调用 exe。

| 线 | 适合 | 怎么用 |
|---|---|---|
| **插件 / pip** | 有 Python、想留在命令行 | `pip install renpy-toolkit`（含 Excel/字体装 `[all]`）；或解压便携 zip 跑 `renpy-toolkit.cmd` |
| **Agent skill** | 给 AI 助手用 | 把 `skills/renpy-toolkit/` 放进 skill 目录 |
| **Windows exe** | 只想点按钮，不碰命令行 | 解压 zip，放到游戏文件夹旁边，双击 `renpy-toolkit.exe` |

exe 的下载在单独的 Release：**exe-v1.0.0**。（本页只挂插件与 skill 两份产物。）

### 主要能力

**生成骨架** `tl gen` 跑游戏本体，一次抓齐对白与界面文本。骨架用**原文占位**而不是
空串——引擎里 `new ""` 是「有效译文」，会把界面文字整片擦成空白。

**没有源码也能读** `decompile` 用白名单 pickle 虚拟机还原 `.rpyc`，不执行游戏的字节码；
`rpa` 读写 RPA-2.0/3.0 归档，支持非默认 key，目录里有两个 `archive.rpa` 也能一起读。

**译文质量** `verify` 检查覆盖率、标签配对、插值、空译文、结巴丢失等；`port` 在游戏
更新后按标识符把旧译本搬到新骨架；`glossary` 用术语表检查译名统一。

**三类「翻不了」的文本** 跨屏多义 → `tl context` 生成消歧补丁；变量拼接
（`f"You bought {n} {item}!"`）→ `tl untranslatable` 列出、`tl fix-fstrings` 给方案；
硬编码字体 → `font map` 生成 `translate <lang> python:` 替换块、`font check` 查缺字。

**改文件不怕改坏** 每次写盘前自动留副本（`.bak`）或 Git 快照
（`--backup-mode git`），`tl backup list/restore` 可逐次回退；机器上没有 git 时自动
降级为 `.bak` 并明确提示。

### exe 形态的两点说明

界面是「本地小服务 + 系统浏览器窗口」，不需要 WebView2；启动后自动扫描 exe 所在目录
及其一级子目录里的 Ren'Py 游戏，弹窗让你选一个（0 个、1 个、多个都会弹）。

除「生成汉化骨架」写进 `game\tl\<语言>\` 之外，其余产物都收在 exe 旁边的
`renpy-toolkit-数据\`（日志、导出表格、反编译结果、备份快照、临时文件），不会在游戏
目录里留东西。

### 已知限制

- `tl gen` 需要游戏自带的解释器（`lib/py3-*/python.exe`）——骨架必须由目标游戏的运行时给出。
- 单文件 exe 每次启动会解包到系统临时目录；若机器策略禁止创建该目录，可用
  `--runtime-tmpdir <可写目录>` 重新打包，或改用一键包形态。
- Git 快照需要 git 2.15+；没有时自动降级为 `.bak`。
- 只提供 Windows exe；Python 形态在三大平台都可运行。

### 许可

MIT。上游参考：[renpy/renpy](https://github.com/renpy/renpy)、
[CensoredUsername/unrpyc](https://github.com/CensoredUsername/unrpyc)、
[Dclef/renpy-tl](https://github.com/Dclef/renpy-tl)、
[ZYKsslm/RenPy-Tutorial](https://github.com/ZYKsslm/RenPy-Tutorial)、
[Shizmob/rpatool](https://github.com/Shizmob/rpatool)。没有 import 它们的代码，
规则依据各自在 `docs/` 里逐条标注。

---

## English

### What this release solves

The official `renpy <game> translate <lang>` command only walks `.rpy` files present on
disk, so for a finished game that ships **only `.rpyc`** it produces no skeleton at
all — which is exactly the case most localizations run into.

This toolkit instead **runs the game's own runtime** and reads
`translator.file_translates` plus the SL2 screen tree from engine memory, so it does not
need sources. Measured on a release with 50 `.rpyc` files:

| Metric | Result |
|---|---|
| Dialogue blocks | 58,249 (the engine's own `lint` reports the same figure for source and translation) |
| Strings | ~2,700 (UI labels, `show text` payloads, menu options) |
| Duration | 19.6 s |
| Coverage | 93.0% of unique source strings, compared against a shipping translation |

### Three independent release lines

The three artifacts stand alone: the exe does not depend on the plugin or the skill,
and neither of them contains or invokes the exe.

| Line | For | Start with |
|---|---|---|
| **Plugin / pip** | Python available, prefer the CLI | `pip install renpy-toolkit` (`[all]` adds Excel and font support); or unzip the portable zip and run `renpy-toolkit.cmd` |
| **Agent skill** | AI assistants | Drop `skills/renpy-toolkit/` into the skills directory |
| **Windows exe** | Clicking buttons, no command line | Unzip beside the game folder, double-click `renpy-toolkit.exe` |

The exe is published as a separate release: **exe-v1.0.0**. This page carries only
the plugin and skill artifacts.

### Capabilities

**Skeleton generation** — `tl gen` runs the game itself and captures both dialogue and UI
text in one pass. Bodies use the **source text as the placeholder**, never `""`: in the
engine `new ""` is a valid translation and renders text as blank.

**Works without sources** — `decompile` restores `.rpyc` through a whitelisting pickle
VM and never executes game bytecode; `rpa` reads and writes RPA-2.0/3.0 archives,
supports non-default keys, and reads both `archive.rpa` and `archive2.rpa` in one go.

**Translation quality** — `verify` checks coverage, tag balance, interpolation, empty
targets and lost stutters; `port` carries old translations into a new skeleton by
identifier after a game update; `glossary` checks terminology consistency.

**Three kinds of untranslatable text** — same string on different screens →
`tl context` emits a disambiguation patch; f-strings (`f"You bought {n} {item}!"`) →
`tl untranslatable` lists them and `tl fix-fstrings` proposes a fix; hard-coded fonts →
`font map` emits a `translate <lang> python:` block and `font check` reports missing
glyphs.

**Safe writes** — every write keeps the previous bytes as a `.bak` copy or a Git
snapshot (`--backup-mode git`), with `tl backup list/restore` to step back; without git
installed the `git` mode degrades to `.bak` and says so.

### About the exe

The window is a local HTTP service plus your system browser in `--app` mode — no
WebView2 required. On start it scans the exe's folder and one level below, and always
shows a picker (zero, one or many games).

Only the skeleton is written into the game (`game\tl\<language>\`). Everything else —
logs, exported spreadsheets, decompiled scripts, snapshots, scratch files — lands in
`renpy-toolkit-数据\` beside the exe.

### Known limitations

- `tl gen` needs the game's own interpreter (`lib/py3-*/python.exe`); the skeleton must
  come from the target game's runtime.
- The single-file exe unpacks into the system temp directory on each start. If machine
  policy blocks that, rebuild with `--runtime-tmpdir <writable dir>` or use the portable
  zip instead.
- Git snapshots need git 2.15+; without it the mode degrades to `.bak`.
- The exe is Windows-only; the Python forms run on any platform.

### License

MIT. Upstream references: [renpy/renpy](https://github.com/renpy/renpy),
[CensoredUsername/unrpyc](https://github.com/CensoredUsername/unrpyc),
[Dclef/renpy-tl](https://github.com/Dclef/renpy-tl),
[ZYKsslm/RenPy-Tutorial](https://github.com/ZYKsslm/RenPy-Tutorial),
[Shizmob/rpatool](https://github.com/Shizmob/rpatool). No upstream code is imported;
each rule is cited in `docs/`.
