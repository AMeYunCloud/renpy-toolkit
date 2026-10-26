# ============================================================================
# renpy-toolkit 生成的 TL 文件格式 —— 逐行说明样本
#
# 这个文件本身不是合法的 TL（注释太多），它的用途是让你看清生成器到底写
# 什么、每个位置能不能改、改错会怎样。真正的样本见同目录 sample_*.rpy。
#
# 编码与行尾（生成器强制）：
#   * UTF-8 with BOM（EF BB BF），因为 Ren'Py 读 TL 时按 BOM 判断编码
#   * LF 行尾（Windows 上也写 LF）
#   * 文件末尾保留一个换行
#   * 不写 `# TODO: Translation updated at ...` 头（会污染 diff）
# ============================================================================


# ----------------------------------------------------------------------------
# 形态 1：一句对白（最常见，占 58,249 块里的绝大多数）
# ----------------------------------------------------------------------------
#
# 第一行：源位置注释。`# game/<源文件名>:<源行号>`
#   * 这是给人看的，也是 tl sort 排序、tl verify 回溯的依据
#   * 改它不会影响翻译是否生效，但会让 diff 与源文件对不上
#
# game/script.rpy:56
#
# 第二行：块头。`translate <语言> <标识符>:`
#   * 语言 = **块头里写的名字**，引擎只认这个，不看文件在哪个目录
#     （实测：文件放在 tl/zzzfmt/ 里但块头写 `translate chinese`，
#      引擎会把它的 strings 登记进 chinese 表，与真正的 tl/chinese 冲突，
#      启动即抛 `A translation for "..." already exists at ...`）
#   * 惯例上目录名与块头语言一致，tl/chinese/*.rpy 里就写 chinese
#   * 标识符由引擎生成：`<label>_<md5(code)[:8]>`
#     code = who + 空格 + 转义后的 what，例如 `mc "Why wouldn't I?"`
#     label 里的 `.` 换成 `_`，重复则追加 _2 / _3
#   * **标识符不能手改**。改了引擎就找不到这个块，该句回落到英文
#
# translate chinese start_a55bd194:
#
# 第三行起：块体。**块头与块体之间不留空行** —— 引擎两种都接受，但紧贴的
# 写法让一个块在视觉上连成一块，而且每块省一行（全量 58,249 块就是省掉
# 58,249 行）。缩进用 4 个空格，块体可以有任意多行。
#   * 每句都是「一行注释镜像 + 一行真实语句」的配对
#   * 注释行 `# mc "原文"`：**删掉不影响功能**，但它是翻译时唯一的对照，
#     而且 tl sort 靠它识别块的归属 label
#   * 真实行 `mc "译文"`：**要改的就是这一行的引号内内容**
#   * who（mc）与 with 尾巴（with hpunch）必须原样保留 ——
#     它们属于标识符的计算输入，改了就失配
#
#     # mc "{i}{size=-6}Why wouldn't I? {w}My name is... [mc]."
#     mc "{i}{size=-6}Why wouldn't I? {w}My name is... [mc]."
#
# 块之间：一个空行分隔


# ----------------------------------------------------------------------------
# 形态 2：带同行 with 的对白
# ----------------------------------------------------------------------------
# `with` 写在同一行时会并进标识符的 code，所以镜像行与真实行都必须带上它。
# 写成独立一行的 `with vpunch` 是另一条语句，不并入。
#
#     # ev "HAHAHAHA." with vpunch
#     ev "HAHAHAHA." with vpunch


# ----------------------------------------------------------------------------
# 形态 3：旁白（没有 who）
# ----------------------------------------------------------------------------
# 没有说话人时，镜像行与真实行都以引号开头，前面只有缩进。
#
#     # "You put on your clothes"
#     "You put on your clothes"


# ----------------------------------------------------------------------------
# 形态 4：字符串（界面文本、菜单选项、_() 包裹的字面量）
# ----------------------------------------------------------------------------
# 每个字面量独立一个 strings 块（这是本工具的写法，引擎也接受把多条
# old/new 塞进同一个块）。
#
# 第一行：源位置 `# game/<文件>:<行号>`
# 第二行：`translate <语言> strings:` —— **没有标识符**
# 之后：`    old "原文"` / `    new "译文"` 配对，缩进 4 空格
#
# **old 必须与源码里的字面量逐字符相同**（含 {size=} {w} {image=} 等标签）。
# 多写一个源码里没有的标签 → 永不命中；少写 → 永不命中。
# new 写成空串 "" → 界面文字变空白（不是"没翻译"，是"翻译成了空"）。
#
# game/gui.rpy:329
# translate chinese strings:
#     old "hide"
#     new "hide"
#
# 同一个 old 在**同一门语言**里只能出现一次，否则引擎启动即抛异常：
#   Exception: A translation for "Enter" already exists at game/tl/chinese/x.rpy:3.
# 不同语言（tl/chinese 与 tl/Spanish）可以有相同的 old，互不冲突。


