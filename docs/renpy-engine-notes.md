# Ren'Py 引擎架构与核心机制研究笔记（面向插件/外部工具开发）

> 版本基准：Ren'Py **8.5.3**（2026-05-15 发布，"We Can Go to the Moon"），文档站为 **8.5.4**（`github.com/renpy/renpy` 默认分支 `master`）。
> 证据标注：**【源码】**= 直接读过 GitHub 上的文件内容/官方 Sphinx 文档原文；**【推断】**= 由命名、目录结构或第三方材料推得，未逐行验证。本次研究未 clone 仓库，仅用网络检索与抓取。

---

## 1. 整体架构

Ren'Py 是一个"Python 包 + Cython/C 运行时 + 资源脚本"的三层结构。

- **引擎主体 `renpy/`**：【源码】顶层有 `main.py`（启动引导）、`bootstrap.py`（无引擎 Python 环境下的最小引导：定位 SDK、准备 `sys.path`、加载 `renpy`）、`__init__.py`（导出 `renpy.exports.*` 的命名空间，约 23 KB）、`config.py`（约 52 KB，全部配置变量）、`game.py`（Game/Context 全局状态，约 12 KB）。
- **解析前端**：`lexer.py` + `lexersupport.pyx`（Cython 加速的词法）、`parser.py`（语句解析器集合）、`ast.py`（约 88 KB，每个语法结构一个 `Node` 子类）、`statements.py`（把 `parser` 函数注册为语句）、`atl.py`（约 73 KB，ATL 动画语言独立解析器）。
- **执行模型**：`execution.py` + `sl2/`。`renpy.game.context()` 返回当前 `Context`，负责"下一条语句"（`next_node`）、`init_phase`、语言标识（`translate_identifier`）、call/return 栈等。语句执行是一个**显式链表遍历**：每个 `Node.execute()` 内部先调用 `renpy.ast.next_node(self.next)` 把控制权交给后继节点，再执行副作用【源码，`ast.py` Node.execute 文档字符串】。
- **显示栈**：`display/`（`core.py`、`behavior.py`、`image.py`、`layout.py`…）+ `gl2/`（GPU 渲染）+ `text/`（文本排版，支持 HarfBuzz）。`display.predict` 做"预加载预测"——这是 Ren'Py 与普通解释器的关键差异：`show` 语句可被预测并提前加载图像，而 `renpy.show()` 函数调用不可预测【源码，python.html 注记】。
- **界面层**：`sl2/`（Screen Language 2：`slparser.py`、`slast.py`、`slcompile.py`、`slscreen.py`、`sldisplayables.py`），把 `screen` 块编译成可执行的屏幕对象。
- **其它**：`loadsave.py`（存档槽管理）、`savelocation.py`、`persistent.py`（跨存档持久数据）、`python.py`（store 字典与 Python 表达式/字节码执行）、`revertable.py`（回滚数据结构）、`rollback.py`、`translation/`（国际化子系统）、`exports/`（把 API 拆分到多个模块后聚合到 `renpy.*`）。

**Python 版本**：Ren'Py 8.x 使用 **Python 3**；官方文档明说"自 8.4 起支持 **Python 3.12**"【源码，python.html】。7.x 为 Python 2 分支（已停止主线维护）【推断，基于官方版本分支持续的 7.x/8.x 并行的说法】。

**启动流程**（`renpy/main.py`）：解析 `arguments.py` 的命令行 → 初始化显示/音频 → `script.py` 加载并编译脚本 → 执行 init 阶段（按 `init` 优先级排序，同优先级按文件路径 Unicode 顺序）→ 进入 splashscreen/main menu。

---

## 2. `.rpy` 脚本语言与编译产物

### 2.1 语法元素（对应 AST 节点的映射）

