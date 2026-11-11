# renpy-toolkit Windows exe 1.0.0 — 一键包（最后一版，已停止维护）

> **先把话说清楚：本项目首推「插件 / pip」与「agent skill」两条线。**
> 它们随版本更新、有支持、跨平台，也是全部功能的唯一落点。
>
> 本页这个 exe 只是给「不想碰命令行」的人用的图形壳：
> **已停止维护（deprecated）——这是最后一版，之后不再修复、不再发新版本。**
> 想长期使用、想拿到修复和新功能，请用插件或 skill。

> **Read this first: the project recommends the plugin / pip line and the agent skill.**
> Both are maintained, supported and cross-platform, and they are where every feature
> lives. The exe on this page is only a GUI shell for people who will not touch a
> command line: **it is deprecated — this is the final build, and it will receive no
> further fixes or releases.** For long-term use, install the plugin or the skill.

---

## 中文

### 三条线怎么分工

| 线 | 分工 | 局限 |
|---|---|---|
| **插件 / pip**（首推） | 全部功能所在，也是唯一持续更新的线；可嵌进 DSH 插件、可进 CI | 需要 Python 3.8+（或用便携 zip 里自带的运行时）；`tl gen` 必须由**目标游戏自带**的 `lib/py3-*/python.exe` 跑 |
| **agent skill**（首推·AI 场景） | 不含代码，把同一套命令与判断教给 AI 助手 | 上限等于插件：本机没有工具包就跑不动；拷走目录后 `scripts/rptk.py` 需能找到工具包或设 `RPTK_HOME` |
| **Windows exe**（本页·已停止维护） | 图形壳，功能与插件一致，不碰命令行 | 仅 Windows；界面借系统浏览器；不再修复 |

### 这个包能做什么

解压后放到游戏文件夹旁边（与游戏文件夹同级，或它的上一级），双击 `renpy-toolkit.exe`：
自动扫描所在目录及一级子目录里的 Ren'Py 游戏（只认真正的 Ren'Py 游戏，看 RPC2 魔数、
`renpy/` 运行时、`lib/py3-*`、启动脚本与 `game/*.rpy` 证据链），弹窗让你选一个，然后点按钮。

覆盖：生成汉化骨架、校验译文、导出/回填 Excel、搬运旧译文、术语检查、硬编码扫描、
备份与回滚、字体工具、归档与反编译。