# ----------------------------------------------------------------------------
# 形态 5：跨行字面量（三引号）
# ----------------------------------------------------------------------------
# 源码里用 """ 或 ''' 写的长文本，old/new 也保持三引号跨行形态，
# 行尾保留字面 \n（两个字符），不写真换行。
#
#     old """第一行\n第二行"""
#     new """第一行译文\n第二行译文"""
#
# 注意：字符串里出现**真的**换行会把一条 say 劈成两半，触发
# MISSING_TRANS_LINE / UNCLOSED_QUOTE。要换行就写字面 \n。


# ----------------------------------------------------------------------------
# 形态 6：可选的 Event Index（tl sort --index 加，tl gen 不加）
# ----------------------------------------------------------------------------
# 纯注释，引擎完全不读。作用是让人在大文件里按 label 定位。
# 本工具默认折叠：同一 label 的连续块只记一行区间。
#
# # === Event Index ===
# # chapter_1: L8
# # night_visit: L12-L480
# # === End Index ===
#
# 与之配套的还有分节标记（--sections）：
#
# # --- chapter_1 ---
#
# 这两套东西都不是引擎语法，删掉不影响加载。


# ----------------------------------------------------------------------------
# 翻译时可以怎么改（合法变形）
# ----------------------------------------------------------------------------
# 下面六条都用 `t_edits.py` 跑过：改完能被本工具解析，也能被引擎 lint 接受。
#
# 1. 一条拆成多条：把 `e "长句"` 换成两条 `e "前半"` + `e "后半"`
#    （引擎接受，`tl verify` 会把它算作同一标识符下的多行，不报错）
# 2. 整块删除：删掉块体里所有语句、只留 `pass`
# 3. 插入 python：块内可以写 `python:` 做副作用以外的事
#    （块内 python 在切语言时会重跑，所以不要有外部副作用）
# 4. 钉死标识符：源码里写 `e "..." id start_61b861a2` 可指定标识符
# 5. 多条 old/new 合并进一个 strings 块（更省行数，引擎同样接受）
# 6. 删掉镜像注释行 `# e "原文"`（引擎不需要它，但它是唯一的对照，
#    而且 tl sort 靠它识别块归属）


# ----------------------------------------------------------------------------
# 会报错或静默失效的改法
# ----------------------------------------------------------------------------
# * 改标识符 → 该句回落英文
# * 改 who 或 with → 标识符失配 → 回落英文
# * new 留空 "" → 界面/对白显示空白（引擎 `if new is not None: return new`，
#   空串不是 None，所以是"翻译成了空"而不是"没翻译"）
# * old 与源码不完全一致（多/少标签）→ 永不命中，静默
# * 同一个 old 在同一语言里重复 → 启动抛 Exception（玩家进不到主菜单）
# * 块头语言与目录名不一致 → 语言表串台，可能与另一门语言撞车而抛 Exception
# * 用真换行代替字面 \n → 劈行，解析失败
# * 用中文引号「」代替转义的 \" → 不报错但显示成两个怪字符
#
# 写译文的推荐做法：用 `tl.set_string_new(tl, old, 文本)` 与
# `tl.apply_new_text(tl, 标识符, 文本)`。两者都接收**解码后的文本**
# （要换行就传真换行 chr(10)，要引号就传 "），函数内部负责转义。
# 直接给 `pair.new` 赋值不会写进文件——render 是从行列表重建的。


# ============================================================================
# 第二部分：硬编码文本 —— 三种「源码里没包 _()、也不会被 tl gen 扫到」的文本
# ============================================================================
#
# 这一部分是**比 strings 更泛用**的兜底：只要文本在运行时以字符串身份出现在
# 界面上，它就会过一次 `renpy.translation.translate_string()`，因此都能翻。
# 三条路按「先试上面的标准 strings，实在不行才用下面这些」的顺序选。
#
#   硬编码**字符串**   → 形态 A（清单 + 标记注释），仍是 strings 块，只是多了身份
#   硬编码**字体**     → 形态 B（translate <lang> python 里改 font_replacement_map）
#   运行时拼出来的串   → 形态 C（包装 translate_string，正则映射）
#
# 为什么硬编码文本值得优先用：它**不动源码**。strings 条目在 TL 侧，游戏更新
# 时只要 old 还能对上就继续生效；改源码则每次升级都要重新打补丁。