| 语法 | AST 类（`renpy/ast.py`） | 备注 |
|---|---|---|
| `label` / `jump` / `call` | `Label`, `Jump`, `Call` | `label x hide:` 会被翻译生成器忽略【源码】 |
| `scene` / `show` / `hide` / `with` | `Scene`, `Show`, `Hide`, `With` | 图像名由 `renpy.exports.image_name_component` 规则切分 |
| `say`（角色台词） | `Say` | 可带 `id` 子句固定翻译标识：`e "text" id start_61b861a2`【源码】 |
| `menu` | `Menu` | 选项文本进入字符串翻译池 |
| `transform` | `Transform` | ATL 由 `atl.py` 解析成 `renpy.atl.ATLBlock` |
| `image` | `Image` | 图像声明，名称→displayable 映射**不参与存档** |
| `screen` | `Screen`（属性 `screen` 为 `sl2.slast.SLScreen`） | 执行时调用 `screen.define((filename, linenumber))` |
| `style` | `Style` | 属性 `style_name/parent/properties/clear/take/delattr/variant`；`config.defer_styles` 且处于 init 阶段时延后到 `renpy.translation.deferred_styles` |
| `init [N] python:` / `python:` / `python early:` | `PyCode`（附在语句节点上） | `PyCode.mode` ∈ `"eval"/"exec"/"hide"` |
| `define` / `default` | `Default`（define 也走 `Default`/`Define` 家族） | `Default.execute_default` 使用 `renpy.python.store_dicts[store]`、`_defaults_set`（RevertableSet）、`ever_been_changed` |
| `translate` 系列 | `Translate`, `TranslateSay`, `EndTranslate`, `TranslateString`, `TranslatePython`, `TranslateBlock`, `TranslateEarlyBlock` | 见第 3 节 |

**关键数据结构 `renpy.ast.Node`**【源码】：
- 槽位：`filename`、`linenumber`、`_name` / `name_version` / `name_serial`、`next`。
- `name` 属性：label 节点是字符串；普通节点是 `(filename, version, serial)` 三元组（为节省内存）；`__hash__ = hash(self.name)`，`__eq__` 比较 name —— 因此 **节点本身可作为 `renpy.script.Script.namemap` 的键**。
- 类属性：`translatable`、`translation_relevant`、`rollback ∈ {"normal","never","force"}`、`warp`。
- 方法：`execute()`、`execute_init()`、`get_init()`、`early_execute()`、`predict()`、`scry()`（返回 `Scry`：`_next/interacts/say/menu_with_caption/who/extend_text/multiple`）、`restructure(callback)`、`get_code(dialogue_filter=None)`（可翻译语句必须实现）、`get_reachable()`、`get_translation_strings()`。
- 模块级：`statement_name(name)`（设 `ast.current_statement_name` 并触发 `config.statement_callbacks`）、`next_node(n)`、`chain_block(block, next)`、`default_statements`。

`PyCode` 会在创建时把自己 append 进 `renpy.game.script.all_pycode`（当 `record_pycode` 为真），便于错误回溯与热重载【源码】。

### 2.2 源码 → `.rpyc`
`.rpy` 先被解析成上述 AST，再**pickle 序列化并压缩**为同名 `.rpyc`（带字节码缓存）。`Node.__getstate__`/`__setstate__`、`PyCode.__getstate__` 都实现了跨版本兼容分支（`PyCode` 的 state 元组有 5/6/7 元素多种历史格式，旧存档/旧 rpyc 仍可反序列化）【源码】。CLI 提供 `--compile`、`--compile-python`、`--keep-orphan-rpyc`；`renpy/script.py` 里有 `orphan`/`old-game` 机制，**CI 构建必须保留 `.rpyc`**，否则旧存档无法加载【源码，cli.html】。

### 2.3 `.rpa` 归档
`.rpa` 由 `renpy/loader.py` 抽象成 `File`/`RPAv3` 读取器，归档内的文件路径与 `game/` 下相对路径一一对应。归档支持"先查归档再查磁盘"的透明叠加，因此 `game/tl/<lang>/gui/main_menu.png` 这类覆盖文件可以放进归档生效【推断：由 loader 的搜索顺序推出，未读 loader.py 全文】。自定义归档扩展名可经 `config.archives` 配置。

### 2.4 字符串如何变成 AST 节点
- 台词 → `ast.Say(loc, who, what, with_, interact, attributes, arguments, temporary_attributes)`；带翻译时被包裹为 `ast.TranslateSay`（附加 `identifier`、`language`、`alternate`）【源码】。
- `translate <lang> strings:` 里的 `old`/`new` → `ast.TranslateString(loc, language, old, new, newloc)`，执行时调用 `renpy.translation.add_string_translation(language, old, new, newloc)`【源码】。
- 函数/角色参数 → `renpy.parameter.ParameterInfo / ArgumentInfo`，`ast.py` 里以 `Parameter`、`Signature`、`EMPTY_ARGUMENTS` 别名导出以保持 pickle 兼容【源码】。