**写盘边界**：只有「生成汉化骨架」写进 `game\tl\<语言>\`；其余产物（日志、导出表格、
反编译结果、备份快照、临时文件）全部收在 exe 旁边的 `renpy-toolkit-数据\`。

**防呆**：放在磁盘根目录会弹出「危险路径，本工具停止工作」并在 10 秒后自动退出；
重复双击不会启动第二个实例；找不到游戏时直接告诉你把 exe 移到游戏旁边。

**关掉网页即退出**：页面每 4 秒一次心跳，**20 秒**没有任何请求就自己结束（判断依据是
页面在不在，不是浏览器进程在不在）。右上角「退出工具」可立即结束。

### 这个包的局限（请先读，再决定要不要用）

1. **已停止维护**：本版之后不再有修复。要修的问题请改用插件/skill，或自行构建。
2. **仅 Windows**，且体积较大（单文件约 17 MB，每次启动要解包）。
3. **界面靠系统浏览器**：默认开一个无地址栏的独立窗口；个别机器上 Edge 自己的
   「无法更新 Microsoft Edge」提示页会挡住界面 —— 改用 `renpy-toolkit.exe --tab`
   在你现有浏览器里开标签页。
4. **受限账户可能起不来**：单文件引导器需要在可写位置创建解包目录，个别机器（受限
   令牌、组策略、安全软件）会报 `Could not create temporary directory`。自建版本可用
   `python tools/build_exe.py --runtime-tmpdir D:\rt` 指定解包位置。
5. **`tl gen` 仍需要游戏自带解释器**：骨架必须由目标游戏自己的运行时给出，exe 不能代替。
6. **Git 备份需要 git 2.15+**；没有 git 时自动降级为 `.bak`。
7. 功能与插件同步，但**不会再跟着插件修 bug**：用得久了，两边行为会逐渐不一致。

### 校验

- 包内 `renpy-toolkit.exe --cli --selftest` 应输出 `self-check passed: renpy-toolkit 1.0.0`。
- 包内 `使用说明.txt` 写了放置位置、数据目录与三条已知限制。

### 许可

MIT。上游参考：[renpy/renpy](https://github.com/renpy/renpy)、
[CensoredUsername/unrpyc](https://github.com/CensoredUsername/unrpyc)、
[Dclef/renpy-tl](https://github.com/Dclef/renpy-tl)、
[ZYKsslm/RenPy-Tutorial](https://github.com/ZYKsslm/RenPy-Tutorial)、
[Shizmob/rpatool](https://github.com/Shizmob/rpatool)。

---

## English

### How the three lines divide the work

| Line | Role | Limits |
|---|---|---|
| **Plugin / pip** (recommended) | Where every feature lives; the only line that keeps receiving updates; usable from a DSH plugin or CI | Needs Python 3.8+ (or the runtime bundled in the portable zip); `tl gen` must run on the **target game's own** `lib/py3-*/python.exe` |
| **agent skill** (recommended for AI use) | No code — teaches an AI assistant the same commands and judgement | Its ceiling is the plugin: it cannot run without the toolkit on the machine; if copied away, `scripts/rptk.py` must locate the toolkit or `RPTK_HOME` must be set |
| **Windows exe** (this page, DEPRECATED) | A GUI shell with the same features, no command line | Windows only; the UI borrows your system browser; no further fixes |

### What this package does

Unzip it beside the game folder (same level as the game folder, or its parent) and
double-click `renpy-toolkit.exe`. It scans its own folder and one level below, keeps only
real Ren'Py games (evidence chain: `RENPY RPC2` magic, a `renpy/` runtime, `lib/py3-*`,
a launcher that imports renpy, and `game/*.rpy` sources), and shows a picker.

Covered: skeleton generation, translation checks, Excel export/import, carrying old
translations over, terminology checks, hard-coded text scan, backup and rollback, font
tools, archives and decompilation.

**Write boundary**: only the skeleton goes into `game\tl\<language>\`. Everything else —
logs, exported spreadsheets, decompiled scripts, snapshots, scratch files — lands in
`renpy-toolkit-数据\` beside the exe.

**Guard rails**: launching from a drive root shows "dangerous path, the tool stops
working" and exits after 10 seconds; a second launch does not start a second instance;
with no game found it tells you to move the exe next to the game.

**Closing the page quits the tool**: the page sends a heartbeat every 4 seconds; with no
request for **20 seconds** the tool exits (it watches the page, not the browser process).
The "退出工具" button quits immediately.

### Limits of this package (read before choosing it)

1. **Deprecated** — no fixes after this build. For fixes, use the plugin or the skill, or
   build your own.
2. **Windows only**, and large (a ~17 MB single file that unpacks on every start).
3. **The UI uses your system browser**: by default it opens a standalone window without
   an address bar; on some machines Edge's own "can't update Microsoft Edge" page covers
   it — use `renpy-toolkit.exe --tab` to open a tab in your existing browser instead.
4. **Restricted accounts may fail to start**: the single-file bootloader must create its
   unpack directory; some machines (restricted token, group policy, security software)
   report `Could not create temporary directory`. A self-built copy can pass
   `--runtime-tmpdir D:\rt`.
5. **`tl gen` still needs the game's own interpreter** — the skeleton must come from the
   target game's runtime; the exe cannot substitute for it.
6. **Git snapshots need git 2.15+**; without git the mode degrades to `.bak`.
7. Features match the plugin today, but **bug fixes will not be backported**: over time
   the two will drift apart.

### Verification

- `renpy-toolkit.exe --cli --selftest` should print `self-check passed: renpy-toolkit 1.0.0`.
- `使用说明.txt` inside the zip covers placement, the data folder and three known limits.

### License

MIT. Upstream references: [renpy/renpy](https://github.com/renpy/renpy),
[CensoredUsername/unrpyc](https://github.com/CensoredUsername/unrpyc),
[Dclef/renpy-tl](https://github.com/Dclef/renpy-tl),
[ZYKsslm/RenPy-Tutorial](https://github.com/ZYKsslm/RenPy-Tutorial),
[Shizmob/rpatool](https://github.com/Shizmob/rpatool).