# ----------------------------------------------------------------------------
# 形态 A：硬编码字符串 —— strings 块 + 身份清单 + 标记注释
# ----------------------------------------------------------------------------
# 适用：源码里直接写 `text "Are you sure?"`、`stack_notify("已保存")`、
#       `$ label = "继续"` 这类**没有 _() 包裹**、`tl gen` 也扫不到的文本
#       （`tl gen` 只静态提取 Say/Menu/_()，运行时才出现的字面量它看不到）。
#
# 关键前提（已实测）：**界面上的 text 字面量一律过翻译表，与源码有没有 _()
# 无关**。`Text.set_text()` → `renpy.substitutions.substitute(translate=True)`
# → `renpy.translation.translate_string(s)`。所以只要 old 逐字符对上，就会生效。
#
# 与普通 strings 的唯一区别是「多一层身份记录」，语法完全一样：
#
#   # hardcoded: confirm screen
#   # game/screens.rpy:120
#   translate chinese strings:
#       old "Are you sure?"
#       new "确定吗？"
#
# * `# hardcoded: <上下文>` 是**纯注释**，引擎不读，解析也不依赖它；
#   它只为人眼与 grep 服务，丢了可以从清单重建。
# * 这一行紧贴在 `# game/...` 源位置注释的**上一行**。
#
# 权威清单：`game/tl/<语言>/hardcoded.json`，记录每条 old 的源位置、原句、
# 上下文、加入时间。作用有三：
#   1. 源文改了以后能查出条目变孤儿（`removed: true`）
#   2. 升级游戏时新增的 strings 与人工新增的分得清
#   3. 可以反向生成报告
#
# 清单形态（`hardcoded.json`，UTF-8）：
# {
#   "version": 1,
#   "language": "chinese",
#   "entries": [
#     {
#       "old": "Are you sure?",          ← 与 old "..." 逐字符相同
#       "new": "确定吗？",                ← 当前译文，随 TL 同步
#       "source": "game/screens.rpy:120", ← 源位置，可写空串
#       "context": "confirm screen",      ← 自由文本，写清是在哪出现的
#       "added": "2026-10-25",
#       "removed": false,                 ← 校验时找不到源文就置 true
#       "kind": "hardcoded"
#     }
#   ]
# }
#
# 命令（都在 `python -m renpy_toolkit` 下，先 `cd plugin/dsh-renpy-toolkit`）：
#   tl hardcoded scan  <语言目录> --language <语言>    扫描 TL，刷新硬编码清单
#   tl hardcoded show  <语言目录> --language <语言>    打印清单内容
#   tl hardcoded apply <语言目录> --language <语言> [--write]
#                                                     给清单命中的块加标记注释
#                                                     默认 dry-run，加 --write 才写盘
#
# 注意：清单里的 old 与 TL 里的 old 必须逐字符一致，包括 {size=} 之类标签。
# 多写一个源码里没有的标签 → 永不命中，且**不报错**（这是最常见的静默失败）。


# ----------------------------------------------------------------------------
# 形态 B：硬编码字体 —— translate <lang> python 里改 font_replacement_map
# ----------------------------------------------------------------------------
# 适用：源码里直接写死字体路径，例如
#
#     text "标题" font "fonts/big_noodle_titling.ttf"
#     style foo:
#         font "fonts/big_noodle_titling.ttf"
#
# 光定义 style 换不掉（调用点把 font 写死了），必须走替换表。
#
# 完整块（照抄，只改两处：语言名、字体路径）：
#
#     init offset = 1
#
#     init python:
#         _original_font_replacement_map = config.font_replacement_map.copy()
#
#     translate chinese python:
#         config.font_replacement_map["fonts/big_noodle_titling.ttf", False, False] = ("tl/chinese/fonts/TBX.ttf", False, False)
#
#     translate None python:
#         config.font_replacement_map.clear()
#         config.font_replacement_map = _original_font_replacement_map.copy()
#
# 逐行解释：
#   * `init offset = 1`        让这段在普通 init 之后执行，避免被覆盖
#   * `init python:`           先备份一份原始表，供切回英文时还原
#   * `translate <lang> python:`  **切到该语言时**执行，所以只有中文下替换
#   * `translate None python:`  **切回原文语言时**执行，清空并还原
#   * 左边 = 源码里写死的字体路径（逐字符相同，含 `fonts/` 前缀）
#   * 右边 = 你用来替换的字体，通常放在 `tl/<语言>/fonts/` 下
#   * 后面两个 `False, False` 是 **(bold, italics)**：
#       不加粗不斜体 → False, False
#       粗体         → True, False
#       斜体         → False, True
#       粗斜体       → True, True
#     **键和值的三个位置必须一一对应**，否则不生效（引擎按三元组精确查表）。
#
# 为什么必须写在 `translate ... python:` 里而不是 `init python:` 里：
# 写在 init 里是全局生效，英文下也会被替换掉。用 translate 块才有语言隔离。
#
# 字体文件要求：
#   * 文件名必须纯 ASCII（中文文件名在部分平台加载失败）
#   * 放 `tl/<语言>/fonts/` 下，随 TL 一起分发
#   * 缺字检测用 `python -m renpy_toolkit font check --tl <语言目录>`
#     （拿字体的 cmap 与译文实际用字比对，报缺哪些字）
#
# 引擎侧只查一次这个表：`renpy/text/font.py` 里 `config.font_replacement_map.get`
# 在 Font 构造时执行，**运行中改表对已构造的 Font 无效**，所以必须靠切语言的
# 时机（translate python 块正好在重建样式之前跑）。
#
# 本工具可以直接生成这个块（`--emit` 只打印模板，不写盘）：
#     python -m renpy_toolkit font map <工程根或game目录> --emit chinese
# 它扫描 `font_replacement_map` / `gui.*_font` / style 里的 font 属性 /
# `Font()` 调用 / 自定义标签 / who_font，汇总成上表形态。
# 加 `--all-states` 会额外要求 regular/bold/italic/bold+italic 四种状态全覆盖。