---

## 3. 国际化 / 翻译机制

### 3.1 四类可翻译对象
1. **对话**（dialogue）：say 语句。
2. **菜单与界面字符串**（strings）：`menu` 选项、`_()` 包裹的字符串。
3. **图像与文件**：同名路径覆盖。
4. **样式**：`translate <lang> style <name>:`。原文语言称为 **None 语言**；其它语言名必须是合法 Python 标识符【源码，translation.html】。

### 3.2 翻译单元与标识符
- 一个翻译单元 = 0..n 条可翻译语句（如 `voice`、`nvl`）+ 一条 say 语句。
- 标识符由"所在 label + 单元内语句内容"生成，形如 `start_636ae3f5`；重复时追加序号 `start_bd1ad9e1m`【源码】。
- **`translate <language> <identifier>:`** 的解析：`language` 为语言名（None 语言块可省略语言名，写作 `translate None start_xxx:` 或 `translate start_xxx:`【推断，语法的 None 省略规则由文档示例推得】），`identifier` 是上一步生成的字符串标识；块体可为任意语句序列（可拆分、可 `pass` 省略、可插入 python/条件），但块内 Python **应当无副作用**，因为切换语言会重跑该块【源码】。
- 生成文件形态：
  ```renpy
  # game/script.rpy:95
  translate piglatin start_636ae3f5:
      # e "You scored a point!"
      e ""
  ```
- **运行时**：`Translate.execute()` 调 `renpy.game.script.translator.lookup_translate(identifier, alternate)`，命中则 `next_node(其他节点)` 跳过去；未命中就继续执行 None 语言块本体。`TranslateSay.execute()` 做同样查找，并在 finally 里把标识符记入 `renpy.game.persistent._seen_translates`（`config.hash_seen` 为真时存 `renpy.astsupport.hash64(identifier)`），同时递增 `renpy.game.seen_translates_count` / `new_translates_count`。`EndTranslate` 负责复位 `ctx.translate_identifier/alternate_translate_identifier/translated`。`Translate.execute()` 在 `language is not None` 时抛 `"Translation nodes cannot be run directly."`【源码】。
- 变量 **`new`**：不是语法变量，而是 `translate <lang> strings:` 块内的字面量关键字（`old "..."` / `new "..."`），落到 `TranslateString` 的 `old` / `new` 字段；同源字符串用 `{#context}` 标签消歧（`"New{#project}"`）【源码】。

### 3.3 目录与加载顺序
- 翻译文件位于 `game/tl/<language>/*.rpy`，**每个游戏脚本对应一个翻译文件**，另有 `common.rpy` 放引擎内置字符串；None 语言的引擎字符串在 `game/tl/None/common.rpym`【源码】。
- 切换语言时：把所有样式复位到 init 结束时的状态 → 按文件顺序执行该语言的 `translate python` 块、`style` 块、`translate style` 块 → 重建样式【源码】。
- `define config.defer_tl_scripts = True` 可让 `tl/<lang>/` 在 init 阶段不加载，首次激活该语言时才加载；此时这些文件里**只允许**出现 translate / translate python / translate style 块【源码】。
- 默认语言解析顺序：`RENPY_LANGUAGE` 环境变量 → `config.language`（一经设置覆盖其后全部）→ 玩家上次的选择 → 首启且 `config.enable_language_autodetect` 时用 `config.locale_to_language_function` / `config.locale_to_language_map` → 首启时 `config.default_language` → None 语言【源码】。

### 3.4 生成流程（`renpy/translation/`）
- `__init__.py`（38 KB）：语言切换、`add_string_translation`、`deferred_styles`、`known_languages` 等运行期逻辑。
- `generation.py`（15 KB）：`translate` 命令的实现，遍历 AST 找 `translatable` 节点、生成 `<lang>/*.rpy` 的 `translate` 块。
- `scanstrings.py`（6.5 KB）：扫描菜单与 `_()` 字符串；`extract.py` / `merge.py`：字符串导出为 JSON 与合并（`extract_strings` / `merge_strings` 命令）。
- `dialogue.py`（11.6 KB）：导出对话为 `dialogue.tab` / `dialogue.txt` 供外部翻译/校对。
- 额外的 "extra translation" 文件：引擎在编译/生成时还会产出 `common.rpy`/`strings.rpy` 等辅助文件，并把原始行以 `#` 注释保留在译文上方，便于人工比对【源码，translation.html 示例与 CLI 命令列表】。

