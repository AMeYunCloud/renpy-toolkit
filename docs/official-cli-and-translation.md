# Ren'Py 翻译机制与 CLI — 权威要点（官方文档核实）

来源：https://www.renpy.org/doc/html/translation.html 与 https://www.renpy.org/doc/html/cli.html
（Ren'Py 8.5.4 文档）

## 1. 四类可翻译对象

1. **Dialogue** — 脚本主对白，支持拆分、合并、省略、重排
2. **Menus and Interface Strings** — 所有界面文本
3. **Images and Files** — `game/tl/<lang>/library.png` 优先于 `game/library.png`
4. **Styles** — `translate style` / `translate python` 块按语言改字体等

## 2. 语言概念

- **None 语言** = 主语言（原文），`_preferences.language == None`
- 备用语言名必须能当 Python 标识符（字母/下划线开头，后接字母数字下划线）
- None 语言下多数翻译功能关闭，但引擎内部字符串仍翻译，位于 `game/tl/None/common.rpym`

## 3. 生成翻译文件

- launcher → "Generate Translations"，或 CLI：`renpy.sh <basedir> translate <language>`
- 目录：`<project>/game/tl/<language>/`，**每个源脚本文件一个翻译文件**
- `common.rpy` 也在该目录下（含所有 Ren'Py 游戏通用字符串）

## 4. 翻译单元（Translation Units）★核心概念

> 翻译的基本单元是「零个或多个可翻译语句（voice / nvl），可选后跟一个 say 语句」组成的块。

- 标识符由**该单元之前的 label 名** + **单元内语句**生成
- 同一 label 内重复 → 第二个及以后追加序列号（`_1`、`_2`）
- 单位在**脚本加载时自动创建**

示例：
```
label start:
    e "Thank you for..."   → start_636ae3f5
    show eileen happy      （非可翻译语句，不影响 unit）
    e "We aim to..."       → start_bd1ad9e1
```

## 5. translate 语句生成形态（关键！插件必须按此格式写）

```
# game/script.rpy:95
translate piglatin start_636ae3f5:

    # e "Thank you for taking a look at the Ren'Py translation framework."
    e ""
```

要点：
- 第一行是**源文件注释** `# game/<源文件名>.rpy:<行号>`（Ren'Py 忽略，仅供人读）
- 第二行 `translate <language> <identifier>:`
- 空行
-   注释行镜像原文（`    # <原语句>`）
-   译文行（无标识符前缀，如 `e "译文"`）

非 1:1 翻译的三种合法形态：

```python
# a) 拆成多行
translate piglatin start_bd1ad9e1:
    # e "..."
    e "前半部分..."
    e "...后半部分"

# b) 删除该语句
translate piglatin start_9e949aac:
    # e "..."
    pass

# c) 插入 Python
translate piglatin start_36562aba:
    # e "You scored [points] points!"
    $ latin_points = to_roman_numerals(points)
    e "Ouyay oredscay [latin_points] ointspay!"
```

只有当主脚本中遇到对应块时，Ren'Py 才检查 translate 语句是否存在 → 存在则改执行它。

## 6. 标识符相关的开发者注意事项

- 想改原文又不想让译者重译 → 在原 say 语句上加 `id` 子句：
  ```
  e "This used to have a typo." id start_61b861a2
  ```
- 加 label 会干扰翻译生成 → 带 `hide` 子句的 label 被忽略：
  ```
  label ignored_by_translation hide:
      "..."
  ```
- translate 块里的 Python **不应有对外的副作用**（切语言会重启该块 → 副作用执行多次）

## 7. 菜单与字符串翻译

- Ren'Py 生成翻译时会扫描脚本中的 **menu** 和 **`_()` 包裹的字符串**
- 全部放进 `translate <language> strings:` 块：
  ```
  translate piglatin strings:
      old "Eileen"
      new "Eileenway"
  ```