# ----------------------------------------------------------------------------
# 形态 C：运行时拼出来的串（f-string）—— 包装 translate_string
# ----------------------------------------------------------------------------
# 适用：`f"You bought 1 {item_name}!"` 这类**运行时才定形**的文本。
#
# 为什么不能直接翻：`f"..."` 在进入 translate_string **之前**就已经求值成
# 具体结果串（"You bought 1 Sandwich!"）。你在表里注册模板键
# `"You bought 1 [item_name]!"` 是**永不命中**的（已实测）。
#
# 但反过来有个好消息：算出来的确切串**还会再过一次 translate_string**
# （`Text.set_text` → `substitute` → `translate_string`）。所以两条不改源码的路：
#
#   C-1 枚举法：为运行时会出现的每一个确切结果串建 strings 条目。
#       物品名有限（几十个）时最省事，纯 TL 侧，零代码。
#       缺点：物品名有量级时条目爆炸。
#
#   C-2 模式法：在 `translate <lang> python:` 里包装 translate_string，
#       按正则把 `You bought 1 (.+)!` 映射成 `你买了 1 个\1！`。
#       覆盖不可枚举的情形，仍然不动源码。
#
# 包装写法（本工具 `tl untranslatable --emit-fix` 生成的就是这个形态）：
#
#     init offset = 1
#
#     init python:
#         _original_translate_string = renpy.translation.translate_string
#
#     translate chinese python:
#         import re as _rtk_re
#         _rtk_rules = [
#             (_rtk_re.compile(r"^You bought 1 (.+)!$"), "你买了 1 个{0}！"),
#         ]
#         def _rtk_translate(s):
#             for _pat, _tpl in _rtk_rules:
#                 _m = _pat.match(s)
#                 if _m:
#                     return _tpl.format(*_m.groups())
#             return _original_translate_string(s)
#         renpy.translation.translate_string = _rtk_translate
#
#     translate None python:
#         renpy.translation.translate_string = _original_translate_string
#
# 注意三点：
#   * 规则用 `^...$` 全匹配，避免误伤更长的句子
#   * 捕获组原样塞进译文，组里如果是人名/物品名，它自己还会再过一次翻译
#   * `translate None python:` 里必须还原，否则切回英文还是中文
#
# 怎么知道有哪些 f-string：`tl gen` 结束时会提示条数，并给出位置清单。
# 想随时复查用：
#     python -m renpy_toolkit tl untranslatable <dump.json>
# 参数是 `tl gen` 留在 `<game>/.renpy-toolkit-gen/.../out/rtk_strings.json`
# 的探针输出（`tl gen` 的结尾会打印这个路径）。
#
# 生成补丁文件（写到临时目录，确认后再拷进游戏）：
#     python -m renpy_toolkit tl fix-fstrings --language chinese --outdir <目录> [--write]
# 它会把上面那段包装代码写成一个 `.rpy`，规则由实际命中的 f-string 自动推导。
# 如果某个变量取值可枚举（比如物品名固定几十个），用 `--values item_name=a,b,c`
# 可以让它改用精确条目，连运行期补丁都不需要。
#
# 什么时候才该改源码：需要精确控制语序、或者一句话里有多个变量要重排时，
# 正则模板会写得很勉强。此时改成
#     _("You bought 1 [item]!").format(item=item)
# 注意 **不是** `_("You bought 1 {item}!")` —— `_()` 不查 .format，方括号才是
# Ren'Py 的插值语法，`[item]` 会被 substitute 展开。改源码时只改命中的那一处，
# 不要顺手重构。