---

## 4. 可扩展点（对插件/外部工具）

### 4.1 运行时 API
- `renpy.config`：所有配置变量（`config.language`、`config.default_language`、`config.defer_tl_scripts`、`config.save_json_callbacks`、`config.statement_callbacks`、`config.rollback_enabled` 等）。
- `renpy.store` / `renpy.python.store_dicts`：默认 store 与所有 named store 的字典；`store_dicts["store"]` 就是全局命名空间。
- `renpy.exports.*`：数百个函数（`renpy.show`、`renpy.save`、`renpy.change_language`…），实现在 `renpy/exports/` 子模块。
- `renpy.loadsave`：存档槽枚举与 JSON 元数据读写（`list_slots`、`slot_json`、`slot_screenshot`）。
- `renpy.game.script`：`Script` 对象，持有 `namemap`（Node→后继）、`all_pycode`、`translator`。
- `renpy.arguments.register_command()`：注册自定义 CLI 子命令（`init python` 中调用），命令会先完成引擎初始化再执行【源码，cli.html】。
- **关于"反编译器"**：`renpy/` 目录下**没有** `bytecode.py` 或 `decompiler.py` 一类模块（目录清单已核对）；`PyCode` 里确实有 `bytecode` 字段，但生产路径上是编译后的 code object 缓存。因此想从 `.rpyc` 还原 `.rpy`，需要**自己 unpickle AST 再写回手**，或依赖第三方 `unrpyc` 工具【推断】。

### 4.2 外部工具复用官方 parser 的做法
官方对"工具开发"的正式口径是 **CLI 不是稳定接口**（cli.html 明言）。若要在外部工具里复用 Ren'Py 的解析器，通常需要拷贝下列模块（它们之间的依赖相对内聚）：

```
renpy/lexer.py            renpy/lexersupport.pyx   (或纯 Python 回退)
renpy/parser.py           renpy/statements.py
renpy/ast.py              renpy/astsupport.pyx
renpy/parameter.py        renpy/cslots.pyx
renpy/atl.py              renpy/pyanalysis.py
renpy/error.py (诊断用)    renpy/config.py (被大量 import)
```
注意 `ast.py` 依赖 `from renpy.cslots import Object, Slot, IntegerSlot` 与 `from renpy.astsupport import hash32, PyExpr`，这两个是 **Cython 模块**（`.pyx`），外部使用要么编译 `.pyx`，要么写纯 Python 替身【推断，基于 import 清单】。

**更省事的替代路径**：把解析放在引擎进程内完成 —— 用 `renpy.arguments.register_command()` 注册一个 `dump-ast` 命令，在 init 之后遍历 `renpy.game.script.namemap` 导出 JSON；或直接用 `--json-dump` 全局开关让引擎自己吐结构化信息【源码，cli.html】。这条路避开了源码拷贝与版本漂移。

### 4.3 许可
Ren'Py 主体为 **MIT**（`ast.py` 文件头即为完整 MIT 声明）；但官方明确"部分 Ren'Py 代码派生自 **LGPL** 源码，因此 Ren'Py 游戏的发布方式必须满足 LGPL"，建议随发行包附带许可证副本并在 README 中说明 `This program contains free software licensed under a number of licenses, including the GNU Lesser General Public License...`【源码，license.html】。捆绑依赖含 FFmpeg/Fribidi/chardet/libusb（LGPL）、Pygame_SDL2（MIT+LGPL）、SDL2 系列（Zlib）等。**插件若整段拷贝 `renpy/*.py`，必须保留 MIT 头并同时评估 LGPL 部分。**

---

## 5. 版本现状

