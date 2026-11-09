# renpy-toolkit 1.0.0（中文说明）

面向 Ren'Py 游戏的汉化与制作辅助工具包。核心能力是**从零生成完整的汉化骨架**——
包括只有 `.rpyc`、拿不到源码的发行版。

> 英文完整变更记录见 [CHANGELOG.md](https://github.com/AMeYunCloud/renpy-toolkit/blob/main/CHANGELOG.md)。

## 这个版本解决什么

官方 `renpy <游戏> translate <语言>` 只遍历磁盘上的 `.rpy` 文件，所以对「作者只发了
`.rpyc`」的成品游戏产不出任何骨架——而这恰恰是汉化最常遇到的形态。

本工具改成**运行游戏本身**，从引擎内存里读 `translator.file_translates` 与 SL2 屏幕树，
因此不依赖源码。在一份 50 个 `.rpyc` 的发行版上实测：

| 指标 | 结果 |
|---|---|
| 对白块 | 58,249（引擎自带 `lint` 对原文与译文报的是同一个数字） |
| 字符串 | 2,679 条（界面文字、`show text`、菜单选项等） |
| 耗时 | 19.6 秒 |
| 覆盖率 | 与一份已上线成品译料对比，唯一原文串覆盖 93.0% |

## 四种使用形态

| 形态 | 适合 | 怎么用 |
|---|---|---|
| **Windows exe** | 只想点按钮，不碰命令行 | 解压 zip，放到游戏文件夹旁边，双击 `renpy-toolkit.exe` |
| **一键包** | 有 Python、想留在命令行 | 解压后跑 `renpy-toolkit.cmd`，或 `python renpy-toolkit.py` |
| **pip 安装** | 已经管着 Python 环境 | `pip install renpy-toolkit`（含 Excel/字体功能装 `[all]`） |
| **Agent skill** | 给 AI 助手用 | 把 `skills/renpy-toolkit/` 放进 skill 目录 |

## 主要能力

**生成骨架**
`tl gen` 跑游戏本体，一次抓齐对白与界面文本。骨架用**原文占位**而不是空串——引擎里
`new ""` 是「有效译文」，会把界面文字整片擦成空白。

**没有源码也能读**
`decompile` 用白名单 pickle 虚拟机还原 `.rpyc`，不执行游戏的字节码；`rpa` 读写
RPA-2.0/3.0 归档，支持非默认 key，目录里有两个 `archive.rpa` 也能一起读。

**译文质量**
`verify` 检查覆盖率、标签配对、插值、空译文、结巴丢失等；`port` 在游戏更新后按标识符
把旧译本搬到新骨架；`glossary` 用术语表检查译名统一。

**三类「翻不了」的文本**
- 跨屏多义（同一个 `Load` 在主菜单和导航屏要译成不同结果）→ `tl context` 生成消歧补丁
- 变量拼接（`f"You bought {n} {item}!"`）→ `tl untranslatable` 列出，`tl fix-fstrings` 给方案
- 硬编码字体 → `font map` 生成 `translate <lang> python:` 替换块，`font check` 查缺字

**改文件不怕改坏**
每次写盘前自动留副本（`.bak`）或 Git 快照（`--backup-mode git`），`tl backup list/restore`
可逐次回退。机器上没有 git 时自动降级为 `.bak` 并明确提示。

## exe 形态的两点说明

界面是「本地小服务 + 系统浏览器窗口」，不需要 WebView2；启动后会自动扫描 exe 所在目录
及其一级子目录里的 Ren'Py 游戏，弹窗让你选一个（0 个、1 个、多个都会弹）。

**除「生成汉化骨架」写进 `game\tl\<语言>\` 之外，其余产物都收在 exe 旁边的
`renpy-toolkit-数据\`**（日志、导出表格、反编译结果、备份快照、临时文件），不会在游戏
目录里留东西。

## 已知限制

- `tl gen` 需要游戏自带的解释器（`lib/py3-*/python.exe`）。这是设计使然：骨架必须由
  目标游戏的运行时给出。
- 单文件 exe 每次启动会解包到系统临时目录。若机器策略禁止创建该目录，可用
  `--runtime-tmpdir <可写目录>` 重新打包，或改用一键包形态。
- Git 快照需要本机有 git 2.15+；没有时自动降级为 `.bak`。
- 只提供 Windows exe；Python 形态在三大平台都可运行。

## 许可

MIT。上游参考项目：[renpy/renpy](https://github.com/renpy/renpy)、
[CensoredUsername/unrpyc](https://github.com/CensoredUsername/unrpyc)、
[Dclef/renpy-tl](https://github.com/Dclef/renpy-tl)、
[ZYKsslm/RenPy-Tutorial](https://github.com/ZYKsslm/RenPy-Tutorial)、
[Shizmob/rpatool](https://github.com/Shizmob/rpatool)。
没有 import 它们的代码，规则依据各自在 `docs/` 里逐条标注。