- **字符串翻译也作用于「未按对话翻译的对白字符串」**
- 同串多处出现可用 `{#...}` 文本标签区分（展示相同但视为不同串）：
  ```
  "New"
  "New{#project}"
  "New{#game}"
  "New{#playlist}"
  ```
- `translate None strings:` 可翻译 None 语言（源码非英语时汉化 Ren'Py 自身 UI）

### 替换插值的翻译：`!t` 转换标志

```python
if mood_points > 5:
    $ mood = _("great")
else:
    $ mood = _("awful")

"I'm feeling [mood!t]."      # ← !t 才会查字符串表；裸 [mood] 不会
```

### 字符串抽取/合并（Extract / Merge String Translations）

多步流程（launcher 或 CLI）：
1. 源项目 → "Generate Translations"
2. 输语言名 → "Extract String Translations"
3. 目标项目 → "Generate Translations"
4. 输语言名 → "Merge String Translations"

两个合并选项：
- **Replace existing translations**：勾选后，「非平凡」（非空且 != 源串）的既有译文也会被替换；默认拒绝覆盖
- **Reverse languages**：合并前反转字符串（可把 en→ru 表反做成 ru→en）

## 8. 图片/文件翻译

- 语言生效时，Ren'Py 优先在翻译目录找文件
- `game/library.png` → 用 `game/tl/piglatin/library.png`
- 子目录也要带上：`game/gui/main_menu.png` → `game/tl/piglatin/gui/main_menu.png`

## 9. 样式翻译

- `translate <language> style <style_name>:` 改样式
- 更常见的是 `translate <language> python:` 改 `gui.text_font` / `gui.system_font`
- 语言激活时：重置样式到 init 阶段结束时的内容 → 跑该语言所有 `translate python` 块、
  所有 style 块、所有 `translate style` 块（保证文件中靠前的先执行）→ 重建样式

## 10. 延迟翻译加载

```
define config.defer_tl_scripts = True
```
- 阻止引擎在 init 阶段加载 `tl/<language>/` 下的脚本文件，改为**语言首次激活时**加载
- 因此这些文件**只能**含 `translate` / `translate python` / `translate style` 块
  （不能含 init / python / screen / image / transform 等 init 期语句）

## 11. 默认语言的选择顺序

1. `RENPY_LANGUAGE` 环境变量
2. `config.language`（覆盖以下所有）
3. 游戏以前选过的语言
4. 首次运行且 `config.enable_language_autodetect` 为 True → 用
   `config.locale_to_language_function` / `config.locale_to_language_map` 自动探测
5. 首次运行 → `config.default_language`
6. 否则 None 语言

## 12. 翻译信息屏与 API

- 开发者菜单（shift+D）→ "Show Translation Info"
- 非开发者：`show screen _translation_info`
- `Language(_language)` action 切语言
- `renpy.change_language(language, force=False, rebuild=False)`
- `renpy.get_translation_identifier()`
- `renpy.get_translation_info(identifier=None, language=None)` →
  `.language / .identifier / .filename / .linenumber / .source`
- `renpy.known_languages()` → 已知语言集合（**不含 None**）
- `renpy.translate_string(s, language=default)` → 立即翻译，**不加入可翻译清单**

## 13. ★ CLI 命令清单（Ren'Py 8.5.3+，插件要封装的核心）

语法：`./renpy.sh [全局选项] [<basedir>] [<command>] [命令选项]`

### 翻译与本地化
| 命令 | 作用 |
| --- | --- |
| `<basedir> translate <language>` | 创建/更新 `tl/<language>` 翻译文件。**只补缺失条目，不动已有译文** |
| `<basedir> dialogue <language>` | 导出对白到 `dialogue.tab`（或 `--text` 出 `dialogue.txt`）。给了语言名则输出译文，否则输出原文 |
| `<basedir> extract_strings <language> <destination.json>` | 把该语言的既有译文导出为 JSON |
| `<basedir> merge_strings <language> <source.json>` | 从 JSON 导入译文（**隐含 `--compile`**） |