- 最新稳定版 **8.5.3**（2026-05-15）；文档站已先行发布 **8.5.4** 文档【源码】。
- Python 目标：**3.12**（8.4 起），脚本应兼容 3.12 及以后【源码】。
- SDK 依赖：仓库内 `sdk-fonts/`、`sphinx/`（文档构建）、`gui/`（新项目模板）、`launcher/`、`the_question/`、`tutorial/`（示例项目）、`testcases/`、`scripts/`、`src/`（C/Cython 原生代码）。8.5 起 SDK 会自动下载平台扩展包（Android 的 RAPT、iOS 的 renios、Web 的 renpyweb）【源码，官方发布说明】。
- **8.5 关键变化**：Web 平台支持 Live2D；新增**自动化测试框架**（`testcases.rst`，`renpy.test.testast.TestCase`，对应 `ast.Testcase`）；**local label 放宽**（可在任意全局 label 下声明）；Unicode 17 与更多 emoji。使用 Live2D 需 5.3 版 Cubism SDK。
- **8.x 相关特性**：`define` 记录文件名/行号（launcher 导航与 Lint 依赖它）；`config.default_language` 与 `config.enable_language_autodetect` 构成"自动翻译/自动选语言"支持；`renpy.translation` 子包提供 `change_language` / `add_string_translation` / `known_languages` 等；`renpy.get_translation_info()` 返回 `language/identifier/filename/linenumber/source`，配合 `JSONDB` 可按 `renpy.get_translation_identifier()` 给每句台词挂额外数据（如气泡对话模式）【源码，python.html 的 JSONDB 示例】。

---

## 6. 对开发外部插件最重要的 10 个 API / 数据结构

1. **`renpy.ast.Node`** —— 所有语句的基类；`execute/next/name/predict/get_code/rollback/translatable` 是插件接入执行流的唯一入口。
2. **`renpy.ast.PyCode`** —— 承载所有内嵌 Python 源码 + `mode` + `hashcode`；`all_pycode` 是静态分析/依赖扫描的索引。
3. **`renpy.game.context()`（`execution.Context`）** —— `next_node`、`init_phase`、`translate_identifier`、call/return 栈；插件要插桩必须理解它。
4. **`renpy.python.store_dicts`** —— 所有 store 的字典集合，`store_dicts["store"]` 即全局命名空间；读写游戏变量的事实标准。
5. **`renpy.config`** —— 行为开关集中地；插件通常以 `config.*` 注入而非改引擎。
6. **`renpy.game.script`（`Script`）** —— `namemap`（节点图）、`all_pycode`、`translator`；静态分析的起点。
7. **`renpy.ast.Translate` / `TranslateSay` / `TranslateString` + `renpy.game.script.translator`** —— 翻译查找的唯一真相来源（`lookup_translate(identifier, alternate)`）。
8. **`renpy.translation` 子包（`change_language`、`add_string_translation`、`deferred_styles`）** —— 语言切换与字符串注入。
9. **`renpy.exports/` 导出的函数族 + `renpy.arguments.register_command`** —— 对外 API 与 CLI 扩展点（注意 CLI 非稳定）。
10. **`renpy.loadsave` / `persistent` / `revertable`** —— 存档槽、跨存档数据与回滚语义；任何改变游戏状态的插件都必须与它们协同（记住：`define` 不存档、`default` 存档、`python in` 的 named store 同等参与回滚）。

---

## 附：关键文件路径清单

```
renpy/__init__.py              renpy/exports/*.py           renpy/config.py
renpy/game.py                  renpy/execution.py           renpy/main.py
renpy/bootstrap.py             renpy/lexer.py               renpy/parser.py
renpy/ast.py                   renpy/statements.py          renpy/atl.py
renpy/python.py                renpy/revertable.py          renpy/rollback.py
renpy/loadsave.py              renpy/savelocation.py        renpy/persistent.py
renpy/script.py                renpy/loader.py              renpy/error.py
renpy/pyanalysis.py            renpy/parameter.py           renpy/dump.py
renpy/translation/__init__.py  renpy/translation/generation.py
renpy/translation/scanstrings.py  renpy/translation/extract.py
renpy/translation/merge.py     renpy/translation/dialogue.py
renpy/sl2/slparser.py          renpy/sl2/slast.py           renpy/sl2/slcompile.py
renpy/display/*.py             renpy/gl2/*.py               renpy/text/*.py
renpy/test/testast.py          gui/  launcher/  sphinx/source/*.rst
```