`translate` 的选项：`--count`（只报缺失数不写文件）、`--rot13`、`--piglatin`、`--empty`、
`--min-priority <int>`、`--max-priority <int>`（默认 499 或 299，取决于
`config.translate_launcher`）、`--strings-only`、`--common-only`、`--no-todo`、
`--string <string>`（可多次）

`dialogue` 的选项：`--text`、`--strings`（输出**所有**可翻译字符串，不只是对白）、
`--notags`（剥文本标签）、`--escape`（转义引号等）

`extract_strings` 的选项：`--merge`（与文件现有内容合并，保留已有译文）、`--force`（语言不存在也不抛异常）

`merge_strings` 的选项：`--reverse`（反转语言）、`--replace`（替换非平凡译文）

### 其它常用
| 命令 | 作用 |
| --- | --- |
| `<basedir> compile [--keep-orphan-rpyc]` | 编译 .rpy → .rpyc |
| `<basedir> lint [<file>]` | Lint 检查；`--error-code` 有 lint 错误时返回 1（CI 用） |
| `<basedir> test [<testcase>]` | 跑自动化测试 |
| `<basedir> run` | 运行（默认命令） |
| `<basedir> quit` | 立即退出 |
| `<basedir> add_from` | 给无 `from` 的 call 加 from 子句（**会改脚本文件**，发行前用） |
| `launcher distribute <basedir> [--destination D]` | 构建 Win/Mac/Linux 发行版 |
| `launcher android_build <basedir>` / `ios_create` / `web_build` | 移动/Web 构建 |
| `launcher generate_gui <basedir>` | 给既有游戏生成 GUI |
| `<basedir> rmpersistent` | 删 persistent 数据 |
| `<basedir> update <url>` | 从 web host 装更新 |

### 全局选项
`--savedir <directory>`、`--trace <level>`、`--version`、`--compile`（强制重编译）、
`--compile-python`、`--keep-orphan-rpyc`、`--errors-in-editor`、`--safe-mode`、`--help`
`--json-dump <file>` / `--json-dump-private` / `--json-dump-common`（导出游戏信息到 JSON）

> ⚠️ 官方明确说 "The CLI isn't a stable interface - it may change between Ren'Py releases"

### 自定义命令
`renpy.arguments.register_command(name, function, uses_display=False)`
→ 可在 `init python:` 块里注册项目专属 CLI 命令，函数返回 True 则继续正常启动，否则终止。

## 14. 未授权翻译（Unsanctioned）— 汉化组的核心技术路径

官方支持的三步（无需作者给脚本）：

1. 设 `RENPY_LANGUAGE=<目标语言>` 环境变量
2. 设 `RENPY_UPDATE_STRINGS=<非空>` 环境变量
3. **完整通玩游戏直到所有文本都出现过**

→ 自动更新 `game/tl/<language>/strings.rpy`，产出含全部字符串的翻译模板。

若游戏不支持切语言，用 `init python` 块强制：
```
init python:
    config.language = "<目标语言>"
```

再配合字符串翻译机制 → 无源码也能汉化整个游戏。
「最好先征得游戏作者同意」——官方原文如此提醒。

---

## 15. 对插件设计的直接推论

1. **权威翻译文件生成必须调用 SDK CLI**（`translate <lang>`），不要自己猜标识符
2. **导出/导入走 `dialogue --strings --escape` + `extract_strings`/`merge_strings`**，
   这是官方 JSON 通道，跨语言、跨版本稳
3. **校验用 `lint --no-orphan-tl` + `--error-code`**，可进 CI
4. **`translate` 是非破坏式增量** → 可以反复跑，适合「先跑官方生成模板，再润色」的工作流
5. **`--count`** 可用于汉化进度统计
6. 字符串翻译的 `{#...}` 标签机制可用于**同一字符串不同上下文给不同译文**
